# USB 模式与 MTP：为什么插上电脑不能传文件（YDPX7-1 / PenOS 4.8.7 实测）

## 一、结论

笔**原生支持 MTP**（内核模块 + 用户态实现 + configfs function 全都在），
"插上电脑没有 MTP"的原因只有一个：

> **开过 ADB 调试之后，系统把 USB 模式配置从 `usb_mtp_en` 覆盖成了 `usb_adb_en`**，
> 于是 USB 只暴露 ADB 接口，MTP 消失了。

原厂默认其实就是 **MTP**：

```
/etc/init.d/.usb_config     = [usb_mtp_en]        ← 出厂默认（只读区）
/tmp/.usb_config            = [usb_adb_en]        ← 开 ADB 调试后被覆盖成这个
```

## 二、机制（三层）

### 2.1 USB gadget 现状（configfs）

```
/sys/kernel/config/usb_gadget/rockchip/
    idVendor/idProduct = 0x2207 / 0x00xx      (0x0011 = MTP+ADB 双接口)
    UDC                = fe500000.usb
    functions/         ffs.adb | ffs.ntb | mtp.gs0 | uvc.gs6      ← 四个都已创建
    configs/b.1/       f1 -> …  f2 -> …                          ← 只有被"绑定"的才真正暴露
```

`configs/b.1/` 里链接了哪些 function，Windows 就看到什么设备（按接口数变成 `MI_00`/`MI_01`）。

### 2.2 谁决定绑定哪些：`/usr/bin/S98usbdevice`

Rockchip SDK 标准脚本（447 行），从 **`/tmp/.usb_config`** 一行行读关键字：

```
usb_mtp_en   usb_adb_en   usb_ums_en   usb_ntb_en   usb_acm_en
usb_uac1_en  usb_uac2_en  usb_uvc_en   usb_rndis_en usb_hid_en
```

主流程：

```sh
case "$1" in
start)
    if [ -e /tmp/.usb_config ]; then USB_CONFIG_FILE=/tmp/.usb_config
    else USB_CONFIG_FILE=/tmp/.usb_config; cp /etc/init.d/.usb_config /tmp/.usb_config; fi
    parameter_init        # 读关键字 → MTP_EN/ADB_EN/... = on
    use_os_desc; echo $PID > idProduct; bind_functions     # 绑到 configs/b.1
    echo $UDC > UDC       # 绑定控制器 = 真正"插上"
    run_binary            # ADB_EN=on → 起 adbd；MTP_EN=on → 起 mtp-server
    ;;
stop)  usb_device_stop    # UDC=none + kill adbd + kill mtp-server + 清空绑定
```

**关键点**：`/tmp/.usb_config` 是 **tmpfs**，重启即丢；丢了就回落到只读区的
`/etc/init.d/.usb_config`（= 仅 MTP）。所以：

- 不碰 ADB 调试 → 每次开机都是 MTP ✔
- 开过 ADB 调试 → 当次运行期变成"仅 ADB"，**重启后又回到"仅 MTP"** ✔

### 2.3 MTP 的实现（笔上齐活）

| 组件 | 路径 | 说明 |
|---|---|---|
| 内核模块 | `/system/lib/modules/usb_f_mtp.ko` | gadget MTP 功能 |
| 用户态 | `/usr/bin/mtp-server`（194KB） | **Android 的 MTP 实现移植版**（`android::MtpServer` / `UbuntuMtpDatabase`） |
| 库 | `/usr/lib/libmtpserver.so.1` | |
| 启动方式 | `start-stop-daemon --background --startas /bin/bash -- -c "exec /usr/bin/mtp-server > /tmp/mtp.log 2>&1"` | 由 S98usbdevice 在 `MTP_EN=on` 时拉起 |
| 日志 | `/tmp/mtp.log` | `MTP server starting...` / `mtp is read only = 0`（可写） |
| 暴露目录 | `/userdisk`（Windows 里显示为 `MTP/Music`、`MTP/Favorite`） | |

## 三、怎么切（工具：`scripts/usb-mode.sh`）

```sh
sh /userdisk/skip_re/usb-mode.sh status          # 看当前模式/gadget 绑定/进程
sh /userdisk/skip_re/usb-mode.sh both            # ★ ADB + MTP 同时（推荐）
sh /userdisk/skip_re/usb-mode.sh mtp             # 仅 MTP（= 原厂默认）
sh /userdisk/skip_re/usb-mode.sh adb             # 仅 ADB
sh /userdisk/skip_re/usb-mode.sh factory         # 回到只读区那份配置
sh /userdisk/skip_re/usb-mode.sh persist both    # 设为 both 且**每次开机自动应用**
sh /userdisk/skip_re/usb-mode.sh persist off     # 取消开机自动应用
```

`persist` 会把选择写到 `/userdisk/skip_re/usb-mode.conf`（ext4 持久化），
开机钩子（`sideload-keeper.sh` 阶段 0b）每次开机 `cp` 到 `/tmp/.usb_config` 并跑一次
`S98usbdevice start` —— 这样 MTP 与 ADB 都能长期保留。

### 实测（Windows 侧）

```
# device manager / PnP
OK  WPD        DictPen         USB\VID_2207&PID_0011&MI_00      ← MTP/便携设备
OK  USBDevice  ADB Interface   USB\VID_2207&PID_0011&MI_01      ← ADB
OK  USB        USB Composite Device  USB\VID_2207&PID_0011\ME82300008900903

# 资源管理器（Shell COM 枚举）
此电脑 → DictPen  [便携式媒体播放器]
   内部存储 → MTP
       ├── Music      [文件夹]
       └── Favorite   [文件夹]

# 笔上
/tmp/.usb_config = [usb_mtp_en usb_adb_en]    idProduct=0x0011
绑定 f1 -> mtp.gs0    f2 -> ffs.adb
进程 adbd=1  mtp-server=1
```

历史枚举痕迹也印证了模式切换（同一支笔）：

| PID | 组合 | 说明 |
|---|---|---|
| `0001` | WPD 单接口 | 仅 MTP（原厂默认） |
| `0006` | USBDevice | 其他模式（UMS/串口等） |
| `0011` | `MI_00`=WPD + `MI_01`=ADB | **MTP + ADB（本 skill 采用的配置）** |

## 四、坑

1. **`S98usbdevice stop` 会 `kill adbd`** —— 如果你的 ADB 是走 USB 的，这一步会直接断连；
   走 **TCP/WiFi 的 adb（`adb connect <笔IP>:5555`）** 才不受影响。
   本次实测：stop 期间 `adb devices` 短暂 `offline`，`start` 完成后（约 10~20s）自动恢复。
   **务必保证有一条独立通道**（TCP adb / SSH）再动 USB 配置。
2. `stop` 后 gadget 处于 `UDC=none`，Windows 会**完全看不到设备**（不是"看不到 MTP"）——
   别误判成线或驱动问题。
3. MTP 存储由 `mtp-server` 决定，目前只映射 `/userdisk` 下的部分目录；
   要传文件请用 `MTP/Favorite`（也是 `adb push` 常用的落地目录）。
4. 改了 `/tmp/.usb_config` 一定要跑一次 `S98usbdevice start` 才生效（只改文件不重绑没用）。

## 五、相关命令速查

```sh
# 看 gadget 到底暴露了什么
G=/sys/kernel/config/usb_gadget/rockchip
ls -l $G/configs/b.1/ | grep -E 'f[0-9]'     # 绑定关系
cat $G/idProduct $G/UDC                       # 0x0011 / fe500000.usb
cat /tmp/.usb_config                          # 当前模式关键字

# 手动重配（慎用，先确保有 TCP adb 通道）
printf 'usb_mtp_en\nusb_adb_en\n' > /tmp/.usb_config
/usr/bin/S98usbdevice stop; sleep 1; /usr/bin/S98usbdevice start

# MTP 日志
cat /tmp/mtp.log
```
