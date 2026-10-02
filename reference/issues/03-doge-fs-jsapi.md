# PenOS 4.3.5 上缺少 `fs` jsapi —— 依赖它的页面全部空白（附自研 `fs` 插件）

## 环境

| 项 | 值 |
|---|---|
| 机型 | 有道词典笔 YDPX6-2（X6 Pro，RK3562 / aarch64 / PenOS **4.3.5**） |
| 小程序 | doge-reader v1.1.2（appid `8001145141919811`）、doge-comic v1.3.1（appid `8001145141919812`） |

## 现象

两个 app 都能启动、界面正常，但凡是需要访问本地文件的地方全空：

- 阅读器：首页正常，「**本地文件**」进去**什么都没有**（白屏/空列表）；
- 漫画：首页正常，「漫画库」一直扫不出任何目录，等于不可用。

## 根因

这些 app 依赖框架的 **`fs` jsapi**（`import fs from 'fs'`，用到
`exists / mkdir / rm / rmdir / unlink / readdir(…, {withFileTypes:true}) / stat / readFile / writeFile`），
而 **PenOS 4.3.5 的框架里没有注册 `fs` 模块** → 模块加载失败 → 页面逻辑拿不到目录内容。

（框架日志里能看到对应的模块加载/导出错误；本机 `miniapp_cli` 下也没有任何 `libjsapi_fs*.so` 可供加载。）

## 我做的修复：自己写一个 `fs` 插件

按框架的 jsapi ABI 实现（导出唯一符号 `custom_init_jsapis`，
用 `registerCModuleLoader("fs", loader)` 注册，`JS_NewCModule` + `JS_AddModuleExport(ctx,m,"default")`，
内部用 `JS_NewPromiseCapability` 返回 Promise）：

```
exists/mkdir/rm/rmdir/unlink/readdir/stat/readFile/writeFile
readdir(path, {withFileTypes:true}) → [{name, isDirectory(), isFile(), path}, …]
```

编译（Windows 上用 zig 即可，**QuickJS 头文件版本必须与宿主一致：2020-07-05**）：

```powershell
zig cc -target aarch64-linux-gnu.2.29 -shared -fPIC -O2 -I quickjs fs_plugin.c -o libjsapi_fs_1000000001.so
```

安装：把 `.so` 推进该 app **活跃槽**的 `libs/`（框架随后会把它规范成 `libjsapi_fs_<id>_<hash>.so`，
看到后缀就说明被框架接纳了），重启框架生效。

实测结果（本机）：

- 阅读器「本地文件」：真实列出 `/userdisk/Favorite` 下的 `miniapps`、`漫画测试`、`WordBook.txt`；
- 首页「最近阅读」也能读出 `WordBook.txt`；
- 漫画：漫画库开始正常扫描目录；
- **重启后依然有效**（框架是全新进程 PID，说明确实从磁盘重新加载，不是内存残留）。

## 给作者的建议

1. **README 增加环境要求**：需要 PenOS 自带 `fs` jsapi 的固件版本；4.3.5 这类固件需自行补插件
   （可以把这个 `fs` 插件作为可选安装项，或在启动时检测 `fs` 是否可用并给出明确提示）；
2. 目前的现象是"静默空白"，建议在 `fs` 不可用时给出**可见的错误提示**（而不是空列表），便于用户自查；
3. 顺带提醒安装方式：`miniapp_cli install` 在**同 appid 已存在**时不会重新提取 `libs/*.so`，
   升级带原生插件的包时需要先 `uninstall`。

我这边有可用的 `fs_plugin.c`（C，约 400 行）与编译好的 aarch64 `.so`，
以及把插件打进 `.amr` 的打包脚本（同步 `manifest.json` 的 cert，其余文件逐字节未变），
**如果愿意收，我可以整理成 PR 或独立的可选插件包**。
