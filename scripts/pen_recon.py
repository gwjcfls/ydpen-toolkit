#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pen_recon.py —— 词典笔一插上就自动做一轮只读侦察

重点验证：
  1. 登录 shell 是不是被 adb_auth.sh 包了（`adb shell <任意命令>` 都变成要密码）
  2. `adb exec-out`（走 exec: 服务，不经登录 shell）能不能绕过鉴权直接拿到 root
  3. 能不能直接读 /usr/bin/adb_auth.sh —— 拿到它就等于知道哈希算法和原始哈希

用法: python pen_recon.py [--wait 600]
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
ADB = os.path.join(ROOT, "tools", "platform-tools", "adb.exe")
LOGS = os.path.join(ROOT, "logs")
TMP = os.path.join(ROOT, "tmp")

sys.path.insert(0, HERE)
import _console  # noqa: E402,F401


def env():
    e = dict(os.environ)
    e["TEMP"] = TMP
    e["TMP"] = TMP
    e["ANDROID_USER_HOME"] = os.path.join(ROOT, ".android")
    return e


def adb(argv, stdin=None, timeout=20):
    try:
        r = subprocess.run([ADB] + argv, input=stdin, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout, env=env())
        return (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired:
        return "<超时：命令没返回（可能是被鉴权卡住）>"
    except Exception as e:  # noqa: BLE001
        return f"<异常 {e}>"


def device_state():
    out = adb(["devices"])
    lines = [l.strip() for l in out.splitlines()[1:] if l.strip()]
    for l in lines:
        parts = l.split()
        if len(parts) >= 2 and parts[1] == "device":
            return parts[0]
    return None


PROBES = [
    ("exec-out id（关键：走 exec: 服务，可能绕过登录壳）", ["exec-out", "id"]),
    ("exec-out uname", ["exec-out", "uname", "-a"]),
    ("exec-out cat /usr/bin/adb_auth.sh", ["exec-out", "cat", "/usr/bin/adb_auth.sh"]),
    ("exec-out cat /usr/bin/adbd_auth.sh", ["exec-out", "cat", "/usr/bin/adbd_auth.sh"]),
    ("exec-out ls /usr/bin", ["exec-out", "ls", "-la", "/usr/bin"]),
    ("exec-out cat /etc/passwd", ["exec-out", "cat", "/etc/passwd"]),
    ("exec-out ps", ["exec-out", "ps", "w"]),
    ("shell id（预期被鉴权挡住）", ["shell", "id"]),
    ("shell cat /etc/passwd", ["shell", "cat", "/etc/passwd"]),
    ("shell，不给命令（看欢迎语）", ["shell"]),
    ("pull /usr/bin/adb_auth.sh", ["pull", "/usr/bin/adb_auth.sh", os.path.join(LOGS, "pen_adb_auth.sh")]),
    ("pull /usr/bin/adbd_auth.sh", ["pull", "/usr/bin/adbd_auth.sh", os.path.join(LOGS, "pen_adbd_auth.sh")]),
    ("exec-out getprop（前 40 行）", ["exec-out", "sh", "-c", "getprop | head -40"]),
    ("exec-out mount", ["exec-out", "sh", "-c", "mount | head -30"]),
    ("exec-out ls /tmp", ["exec-out", "ls", "-la", "/tmp"]),
]


def recon(serial: str) -> None:
    log = [f"\n{'='*78}\n侦查时间 {time.strftime('%Y-%m-%d %H:%M:%S')}  设备 {serial}\n{'='*78}"]
    for label, argv in PROBES:
        out = adb(argv, timeout=25)
        block = f"\n--- {label} ---\n{out.strip()[:4000]}"
        print(block, flush=True)
        log.append(block)
        if any(k in out for k in ("uid=0", "root")) and "exec-out id" in label:
            print("[+++] exec-out 直接就是 root！可以绕过密码了！", flush=True)
    os.makedirs(LOGS, exist_ok=True)
    with open(os.path.join(LOGS, "pen_recon.txt"), "a", encoding="utf-8") as f:
        f.write("\n".join(log) + "\n")
    print(f"\n[*] 报告已存 logs\\pen_recon.txt", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wait", type=int, default=900)
    args = ap.parse_args()
    os.makedirs(TMP, exist_ok=True)
    os.makedirs(LOGS, exist_ok=True)
    print(f"[*] 等词典笔上线（最多 {args.wait}s）…", flush=True)
    t0 = time.time()
    done = set()
    while time.time() - t0 < args.wait:
        s = device_state()
        if s and s not in done:
            print(f"[+] 设备上线: {s}", flush=True)
            recon(s)
            done.add(s)
            print("[*] 本轮结束，继续等下一次插拔…", flush=True)
            time.sleep(3)
        time.sleep(2)
    print("[*] 等待超时。", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
