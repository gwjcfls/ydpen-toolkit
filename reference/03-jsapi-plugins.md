# 03 · 自研 jsapi 原生插件（完整契约）

框架（falcon / aiot / JQuick / jqutil_dist）的 jsapi 插件 = 一个 `.so`，暴露一个 C 函数。
自己写一个就能给任何 miniapp 补能力，或替换掉功能残缺的官方插件。

## 1. ABI 契约

```c
#include "quickjs.h"

extern void registerCModuleLoader(const char *name, JSModuleDef *(*loader)(JSContext*, const char*));

void custom_init_jsapis(void)                 // ★ 唯一必须导出的符号
{
    registerCModuleLoader("<moduleName>", loader);
}
```

证据来源（反汇编官方 `libjsapi_shell.so` 的 `custom_init_jsapis`，40 字节）：

```
adrp x0, "shell"        ; 模块名字符串
mov  x1, x0             ; 同一个字符串
ldr  x0, [x29, #0x10]   ; 第一个参数 = module_name
bl   strcmp             ; 比较
b.ne 返回               ; 不等就返回 NULL
... JS_NewCModule(ctx, name, init)
... JS_AddModuleExport(ctx, m, ...)
```

- `registerCModuleLoader` 来自 **`/usr/lib/libjsapi_proxy.so`**；
- `JS_*` 来自**宿主进程的全局符号**（官方插件也**没有**链接 libquickjs，NEEDED 只有
  libstdc++/libm/libgcc_s/libc）→ 我们也**不要**链 libquickjs，让它动态解析；
- **QuickJS 头版本必须等于宿主**：本机是 **2020-07-05**（`manifest.json` 里的
  `"quickjs": {"version": "20200705"}` 也印证）。头不匹配 = JSValue 布局不一致 = 崩溃。

## 2. 三件套：模块名 / 导出名 / 事件名

这三样**必须和 app 侧完全对齐**，任一处错都表现为"页面黑屏"或"功能没反应"。

| 契约 | 怎么查 |
|---|---|
| **模块名**（`registerCModuleLoader` 的第一个参数） | 反汇编原插件里 `strcmp` 的那个字符串；或看 app 的 import 原子（如 `fileServer`） |
| **导出名**（`JS_AddModuleExport`） | 翻原插件字符串表（例：`FileServer` / `fileServer` / `default` 三个都在）；app 用 `import { FileServer } from 'fileServer'` 就要有 `FileServer` |
| **事件名** | app 字节码里的 `.on` 之后的原子（例：`server_started` / `server_stopped` / `server_error`） |

每个导出名各自持有一份引用（`JS_SetModuleExport` 接管所有权）：

```c
static int module_init(JSContext *ctx, JSModuleDef *m) {
    JSValue o = JS_NewObject(ctx);
    JS_SetPropertyStr(ctx, o, "start", JS_NewCFunction(ctx, js_start, "start", 2));
    /* ... */
    JS_SetModuleExport(ctx, m, "default",    JS_DupValue(ctx, o));
    JS_SetModuleExport(ctx, m, "FileServer", JS_DupValue(ctx, o));
    JS_SetModuleExport(ctx, m, "fileServer", o);     /* 最后一次用原引用 */
    return 0;
}

static JSModuleDef *loader(JSContext *ctx, const char *module_name) {
    JSModuleDef *m;
    if (strcmp(module_name, "fileServer") != 0) return NULL;
    m = JS_NewCModule(ctx, module_name, module_init);
    JS_AddModuleExport(ctx, m, "default");
    JS_AddModuleExport(ctx, m, "FileServer");
    JS_AddModuleExport(ctx, m, "fileServer");
    return m;
}
```

**症状对照**：只导出 `default` 时框架日志出现
`SyntaxError: Could not find export 'FileServer' in module 'fileServer'`，app 页面**直接黑屏**
（页面 created() 里 import 失败）。这是本工程最隐蔽的一个坑。

## 3. 线程规则（硬约束）

- QuickJS **只能在 JS 线程**上调用。插件里的后台线程绝不能碰 JSValue。
- **不要用 `popen` / `fork`**：多线程进程里 fork 有死锁风险（曾导致黑屏）。取本机 IP 用
  `getifaddrs()`，取文件信息用 `stat/readdir`，别起子进程。
- 需要"回调 JS"（事件）时，把回调**存在插件里**，在本来就在 JS 线程执行的入口里同步触发：

```c
static JSValue js_start(...) {
    int rc = server_start(port, root);     /* 起来服务线程 */
    if (rc == 0) fire_event(ctx, "server_started");   /* ← 这里仍在 JS 线程，安全 */
    else         fire_event(ctx, "server_error");
    return make_status(ctx);               /* 返回对象，兼容 res.running 与 if(res) */
}
```

app 侧订阅形式是 `Module.on('server_started', cb)`，所以插件要实现 `on(event, fn)`
（顺手加 `addEventListener` / `off` / `removeEventListener` 别名更稳）。

## 4. Promise 式 API（可选风格）

`fs` 插件大量方法返回 Promise（app 里是 `await fs.exists(...)`）：

```c
JSValue promise, resolve, reject;
promise = JS_NewPromiseCapability(ctx, resolving_funcs);
/* 业务线程完成后，把结果写进 resolve（注意仍需在 JS 线程调用 resolve） */
```

复杂/耗时操作（HTTP、文件扫描）建议做成"同步返回 + 事件通知"，比跨线程 resolve 简单得多。

## 5. 编译与自检

```powershell
cd <工作区>\ydpen-adb\tools\build
$env:ZIG_GLOBAL_CACHE_DIR="$PWD\zig-cache-global"; $env:ZIG_LOCAL_CACHE_DIR="$PWD\zig-cache"

# 插件
.\zig\zig.exe cc -target aarch64-linux-gnu.2.29 -shared -fPIC -O2 -I quickjs `
    fileserver_plugin.c -o libjsapi_fileserver_9000000001.so -lpthread

# 独立可执行版（同一份源码，便于在笔上单跑/自测）
.\zig\zig.exe cc -target aarch64-linux-gnu.2.29 -O2 -DFS_TEST_MAIN -DFS_DEBUG `
    fileserver_plugin.c -o fssrv -lpthread
```

产物自检（脚本化，见主 SKILL 附的 Python 片段）：

- `.dynsym` 里定义（非 UND）的符号**只有** `custom_init_jsapis`；
- NEEDED 只有 `libc.so.6`（+ 用到线程时 `libpthread.so.0`）。

**dlopen 冒烟测试**（确认框架能加载）：`scripts\dltest.c` 编成 `dltest` 推上笔运行：

```
/tmp/dltest /path/to/plugin.so
# 内部先 dlopen libquickjs.so / libfalcon.so / libjsapi_proxy.so(RTLD_GLOBAL)，再 dlopen 插件
# 期望：LOADED ok + custom_init_jsapis found
```

## 6. 安装到笔上

两种情况：

**A. 新增插件（app 缺少某能力，例如 `fs`）** —— 推进该 app **活跃槽**的 `libs/`：

```powershell
python scripts\install_fs_plugin.py        # 会自动找 appid 8001145141919811/12 的活跃槽
```
框架随后会把它改名成 `libjsapi_fs_1000000001_<hash>.so`（= 被接纳）。

**B. 替换 app 自带插件（例如 `fileServer`）** —— 同名覆盖原文件，**先备份**：

```powershell
python scripts\fileserver_plugin.py install   # 自动把原版存成 .orig + 本地一份
python scripts\fileserver_plugin.py restore   # 回滚
```

> ⚠️ A 和 B 之后**都必须重启笔**：dlopen 缓存 + mmap 里的旧 `.so` 会让热替换无效甚至黑屏。

## 7. 验证套路（每次都照做）

1. `adb shell "grep -iE 'SyntaxError|Could not find export|not available' /data/applog/YD_PEN_APP.log | tail"`
   —— 无报错是第一关；
2. `miniapp_cli start <appid> --<page>` + 唤醒 + `capture` —— 页面渲染出来（正常 15~26KB，
   空白 1667 字节）；
3. **功能级验证**：自研服务就用 `scripts\test_upload.py` 这类端到端脚本
   （上传 → 笔上 md5 比对 → 下载 → Range → 建目录）；
4. 重启后再测一次，确认是**从磁盘加载**而非内存残留。

## 8. 本工具箱已交付的两个插件

| 插件 | 模块名 | 导出 | 解决的问题 |
|---|---|---|---|
| `fs` | `fs` | `default` | 4.3.5 缺 `fs` jsapi → 阅读器"本地文件"、漫画库白屏。方法：`exists/mkdir/rm/rmdir/unlink/readdir(含 withFileTypes)/stat/readFile/writeFile` |
| `fileServer` | `fileServer` | `default`/`FileServer`/`fileServer` | 原版只有 GET（只读列表）→ 文件互传不能上传。方法：`start/stop/getStatus/getPort/isRunning/getRoot/on/off` + 自研 HTTP 服务（multipart 流式上传、Range 下载、建目录） |

源码在 `assets\plugins\`，编译产物同目录，`-DFS_TEST_MAIN` 自测可执行文件在 `scripts\fssrv`。

## 9. 写 HTTP 服务端时的两个致命 bug（务必别重犯）

1. **缓冲满又不推进 → 死循环空转**：会吃满 CPU，把笔拖到 ADB 掉线（表现为"笔变砖"）。
   规则：任何 `for(;;)` 循环，每轮必须**要么消费数据、要么读到新数据、要么退出**；
   分隔符扫描时把"安全部分"（保留尾部 `boundary_len+4`）吐出去。
2. **找到分隔符就直接返回 → 丢失分隔符之前的数据**（实测 8MB 文件少了 4019 字节）。
   规则：返回前先把 `[pos, pos+i)` 写盘。
