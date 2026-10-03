#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scan_adb.py —— 在局域网里找开着 5555 端口的设备（词典笔 ADB）

用法: python scan_adb.py [网段前缀，默认 192.168.1]
"""
import socket
import sys
import concurrent.futures


def probe(ip):
    s = socket.socket()
    s.settimeout(0.35)
    try:
        s.connect((ip, 5555))
        # 试着读 adb 的 banner（形如 "shell:..." 或者版本串）
        try:
            s.settimeout(0.6)
            data = s.recv(64)
        except Exception:
            data = b""
        return ip, data
    except Exception:
        return None
    finally:
        s.close()


def main():
    prefix = sys.argv[1] if len(sys.argv) > 1 else "192.168.1"
    ips = ["%s.%d" % (prefix, i) for i in range(1, 255)]
    hits = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=128) as ex:
        for r in ex.map(probe, ips):
            if r:
                hits.append(r)
                print("  找到: %s  banner=%r" % (r[0], r[1][:40]))
    if not hits:
        print("  %s.x 网段没有开放 5555 的设备" % prefix)
    return 0


if __name__ == "__main__":
    sys.exit(main())
