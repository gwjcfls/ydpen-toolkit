# 01 · 接入、认证与 ADB 踩坑

## 两条通路

| 通路 | 优点 | 缺点 |
|---|---|---|
| **TCP** `adb connect <笔IP>:5555` | 稳定，不怕 USB 掉线；HTTP 测试可直接从电脑访问笔 | 需要笔连着热点/局域网，IP 会变 |
| **USB** | 不依赖网络 | 经常 `offline`（adbd 没起），需要"拔线→重开 ADB→插线" |

找 IP：

```powershell
python scripts\lan_scan_adb.py --port 5555 --timeout 0.5   # 扫 192.168.137.0/24 与 192.168.1.0/24
```

热点相关：`Get-Service icssvc,SharedAccess`（要 Running），网关 `192.168.137.1`。
`scripts\hotspot.ps1` 可尝试开热点（本机实测 `GetResults` COM 会报错，但热点仍能开起来）。

## 认证

```powershell
"ydpen2026" | adb -s <serial> shell auth      # 期望输出：... 's password: success.
adb -s <serial> shell id                      # uid=0(root) gid=0(root)
```

- 密码是本套流程通过 OTA 补丁改进去的（原密码是厂商随机哈希，爆破未果，见 `04-ota-firmware.md`）。
- **重启后 `/tmp/.adb_auth_verified` 消失** → 每条会话都要重新 `shell auth`。
- 未认证时任何 shell 命令只返回 `login with "adb shell auth" to continue.`

⚠️ **不要对 adb 命令用 `Select-Object -First N`**：它在拿到 N 行后就终止上游管道，
会把 adb 进程提前杀掉，导致 `auth` 没跑完（表现为"输了密码却还是未认证"）。
需要截断输出就先把结果存进变量再截。

## 环境变量（沙箱/权限）

DSH 沙箱下 adb 会因写 `%USERPROFILE%\.android` 与 `%TEMP%\adb.log` 失败。固定套路：

```powershell
$root='...\ydpen-adb'
$env:TMP="$root\tmp"; $env:TEMP="$root\tmp"; $env:ANDROID_USER_HOME="$root\.android"
```

已经做过的处理：`C:\Users\Sever\.android` → 工作区 `.android` 的 junction；`adbkey` 已生成。
`tools\adb.cmd` 是封装好的调用入口。

## 让进程在笔上活下来

`adb shell "nohup cmd &"` 在 shell 退出后进程会被带走（实测 `ps` 里就没了）。
两个可靠做法：

1. **用 DSH 后台任务跑前台命令**：`adb shell "/tmp/fssrv 8080 /userdisk"` 作为 background job ——
   连接在，进程就在。
2. 需要真正脱离 adb 时，用 `setsid`/`start-stop-daemon`（本机没验证过 setsid，谨慎）。

杀进程：`adb shell "killall fssrv"`。

## ADB 开关复位（每次重启后大概率遇到）

1. 拔下 USB；
2. 笔上 **设置 → 法律监管**，对着文本**连点 10~15 下**，直到弹出"ADB 调试已打开"提示；
3. 等 5 秒；
4. 插上 USB，**放着别动**。

反复插拔会让 USB 复合设备在"出现/消失"之间跳，`adb devices` 只会显示空或 `offline`。

## 判断笔"在不在线"的正确姿势

```powershell
Get-PnpDevice -PresentOnly | Where-Object { $_.InstanceId -match 'VID_2207' } | Select Status,Class,FriendlyName
adb devices -l
```

- 有三个 `VID_2207&PID_0011` 条目（Composite / ADB Interface / WPD DictPen）说明 USB 枚举正常；
- 此时 `adb devices` 若是 `offline` → **开关问题**，不是驱动/线的问题。

## 固件补丁的持久性（已验证）

重启后 `adb shell auth` 仍能用 `ydpen2026` 登录 → 说明改的是磁盘上的
`/usr/bin/adbd_auth.sh`，不是内存。回滚方式见 `04-ota-firmware.md`。
