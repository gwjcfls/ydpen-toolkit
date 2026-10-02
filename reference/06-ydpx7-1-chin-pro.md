# 06 · YDPX7-1 CHN PRO（X7 Pro）接入 + ADB root

> 2026-10-02 实战完成。完整过程、命令、产物清单见工作区 **`ydpen-adb\YDPX7-1接入记录.md`**。
> 本页是给"下次换机器"用的速查版。

## 结论：同一套流程，换三个参数就行

X7 Pro 的 ADB 鉴权脚本与 X6 **字节级同源**，所以 `patch_firmware.py` + `fake_ota_server.py` +
`file_server.py` + `ota_meta.py` 全部直接可用，只需换 **productId / OTA 路径 key / 固件包**。

| 参数 | YDPX6-2 (X6 Pro) | **YDPX7-1 (X7 Pro)** |
|---|---|---|
| productId | 1687695728 | **1715159243** |
| OTA 路径 key | 3f57f6d234286e04 | **02101ba2f0a04473** |
| 出厂/上报表版本 | 4.3.5 | 4.8.6（装完 `/Version` = **4.8.7**） |
| 全量包大小 / md5 | 2,125,597,258 / 78c1c3… | **2,328,236,618 / 01da2289…** |
| 鉴权脚本 | `/usr/bin/adbd_auth.sh`，md5 + `echo $PASSWD`（带 `\n`） | **完全相同** |
| 厂商原哈希 | 302af1d80be1106586775350cc0a2c92 | **相同** |
| 补丁后（ydpen2026） | 054bda46d56c8496e14e81d78f5a3c27 | **相同** |

## 机型差异（照抄 X6 流程会踩的坑）

| 项 | YDPX7-1 | 影响 |
|---|---|---|
| 触摸设备 | **`/dev/input/event4`**（`hyn_ts`），ABS **X 0..480 / Y 0..960 竖屏物理坐标** | 老 `tap` 工具直接发逻辑坐标**完全点不中**；必须 `touchinfo ... tapL` |
| 触摸换算 | `phys_x = 逻辑_y + 107`；`phys_y = 959 - 逻辑_x` | 107 = (480-266)/2，界面居中在 960x480 逻辑屏里 |
| 显示 | 无 `/dev/fb*`、无 `/sys/class/graphics`；DRM `card0-DSI-1` 480x960 | `captureFB`/fb0 截图法失效，只能用 `miniapp_cli capture` |
| adb TCP | **开箱监听 5555** | 不用 USB，`adb connect <IP>:5555` 即可 |
| SoC/系统 | RK3562 ORANGE LP4 V10；Buildroot 2021.11；内核 5.10.160 | miniapp_cli / libquickjs / libfalcon 都在，miniapp 玩法照旧 |
| 背光 | `/sys/class/backlight/backlight/brightness`（max 255，5=息屏，200=亮） | 与 X6 同 |
| 分区 | uboot_a/b trust_a/b misc boot_a/b recovery system_a(p9)/b(p10) userdata(p11) userdisk(p12) | 全量包会**两个槽一起刷** |

## OTA 接口语义（实测，重要）

`POST http://iotapi.abupdate.com/product/<productId>/<key>/ota/checkVersion`
body `{timestamp, sign, mid, productId, version, networkType}`

* **key 段不校验**（随便填都能过；整段删掉才 `5000`）。
* **sign 校验、且与 timestamp 绑定**：抓到的请求可原样重放（同 timestamp），
  但**不能自己改 timestamp 造新请求**（会 `2001 The sign is error`）。
* `mid` 必须已在服务器注册，否则 `2103 Device not registered`。`mid` = 笔的 SN = adb 序列号。
* `version` 填当前版本 → `2101 No new updates`；填假高版本（`99.99.90`）→ `1000` + 全量包。
* 响应里 **`data.version.sha` 才是整包 SHA256**（用原版镜像核对过）；
  **`data.sha256` 不是镜像哈希，改它没用，保持官方原值**（`ota_meta.py` 会把两个都改，
  需要手工还原 `data.sha256`）。

## 一次性流程

```powershell
$root='C:\Users\Sever\Documents\deepseek-harness\default-workspace\ydpen-adb'
$env:TMP="$root\tmp"; $env:TEMP="$root\tmp"; $env:ANDROID_USER_HOME="$root\.android"

# 1) 抓笔的真实请求：电脑开热点 → 笔连热点 → hosts 劫持 → 笔上点“检查更新”
python "$root\tools\fake_ota_server.py" --mode probe --tls-detect          # 后台
powershell -File "$root\tools\hosts_tool.ps1" -Add 192.168.137.1           # UAC
#   抓到的：out\pen_request.json（mid/productId/sign）、out\real_response.json（官方响应）

# 2) 下载全量包（CDN 会 403/截断，必须带 UA + 多线程）
python "$root\tools\fast_download.py" --url "<deltaUrl>" --out "$root\firmware\x.img" --md5 <md5sum> --threads 32

# 3) 隔离原版 + 扫脚本 + 改哈希（等长替换，大小不变）
Copy-Item "$root\firmware\x.img" "$root\firmware\backup\x-orig.img"
python "$root\tools\patch_firmware.py" scan  "$root\firmware\x.img" --dump-script "$root\out\scripts"
python "$root\tools\patch_firmware.py" patch "$root\firmware\x.img" --password ydpen2026

# 4) 重算校验 + 造伪造响应 + 打开自动重启
python "$root\tools\ota_meta.py" --img "$root\firmware\x.img" `
  --response "$root\out\real_response.json" --url "http://192.168.137.1:14514/x.img"
python "$root\tools\patch_policy.py" "$root\out\patched_response.json"    # rebootUpgrade=true、清 force
#   （把 patched_response.json 里的 data.sha256 手工还原成官方原值！见上）

# 5) 喂给笔
python "$root\tools\file_server.py"  --file "$root\firmware\x.img" --port 14514   # 后台
python "$root\tools\fake_ota_server.py" --mode serve                              # 后台
#   笔上：设置 → 检查更新 → 下载（X7 实测 ~1.5-2 MB/s，2.2GB 约 25 分钟）→ 安装 → 自动重启

# 6) 重启后
#   笔上：设置 → 法律监管 → 连点文本 10~15 下
"ydpen2026" | & "$root\tools\platform-tools\adb.exe" -s <serial> shell auth
& "$root\tools\platform-tools\adb.exe" -s <serial> shell id     # uid=0(root)

# 7) 收尾
powershell -File "$root\tools\hosts_tool.ps1" -Remove
```

## 触控/截图（X7 专用）

```powershell
$adb="$root\tools\platform-tools\adb.exe"; $s='<笔IP>:5555'
& $adb -s $s push "$root\tools\build\touchinfo" /tmp/touchinfo
& $adb -s $s shell "chmod +x /tmp/touchinfo"
& $adb -s $s shell "/tmp/touchinfo /dev/input/event4 info"            # 看 ABS 范围
& $adb -s $s shell "/tmp/touchinfo /dev/input/event4 tapL 480 101"    # 逻辑坐标点击（自动换算）
& $adb -s $s shell "/tmp/touchinfo /dev/input/event4 rep 480 101 12"  # 连点（开 ADB 的隐藏手势）
& $adb -s $s shell "echo 200 > /sys/class/backlight/backlight/brightness"
& $adb -s $s shell "miniapp_cli capture /userdisk/Favorite/shot.png"
& $adb -s $s pull /userdisk/Favorite/shot.png out\shot.png            # 15~30KB 正常；1667B≈息屏
```

**给新机型标定触摸坐标**：`touchinfo <evdev> read 60`，让手指点一个屏幕上位置已知的地方，
读出物理坐标，再解 `phys = A·logical + b`（注意可能是竖屏物理坐标 + 旋转 + 居中偏移）。

## miniapp 安装（X7 实测）

```powershell
& $adb -s $s push "$root\miniapps\dist\xxx.amr" /userdisk/Favorite/miniapps/
& $adb -s $s shell "miniapp_cli install /userdisk/Favorite/miniapps/xxx.amr"   # -> {"appid":...,"ret":0}
```

* **安装落点**：`/userdisk/secondary/miniapp/data/mini_app/pkg/<appid>/a/`
  （这台笔有**三个** miniapp 根：`/userdata/miniapp`、`/userdisk/miniapp`、`/userdisk/secondary/miniapp`，
  别只翻第一个）。
* **`libs/<abi>` 的 token 随机型变**：本机官方包是 **`libs/arm64-orange`**（构建产物名 `orange`，
  `youdao/libs/orange`）；而 X6 时代的 `libs/arm64` / `libs/arm64-cherry3566` 包**实测照样能装** ——
  框架会回退到 `libs/arm64`。所以老包**不用重打包**。
* **框架提取 .so 时会重写元数据，体积会变**：包里 109768 → 笔上 197880（shell 5352112 → 6254616）。
  正常现象，用 `strings` 核对功能符号（如 `handle_upload`）即可。
* `miniapp_cli start <appid> fs` —— **页面名别写 `--fs`**，会被当成页面名 `--fs` 而 `fpath` 报错。
* 文件管理器 `8001771940015915`（`miniapps\dist\file-manager-1.2.0-upload.amr`）装上后框架自动启动，
  并自动开启 **8080 端口的"文件互传"**；上传实测通过（中文名 + 逐字节 md5 一致）。

## 其它注意

* 刷完 `/Version` 会变成包内版本（X7 是 4.8.7），**A/B 两槽都会被刷** → 笔上无旧固件可回落，
  回滚只能用 `firmware\backup\` 里的官方原件重刷。
* `ydpen.py` 的 `find_firmware()` 取 `firmware\` **顶层最大的 .img**：多机型共存时把别的机型
  挪进子目录（如 `firmware\x6\`），否则会挑错包。
* 文件名里的版本号按"笔 OTA 上报表上记得的版本"命名，可能与包内 `/Version` 不一致（X7 就是 4.8.6 vs 4.8.7）。
