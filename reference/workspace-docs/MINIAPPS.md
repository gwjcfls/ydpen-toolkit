# 词典笔 miniapp 安装记录（YDPX6-2 / X6 Pro，系统 4.3.5）

## 最终状态

| miniapp | appid | 状态 |
|---|---|---|
| Doge 计算器 1.2.0 | `8001145141919810` | ✅ 正常 |
| Doge 阅读 1.1.2 | `8001145141919811` | ✅ 正常（装了自制的 `fs` 插件后，本地文件能浏览、能读书） |
| Doge 漫画 1.3.1 | `8001145141919812` | ✅ 正常（漫画库扫描、文件浏览都可用） |
| 元素周期表 0.0.2 | `8001749644971193` | ✅ 正常 |
| 文件管理器 1.2.0 | `8001771940015915` | ✅ 正常（自带原生插件） |

实机截图：`out/x_reader_fm.png`（阅读器文件浏览）、`out/x_comic_index.png`（漫画首页）、
`out/y_comic_local.png`、`out/shot_periodic.png`、`out/shot_fm2.png`、`out/shot_calc2.png`。

## 安装方法（可复用）

```powershell
# 1) 从各仓库 Releases 下 .amr（本机在 miniapps\ 目录）
# 2) 推到笔上
tools\adb.cmd push miniapps\doge-comic-1.3.1.amr /userdisk/Favorite/miniapps/
# 3) 安装（返回 {"appid": "...", "ret": 0} 即成功）
tools\adb.cmd shell "miniapp_cli install /userdisk/Favorite/miniapps/doge-comic-1.3.1.amr"
```

常用命令：

```powershell
tools\adb.cmd shell "miniapp_cli start <appid>"             # 启动
tools\adb.cmd shell "miniapp_cli start <appid> --<page>"    # 启动到指定页（如 --filemanager）
tools\adb.cmd shell "miniapp_cli uninstall <appid>"         # 卸载
tools\adb.cmd shell "miniapp_cli capture /userdisk/Favorite/shot.png"   # 截屏（息屏时会截到空白！）
tools\adb.cmd pull /userdisk/Favorite/shot.png out\shot.png
```

> **截屏注意**：词典笔息屏后背光降到 5，框架会暂停渲染，`miniapp_cli capture` 只能拿到空白。
> 拉回背光+注入一次触摸就能唤醒：
> ```powershell
> tools\adb.cmd shell "echo 200 > /sys/class/backlight/backlight/brightness"
> tools\adb.cmd push tools\build\tap /tmp/tap && tools\adb.cmd shell "chmod +x /tmp/tap; /tmp/tap /dev/input/event1 480 133"
> ```
> （`tap` 是我们自己编的输入注入工具，源码 `tools/build/tap.c`，还能用来远程点屏幕。）

---

## 重点：给 4.3.5 补一个 `fs` jsapi（Doge 阅读/漫画 必需）

### 问题

Doge 阅读/漫画 读写本地文件靠**框架提供的 `fs` 模块**，但 4.3.5 的框架里没有：

- `/usr/bin/miniapp` 只导出 `jsapi_proxy`；全系统 34 个 `libjsapi_*.so` 里没有 `fs`；
- 框架上游 [penosext/miniapp](https://github.com/penosext/miniapp) 的 jsapi 只有
  `AI / Database / Fetch / IME / ScanInput / Shell / Update`，本来就没有 fs；
- 两个 app 的发布包 `all.amr` 里也没有任何 `.so`，所以完全依赖设备。

现象：阅读器首页能显示，但「本地文件」白屏；漫画首页整页白屏。

### 解决：自己写一个 fs 插件

用**文件管理器自带的 `libjsapi_shell.so`**反汇编逆向出插件契约：

```c
/* 插件唯一需要导出的符号 */
extern "C" void custom_init_jsapis(void) {
    registerCModuleLoader("fs", loader);      // registerCModuleLoader 由 /usr/lib/libjsapi_proxy.so 导出
}
/* loader 就是标准 QuickJS 动态模块入口 */
JSModuleDef *loader(JSContext *ctx, const char *module_name) {
    if (strcmp(module_name, "fs") != 0) return NULL;
    JSModuleDef *m = JS_NewCModule(ctx, module_name, module_init);
    JS_AddModuleExport(ctx, m, "default");    // 默认导出 → import fs from 'fs'
    return m;
}
```

依赖核对（都满足）：`JS_*` 由 `/usr/lib/libquickjs.so` 导出（18 个符号逐一核对通过），
`registerCModuleLoader` 由 `/usr/lib/libjsapi_proxy.so` 导出。

API 面是从两个 app 的源码里统计出来的（不是猜的）：

| 调用 | 用途 |
|---|---|
| `await fs.exists(p)` | 存储目录是否存在 |
| `await fs.mkdir(p)` | 建目录（支持多级） |
| `await fs.rm(p)` | 递归删目录 |
| `await fs.readdir(p)` | 文件名字符串数组 |
| `await fs.readdir(p,{withFileTypes:true})` | `[{name, isDirectory(), isFile()}]` |
| `await fs.stat(p)` | `{size,mode,mtimeMs,isFile,isDirectory}` |
| `await fs.readFile(p)` / `writeFile(p,txt)` / `unlink(p)` | 读写文本 |

> 图片不需要二进制读：漫画用 `file://<path>` 交给框架渲染。

### 编译（Windows 上就能做，不需要 Linux）

```powershell
# 源码在 miniapps\fs-plugin\（fs_plugin.c + quickjs.h，QuickJS 头文件版本必须与宿主一致：2020-07-05）
cd miniapps\fs-plugin
$env:ZIG_GLOBAL_CACHE_DIR="..\..\tools\build\zig-cache-global"
$env:ZIG_LOCAL_CACHE_DIR="..\..\tools\build\zig-cache"
..\..\tools\build\zig\zig.exe cc -target aarch64-linux-gnu.2.29 -shared -fPIC -O2 `
    -I . fs_plugin.c -o libjsapi_fs_1000000001.so
```

产物特征（和官方插件一致）：AArch64 共享库、只导出 `custom_init_jsapis`、只依赖 `libc.so.6`。

### 安装

```powershell
python tools\install_fs_plugin.py        # 自动找设备、读活跃槽位、推进两个 app 的 libs/
```

**坑**：miniapp 是 A/B 双槽（`a/`、`b/`），活跃槽写在
`/userdisk/miniapp/resources/slot_info.sh`（内容是 `_b`）；
`miniapp_cli install` **不会把 .amr 里的 `libs/*.so` 复制进去**（我们试过把插件打进包 + 写进 `manifest.cert`，安装后 `a/libs/` 依然不存在），
而且重装会切槽。所以：
**每次重装 Doge 阅读/漫画 之后，都要重新跑一次 `install_fs_plugin.py`。**
装好后框架会自动把文件名规范成 `libjsapi_fs_1000000001_<hash>.so`，看到这个后缀就说明被框架采纳了。

### 验证结果

- 阅读器「本地文件」：真实列出 `/userdisk/Favorite` 下的 `miniapps`、`漫画测试`、`WordBook.txt`；
  点进 `漫画测试` 显示"什么也没有喵…"（阅读器只认文本文件，PNG 被过滤，属正常）；
  首页「最近阅读」也读出了 `WordBook.txt`；
- 漫画首页：`本地文件 / 我的收藏 / 完整历史` 正常渲染，漫画库开始扫描目录；
- 无崩溃：`/userdisk/corefile/` 没有新增 dump，框架进程 PID 稳定。

**重启持久化验证（已通过）**：`adb reboot` 后框架是全新进程（PID 987），
`slot_info.sh` 仍为 `_b`，插件文件仍在活跃槽；启动漫画截图 **12266 字节**（正常渲染），
阅读器首页 **24694 字节** 且能进入文件浏览 —— 说明插件确实是从磁盘重新加载的，不是内存残留。
另外这次重启顺带证明了**固件补丁也是持久的**：重启后 `adb shell auth` 依旧用 `ydpen2026` 登录成功。

## 笔上文件位置

- 安装包：`/userdisk/Favorite/miniapps/`（含我们打的 `*-fsfix.amr`）
- 安装后的 app：`/userdisk/miniapp/data/mini_app/pkg/<appid>/<活跃槽>/`
- 本地备份：`ydpen-adb/miniapps/`、插件源码与产物 `ydpen-adb/miniapps/fs-plugin/`

## 另一个自研插件：文件管理器的 `fileServer`（让「文件互传」能上传）

同一个套路（自己写 jsapi 插件替换原版）也用在了**文件管理器**上：
原版 `libjsapi_fileserver.so` 的 HTTP 服务只有 GET（只读目录列表、没有上传接口），
所以「文件互传」网页根本没法上传文件。重写后的插件支持拖拽/多选上传、大文件流式落盘、
Range 下载、新建文件夹，并补齐了 app 需要的具名导出与 `server_started`/`server_stopped` 事件。

- 成品包：`miniapps/dist/file-manager-1.2.0-upload.amr`
- 源码与产物：`tools/build/fileserver_plugin.c`、`tools/build/libjsapi_fileserver_9000000001.so`
- 一键安装/还原：`python tools\fileserver_plugin.py install|restore|status`（改完**必须重启笔**）
- 完整原因分析、踩坑清单与验证记录：[文件互传上传修复说明.md](文件互传上传修复说明.md)
