# 04 · OTA 固件补丁（ADB 密码的真正来源）

## 背景

笔的 `adbd` 用一个校验脚本 `/usr/bin/adbd_auth.sh` 存密码哈希；原始哈希
`302af1d80be1106586775350cc0a2c92` 是**厂商随机**的（见文末爆破结论）。
系统分区不可写、也没有其它提权入口，所以走 **OTA 中间人**：让笔"自己刷"一个我们改过的镜像。

## 为什么可行（三个前提，全部实测成立）

1. OTA 走**明文 HTTP**（`iotapi.abupdate.com`），没有 TLS 校验障碍；
2. 固件镜像**不签名**（只做 MD5/SHA 校验，而校验值就在我们能改的响应里）；
3. 安装策略里有 `policy.install.rebootUpgrade` 开关，打开后笔会在装完后自动重启应用补丁。

## 攻击链

```
笔 → checkVersion (HTTP)
      ↓ hosts 劫持 iotapi.abupdate.com → 192.168.137.1（本机）
假 OTA 服务（scripts\fake_ota_server.py，监听 80）
      ├─ probe 阶段：把笔的真实请求转发给真服务器，存下 out\real_response.json（拿正确的 sign/mid）
      └─ serve 阶段：返回我们改过的 JSON（新版号 99.99.91 + 改过的校验值 + 指向本机的下载地址）
      ↓
笔下载镜像（scripts\file_server.py 监听 14514，支持 Range 206）
      ↓
"正在安装 100.00%" → 重启 → 补丁生效（登录密码变成 ydpen2026）
```

## 镜像要改什么

`scripts\patch_firmware.py`（会自动探测哈希算法/md5/sha256 与 `echo -n` 换行差异，做**等长替换**）：

| 目标 | 改动 | 备注 |
|---|---|---|
| `/usr/bin/adbd_auth.sh` | 原始哈希 → 自己的密码哈希 | **必须等长**（in-place 替换，不改文件大小） |
| 安装策略 | `policy.install.rebootUpgrade="false"` → `"true"` | 不打开的话会卡在"正在安装 100.00%"，笔不重启、补丁不生效 |

`scripts\ota_meta.py`：一次遍历算出并重写
`segmentMd5 / md5sum / sha / sha256 / fileSize / storageSize`，并把 `bakUrl/deltaUrl` 指向本机；
可强制版本号（`--force-version 99.99.91`）。输出 `out\patched_response.json`。

## 镜像分发与下载

- `scripts\file_server.py`：给笔下载的 HTTP 服务（监听 14514），支持 `Range`；
- `scripts\fast_download.py`：本机从 CDN 拉原版镜像用。**CDN 会截断长连接**
  （`提前结束`），所以用 1MB 分块 + 32 线程 + 块内续传；并且**必须带浏览器 User-Agent**，
  否则 `HTTP 403`。

镜像格式与分区（了解即可）：Rockchip **RKFW/RKAF**，GPT + `mtdparts`，
`system_a` 对应镜像里的 `rootfs.img`。

## 结果与回滚

| 项 | 值 |
|---|---|
| 补丁后镜像 md5 | `4dd3516643d12c105b2a7036ce76975a`（**大小不变**：2,125,597,258 字节） |
| 原版备份 | `firmware\backup\*.img` |
| 登录密码 | `ydpen2026` |
| 持久性 | 已验证：重启后仍能用该密码 `adb shell auth` |

回滚两种方式：

```bash
# 1) 直接改回哈希（等长替换，无需刷机）
sed -i 's/054bda46d56c8496e14e81d78f5a3c27/302af1d80be1106586775350cc0a2c92/' /usr/bin/adbd_auth.sh

# 2) 重新刷回 backup 里的原版镜像（走同样的 OTA 通路）
```

## hosts 劫持

`scripts\hosts_tool.ps1 -Add 192.168.137.1` 把 `iotapi.abupdate.com` 指到本机（**需要点 UAC 的"是"**）。
脚本会先检查是否已经劫持，避免重复弹 UAC。做完 OTA 记得移除劫持，否则日常在线升级会被劫持。

## 密码爆破为什么放弃（省得以后再试）

`scripts\crack_adb_md5.py` / `crack_adb_md5_mp.py` / `hc_campaign.py` 记录了完整尝试：

- rockyou 字典 + OneRule 变形；
- 有道相关词表（设备名、品牌、常见后缀）；
- 9–12 位纯数字、6–8 位小写字母、字母+数字掩码；
- 硬件：RTX 2080 Ti（hashcat 6.2.6，见 `tools\build\hashcat\`）。

全部无果 → 结论：**厂商随机生成**，爆破不现实，只能改固件。

## 顺带说明：为什么不能只改分区

系统分区只读且被 A/B 槽 + 校验保护；直接 `mount -o rw` 改 `/usr/bin/adbd_auth.sh` 会因为
校验/只读层失败或被下次升级覆盖。**走 OTA 打补丁**是唯一稳定持久的路子。

## 风险提醒

- 刷机有变砖风险：**动手前确认 `firmware\backup\` 里的原版镜像可用**；
- 别在低电量时刷；
- 改镜像时保持**文件大小不变**（等长替换）能显著降低校验失败概率；
- 每一步都在 `out\` 留报告（`patch_report.json` / `patched_response.json`），出问题可回溯。
