# 有道词典笔 YDPX6-2 (X6 Pro CHN PLUS) 获取 ADB 权限 —— 一站式工具包

> ## ✅ 已完成（2026-10-01）
> 本机这台 **YDPX6-2（X6 Pro CHN PLUS，RK3562，系统 4.3.5）已经拿到 root**：
> ADB 密码已改成 **`ydpen2026`**，`adb shell id` → `uid=0(root)`。
> 日常使用、坑位清单、回滚方法见 **[战果与使用说明.md](战果与使用说明.md)**。
> 下面这份是本工具包的完整原理与流程说明（含所有自动化脚本的用法）。

> 依据 PenUniverse 教程：[discussion#277 (S6 教程)](https://github.com/orgs/PenUniverse/discussions/277)、
> [86lbs/ydpen-adb-unlock](https://github.com/86lbs/ydpen-adb-unlock)、
> [PenMods Wiki 破解 ADB 密码的两种方法](https://github-wiki-see.page/m/PenUniverse/PenMods/wiki/%E7%A0%B4%E8%A7%A3%E6%9C%89%E9%81%93%E8%AF%8D%E5%85%B8%E7%AC%94ADB%E5%AF%86%E7%A0%81%E7%9A%84%E4%B8%A4%E7%A7%8D%E6%96%B9%E6%B3%95) 整理实现。

本工具包把教程里**所有电脑端的活儿都脚本化**了（不用 WinHex、不用 SOJSON 手工发请求、不用手工算 MD5），
你只需要做三件物理动作：**开热点、在笔上点“检查更新”、在笔上点“安装更新”**。

```
ydpen-adb\
├─ ydpen.py                  ← 一条龙编排器（env / usb / watch / adb / probe / download / patch / serve / all）
├─ tools\
│   ├─ platform-tools\adb.exe   已下载好的 adb
│   ├─ patch_firmware.py        在固件里定位 adb 鉴权脚本，自动判断算法/换行，等长替换哈希
│   ├─ ota_meta.py              一趟读盘算分段MD5/整包MD5/SHA1/SHA256，生成伪造 OTA 响应
│   ├─ fake_ota_server.py       伪装 iotapi.abupdate.com（顺带替 Wireshark 抓请求）
│   ├─ file_server.py           固件直链服务器（支持 Range 断点续传，带进度日志）
│   ├─ hosts_tool.ps1           劫持 iotapi.abupdate.com → 本机（自动 UAC 提权）
│   ├─ hotspot.ps1              尝试一键开移动热点（失败就手动开）
│   └─ adb.cmd                  adb 包装器（配置目录放工作区，免权限报错）
├─ firmware\                 ← 下载到的官方全量包放这里
├─ out\                      ← 抓到的官方响应、patch 报告、伪造响应
└─ logs\                     ← 服务器日志（判断卡进度用）
```

---

## 0. 原理（30 秒版）

1. 有道词典笔的 **OTA 走明文 HTTP**，且升级包**没有签名校验**；
2. ADB 密码存在固件 `/usr/bin/adb_auth.sh`(或 `adbd_auth.sh`) 里的一个 **md5 / sha256 哈希**；
3. 所以：把官方全量包下下来 → 把哈希换成**我们密码的哈希**（等长替换，文件大小不变）→
   重算分段 MD5/整包 MD5 → 用本地服务器冒充官方更新服务器 → 让笔把我们的包刷进去 → 用新密码 `adb shell auth`。

⚠️ 2026 年起有道开始把 OTA 换成 HTTPS 修这个洞。本工具包会用 `fake_ota_server.py --tls-detect`
直接告诉你“洞还在不在”。洞没了就只能走**路线三（dump 分区）**。

---

## 1. 词典笔 ADB 开关在哪

设置 → **法律监管** → 连续点文本 **10~15 下**，直到出现“ADB 调试已打开”之类的提示。
打开后 USB 插电脑，`adb devices` 里应该能看到设备。

**关键判断**：

| `adb devices` 显示 | 含义 | 怎么办 |
|---|---|---|
| `(no serial number)  offline` | USB 的 adb 接口在，但笔里的 `adbd` 没跑 → **开关没打开** | 去点开 ADB 开关，再插拔一次 |
| `<序列号>  device` | 正常，可以被要求输密码了 | `python ydpen.py adb` |
| 什么都没有 | 线不行 / 驱动没装 | `python ydpen.py usb` 看设备有没有黄色感叹号 |

---

## 2. 路线一：先试历史固定密码（最省事，先试这个！）

老固件的密码是写死的：

| 系统版本 | 密码 |
|---|---|
| 2.0.0 之后 | `CherryYoudao` |
| 2.7.0 之后 | `x3sbrY1d2@dictpen` |

```powershell
cd <这个目录>
python ydpen.py watch          # 一边盯设备状态，一边等你在笔上打开 ADB
# 或者设备已经 online 了：
python ydpen.py adb
```

成功会打印 `[+] 密码是 'xxx'！` 和 `uid=0(root)`。
失败（`password incorrect!`）说明你的固件用的是**每台机器不同的哈希**，走路线二。

> 手动等价命令：`tools\adb.cmd shell auth`，然后输入密码。

---

## 3. 路线二：OTA 中间人 + 改固件哈希（教程主线）

### 3.1 准备网络

1. 电脑连上 Wi-Fi，然后 **设置 → 网络和 Internet → 移动热点 → 打开**（共享 WLAN）。
   （也可以试 `powershell -File tools\hotspot.ps1`，不保证成功）
2. 词典笔连这个热点。
3. 确认热点网关 IP 一般是 `192.168.137.1`：`python ydpen.py env` 会告诉你“建议固件直链主机”。
4. hosts 劫持（会弹 UAC，点“是”）：
   ```powershell
   powershell -ExecutionPolicy Bypass -File tools\hosts_tool.ps1 -Add 192.168.137.1
   powershell -ExecutionPolicy Bypass -File tools\hosts_tool.ps1 -List
   ```

> 热点网关不一定是 192.168.137.1，`ydpen.py env` 里带 `<- 移动热点网关` 标记的就是。

### 3.2 抓请求 + 拿全量包链接（自动）

```powershell
python ydpen.py probe
```

这个命令会：起欺骗服务器（80 端口，同时监听 443 判断是否已上 HTTPS）→ 写 hosts →
等你**在词典笔上点“检查更新”** → 笔的请求打到我们服务器上 → 我们立刻用同样的
`sign/mid/productId`（只把 `version` 改成 `99.99.90`）向真服务器查一次 →
把最新全量包信息存到 `out\real_response.json`，同时**原样转发**笔的请求，笔那边表现正常。

- 笔上点“检查更新”**可能什么都没发**（界面装的）。反复点几次；实在不行：
  **恢复出厂设置 → 联网激活 → 再点检查更新**（教程原文的坑）。
- 看到 `logs\ota_probe.log` 里有 `[tls] 词典笔 xxx 在用 HTTPS 连接！` → 洞已修，转路线三。

然后下载（约 1~2 GB，可断点续传）：

```powershell
python ydpen.py download
```

### 3.3 改密码 + 算校验（自动）

```powershell
python ydpen.py patch --password 你的新密码
```

它会：
1. 扫描固件，抽出 `adb_auth.sh` / `adbd_auth.sh` 全文并**打印给你看**（同时存到 `out\scripts\`）；
2. 自动判断：是 `md5sum` 还是 `sha256sum`；是 `echo -n`（不补换行）还是 `echo`（**补一个 `\n`** —— 教程里最大的坑）；
3. 用你的密码算出新哈希，**等长原地写回**，复查文件大小不变、旧哈希消失、新哈希存在，报告写进 `out\patch_report.json`；
4. 按官方响应里的分片边界重算分段 MD5、整包 MD5/SHA1/SHA256，生成 `out\patched_response.json`
   （里面 `bakUrl`/`deltaUrl` 已指向 `http://<本机IP>:14514/<固件名>`，版本号抬到 99.99.91）。

先只看不改：`python tools\patch_firmware.py scan firmware\xxx.img --dump-script out\scripts`
手动指定：`--algo md5|sha256`、`--newline yes|no`、`--old-hash <原哈希>`

### 3.4 让笔升级（最后一步）

```powershell
python ydpen.py serve
```

启动固件直链服务器（14514，**支持 Range 断点续传**，进度写 `logs\file_server.log`）
和欺骗服务器（80）。

然后**在词典笔上点“检查更新”** → 会提示一个很大的更新包 → 下载 → 安装 → 自动重启。
下载/安装期间两个窗口别关。

### 3.5 登录 ADB

笔重启后，重新去 **法律监管** 里点开 ADB，USB 插电脑：

```powershell
python ydpen.py adb --passwords 你的新密码
# 等价手动：
tools\adb.cmd shell auth        # 提示 password: 时输入新密码明文
tools\adb.cmd shell             # 进去就是 root
```

看到 `[root@YoudaoDictionaryPen-xxx:/]#` 就成了。

### 3.6 完事记得收尾

```powershell
powershell -ExecutionPolicy Bypass -File tools\hosts_tool.ps1 -Remove
```
否则笔以后一直以为有 99.99.91 新版本。

---

## 4. 路线三：进 LOADER 模式 dump 分区（洞被修了才用）

不依赖 OTA：让笔进 Rockchip LOADER/MASKROM 模式，用 RKDevTool 2.86 读出 `system`/`rootfs` 分区，
改里面的哈希再写回去（工具：RKDevTool 2.86 + 2.38 + DriverAssistant + DiskGenius）。

- 进 LOADER 模式的方法随机型而异（一般是按住某键插线，或拆机短接 eMMC/SPI 的 CLK 与 GND）。
- dump 出来的分区用 DiskGenius 打开，路径还是 `/usr/bin/adb_auth.sh`，改法同 3.3（用
  `python tools\patch_firmware.py patch <分区.img> --password xxx` 一样适用）。
- 这一步风险比 OTA 大（可能变砖），不确定就先去 QQ 群 1042220426 问。

---

## 5. 疑难杂症对照表

| 症状 | 原因 / 解决 |
|---|---|
| `adb devices` 显示 `no serial number  offline` | 笔上的 ADB 开关没打开（adbd 没跑）。去 设置→法律监管 连点，然后插拔 USB |
| `adb devices` 什么都没有 | 数据线/驱动问题。`python ydpen.py usb`，出现带感叹号的设备告诉我 VID/PID，我给你装驱动 |
| `cannot mkdir C:\Users\<你>\.android` / `cannot open ...\adb.log` | 用 `tools\adb.cmd`（已把配置目录和 TEMP 指到工作区）；或 `python ydpen.py adb` |
| `adb shell auth` 一直 `password incorrect!` | ① 哈希长度/算法不对 → 看 `out\scripts\` 里的脚本；② **换行符坑**：脚本用 `echo $PASSWD` 时哈希要算“密码+\n”，用 `echo -n` 就不加。`patch_firmware.py` 会自动判，但你可以 `--newline yes/no` 覆盖 |
| 升级下载进度卡住不动 | 分片 MD5 与服务器描述不一致：`endpos` 填错/响应里的分片没取全。重跑 `ota_meta.py`，日志看 `logs\file_server.log` |
| 笔上点“检查更新”没反应 | 界面假检查。恢复出厂 → 联网激活 → 再点（教程明确提到） |
| hosts 改了没用 | 1) 确认 UAC 提权成功（`hosts_tool.ps1 -List`）；2) 笔的 DNS 不一定走电脑；若不行改用 Wireshark 抓一次真实请求域名，告诉我加进 hosts；3) 也可用 ARP 欺骗（需要 Npcap，可后装） |
| `logs\ota_probe.log` 出现 `[tls] ... HTTPS` | 官方已修洞，走路线三 |
| 升级完 `adb shell auth` 还是失败 | 说明改的不是笔真正用的那个脚本：把 `out\patch_report.json` + `out\scripts\*.sh` 发我，我按你固件的真实脚本改工具 |

---

## 6. 风险与免责

- 刷机有**变砖**风险，请保证电量 > 50%，刷机过程中不要断线断电。
- 改的是你自己的设备；本工具不做任何绕过账号/在线鉴权的事，仅利用明文 OTA + 无签名校验。
- 请勿用这个漏洞去动别人的设备。

---

## 7. 给 AI 的交接信息（万一要接着排查）

```powershell
python ydpen.py env                     # 环境
python ydpen.py usb                     # USB 设备
python tools\patch_firmware.py scan firmware\xxx.img --dump-script out\scripts   # 看看真实鉴权脚本
Get-Content logs\ota_probe.log -Tail 50
Get-Content logs\file_server.log -Tail 50
Get-Content out\patch_report.json
```
把上面输出贴给 AI，它就能接着定位。

---

## 8. 本次实战记录（YDPX6-2 / X6 Pro CHN PLUS，系统版本 4.3.5）

硬件与固件：

- SoC：**Rockchip RK3562**；固件格式 `RKFW`（内含 `RKAF`），GPT 分区表，
  `MNT` 里 `system_a` 由包内的 `rootfs.img` 写入；`FIRMWARE_VER: 1.0 / MANUFACTURER: Youdao`
- 分区（mtdparts）：
  `uboot_a/uboot_b/trust_a/trust_b/misc/boot_a/boot_b/recovery/system_a/system_b/userdata/userdisk(grow)`，
  另有 GPT 分区 `rootfs`（uuid 614e0000-…-1d28000054a9）

词典笔真实请求（我们的欺骗服务器抓到的，比 Wireshark 省事）：

```json
{"timestamp":1790844918,
 "sign":"77e115d2a2c86779126d44155d0afe15",
 "mid":"9DA2600007503031",
 "productId":"1687695728",
 "version":"4.3.5",
 "networkType":"WIFI"}
```

官方返回的全量包：`http://iotdown-jd.mayitek.com/1687695728/9502418/d5c214b2-7a1a-4317-a457-9bc7e186a81a.img`
大小 2,125,597,258 字节（2.1 GB）、21 个分片、md5 `78c1c3fbd2151dc8bac1e01c8ae527c0`。

### 踩过的坑（按价值排序）

| 坑 | 现象 | 解决办法 |
|---|---|---|
| adb 写不了用户目录 | `adb.exe: cannot mkdir C:\Users\<你>\.android` / `cannot open %TEMP%\adb.log: Permission denied` | **junction**：`mklink /J %USERPROFILE%\.android <工作区>\.android`，并让 `TEMP/TMP` 指向工作区。工作区里 adb 就能正常生成 `adbkey` |
| adb 只显示 offline / no serial | 说明笔里的 `adbd` 没起来（ADB 开关没真生效）。**每次“开 ADB”只给一个窗口期**，连续 3 次密码错误后 adbd 会下线 | 拔线 → 重新点开 ADB → 插线 |
| 以为 exec/pull/push 能绕过 | `adb exec-out id` 也回 `login with "adb shell auth"`，`adb pull/push` 报 `protocol fault: stat response has wrong message id` | 这版把 shell/exec/sync **三个服务全包了**，只能改固件 |
| 老密码没用 | 4.3.5 上 `CherryYoudao` 报 `password incorrect!` | 走 OTA 改哈希 |
| 官 CDN 403 | `urllib` 直接下载返回 `HTTP Error 403: Forbidden` | 加浏览器 User-Agent（`tools/fast_download.py` 已带） |
| 单线程 0.5MB/s | 2.1GB 要下 1 小时 | 多线程分段下载（`tools/fast_download.py --threads 32`，约 4.5MB/s，7 分钟） |
| 热点 DNS 劫持能不能用 | 官方教程做法 | **实测 192.168.137.1 的 ICS DNS 代理会读 hosts**，所以 hosts 劫持有效，不需要 ARP 欺骗 |

### 一键流程（本次实际跑通的命令）

```powershell
# 0) 电脑开移动热点（SSID/密码可在设置里看；本次是 DESKTOP-DC21P9K 9640 / N23e02=5）
# 1) 词典笔连上热点
# 2) 起欺骗服务器 + 写 hosts
python ydpen.py probe            # 然后在笔上点“检查更新”，会打印出全量包地址
# 3) 多线程下载 2.1GB
python tools\fast_download.py --url <deltaUrl> --out firmware\xxx.img --threads 32 --md5 <md5sum>
# 4) 改哈希 + 重算校验
python ydpen.py patch --password 你的新密码
# 5) 起服务器，让笔升级
python ydpen.py serve
```

