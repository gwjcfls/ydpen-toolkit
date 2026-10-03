---
name: ydpen-toolkit
description: 有道词典笔（YDPX6-2 / X6 Pro、YDPX7-1 / X7 Pro；RK3562；PenOS 4.3.5~4.8）的越权与改造工具集：ADB root 获取与认证、USB offline 排错、miniapp(.amr) 安装与打包、自研 jsapi 原生插件开发（fs / fileServer）、OTA 固件补丁（MITM 解锁）、截图唤醒与触控注入、框架日志排错。Use when working on a Youdao dictionary pen (词典笔, YDPX6-2, X6 Pro, YDPX7-1, X7 Pro, Rockchip RK3562), miniapp / .amr packages, jsapi native plugins, `adb shell auth`, file transfer (文件互传/文件服务器), or OTA firmware patching.
---

# 有道词典笔工具箱（ydpen-toolkit）

两台笔都已经被完整拿下：
**YDPX6-2（X6 Pro，PenOS 4.3.5）** —— ADB root 持久化、5 个第三方 miniapp 可用、自研原生插件可编译安装；
**YDPX7-1（X7 Pro，4.8.6 → 装完 `/Version` 4.8.7）** —— ADB root 持久化，机型差异见
[reference/06-ydpx7-1-chin-pro.md](reference/06-ydpx7-1-chin-pro.md)。
本 skill 是复用这套能力的作战手册。

## 0. 先看这里

**工作区（历史产物、amr 成品、固件镜像全在这）**

```
C:\Users\Sever\Documents\deepseek-harness\default-workspace\ydpen-adb\
├── tools\            脚本（adb 封装、OTA 工具、插件安装器…）
├── tools\build\      交叉编译产物与源码（zig、tap、quickjs 头、插件 .so）
├── miniapps\         .amr 安装包（含我们重打包的 *-fsfix.amr / *-upload.amr）
│   └── dist\         交付成品包
├── firmware\         OTA 固件（patched + backup\ 原版）
├── out\              截图与日志
└── *.md              README / MINIAPPS / 战果与使用说明 / 文件互传上传修复说明
```

本 skill 目录下 `scripts/` 与 `assets/` 是同一批工具的**自包含副本**（工作区换机器时用它们）。
`reference/` 放分主题深挖文档，`reference/workspace-docs/` 是工作区原始文档的备份。

> **路径约定**：下文出现的 `scripts\xxx`、`assets\xxx`、`reference\xxx` 都**相对本 skill 目录**
> （加载时给出的 skill directory）。工作区里还有同名工具，位于
> `ydpen-adb\tools\`（脚本）与 `ydpen-adb\tools\build\`（编译产物）。两边内容一致，
> 优先用 skill 目录里的副本，产物路径默认当工作区用。

**动手前必读**

- 涉及**改固件/刷机**的操作有变砖风险：先确认 `firmware\backup\` 里有原版镜像，再动手。
- 改完插件或固件都要**重启笔**才生效；重启后 ADB 开关可能复位（见 §1）。
- 任何"替换正在被框架 mmap 的 .so"都可能导致黑屏 —— 别热替换，直接重启。

## 1. 连上笔 + 拿到 root

```powershell
$root='C:\Users\Sever\Documents\deepseek-harness\default-workspace\ydpen-adb'
$env:TMP="$root\tmp"; $env:TEMP="$root\tmp"; $env:ANDROID_USER_HOME="$root\.android"   # 必须！否则 adb 因权限失败

# TCP（笔连着热点/局域网时优先，比 USB 稳）
& "$root\tools\platform-tools\adb.exe" connect <笔IP>:5555

# 认证（本套流程给笔设的密码是 ydpen2026；换机器时用交互式输入）
"ydpen2026" | & "$root\tools\platform-tools\adb.exe" -s <serial> shell auth   # 打印 success. 即通过
& "$root\tools\platform-tools\adb.exe" -s <serial> shell id                    # 必须 uid=0(root)
```

找笔的 IP：`python scripts\lan_scan_adb.py --port 5555`（扫 192.168.137.0/24 与 192.168.1.0/24）。

**USB 显示 `offline` 或 `(no serial number)` = adbd 没起来，不是线的问题**：

1. 拔下 USB；
2. 笔上 **设置 → 法律监管 → 连点文本 10~15 下**（弹出"ADB 调试已打开"）；
3. 等 5 秒，插上 USB，**放着别反复插拔**。

其它已踩过的坑（详见 `reference/01-access-and-adb.md`）：
- `Select-Object -First N` 会提前掐断 adb 进程 → **认证会失败**，别对 adb 命令用；
- `/tmp/.adb_auth_verified` 是认证标记，重启即失效 → 重新 `shell auth`；
- 杀进程用 `killall`；后台常驻要用 DSH 后台任务跑 `adb shell <cmd>`，否则 shell 一退进程就没了。

## 2. 设备与环境事实（速查）

> **YDPX7-1（X7 Pro）** 的差异速查见 **[reference/06-ydpx7-1-chin-pro.md](reference/06-ydpx7-1-chin-pro.md)**：
> productId `1715159243`（X6 是 `1687695728`）、OTA 路径 key `02101ba2f0a04473`、
> 触摸是 **event4 且上报竖屏物理坐标（必须换算）**、**没有 fb0**（截图只能用 `miniapp_cli capture`）、
> **adb TCP 5555 开箱可用**。下表是 X6 的原始记录。

| 项 | 值 |
|---|---|
| SoC / 系统 | Rockchip **RK3562**（MELON LP4 V10 Board），Linux 5.10.160 aarch64 |
| 框架 build target | `dictpen_youdao_3566`，PenOS **4.3.5** |
| 屏幕 | **960×266**（rotation 270），fb `/dev/fb0` |
| 触摸设备 | `/dev/input/event1`（`fts_ts`） |
| 背光 | `/sys/class/backlight/backlight/brightness`（**5 = 息屏，200 = 亮**） |
| 框架日志 ★ | **`/data/applog/YD_PEN_APP.log`**（JS 报错、dlopen、topAppId 都在这） |
| 其它日志 | `/data/applog/DictPen_*.log`、`Capframe_*.log` |
| core dump | `/userdisk/corefile/4.3.5/` |
| app 目录 | `/userdisk/miniapp/data/mini_app/pkg/<appid>/<slot>/` |
| 活跃槽 | `/userdisk/miniapp/resources/slot_info.sh`（内容 `_a` 或 `_b`） |
| 插件目录 | `<slot>/libs/`（扁平化，框架会改名成 `libjsapi_<name>_<hash>.so`） |
| 共享目录 | `/userdisk/Favorite/`（放 amr、截图都在这） |
| 系统 jsapi 库 | `/usr/lib/libquickjs.so`、`/usr/lib/libjsapi_proxy.so`、`/usr/lib/libfalcon.so` |
| QuickJS 版本 | **2020-07-05**（编插件时必须对齐） |

**已安装的 appid**

| appid | 应用 |
|---|---|
| 8001145141919810 | Doge 计算器 |
| 8001145141919811 | Doge 阅读（需 `fs` 插件） |
| 8001145141919812 | Doge 漫画（需 `fs` 插件） |
| 8001749644971193 | 元素周期表 |
| 8001771940015915 | 文件管理器（自带 `shell` + `fileServer` 插件） |

## 3. miniapp 安装 / 打包

```powershell
$adb="$root\tools\platform-tools\adb.exe"; $s='<serial>'
& $adb -s $s push miniapps\dist\xxx.amr /userdisk/Favorite/miniapps/
& $adb -s $s shell "miniapp_cli uninstall <appid>"      # 装过就先卸载（见下）
& $adb -s $s shell "miniapp_cli install /userdisk/Favorite/miniapps/xxx.amr"
& $adb -s $s shell "miniapp_cli start <appid>"           # 指定页面：--<page>
```

`miniapp_cli` 子命令：`install / uninstall / start {appId} --{page} / capture {path} / captureFB / memoryApp / memoryUsage / dumpMemory`。

**三条铁律**

1. **覆盖安装不会重新提取 `libs/*.so`** —— 同一 appid 已存在时只更新 JS。
   要让包里的原生插件生效，**必须先 `uninstall`**（全新设备天然满足）。
2. `.amr` 是 zip，`manifest.json` 的 `cert` 记录每个文件的 `size`+`md5`；
   **改包必须同步 cert**，否则装不上。打包脚本现成的：`scripts\pack_upload_amr.py`（可照抄改）。
3. 页面路由名从 app 内的 `app.js.bin` 原子串里读（例如文件管理器是 `fs`）。
   `--fs` 这类参数若写错，日志里会出现 `fpath(.../--fs.js.bin) open failed` + `SyntaxError`。

**"原本异常的包"重打包配方**（Doge 阅读/漫画缺 `fs` jsapi、文件管理器缺上传能力）：
用原始 `.amr` 为底 → 只替换需要的 `libs/<arch>/*.so` → 同步对应 cert 的 size/md5 →
**逐字节校验其余文件未变**。成品在 `miniapps\dist\`。

## 4. 自研 jsapi 原生插件（本工具箱最核心的技术）

框架的 jsapi 插件就是一个 `.so`，用**同一套 ABI** 就能自己写：

```c
extern void registerCModuleLoader(const char *name, JSModuleDef *(*loader)(JSContext*, const char*));

void custom_init_jsapis(void) {          /* 唯一需要导出的符号 */
    registerCModuleLoader("fileServer", loader);   /* 模块名必须和 app 的 import 一致 */
}

static JSModuleDef *loader(JSContext *ctx, const char *module_name) {
    JSModuleDef *m = JS_NewCModule(ctx, module_name, module_init);
    JS_AddModuleExport(ctx, m, "default");
    JS_AddModuleExport(ctx, m, "FileServer");   /* ← app 用 import { FileServer } 时必须有 */
    JS_AddModuleExport(ctx, m, "fileServer");
    return m;
}
```

**五个致命细节**（每一个都真实踩过，症状见 `reference/05-troubleshooting.md`）

1. **导出名必须与 app 的 import 完全一致**。缺具名导出 → 框架日志
   `SyntaxError: Could not find export 'FileServer' in module 'fileServer'` → **app 页面黑屏**。
   怎么知道要导出什么：翻原插件字符串表的导出名，或看 app 字节码里的 import 原子。
2. **模块名 = 原插件里 `strcmp(module_name, "...")` 的那个字符串**（用反汇编确认）。
3. **不要在插件里用 `popen`/`fork`**（多线程框架进程会死锁 → 黑屏）。取 IP 用 `getifaddrs()`。
4. **不要跨线程调 JS**。需要发事件时，在本来就在 JS 线程上的入口（如 `start()`）里**同步**回调：
   app 侧订阅是 `Module.on('event', cb)`，插件实现 `on()` 存回调 + 在 start/stop 里触发即可。
5. **QuickJS 头文件版本必须等于宿主版本（2020-07-05）**，否则 JSValue ABI 不匹配。

**编译**（Windows 上就行，无需 Linux）

```powershell
cd $root\tools\build
$env:ZIG_GLOBAL_CACHE_DIR="$PWD\zig-cache-global"; $env:ZIG_LOCAL_CACHE_DIR="$PWD\zig-cache"
.\zig\zig.exe cc -target aarch64-linux-gnu.2.29 -shared -fPIC -O2 -I quickjs `
    fileserver_plugin.c -o libjsapi_fileserver_9000000001.so -lpthread
# 自测可执行版：额外加 -DFS_TEST_MAIN（想带日志再加 -DFS_DEBUG）
```

产物自检：只导出 `custom_init_jsapis`，NEEDED 只有 `libc.so.6`（+`libpthread.so.0`）。
想验证"框架能不能加载"：`scripts\dltest.c` 编出来推上笔，先 dlopen
`libquickjs.so`/`libfalcon.so`/`libjsapi_proxy.so` 再 dlopen 插件。

**已有两个自研插件**

| 插件 | 解决什么 | 源码 |
|---|---|---|
| `fs` | 4.3.5 缺 `fs` jsapi → Doge 阅读"本地文件"、漫画库全白 | `assets\plugins\fs_plugin.c` |
| `fileServer` | 原插件只有 GET（只读列表）→ 文件互传**不能上传** | `assets\plugins\fileserver_plugin.c` |

两者都支持**重启后从磁盘重新加载**（已验证），不是内存残留。

## 5. 文件互传上传服务

原版 `libjsapi_fileserver.so` 的 HTTP 服务只实现了 GET（内嵌页面就是只读目录列表，
插件里连 `upload`/`multipart` 字样都没有），所以"文件互传"网页根本不能上传 —— 这是
**功能缺失**，不是配置问题。自研插件补齐了：拖拽/多选上传、流式落盘（不限大小）、
中文名、重名自动改名、`Range` 断点续传、新建文件夹。

```powershell
python scripts\fileserver_plugin.py status      # 看笔上装的是原版(只读)还是自研版(可上传)
python scripts\fileserver_plugin.py install     # 替换（自动备份原版为 .orig）
python scripts\fileserver_plugin.py restore     # 还原原版
# 两条都要重启笔才生效
```

全新设备直接装成品包 `miniapps\dist\file-manager-1.2.0-upload.amr`。
脱离 app 也能单跑：`scripts\fssrv <port> <root>`（笔上执行），浏览器开 `http://<笔IP>:<port>/`。
自测套件：`python scripts\test_upload.py --base http://<笔IP>:8080 --adb <adb> --serial <s>`。

细节与验证记录：[reference/workspace-docs/文件互传上传修复说明.md](reference/workspace-docs/文件互传上传修复说明.md)。

## 6. OTA 固件补丁（ADB 密码来源）

笔的 ADB 认证密码原本是厂商随机哈希（`302af1d8…`，爆破已放弃：rockyou/掩码/数字全试过，
RTX 2080 Ti 也跑不出来）。走的是 **OTA MITM 改固件**这条路：

明文 HTTP OTA + 镜像不签名 → hosts 劫持 `iotapi.abupdate.com` 到本机 → 假 OTA 服务返回
伪造的 `checkVersion` → 让笔下载我们改过的镜像 → 笔自己刷进去。

**要改的地方**：`adbd_auth.sh` 里的密码哈希（**等长替换**）、`policy.install.rebootUpgrade="true"`
（否则装到 100% 也不重启）、响应里的 `segmentMd5/md5sum/sha/sha256/fileSize`（必须重算）、
`bakUrl/deltaUrl` 指向自己。

工具链都在 `scripts\`：`patch_firmware.py`（改镜像+写报告）、`ota_meta.py`（重算所有校验）、
`fake_ota_server.py`（假 OTA）+ `file_server.py`（分发镜像）、`fast_download.py`（多线程分块下载，
CDN 会截断长连接）、`hosts_tool.ps1`（hosts 劫持，需要点 UAC）、`hotspot.ps1`。

**回滚**：改回来刷备份镜像，或
`sed -i 's/<新md5>/302af1d80be1106586775350cc0a2c92/' /usr/bin/adbd_auth.sh`。
完整流程与坑位：[reference/04-ota-firmware.md](reference/04-ota-firmware.md)、
[reference/workspace-docs/README.md](reference/workspace-docs/README.md)。

## 7. 远程操作 UI（截图 / 唤醒 / 触控）

```powershell
& $adb -s $s push scripts\tap /tmp/tap; & $adb -s $s shell "chmod +x /tmp/tap"
& $adb -s $s shell "/tmp/tap /dev/input/event1 480 133 >/dev/null"      # 注入点击（x y）
& $adb -s $s shell "echo 200 > /sys/class/backlight/backlight/brightness"  # 唤醒背光
& $adb -s $s shell "miniapp_cli capture /userdisk/Favorite/shot.png"; & $adb -s $s pull /userdisk/Favorite/shot.png out\shot.png
```

**截图空白（≈1667 字节）几乎总是屏幕息屏**（息屏时框架暂停渲染）：先设背光 200 + 注入一次触摸，
再 capture。正常内容一般是 15KB~26KB。`captureFB` 在息屏时会返回 `ret:-1`。

坐标是**显示坐标**（960×266）。判断"当前前台是哪个 app"看框架日志里的
`home-index===topAppId===from X change to Y`。

## 8. 排错速查（症状 → 原因）

| 症状 | 原因 / 处理 |
|---|---|
| `adb devices` 显示 `offline` / `no serial number` | adbd 没起：拔线 → 设置→法律监管连点 → 插线（别反复插拔） |
| `login with "adb shell auth" to continue.` | 没认证：`shell auth` 输密码（重启后必然要重认证） |
| 认证后仍是 `login with...` | 多半是命令里用了 `Select-Object -First N` 提前掐断了 adb |
| adb 报权限错误（`.android` / `Temp\adb.log`） | 设 `ANDROID_USER_HOME`、`TMP/TEMP` 到工作区，或给 `~\.android` 做 junction |
| app 打开黑屏 | ① 热替换了 mmap 中的 .so → 重启笔；② 插件导出名不全（查框架日志）；③ 插件里用了 popen/fork |
| 框架日志 `Could not find export 'X' in module 'Y'` | 插件缺具名导出，补 `JS_AddModuleExport` + `JS_SetModuleExport` |
| 装了插件但功能没变 | 覆盖安装没提取 libs（要 uninstall 重装），或没重启（dlopen 缓存） |
| 界面显示"未启动"但服务真的起了 | app 靠事件刷新 UI：插件要发 `server_started`/`server_stopped`，且 `start()` 返回带 `running` 的对象 |
| `PS1 脚本报"缺少 }"` 之类语法错 | Windows PowerShell 5.1 按 GBK 读无 BOM 的 .ps1 → **存成 UTF-8 with BOM** |
| `zig.exe : error: AccessDenied` | 设 `ZIG_GLOBAL_CACHE_DIR` / `ZIG_LOCAL_CACHE_DIR` 到可写目录 |
| **X7 上 `tap` 点了没反应 / 截图老是 1667B 空白** | X7 触摸是 `/dev/input/event4` 且上报**竖屏物理坐标** → 必须用 `touchinfo <evdev> tapL x y`（自动换算，公式 `px=y+107, py=959-x`）；X7 **没有 fb0**，截图只能用 `miniapp_cli capture` |
| `adb pull`/`push` 报 `protocol fault: stat response has wrong message id` | 这版 adbd 把 shell/exec/sync 三个服务全包了 → 只能先 `adb shell auth` 拿到 `/tmp/.adb_auth_verified` |
| 下载固件 `HTTP 403` | 加浏览器 User-Agent；大文件分块下（CDN 会截断） |
| 上传后文件 md5 不一致/尾部缺字节 | multipart 解析要"找到分隔符时先把之前的数据落盘"（见 fileserver 插件注释） |
| 插件线程把笔 CPU 打满、ADB 掉线 | multipart/循环逻辑出现"缓冲满又不推进"的空转 → 保证每次循环都有进展 |
| **侧载应用重启后消失，且应用数据（登录态/下载/草稿）一起丢** | **不是 APP_UPD 干的**（它只是订阅 PMS 事件做记录）。真凶是**桌面里的 `AppWhitelistCleaner`**：开机拉云端白名单 `api-overmind.youdao.com/.../appWhitelist`（116 个 appid），**不在名单里的应用直接 `pm.removePackage`**；应用数据就存在 `<包>/data/`，所以连数据一起没。判定依据是"是否在白名单"，`flag: 16384` 只是日志信息。**线上配置 = DNS 劫持 + keeper 自愈自退出**（`scripts\dns-blackhole.sh` 让 cleaner 跳过；`scripts\sideload-keeper.sh` 盯日志判定：确认跳过就做完镜像→交给 cron→自行退出，出现 `removing:` 才修复并常驻）。实测 `removing:` 从每次 3~4 降到 **0**。详见 `reference/09-appupd-and-whitelist-cleaner.md` |
| **要改笔的 DNS / 屏蔽某个域名** | `/etc/resolv.conf` 是软链到 `/tmp`（dhcpcd 会重写）；**`/etc/hosts` 虽是只读 erofs 上的文件，但 `mount --bind` 可覆盖** → 用 `scripts\dns-blackhole.sh`（自定义 hosts 放 `/userdisk`，开机钩子重挂，可逆） |
| **插上电脑没有 MTP / 不能传文件** | 笔原生支持 MTP（`usb_f_mtp.ko` + `mtp-server`）。**开过 ADB 调试后系统把 `/tmp/.usb_config` 从 `usb_mtp_en` 覆盖成 `usb_adb_en`**（原厂 `/etc/init.d/.usb_config` 是 MTP）→ USB 只暴露 ADB。用 `scripts\usb-mode.sh both` 同时开 ADB+MTP，`persist both` 让它每次开机自动生效。详见 `reference/10-usb-modes-and-mtp.md` |
| 动了 USB 配置后 adb 掉线 / Windows 完全看不到设备 | `S98usbdevice stop` 会 `kill adbd` 且把 `UDC` 置 none → **先确保有 TCP adb 或 SSH 通道**再操作；`start` 完成后约 10~20s 自动恢复 |
| `pm name=<名字> type=removed` 但包还在 | 只是注册表条目丢了 → 重新 `miniapp_cli install` 即恢复 `type=installed`（`scripts\pen_registry.py check/fix` 可巡检） |
| `pkill -f skip_login.sh` 之后 ADB 掉线/offline | 脚本名匹配到了 `sh` 自己的命令行，把调用方一起杀了 → 用 pid 文件停（`kill $(cat /tmp/sideload-keeper.pid)`） |

## 9. 本 skill 的文件

| 路径 | 说明 |
|---|---|
| `scripts\` | 全套工具（自包含副本）：`ydpen.py`、`install_fs_plugin.py`、`fileserver_plugin.py`、`pack_upload_amr.py`、`patch_firmware.py`、`ota_meta.py`、`fake_ota_server.py`、`fast_download.py`、`hosts_tool.ps1`、`lan_scan_adb.py`、`tap`/`tap.c`、**`touchinfo`/`touchinfo.c`（X7 触控，逻辑坐标换算）**、**`ota_fetch.py`**、**`ota_direct_probe.py`**、**`patch_policy.py`**、**`dns-blackhole.sh`（bind mount 覆盖 /etc/hosts 劫持域名）**、**`usb-mode.sh`（切 USB 模式：MTP/ADB/两者，可持久化）**、**`build_terminal.py`（PenTerm 一键构建：插件+页面+打包+安装）**、**`sideload-keeper.sh`（保活+数据镜像，确认 cleaner 跳过后自行退出）**、**`deploy_keeper.py`**、**`keeper_selftest.py`（离线验证数据恢复）**、**`keeper_decision_test.py`（离线验证"跳过→退出 / 清理→常驻"两分支）**、**`pen_registry.py`（注册表巡检/修复）**、**`jsfmc`/`jsfmc.c`（笔上 JS→.js.bin）**、**`pack_amr.py`**、**`mkfont.py`/`mkcjk.py`/`mkicon.py`**、**逆向四件套：`elf_strings.py`/`elf_syms.py`/`elf_range.py`/`jsbin_atoms.py`**、**`probe_ports.py`**、`fssrv`、`dltest.c`、`test_upload.py` |
| `assets\plugins\` | `fs_plugin.c`、`fileserver_plugin.c` 及编译好的 `.so` |
| `assets\quickjs\` | QuickJS 2020-07-05 头文件（重编插件必需） |
| `reference\` | 分主题深挖：接入/ADB、miniapp/amr、jsapi 插件、OTA 固件、排错、**06-YDPX7-1(X7 Pro) 接入**、**07-miniapp 自研工具链与入口契约**、**08-侧载应用与数据持久化**、**09-APP_UPD 与 AppWhitelistCleaner 真实机制（逆向）**、**10-USB 模式与 MTP（为什么插电脑不能传文件）**、**11-PenTerm 命令历史/常用命令（含构建三坑）**、**12-笔上 sshd 密码登录（bind mount 覆盖 /etc/shadow）** |
| `reference\workspace-docs\` | 工作区原始文档备份（README、MINIAPPS、战果与使用说明、文件互传修复说明、全新设备安装指南） |

## 10. 给未来的一句话总结

**这台笔上任何"缺功能/装不上/白屏"的问题，无非四种解法**：
① 找原始 `.amr` 重打包（补 cert、补 libs）；
② 自己写一个同 ABI 的 jsapi 插件替换原版（模块名+导出名+事件三件套要对）；
③ 走 OTA MITM 改固件（改脚本 + 重算校验）；
④ 纯 adb/root 外部脚本绕过（如 `fssrv`、`tap`、`miniapp_cli`）。
先看 `/data/applog/YD_PEN_APP.log`，它几乎总是直接告诉你答案。
