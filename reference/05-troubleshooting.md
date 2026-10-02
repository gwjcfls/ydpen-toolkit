# 05 · 排错速查（症状 → 定位 → 修复）

排错第一站永远是框架日志：

```powershell
adb shell "tail -400 /data/applog/YD_PEN_APP.log | grep -iE 'error|SyntaxError|TypeError|ReferenceError|not available|open failed|topAppId'"
```

---

## A. 连不上 / 权限

| 症状 | 定位 | 修复 |
|---|---|---|
| `adb devices` → `offline` 或 `(no serial number)` | USB 枚举正常但 adbd 没起 | 拔线 → 设置→法律监管连点 10~15 下 → 等 5 秒 → 插线，**别反复插拔** |
| `adb devices` 全空 | 笔不在网络也没插好，或 adb server 状态脏 | `adb kill-server` 后重试；TCP 用 `lan_scan_adb.py` 找 IP |
| 每次重启后都要重新认证 | `/tmp/.adb_auth_verified` 是 tmpfs，重启即失效 | 正常现象：`shell auth` 重新输密码 |
| 输了密码仍是 `login with "adb shell auth"` | **命令里用了 `Select-Object -First N`**，把 adb 进程提前杀掉 | 先把输出存变量再截断 |
| `cannot mkdir 'C:\Users\...\.android': Permission denied`、`cannot open ...\Temp\adb.log` | 沙箱/权限 | 设 `ANDROID_USER_HOME`、`TMP`、`TEMP` 到工作区；或给 `~\.android` 做 junction |
| `adb: device offline` 但 `id` 之前还能用 | 上一轮会话把 adbd 拖死了（常见于插件把 CPU 打满） | 等一会/重启笔；并去修插件的死循环 |

## B. app 黑屏 / 白屏

按可能性排序：

| 症状 | 定位 | 修复 |
|---|---|---|
| 打开 app 全黑 | 日志 `SyntaxError: Could not find export 'X' in module 'Y'` | 插件缺**具名导出**：补 `JS_AddModuleExport` + `JS_SetModuleExport`（例：`FileServer`） |
| 打开 app 全黑 | 刚替换过 `.so` 但没重启 | 热替换 mmap 中的 `.so` 会崩：**重启笔**（或用 uninstall→install 重装 app） |
| 打开 app 全黑 | 插件里用了 `popen`/`fork` | 换成 `getifaddrs()` / 直接系统调用；多线程进程里 fork 会死锁 |
| 页面白但 app 没崩 | 截图 1667 字节 = 屏幕息屏 | 设背光 200 + 注入触摸再 capture |
| 页面内容全空（如"本地文件"没东西） | 该 app 依赖的 jsapi 缺失（如 Doge 阅读缺 `fs`） | 装对应插件：`install_fs_plugin.py`，然后重启 |
| 页面上有元素但点了没反应 | 缺事件通知（app 靠 `on('xxx')` 刷新 UI） | 插件实现 `on()` 并在 JS 线程入口同步触发事件 |

## C. 安装 / 插件不生效

| 症状 | 定位 | 修复 |
|---|---|---|
| 装完插件目录里没有 `.so` | `miniapp_cli install` **覆盖**安装时只更新 JS，不提取 libs | 先 `uninstall <appid>` 再 install（全新设备天然 OK） |
| 包里的 `.so` 不在 app 目录 | 同上 | 或手工推进活跃槽 `libs/`（`install_fs_plugin.py` / `fileserver_plugin.py`） |
| 装了、重启了，功能还是老样子 | 装到非活跃槽；或框架改名前文件不合法 | 查 `/userdisk/miniapp/resources/slot_info.sh`；确认文件名变成 `libjsapi_..._<hash>.so` |
| `miniapp_cli install` 报错/装不上 | manifest `cert` 与实际文件 md5/size 不一致 | 改包必须同步 cert（`pack_upload_amr.py` 有校验逻辑） |
| `miniapp_cli start <appid> --<page>` 无反应 | page 名写错 | 日志 `fpath(.../--fs.js.bin) open failed` + `SyntaxError: unexpected end of input`；从 `app.js.bin` 原子串里取正确路由 |
| `miniapp_cli: not found` | 该系统没有 miniapp 框架 | 此路不通，改用纯 adb/root 方案 |

## D. 自研 HTTP 服务 / 文件互传

| 症状 | 定位 | 修复 |
|---|---|---|
| 网页能开但没有上传入口 | 用的是**原版** `libjsapi_fileserver.so`（只有 GET + 只读列表） | 换自研版：`fileserver_plugin.py install` + 重启；或装 `file-manager-1.2.0-upload.amr` |
| `POST /upload` → **405** | 同上（原版只实现 GET） | 同上 |
| 上传成功但 md5 不一致、文件尾部少几百~几千字节 | multipart 解析**找到分隔符就返回**，丢掉分隔符之前的缓冲数据 | 返回前先 `fwrite(buf+pos, 1, i, out)`（见 `fileserver_plugin.c` 的 `mp_find`） |
| 笔 CPU 满载、ADB 掉线、app 卡死 | multipart 缓冲满且无分隔符时**空转死循环** | 保证每次循环都有进展：消费安全部分 / 扩容 / 退出；缓冲上限 16MB 后直接报错 |
| 界面显示"未启动"但服务真的在跑 | app 靠 `server_started` 事件刷新 UI | 插件 `on()` + 在 `start()` 里同步 `fire_event("server_started")`，并让 `start()` 返回带 `running` 的状态对象 |
| `Range` 拖动视频失败 | 未实现 206 | 实现 `Range: bytes=a-b` → 206 + `Content-Range` |
| 大文件上传中断 | 一次性读进内存 / 无流式 | 用流式落盘（`fileserver_plugin.c` 的做法） |

## E. 构建环境

| 症状 | 修复 |
|---|---|
| `zig.exe : error: AccessDenied` | 设 `ZIG_GLOBAL_CACHE_DIR` / `ZIG_LOCAL_CACHE_DIR` 到可写目录（如 `tools\build\zig-cache*`） |
| `.ps1` 报奇怪的"缺少 `}`"、中文乱码 | Windows PowerShell 5.1 用 ANSI/GBK 读无 BOM 的脚本 → **另存为 UTF-8 with BOM** |
| `py7zr ... BCJ2 filter is not supported` | 换 `7zr.exe`（7-zip.org）解压 |
| 插件加载失败（dlopen） | 用 `dltest` 复现：先 `libquickjs.so`/`libfalcon.so`/`libjsapi_proxy.so` 再插件；看 `dlerror` |
| 插件一加载就崩 | QuickJS 头版本与宿主不一致（必须 2020-07-05）；或 JSValue 用法跨线程 |

## F. OTA / 固件

| 症状 | 修复 |
|---|---|
| 固件下载 `HTTP 403` | 请求头加浏览器 `User-Agent` |
| 下载"提前结束" | CDN 截断长连接 → 1MB 分块 + 多线程 + 块内续传（`fast_download.py`） |
| 假 OTA 服务把真服务器请求打回 | probe 阶段**只转发笔的真实 sign/mid**，别自己构造（真服务器会回 `MID length is invalid`） |
| 笔卡在"正在安装 100.00%"，不重启 | `policy.install.rebootUpgrade` 仍是 `"false"` → 改成 `"true"` |
| 安装后校验失败 | `segmentMd5/md5sum/sha/sha256/fileSize/storageSize` 没全部重算（用 `ota_meta.py`） |
| 脚本里 `'State' object has no attribute 'path'` | Python `BaseHTTPRequestHandler` 里是 `self.path`，别写 `self.path` 之外的 `st.path` |

## G. 屏幕 / 交互

| 症状 | 修复 |
|---|---|
| 截图全白/全黑、只有 1667 字节 | 息屏时框架暂停渲染：`echo 200 > /sys/class/backlight/backlight/brightness` + 注入一次触摸（`tap /dev/input/event1 x y`） |
| `miniapp_cli captureFB` 返回 `ret:-1` | 同上，息屏状态下抓 fb 会失败 |
| 点击没反应 | 坐标是**显示坐标**（960×266）；先用 `capture` 看清控件位置再点 |
| 不知道现在前台是哪个 app | 日志 `home-index===topAppId===from X change to Y` |

## H. 一句话排查顺序

1. 设备在线吗？`id` 是 root 吗？
2. **看 `/data/applog/YD_PEN_APP.log`** —— 90% 的答案在这（JS 报错 / open failed / topAppId）。
3. 插件在活跃槽的 `libs/` 里吗？文件名带 hash 后缀了吗？
4. 重启过了吗？（dlopen 缓存 + mmap 是最大的两个"幽灵"）
5. 截图确认 UI 真实状态（别猜，先唤醒再抓）。
