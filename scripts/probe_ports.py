#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""probe_ports.py —— 快速探测某个 IP 上常见端口（找词典笔的可达服务）"""
import socket
import sys
import concurrent.futures

PORTS = [22, 80, 443, 5555, 5037, 8080, 8000, 2222, 22222, 7777, 8888,
         5554, 5556, 14514, 12345, 27015, 3000, 5000, 9000, 10000]


def probe(host, port):
    s = socket.socket()
    s.settimeout(1.2)
    try:
        s.connect((host, port))
        try:
            s.settimeout(1.0)
            data = s.recv(96)
        except Exception:
            data = b""
        return port, "开放", data
    except socket.timeout:
        return None
    except ConnectionRefusedError:
        return port, "拒绝", b""
    except Exception as e:
        return None
    finally:
        s.close()


def main():
    host = sys.argv[1] if len(sys.argv) > 1 else "192.168.137.100"
    print("探测 %s …" % host)
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(PORTS)) as ex:
        futs = [ex.submit(probe, host, p) for p in PORTS]
        for f in concurrent.futures.as_completed(futs):
            r = f.result()
            if not r:
                continue
            port, state, data = r
            if state == "开放":
                print("  ✔ %-6d 开放   banner=%r" % (port, data[:40]))
            else:
                print("  ✘ %-6d %s" % (port, state))
    return 0


if __name__ == "__main__":
    sys.exit(main())
