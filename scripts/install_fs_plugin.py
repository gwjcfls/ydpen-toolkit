#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
install_fs_plugin.py —— 把自制的 `fs` 插件装进有道词典笔 miniapp 的**活跃槽位**

为什么需要它：
  miniapp 的 app 目录是 A/B 双槽（a/ 和 b/），当前活跃槽写在
  /userdisk/miniapp/resources/slot_info.sh（内容是 `_b` 这种）。
  用 miniapp_cli install 装 .amr 时，**安装器不会把包里的 libs/*.so 复制进去**
  （试过：cert 里有条目也不行），而且重装会切到另一个槽。
  所以每次重装 Doge 阅读/漫画之后，都要用本脚本把 fs 插件放进活跃槽。

用法（在 ydpen-adb 目录下）：
  python tools\install_fs_plugin.py                 # 自动找设备，装到默认两个 app
  python tools\install_fs_plugin.py --apps 8001145141919812
  python tools\install_fs_plugin.py --serial 192.168.137.191:5555
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
ADB = os.path.join(ROOT, "tools", "platform-tools", "adb.exe")
SO = os.path.join(ROOT, "miniapps", "fs-plugin", "libjsapi_fs_1000000001.so")
PKG = "/userdisk/miniapp/data/mini_app/pkg"
DEFAULT_APPS = {
    "8001145141919812": "Doge漫画",
    "8001145141919811": "Doge阅读",
}

sys.path.insert(0, HERE)
import _console  # noqa: E402,F401


def adb(serial: str, args: list[str], timeout: int = 60) -> str:
    cmd = [ADB] + (["-s", serial] if serial else []) + args
    env = dict(os.environ)
    env["TMP"] = env["TEMP"] = os.path.join(ROOT, "tmp")
    env["ANDROID_USER_HOME"] = os.path.join(ROOT, ".android")
    os.makedirs(env["TMP"], exist_ok=True)
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, env=env)
    return (r.stdout or "") + (r.stderr or "")


def pick_serial(explicit: str | None) -> str | None:
    if explicit:
        return explicit
    out = adb("", ["devices"])
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            return parts[0]
    return None


def active_slots(serial: str) -> list[str]:
    """返回活跃槽位字母（通常是 'b'），顺便返回该 app 目录里所有存在的槽位。"""
    out = adb(serial, ["shell", "cat /userdisk/miniapp/resources/slot_info.sh 2>/dev/null"])
    slot = "a"
    for ch in out.strip():
        if ch in "ab":
            slot = ch
    return [slot]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--serial", default=None)
    ap.add_argument("--apps", default=None, help="逗号分隔的 appid，默认 Doge 漫画 + Doge 阅读")
    ap.add_argument("--so", default=SO)
    args = ap.parse_args()

    if not os.path.isfile(args.so):
        print(f"[!] 找不到插件: {args.so}")
        print("    先编译：cd tools/build && zig cc -target aarch64-linux-gnu.2.29 -shared -fPIC -O2 -I quickjs fs_plugin.c -o ../path/libjsapi_fs_1000000001.so")
        return 1

    serial = pick_serial(args.serial)
    if not serial:
        print("[!] 没有在线设备。先在笔上开 ADB 并插拔一次 USB，或先 adb connect <ip>:5555")
        return 2
    print(f"[*] 设备: {serial}")

    r = adb(serial, ["shell", "id"])
    if "uid=0" not in r:
        print("[!] 当前不是 root（ADB 密码没认证）。先跑：")
        print(f'    "{ADB}" -s {serial} shell auth    # 提示密码时输入你的密码（如 ydpen2026）')
        return 3

    slot = active_slots(serial)[0]
    print(f"[*] 活跃槽位: {slot}")

    apps = args.apps.split(",") if args.apps else list(DEFAULT_APPS)
    rc = 0
    for appid in apps:
        name = DEFAULT_APPS.get(appid, "")
        target = f"{PKG}/{appid}/{slot}/libs"
        adb(serial, ["shell", f"mkdir -p {target}"])
        out = adb(serial, ["push", args.so, target + "/"])
        ok = "1 file pushed" in out or "skipped" in out
        print(f"  [{'OK' if ok else '!!'}] {appid} {name} -> {target}/")
        if not ok:
            print("       " + out.strip().splitlines()[-1] if out.strip() else "")
            rc = 4

    print("\n[*] 复查：")
    print(adb(serial, ["shell", f"find {PKG} -name 'libjsapi_fs*' 2>/dev/null"]).strip())

    print("\n[i] 插件会在**下一次启动该 app 时**被框架加载（要彻底生效建议重启一次词典笔）。")
    print("    重启后 ADB 开关会复位，记得重新点开。")
    return rc


if __name__ == "__main__":
    sys.exit(main())
