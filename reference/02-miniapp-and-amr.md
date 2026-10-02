# 02 · miniapp(.amr) 安装、调试与重打包

## miniapp_cli

```
miniapp_cli install {amrPath}          # 返回 {"appid":"...","ret":0}
miniapp_cli uninstall {appid}
miniapp_cli start {appId} --{page}     # page 是页面路由名，见下
miniapp_cli startService {appId} {service}
miniapp_cli capture {path}             # PNG 截图
miniapp_cli captureFB {path}           # 直接抓 fb（息屏时返回 ret:-1）
miniapp_cli memoryApp | memoryUsage | memoryUsageGC | dumpMemory | trimImageCache
```

## 目录与槽位

```
/userdisk/miniapp/data/mini_app/pkg/<appid>/<slot>/      app 内容（slot 通常是 a / b）
/userdisk/miniapp/data/mini_app/pkg/<appid>/<slot>/libs/ 原生插件（扁平化）
/userdisk/miniapp/resources/slot_info.sh                 全局活跃槽（_a / _b）
/userdisk/miniapp/data/mini_app/                         app 数据
/etc/miniapp/resources/presetpkgs/                       预置包
```

- 框架加载插件后会把文件名规范化成 `libjsapi_<name>_<hash>.so`；
  **看到带 hash 的名字就说明被框架接纳了**。
- app 只有 `a` 目录时就用 `a`（`slot_info.sh` 说的是系统偏好槽，不是该 app 的实际槽）。

## .amr 包结构

`.amr` = zip。关键文件 `manifest.json`：

```json
{
  "appName": "文件管理器", "version": "1.2.0", "appid": "8001771940015915",
  "icon": "app_icon.png",
  "quickjs": {"version": "20200705", "bigNum": false},
  "cert": {
    "libs/arm64/libjsapi_fileserver.so": {"size": 5398032, "md5": "327ce698..."},
    "app.js.bin": {"size": ..., "md5": "..."}
  }
}
```

- `cert` 覆盖包内每个文件（`size` + `md5`）。**改文件必须同步 cert**。
- 多架构：`libs/arm/`（32 位）、`libs/arm64/`、`libs/arm64-cherry3566/`。
  本机（RK3562）用 **arm64-cherry3566**。
- 页面路由名藏在 `app.js.bin` 的原子串里，例如：
  `... about,pages/index/update.vue  fs(pages/index/html.vue ...` → 路由 `fs` = `pages/index/fs.vue`。
  快速提取：搜可打印字符串 + 上下文窗口（见 SKILL §3）。

## 三条铁律（都用血换的）

1. **覆盖安装不提取 `libs/*.so`**。同一 appid 已存在时 `install` 只更新 JS 文件。
   → 要让包内原生插件生效，必须**先 `uninstall`**（全新设备天然满足）。
   验证：`adb shell "find /userdisk/miniapp/data/mini_app/pkg/<appid> -name 'libs' -o -name '*.so'"`。
2. **装完要重启才稳**。dlopen 会把已加载的 `.so` 按路径缓存；替换文件后框架仍用旧映射，
   热替换一个 mmap 中的 `.so` 甚至会让框架崩溃（黑屏）。
3. **`--{page}` 写错会静默失败**：日志出现
   `fpath(.../a//--fs.js.bin) open failed, errno 2` + `SyntaxError: unexpected end of input`，
   页面什么都不显示。

## 重打包配方（"原本异常的包"）

以原始 `.amr` 为底，只动必要文件：

```python
# 1) 读原包 zip + manifest.json
# 2) 替换目标：libs/arm64*/<xxx>.so （只换需要换的架构）
# 3) cert[path] = {"size": len(new), "md5": md5(new)}
# 4) 其余条目用原始字节写出（zipfile.ZIP_DEFLATED，顺序不变）
# 5) 校验：除替换项外，每个文件 md5 必须等于 cert 里的值
```

现成脚本：`scripts\pack_upload_amr.py`（给文件管理器打上传插件）。
历史上用同一套方法做了 `doge-comic-1.3.1-fs.amr` / `doge-reader-1.1.2-fs.amr`
（补 `fs` 插件）、`file-manager-1.2.0-upload.amr`（补可上传的 fileServer）。

## 已装 app 与依赖

| appid | 应用 | 依赖的自研插件 |
|---|---|---|
| 8001145141919810 | Doge 计算器 | — |
| 8001145141919811 | Doge 阅读 | `fs`（否则"本地文件"空白） |
| 8001145141919812 | Doge 漫画 | `fs`（否则漫画库空白） |
| 8001749644971193 | 元素周期表 | — |
| 8001771940015915 | 文件管理器 | 自带 `shell`；`fileServer` 换成自研版才有上传 |

重装 Doge 阅读/漫画后要重跑 `python scripts\install_fs_plugin.py`（覆盖安装丢 libs）。
重装文件管理器后要重跑 `python scripts\fileserver_plugin.py install`。

## 调试手段

- **框架日志**：`/data/applog/YD_PEN_APP.log`
  - JS 报错（SyntaxError / TypeError / ReferenceError）原文在这；
  - `home-index===topAppId===from X change to Y` 告诉你前台 app 是谁、有没有异常退回桌面；
  - `fpath(...) open failed` 说明模块/文件没找到。
- **截图**：`miniapp_cli capture`（必须先唤醒，见 SKILL §7）。
- **core dump**：`/userdisk/corefile/4.3.5/`。
- 结构验证：`adb shell "ls -la <appdir>/libs/"`、`find` 找 `libjsapi_*.so`。
