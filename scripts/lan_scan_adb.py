#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
lan_scan_adb.py —— 扫本地网段，找开着 ADB(5555) 的设备（词典笔也可能走网络 adb）

用法:
  python lan_scan_adb.py                 # 扫本机 /24 网段的 5555
  python lan_scan_adb.py --port 5555 --port 8080
  python lan_scan_adb.py --cidr 192.168.1.0/24
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import ipaddress
import socket
import sys
import time

import _console  # noqa: F401


def local_cidrs():
    cidrs = []
    try:
        host = socket.gethostbyname(socket.gethostname())
        if not host.startswith("127."):
            cidrs.append(str(ipaddress.ip_network(host + "/24", strict=False)))
    except Exception:  # noqa: BLE001
        pass
    # 常见热点网段
    cidrs += ["192.168.137.0/24", "192.168.1.0/24"]
    seen = []
    for c in cidrs:
        if c not in seen:
            seen.append(c)
    return seen


def probe(ip: str, port: int, timeout: float) -> str | None:
    s = socket.socket()
    s.settimeout(timeout)
    try:
        if s.connect_ex((ip, port)) == 0:
            banner = ""
            try:
                s.settimeout(1.0)
                data = s.recv(64)
                if data:
                    banner = repr(data[:40])
            except Exception:  # noqa: BLE001
                pass
            return f"{ip}:{port} 开放 {banner}"
    except Exception:  # noqa: BLE001
        pass
    finally:
        s.close()
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cidr", default=None)
    ap.add_argument("--port", action="append", type=int, default=None)
    ap.add_argument("--timeout", type=float, default=0.35)
    ap.add_argument("--workers", type=int, default=200)
    args = ap.parse_args()

    cidrs = [args.cidr] if args.cidr else local_cidrs()
    ports = args.port or [5555]
    targets = []
    for c in cidrs:
        net = ipaddress.ip_network(c, strict=False)
        targets += [(str(ip), p) for ip in net.hosts() for p in ports]
    print(f"[*] 扫描 {len(targets)} 个目标（网段 {cidrs}，端口 {ports}），最多 {args.timeout*len(targets)/args.workers:.0f} 秒 …")
    t0 = time.time()
    found = []
    with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(probe, ip, p, args.timeout) for ip, p in targets]
        for f in cf.as_completed(futs):
            r = f.result()
            if r:
                print("  [+] " + r)
                found.append(r)
    print(f"[*] 完成，用时 {time.time()-t0:.1f}s，开放 {len(found)} 个")
    if not found:
        print("    没扫到 —— 词典笔可能没连 WiFi，或者 adbd 没在 5555 上监听。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
