#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
fileserver_plugin.py —— 给文件管理器（8001771940015915）安装/还原自研的 fileServer 插件

背景：
  原版 libjsapi_fileserver_*.so 的 HTTP 服务只有 GET（只读目录列表），没有上传接口，
  所以「文件互传」网页上根本没法上传文件。自研插件用同一套框架 ABI 重写，支持上传。

用法：
  python tools\fileserver_plugin.py status     # 看当前装的是原版还是自研版
  python tools\fileserver_plugin.py install    # 替换成自研版（先备份原版到 .orig 和本地）
  python tools\fileserver_plugin.py restore    # 还原原版

注意：
  * 替换 .so 后**必须重启词典笔**（或至少让框架进程重新加载），否则框架里映射的还是旧文件；
    热替换一个正在被 mmap 的 .so 有概率让框架崩溃 → 表现为点开 app 黑屏。
  * 装完想看效果：笔上打开「文件管理器 → 菜单 → 文件互传」，然后用电脑浏览器访问
    它显示的 http://<笔IP>:8080/ ，页面上会有拖拽上传区域。
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
ADB = os.path.join(ROOT, "tools", "platform-tools", "adb.exe")
NEW_SO = os.path.join(ROOT, "tools", "build", "libjsapi_fileserver_9000000001.so")
ORIG_SO = os.path.join(ROOT, "tools", "build", "orig_libjsapi_fileserver_1448595683.so")
APPID = "8001771940015915"
SO_NAME = "libjsapi_fileserver_1448595683.so"

sys.path.insert(0, HERE)
import _console  # noqa: E402,F401


def env():
    e = dict(os.environ)
    e["TMP"] = e["TEMP"] = os.path.join(ROOT, "tmp")
    e["ANDROID_USER_HOME"] = os.path.join(ROOT, ".android")
    os.makedirs(e["TMP"], exist_ok=True)
    return e


def adb(serial: str | None, args: list[str], timeout: int = 120) -> str:
    cmd = [ADB] + (["-s", serial] if serial else []) + args
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, env=env())
    return (r.stdout or "") + (r.stderr or "")


def pick_serial(explicit: str | None) -> str | None:
    if explicit:
        return explicit
    out = adb(None, ["devices"])
    for line in out.splitlines()[1:]:
        p = line.split()
        if len(p) >= 2 and p[1] == "device":
            return p[0]
    return None


def libdirs(serial: str) -> list[str]:
    """找到该 app 所有槽位里的 libs 目录"""
    out = adb(serial, ["shell", f"ls -d /userdisk/miniapp/data/mini_app/pkg/{APPID}/*/libs 2>/dev/null"])
    return [l.strip() for l in out.splitlines() if l.strip().startswith("/")]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["status", "install", "restore"])
    ap.add_argument("--serial", default=None)
    args = ap.parse_args()

    serial = pick_serial(args.serial)
    if not serial:
        print("[!] 没有在线设备（USB 插好并打开 ADB，或先 adb connect <ip>:5555）")
        return 2
    r = (adb(serial, ["shell", "id"])).strip()
    if "uid=0" not in r:
        print("[!] 不是 root。先 `tools\\adb.cmd shell auth` 输密码（本机是 ydpen2026）")
        return 3
    print(f"[*] 设备 {serial}  root ✔")

    dirs = libdirs(serial)
    if not dirs:
        print(f"[!] 找不到 app {APPID} 的 libs 目录（文件管理器没装？）")
        return 4

    print(f"[*] 插件目录 {len(dirs)} 个：")
    for d in dirs:
        sz = adb(serial, ["shell", f"ls -l {d}/{SO_NAME} 2>/dev/null | awk '{{print $5}}'"]).strip()
        kind = "?"
        try:
            n = int(sz)
            kind = "原版(只读列表)" if n > 2_000_000 else "自研版(支持上传)"
        except Exception:  # noqa: BLE001
            kind = "缺失"
        print(f"    {d}   {sz or '-'} 字节  -> {kind}")

    if args.action == "status":
        return 0

    if args.action == "install":
        if not os.path.isfile(NEW_SO):
            print(f"[!] 缺少自研插件: {NEW_SO}")
            return 5
        for d in dirs:
            # 备份原版（首次）
            adb(serial, ["shell", f"[ -f {d}/{SO_NAME}.orig ] || cp {d}/{SO_NAME} {d}/{SO_NAME}.orig"])
            adb(serial, ["push", NEW_SO, f"{d}/{SO_NAME}"])
            print(f"    [OK] 已安装 -> {d}/{SO_NAME}")
        if not os.path.isfile(ORIG_SO):
            adb(serial, ["pull", f"{dirs[0]}/{SO_NAME}.orig", ORIG_SO])
            print(f"    [OK] 原版已备份到 {ORIG_SO}")
        print("\n[!] 重要：现在必须**重启词典笔**，让框架重新加载插件（热替换可能导致黑屏）")
        return 0

    if args.action == "restore":
        ok = False
        for d in dirs:
            out = adb(serial, ["shell", f"if [ -f {d}/{SO_NAME}.orig ]; then cp {d}/{SO_NAME}.orig {d}/{SO_NAME}; echo restored; fi"])
            print(f"    {d}: {out.strip() or '没有 .orig 备份'}")
            if "restored" in out:
                ok = True
        if not ok and os.path.isfile(ORIG_SO):
            for d in dirs:
                adb(serial, ["push", ORIG_SO, f"{d}/{SO_NAME}"])
            print("    [OK] 已用本地备份还原")
            ok = True
        if not ok:
            print("[!] 找不到原版备份，无法还原")
            return 6
        print("\n[!] 还原后同样需要重启词典笔生效")
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
