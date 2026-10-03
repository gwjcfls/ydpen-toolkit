#!/bin/sh
# usb-mode.sh —— 切换词典笔的 USB 模式（MTP / ADB / 同时），并可持久化
# ============================================================================
# 原理（实测，详见 skill reference/10）：
#   /usr/bin/S98usbdevice 按 /tmp/.usb_config 里的一行行关键字决定 USB 暴露哪些功能：
#       usb_mtp_en  usb_adb_en  usb_ums_en  usb_ntb_en  usb_acm_en
#       usb_uac1_en usb_uac2_en usb_uvc_en  usb_rndis_en usb_hid_en
#   原厂 /etc/init.d/.usb_config = usb_mtp_en（只有 MTP）；
#   但**一旦开过 ADB 调试**，系统会把 /tmp/.usb_config 覆盖成 usb_adb_en
#   → 电脑上就只看到 ADB 接口、没有 MTP → "插上电脑不能传文件"。
#
#   本脚本就是往 /tmp/.usb_config 写关键字并重新绑定 gadget；
#   `persist` 会把选择记到 /userdisk（ext4 持久化），由开机钩子每次开机自动应用
#   （否则 /tmp 一重启就没了，会回落到只读区那份 = 仅 MTP，连 adbd 都不启动）。
#
# 用法：
#   sh usb-mode.sh status            # 看当前模式与 gadget 绑定
#   sh usb-mode.sh both              # ADB + MTP（推荐：能传文件也能调试）
#   sh usb-mode.sh mtp               # 仅 MTP（= 原厂默认）
#   sh usb-mode.sh adb               # 仅 ADB
#   sh usb-mode.sh persist both      # 设为 both，并在每次开机自动应用
#   sh usb-mode.sh persist off       # 取消开机自动应用
# ============================================================================

USBDEV=/usr/bin/S98usbdevice
CFG=/tmp/.usb_config
FACTORY=/etc/init.d/.usb_config
PERSIST=/userdisk/skip_re/usb-mode.conf
G=/sys/kernel/config/usb_gadget/rockchip

show() {
    echo "== 当前 USB 模式 =="
    echo "  /tmp/.usb_config    : [$(tr '\n' ' ' < $CFG 2>/dev/null)]"
    echo "  /etc/init.d/.usb_config（原厂）: [$(tr '\n' ' ' < $FACTORY 2>/dev/null)]"
    [ -s "$PERSIST" ] && echo "  开机自动应用        : [$(tr '\n' ' ' < $PERSIST)]" || echo "  开机自动应用        : 未设置"
    echo "  idProduct           : $(cat $G/idProduct 2>/dev/null)"
    echo "  UDC                 : $(cat $G/UDC 2>/dev/null)"
    echo "  已绑定功能          :"
    ls -l $G/configs/b.1/ 2>/dev/null | grep -E 'f[0-9]' | awk '{print "      " $9 " -> " $11}'
    echo "  进程                : adbd=$(ps | grep -c '[a]dbd')  mtp-server=$(ps | grep -c '[m]tp-server')"
    if [ -f /tmp/mtp.log ]; then
        echo "  mtp.log 末尾        : $(tail -1 /tmp/mtp.log 2>/dev/null | cut -c1-90)"
    fi
}

apply() {
    keys=$1
    case "$keys" in
        both) printf 'usb_mtp_en\nusb_adb_en\n' > $CFG ;;
        mtp)  printf 'usb_mtp_en\n' > $CFG ;;
        adb)  printf 'usb_adb_en\n' > $CFG ;;
        factory) cp $FACTORY $CFG ;;
        *) echo "[!] 未知模式: $keys（可选 both|mtp|adb|factory）"; return 1 ;;
    esac
    echo "[i] 写入 $CFG: [$(tr '\n' ' ' < $CFG)]"
    $USBDEV stop >/dev/null 2>&1
    $USBDEV start 2>&1 | head -5
    sleep 2
    echo "[✓] 已应用。当前绑定："
    ls -l $G/configs/b.1/ 2>/dev/null | grep -E 'f[0-9]' | awk '{print "      " $9 " -> " $11}'
    echo "    adbd=$(ps | grep -c '[a]dbd')  mtp-server=$(ps | grep -c '[m]tp-server')"
}

case "$1" in
    status|"") show ;;
    mtp|adb|both|factory) apply "$1" ;;
    persist)
        case "$2" in
            off)
                rm -f "$PERSIST"
                echo "[✓] 已取消开机自动应用（下次开机回落到原厂：$(tr '\n' ' ' < $FACTORY)）"
                ;;
            mtp|adb|both)
                mkdir -p "$(dirname "$PERSIST")"
                case "$2" in
                    both) printf 'usb_mtp_en\nusb_adb_en\n' > "$PERSIST" ;;
                    mtp)  printf 'usb_mtp_en\n' > "$PERSIST" ;;
                    adb)  printf 'usb_adb_en\n' > "$PERSIST" ;;
                esac
                echo "[✓] 已记录：$(tr '\n' ' ' < "$PERSIST")（开机钩子每次开机自动应用）"
                apply "$2"
                ;;
            *) echo "用法: $0 persist {both|mtp|adb|off}" ;;
        esac
        ;;
    *) echo "用法: $0 {status|mtp|adb|both|factory|persist {both|mtp|adb|off}}" ;;
esac
