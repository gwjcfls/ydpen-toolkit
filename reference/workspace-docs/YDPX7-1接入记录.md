# 有道词典笔 YDPX7-1 CHN PRO 获取 ADB 权限（实战记录）

> 设备：**YDPX7-1 CHN PRO**，SN / adb 序列号 `ME82300008900903`
> 方法：**OTA 明文 HTTP 中间人 + 改固件里的 ADB 密码哈希** —— 与 YDPX6-2 用的是同一套工具
> 新密码：`ydpen2026`

---

## 0. 一句话结论

> ✅ **已完成（2026-10-02）**：`adb shell auth` 用 `ydpen2026` 登录成功，`adb shell id` → `uid=0(root)`；
> 重启后依然有效（磁盘上的 `/usr/bin/adbd_auth.sh` 已换成我们的哈希，已 `cat` 确认）。

这台笔和 X6 Pro（YDPX6-2）**鉴权脚本字节级同源**，所以整套 `ydpen-adb` 工具链直接可用，
只需要换掉三个"型号相关"参数：**productId、OTA 路径 key、固件包**。

| 参数 | YDPX6-2 (X6 Pro) | **YDPX7-1 (X7 Pro)** |
|---|---|---|
| productId | 1687695728 | **1715159243** |
| OTA 路径 key | 3f57f6d234286e04 | **02101ba2f0a04473** |
| 系统版本 | 4.3.5 | **4.8.6**（build 分支 `dictpen_release_4.8.6`） |
| 全量包 | 2,125,597,258 B | **2,328,236,618 B** |
| 包 md5 | 78c1c3fbd2151dc8bac1e01c8ae527c0 | **01da22898f03346a24d256a4605802b4** |

---

## 1. 设备事实（速查）

| 项 | 值 |
|---|---|
| SoC | Rockchip **RK3562**（镜像内含 `rk3562_ddr_1056MHz_v…`、`rk3562_usbplug_v1…`） |
| 系统 | 词典笔 OS **4.8.6**（`dictpen_release_4.8.6`） |
| USB | `VID_2207&PID_0006` REV_0310，WinUSB，设备名 **DictPen** —— **只有 ADB 一个接口**（不像 X6 还有 WPD/MTP） |
| adb banner | `product:occam model:Nexus_4 device:mako` ← 厂商没改的 AOSP 默认值，别被它骗了 |
| 鉴权脚本 | `/usr/bin/adbd_auth.sh`（镜像里搜 `.adb_auth_verified` 可定位到脚本体） |
| 校验方式 | **md5 + 换行**：`[ "$(echo $PASSWD \| md5sum)" = "<hash>  -" ]` |
| 厂商原始哈希 | `302af1d80be1106586775350cc0a2c92`（**与 X6 完全相同**） |
| 通过标记 | `/tmp/.adb_auth_verified`（`touch` 之后 shell / exec / sync / root 全部放行） |
| 尝试次数 | 脚本里 `for i in $(seq 1 3)` —— **连错 3 次本轮 ADB 会话结束**，要重新去"法律监管"点开 |
| 固件格式 | `RKFW` 全量包（内含 UsbHead / FlashHead / FlashBoot / FlashData） |

脚本原文（从镜像里直接抽出来的，一字未改）：

```sh
#!/bin/sh
VERIFIED=/tmp/.adb_auth_verified

if [ -f "$VERIFIED" ]; then
    echo "success."
    exit
fi

for i in $(seq 1 3); do
    read -p "$(hostname -s)'s password: " PASSWD
    if [ "$(echo $PASSWD | md5sum)" = "302af1d80be1106586775350cc0a2c92  -" ]; then
        echo "success."
        touch $VERIFIED
        exit
    fi

    echo "password incorrect!"
done

false
```

---

## 1.1 刷完之后实测到的环境（2026-10-02，照抄可用）

| 项 | 值 |
|---|---|
| `/Version` | `Version: 4.8.7`、`Build_Time: Wed Apr 22 16:45:06 CST 2026` |
| 内核 | `Linux YoudaoDictionaryPen-903 5.10.160 #6 SMP … aarch64` |
| 系统 | **Buildroot 2021.11**（不是 Android），`rockchip_rk3562_orange_defconfig` |
| 主板 | `Rockchip RK3562 ORANGE LP4 V10 Board`（X6 是 MELON LP4 V10） |
| 主机名 | `YoudaoDictionaryPen-903` |
| 运行槽 | `androidboot.slot_suffix=_a`、`root=/dev/mmcblk0p9`(system_a) |
| 分区 | uboot_a/b, trust_a/b, misc, boot_a/b, recovery, system_a(p9)/b(p10), userdata(p11), userdisk(p12, 24G) |
| 屏幕 | DRM `card0-DSI-1` = `480x960`（竖屏原生）；**框架界面 = 960x266** |
| **没有 framebuffer** | 无 `/dev/fb*`、无 `/sys/class/graphics` → X6 那套 `captureFB`/fb0 截图法**不适用**，改用 `miniapp_cli capture` |
| 触摸 | **`/dev/input/event4`**（`hyn_ts`），ABS 范围 **X 0..480 / Y 0..960（竖屏物理坐标）** |
| 其它 event | 0=rk805 pwrkey, 1=lcd_gamma, 2=adc-keys, 3=led_control, 5=Typec_Headphone, 6-8=gsensor, 9=fake-keys |
| 背光 | `/sys/class/backlight/backlight/brightness`（max 255；5=息屏，200=亮）—— 与 X6 同路径 |
| **adb over TCP** | **开箱就监听 5555**：`adb connect <笔IP>:5555` 直接可用，不依赖 USB |
| miniapp_cli | `/bin/miniapp_cli`（子命令与 X6 相同） |
| jsapi 库 | `/usr/lib/libquickjs.so`、`libjsapi_proxy.so`、`libfalcon.so` |

### 触摸坐标映射（实测标定，X7 必须换算）

X7 的触摸设备上报的是**竖屏物理坐标**，直接发界面坐标（960x266）**点不中任何东西**
（本次第一版就是直接发逻辑坐标，截图逐字节不变 = 完全没反应）。

标定方法与结果（用 `tools\build\touchinfo` 的 `read` 抓真实手指坐标，再用注入验证）：

```
物理 phys_x = 逻辑_y + 107
物理 phys_y = 959 - 逻辑_x
```

* `107` 的来源：960x266 的界面**居中**放在 960x480 的逻辑屏里 —— (480-266)/2 = 107。
* 校验 ①：逻辑(30,42)（返回箭头）→ 物理(149,929)，点击后界面退回上一页 ✔
* 校验 ②：逻辑(480,101)（"法律监管"行）→ 物理(208,480)，点击后进入该页 ✔
* 标定原始数据：手指点"返回"落在物理(145,953)、点界面正中文字行落在物理(≈204,458)。

```powershell
# 探测：ABS 范围决定要不要映射（X6 那种 960x266 范围内的就不用）
& $adb -s $s shell "/tmp/touchinfo /dev/input/event4 info"
# 按“逻辑坐标”点击（自动换算）
& $adb -s $s shell "/tmp/touchinfo /dev/input/event4 tapL 480 101"
# 连点 N 次（开启 ADB 的隐藏手势 = 连点文字 12 下）
& $adb -s $s shell "/tmp/touchinfo /dev/input/event4 rep 480 101 12"
# 给新机型标定：读原始事件，看手指落点的物理坐标
& $adb -s $s shell "/tmp/touchinfo /dev/input/event4 read 60"
```

⚠️ 两个容易踩的坑：

1. **文件名里的 "4.8.6" ≠ 包内版本**。笔的 OTA 上报表报的是 `4.8.6`，但官方全量包装进去后
   `/Version` 是 **4.8.7**（构建于 2026-04-22，源码分支仍是 `dictpen_release_4.8.6`）。
   所以 `firmware\backup\ydpx7-4.8.6-orig.img` 是**官方全量包原件**（未打补丁），
   而不是"笔出厂固件的 dump"。
2. **这次 OTA 把 A、B 两个槽都刷了**（只读挂载 `system_b` 检查，里面鉴权哈希同样是我们的
   `054bda46…`）。也就是笔上**已经没有旧固件可回落**，要回滚只能用我们存的那份官方原件重刷一次。

---

## 2. OTA 接口语义（本次实测补充，重要）

`POST http://iotapi.abupdate.com/product/<productId>/<key>/ota/checkVersion`

body：`{timestamp, sign, mid, productId, version, networkType}`

| 实测 | 结果 | 结论 |
|---|---|---|
| key 段换成全 0 / 沿用别的 key | 照常 200 | **路径 key 不校验** |
| key 段整个删掉 | `5000 Global exception` | 段数必须对 |
| 原 timestamp + 原 sign 重放 | `1000 success` | 抓到的请求可以**离线重放**，固件直链随时能再取 |
| **新 timestamp + 原 sign** | `2001 The sign is error` | sign 与 timestamp 绑定，**不能自己造新请求** |
| mid 未注册 | `2103 Device not registered` | mid 必须是服务器认识的设备 |
| version 填当前版本 | `2101 No new updates were found` | 说明当前已是最新 |
| version 填 `99.99.90` | `1000` + 全量包 | 假高版本号 = 要全量包 |

> ⚠️ 响应里有**两个** sha 字段，别搞混：
> * `data.version.sha` = **整包 SHA256**（已用原版镜像核对，完全吻合）→ **必须重算**；
> * `data.sha256` = 不是镜像哈希（可能是清单/签名相关）→ **保持官方原值不要动**。
>
> `ota_meta.py` 会把两个都写成本地重算值，本次已手工把 `data.sha256` 还原为官方值
> （最小改动原则；X6 那边两个都改也装成功了，但没必要冒这个险）。

---

## 3. 本次实际操作（可复刻）

```powershell
$root='C:\Users\Sever\Documents\deepseek-harness\default-workspace\ydpen-adb'
$env:TMP="$root\tmp"; $env:TEMP="$root\tmp"; $env:ANDROID_USER_HOME="$root\.android"
$env:PYTHONIOENCODING='utf-8'
```

**① 抓笔的真实请求**（电脑开热点 → 笔连热点 → hosts 劫持 → 笔上点"检查更新"）

```powershell
python "$root\tools\fake_ota_server.py" --mode probe --tls-detect     # 后台
powershell -File "$root\tools\hosts_tool.ps1" -Add 192.168.137.1      # 弹 UAC
# 笔上：设置 → 检查更新
# 抓到的请求在 out\pen_request.json，官方响应在 out\real_response.json
```

本次抓到：

```json
{"timestamp":1790937333,"sign":"0422b09247cb2581840e59f98df0b513",
 "mid":"ME82300008900903","productId":"1715159243","version":"4.8.6","networkType":"WIFI"}
```

路径：`/product/1715159243/02101ba2f0a04473/ota/checkVersion`

**② 下载全量包**（2.17 GiB，多线程 + 浏览器 UA，CDN 会 403/截断）

```powershell
python "$root\tools\fast_download.py" `
  --url "http://iotdown-jd.mayitek.com/delta/1715159243/19037290/kgLw35BO.img" `
  --out "$root\firmware\ydpx7-4.8.6-orig.img" `
  --md5 01da22898f03346a24d256a4605802b4 --threads 32
# -> md5 01da22898f03346a24d256a4605802b4 ✔ 与官方一致
```

**③ 隔离原版 → 扫描鉴权脚本**

```powershell
Copy-Item "$root\firmware\ydpx7-4.8.6-orig.img" "$root\firmware\backup\ydpx7-4.8.6-orig.img"
Move-Item "$root\firmware\ydpx7-4.8.6-orig.img" "$root\firmware\ydpx7-4.8.6-patched.img"
python "$root\tools\patch_firmware.py" scan "$root\firmware\ydpx7-4.8.6-patched.img" `
  --dump-script "$root\out\x7\scripts" --json-out "$root\out\x7\scan.json"
```

**④ 改哈希（等长原地替换）**

```powershell
python "$root\tools\patch_firmware.py" patch "$root\firmware\ydpx7-4.8.6-patched.img" `
  --password "ydpen2026" --report "$root\out\x7\patch_report.json"
# 302af1d80be1106586775350cc0a2c92 -> 054bda46d56c8496e14e81d78f5a3c27  (= md5("ydpen2026\n"))
# 共 1 处；文件大小不变 2,328,236,618 B ✔
```

**⑤ 重算校验 + 造伪造响应**

```powershell
python "$root\tools\ota_meta.py" --img "$root\firmware\ydpx7-4.8.6-patched.img" `
  --response "$root\out\x7\real_response.json" `
  --url "http://192.168.137.1:14514/ydpx7-4.8.6-patched.img" `
  --out "$root\out\patched_response.json"
python "$root\tools\patch_policy.py" "$root\out\patched_response.json"
# data.sha256 还原成官方原值（见 §2 警告）
```

补丁后镜像：md5 `fce9fc7c74fdb5f87baee194fab527a5`，sha256 `e4140bee71639e6f91da9cbd788a77e076ae27a6d88e55d36e0b3360a3fd0c40`。
改动只落在第 **20/23** 个分片（哈希所在处），前 19 个分片 MD5 与官方完全一致。

**⑥ 喂给笔**

```powershell
python "$root\tools\file_server.py" --file "$root\firmware\ydpx7-4.8.6-patched.img" --port 14514   # 后台
python "$root\tools\fake_ota_server.py" --mode serve                                              # 后台
# 笔上：设置 → 检查更新 → 下载(2.2GB) → 安装 → 自动重启
```

**⑦ 重启后**

```powershell
# 笔上：设置 → 法律监管 → 连点文本 10~15 下，重开 ADB
"ydpen2026" | & $adb -s ME82300008900903 shell auth     # 期望 success.
& $adb -s ME82300008900903 shell id                     # 期望 uid=0(root)
```

---

## 4. 产物清单

| 路径 | 说明 |
|---|---|
| `firmware\backup\ydpx7-4.8.6-orig.img` | **原版固件**（2,328,236,618 B，md5 `01da2289…`）—— 回滚用 |
| `firmware\ydpx7-4.8.6-patched.img` | 补丁固件（md5 `fce9fc7c…`），喂给笔的那份 |
| `out\x7\real_response.json` | 官方 checkVersion 原始响应（含 23 个分片 endpos） |
| `out\x7\scripts\*.sh` | 从镜像里抽出的鉴权脚本原文 |
| `out\x7\patch_report.json` / `out\x7\scan.json` | 补丁报告 / 扫描报告 |
| `out\patched_response.json` | **伪造响应**（status=1000，指向本机固件，rebootUpgrade=true） |
| `out\pen_request.json` | 笔的真实请求（sign/mid/productId 都在，可离线重放） |
| `firmware\x6\`、`out\x6\` | X6 那台的固件与产物（已隔离，避免 `find_firmware()` 挑错包） |
| `out\x7\shot_*.png` | 刷机后界面截图（法律监管页 / 设置页，用于确认界面无损） |

> ⚠️ `ydpen.py` 的 `find_firmware()` 会取 `firmware\` **顶层最大的 .img**。
> 所以 X6 的固件已挪进 `firmware\x6\`，顶层只留 X7 的包。

---

## 4.5 日常怎么连（拿到 root 之后）

```powershell
$root='C:\Users\Sever\Documents\deepseek-harness\default-workspace\ydpen-adb'
$env:TMP="$root\tmp"; $env:TEMP="$root\tmp"; $env:ANDROID_USER_HOME="$root\.android"
$adb="$root\tools\platform-tools\adb.exe"
$s='192.168.137.153:5555'      # IP 会变：用 tools\lan_scan_adb.py 扫，或看热点客户端列表

# ① TCP（推荐。X7 开箱就监听 5555）
& $adb connect $s
# ② 或 USB：笔上 设置 → 法律监管 → 连点文本 10~15 下 打开 ADB，再插线
& $adb devices -l

# 认证（同一会话内一次即可；重启后要重新认证）
"ydpen2026" | & $adb -s $s shell auth     # 期望 success.
& $adb -s $s shell id                     # 期望 uid=0(root)
```

常用操作：

```powershell
# 截图（先唤醒背光；X7 没有 fb0，必须用 miniapp_cli capture）
& $adb -s $s shell "echo 200 > /sys/class/backlight/backlight/brightness"
& $adb -s $s shell "miniapp_cli capture /userdisk/Favorite/shot.png"
& $adb -s $s pull /userdisk/Favorite/shot.png out\shot.png   # 正常 15~30KB；1667B≈息屏

# 注入触摸（X7：event4 + tapL 自动换算逻辑坐标）
& $adb -s $s push "$root\tools\build\touchinfo" /tmp/touchinfo
& $adb -s $s shell "chmod +x /tmp/touchinfo; /tmp/touchinfo /dev/input/event4 tapL 480 101"

# 前台是哪个 app
& $adb -s $s shell "grep -a topAppId /data/applog/DictPen_*.log | tail -3"
```

> 注意：老工具 `tap` 在 X7 上**点不中**（不换算坐标），必须用 `touchinfo ... tapL`。

---

## 5. 本次新增/修改的工具

| 工具 | 作用 |
|---|---|
| `tools\ota_fetch.py` | 用 mid+productId 直接向官服要全量包（需带有效 sign，见 §2） |
| `tools\ota_direct_probe.py` | 探测官服：sign/key/mid 各自到底校不校验 |
| `tools\patch_policy.py` | 把伪造响应里的 `install.rebootUpgrade` 改 true、清空 `force` 时间窗 |
| `tools\build\touchinfo.c` / `touchinfo` | **X7 触控必备**：读 ABS 范围(`info`)、逻辑坐标点击(`tapL`)、连点(`rep`)、读原始事件标定(`read`)；zig 交叉编译 |
| `out\x7\` | 本机型的响应 / 脚本 / 报告 / 截图 |

## 6. 回滚

1. **首选**：把 `firmware\backup\ydpx7-4.8.6-orig.img` 按 §3-⑤⑥ 重算校验后喂给笔，
   笔会自己刷回原版（原版哈希就是官方的 `302af1d8…`）。
2. 已经 root 了想只改密码：等能 `adb shell` 之后
   `sed -i 's/<新哈希>/302af1d80be1106586775350cc0a2c92/' /usr/bin/adbd_auth.sh`。
3. 收尾别忘：`powershell -File tools\hosts_tool.ps1 -Remove`，否则笔一直以为有 99.99.91 新版本。

## 7. 风险与注意事项

- 刷的是**官方同版本全量包**（4.8.6 → 4.8.6），只动了 32 字节哈希字符串，长度不变 → 变砖风险极低。
- 安装策略要求**电量 > 30%**（`policy.install.battery = 30`），否则笔会拒绝安装。
- 笔端下载速度实测 ~1.5 MB/s（每 100MB 分片约 65 秒），2.2GB 约 **25 分钟**；期间别断网、别拔 USB。
- 空密码/连续 3 次错密码会让 adbd 下线，必须回"法律监管"重新点开。
- `iotapi.abupdate.com` 一旦改成 HTTPS 并做证书校验，这条路就断了 → 届时只能走 RKDevTool LOADER dump 分区。

---

## 8. miniapp 安装（X7 实测：文件管理器 1.2.0-upload）

```powershell
$adb="$root\tools\platform-tools\adb.exe"; $s='<笔IP>:5555'
& $adb -s $s push "$root\miniapps\dist\file-manager-1.2.0-upload.amr" /userdisk/Favorite/miniapps/
& $adb -s $s shell "miniapp_cli install /userdisk/Favorite/miniapps/file-manager-1.2.0-upload.amr"
# -> {"appid": "8001771940015915", "ret": 0}    装完框架会自动把应用拉起来
```

实测结果（2026-10-02，全部通过）：

| 项 | 结果 |
|---|---|
| 安装位置 | `/userdisk/secondary/miniapp/data/mini_app/pkg/8001771940015915/a/`（**不是** `/userdisk/miniapp`） |
| 插件提取 | `a/libs/libjsapi_shell_1280949930.so`、`a/libs/libjsapi_fileserver_1280949930.so` ✔ |
| 是否我们的增强版插件 | ✔ `strings` 里有 `handle_upload` / `multipart_done` / `Content-Range` / 上传页面 HTML |
| 应用启动 | 框架日志 `AppLifecycle->appResumed … getNickName()=文件管理器 version=1.2.0` |
| 文件互传服务 | 自动启动，界面显示 `http://192.168.137.153:8080` "运行中" |
| **上传功能** | `POST /upload` → `{"ok":true,"count":1,…}`；落盘 **5048 字节 / md5 与本地完全一致**、中文名保留 ✔ |

**X7 特有的几个坑（与 X6 的差别）**：

1. **amr 里 `libs/` 的子目录 token 与机型有关**：本机官方包用 `libs/arm64-orange`
   （构建产物叫 `orange`，`youdao/libs/orange`），而 X6 时代重打包的包里只有
   `libs/arm64` 与 `libs/arm64-cherry3566`。**实测：没有 `arm64-orange` 也能正常装** ——
   框架会回退到 `libs/arm64` 并成功加载，所以这个包**不需要重打包**。
2. **框架提取 .so 时会重写/附加元数据，体积会变**：包里 `libjsapi_fileserver.so` 是 109768 字节，
   提取到笔上变成 197880 字节（shell 也从 5352112 变成 6254616）。**这是正常现象**，
   用 `strings <提取出来的.so> | grep handle_upload` 核对功能符号即可，别以为被掉包了。
3. **`miniapp_cli start` 的页面名别带横线**：`start <appid> --fs` 会被框架当成页面名 `--fs`
   （日志报 `fpath(.../--fs.js.bin) stat failed`）；写 `fs` 就不报错。
4. 这台笔有**三个 miniapp 根**：`/userdata/miniapp`、`/userdisk/miniapp`、`/userdisk/secondary/miniapp`；
   当前用户档案的安装落在最后一个，找文件别只翻 `/userdisk/miniapp`。
5. 应用会把 **8080 端口的文件互传服务**自动开起来（对 `/userdisk` 有读写权限）。
   不想一直开着就在应用里停掉，或 `miniapp_cli uninstall 8001771940015915` 卸载。
