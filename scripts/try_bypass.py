#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
try_bypass.py —— 不刷机、不猜密码，直接试“绕过 adb 鉴权”的路子（安全版）

依据（官方鉴权脚本 adb_auth.sh）：
    VERIFIED=/tmp/.adb_auth_verified
    if [ -f "$VERIFIED" ]; then echo "success."; exit; fi      <-- 这个文件在就直接放行
    ... read PASSWD; 比对 md5/sha256 ...

adbd 不止一个服务：
    shell: 服务 —— 被包成了 adb_auth.sh，所以 `adb shell 任何命令` 都要密码
    exec:  服务 —— `adb exec-out`，直接执行命令，可能没被包
    sync:  服务 —— `adb push/pull`，也走 adbd 自己的权限

安全策略（重要）：**绝不随便调 shell 服务**，因为 `adb shell <命令>` 会跑鉴权脚本，
空输入 = 连续 3 次密码错误，部分固件会因此把 adbd 冷却/下线一段时间。

所以顺序是：
    1. adb exec-out id                       只读；通了就是 root
    2. adb pull /usr/bin/adb_auth.sh         只读；拿到真脚本=拿到算法和原哈希
    3. adb push 空文件到 /tmp/.adb_auth_verified   只写 /tmp（不改固件）
       再用 adb pull /tmp/.adb_auth_verified 确认写进去了（不确认就不去试 auth，免得触发冷却）
    4. 确认标记在位后，才 adb shell auth 空密码 → 应回 success. → 再 adb shell id

用法:
    python tools\try_bypass.py            # 现在设备在线就立刻跑
    python tools\try_bypass.py --watch     # 等设备上线自动跑（推荐）
"""

from __future__ import annotations

import argparse
import os
import re
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


def adb(argv, stdin=None, timeout=25):
    try:
        r = subprocess.run([ADB] + argv, input=stdin, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout, env=env())
        return (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired:
        return "<超时>"
    except Exception as e:  # noqa: BLE001
        return f"<异常 {e}>"


def state():
    out = adb(["devices"])
    for l in out.splitlines()[1:]:
        p = l.split()
        if len(p) >= 2 and p[1] == "device":
            return p[0]
    return None


def show(label, out, limit=2000):
    print(f"\n--- {label} ---", flush=True)
    print((out.strip()[:limit] if out.strip() else "(无输出)"), flush=True)


def run_once() -> int:
    os.makedirs(LOGS, exist_ok=True)
    os.makedirs(TMP, exist_ok=True)
    serial = state()
    if not serial:
        print("[!] 没有在线设备（offline / 无设备 都不行）。", flush=True)
        return 2
    print(f"[*] 设备 {serial} 在线，开始安全探测", flush=True)

    # 1) exec: 服务
    out = adb(["exec-out", "id"])
    show("1. exec-out id", out)
    if "uid=0" in out:
        print("[+++] exec: 服务没有被鉴权包住 —— 你已经拿到 root shell！", flush=True)
        show("   exec-out cat /usr/bin/adb_auth.sh", adb(["exec-out", "cat", "/usr/bin/adb_auth.sh"]), 4000)
        show("   exec-out ls -la /", adb(["exec-out", "sh", "-c", "ls -la / | head -30"]))
        print("\n[结论] 用 tools\\adb.cmd exec-out sh -c '<命令>' 即可，无需密码。", flush=True)
        return 0

    # 2) sync: 服务读鉴权脚本
    dest = os.path.join(LOGS, "pen_adb_auth.sh")
    if os.path.exists(dest):
        os.remove(dest)
    show("2. pull /usr/bin/adb_auth.sh", adb(["pull", "/usr/bin/adb_auth.sh", dest]))
    if os.path.isfile(dest):
        with open(dest, "r", encoding="utf-8", errors="replace") as f:
            txt = f.read()
        show("   —— 笔上真实的鉴权脚本 ——", txt, 4000)
        print(f"   脚本里的哈希: {re.findall(r'[0-9a-fA-F]{32,64}', txt)}", flush=True)
    else:
        print("   （pull 失败：sync 服务可能也被包住了）", flush=True)

    # 3) sync: 服务写 /tmp 标记
    marker = os.path.join(TMP, ".adb_auth_verified")
    with open(marker, "w", encoding="utf-8") as f:
        f.write("ok\n")
    show("3a. push -> /tmp/.adb_auth_verified", adb(["push", marker, "/tmp/.adb_auth_verified"]))
    back = os.path.join(TMP, "verify_back")
    if os.path.exists(back):
        os.remove(back)
    pull_back = adb(["pull", "/tmp/.adb_auth_verified", back])
    show("3b. pull 回来确认", pull_back)
    confirmed = os.path.isfile(back)

    if not confirmed:
        print("\n[结论] 标记没写进去，就不去试 auth（避免连续错密码把 adbd 冷却）。", flush=True)
        print("       下一步走 README 路线二：OTA 中间人改固件哈希（python ydpen.py probe）。", flush=True)
        return 3

    # 4) 标记确认在位，才调 shell 服务
    auth_out = adb(["shell", "auth"], stdin="\n", timeout=20)
    show("4a. shell auth（空输入）", auth_out)
    if "success" in auth_out.lower():
        print("[+++] 绕过成功：/tmp/.adb_auth_verified 生效", flush=True)
        show("4b. shell id", adb(["shell", "id"]))
        print("\n[结论] 不重启就一直有效：tools\\adb.cmd shell", flush=True)
        return 0
    print("\n[结论] 写进去了但 auth 仍不放行 —— 说明这版脚本还查别的东西，把日志发我。", flush=True)
    return 4


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--wait", type=int, default=1800)
    args = ap.parse_args()
    if not args.watch:
        return run_once()
    print(f"[*] 等设备上线（{args.wait}s 内），上线就跑一次 …", flush=True)
    t0 = time.time()
    done = set()
    while time.time() - t0 < args.wait:
        s = state()
        if s and s not in done:
            print(f"[+] {s} 上线", flush=True)
            rc = run_once()
            done.add(s)
            if rc == 0:
                return 0
            print("[*] 本轮没成功，继续等下一位“选手”（重新插拔/重开 ADB 后再来）…", flush=True)
        time.sleep(2)
    print("[*] 超时退出。", flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())
