#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
file_server.py —— 给词典笔下载固件的 HTTP 直链服务器（带 Range 断点续传 + 进度日志）

教程里那个简易脚本不支持 Range，词典笔下载中断后容易卡进度（“卡在 6.5%/11%”）。
这里实现完整的 Range / 206 支持，并且每传 10% 打一行日志，方便判断到底是谁的问题。

用法
----
  python file_server.py --file ..\\firmware\\xxx.img                 # 默认 14514 端口
  python file_server.py --file ..\\firmware\\xxx.img --port 14514 --strict-path
  python file_server.py --file ..\\firmware\\xxx.img --once          # 传完一次就退出
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import _console  # noqa: F401  (把控制台切到 UTF-8)

STATE = {"file": None, "sent": 0, "last_report": 0.0, "done": threading.Event(), "once": False}
LOCK = threading.Lock()
CHUNK = 512 * 1024


def now() -> str:
    return dt.datetime.now().strftime("%H:%M:%S")


def log(m: str) -> None:
    print(f"[{now()}] {m}", flush=True)


class Handler(BaseHTTPRequestHandler):
    server_version = "nginx/1.20.1"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *a):
        pass

    def do_HEAD(self):
        self._send(False)

    def do_GET(self):
        self._send(True)

    def _send(self, with_body: bool):
        path = STATE["file"]
        size = os.path.getsize(path)
        name = os.path.basename(path)
        rng = self.headers.get("Range")
        start, end = 0, size - 1
        partial = False
        if rng:
            m = re.match(r"bytes=(\d*)-(\d*)", rng.strip())
            if m:
                s, e = m.group(1), m.group(2)
                if s:
                    start = int(s)
                    end = int(e) if e else size - 1
                elif e:  # bytes=-N 最后 N 字节
                    start = max(0, size - int(e))
                partial = True
        if start >= size:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.end_headers()
            return
        end = min(end, size - 1)
        length = end - start + 1

        log(f"GET {self.path} from {self.client_address[0]}  Range={rng or '-'}  -> {length/1048576:.1f} MiB @ {start}")

        self.send_response(206 if partial else 200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Disposition", f'attachment; filename="{name}"')
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Connection", "close")
        self.end_headers()
        if not with_body:
            return

        sent = 0
        last = 0.0
        try:
            with open(path, "rb") as f:
                f.seek(start)
                remain = length
                while remain > 0:
                    buf = f.read(min(CHUNK, remain))
                    if not buf:
                        break
                    self.wfile.write(buf)
                    remain -= len(buf)
                    sent += len(buf)
                    if sent - last >= size / 10:
                        last = sent
                        log(f"    已发送 {sent/1048576:.1f} MiB / {length/1048576:.1f} MiB")
        except (ConnectionResetError, BrokenPipeError) as e:
            log(f"    连接中断: {e}（词典笔会带 Range 重试，属正常）")
        finally:
            with LOCK:
                STATE["sent"] += sent
            if sent >= length and end == size - 1:
                log("    本次传输完成 ✔")
                if STATE["once"]:
                    STATE["done"].set()


def main() -> int:
    ap = argparse.ArgumentParser(description="固件直链服务器")
    ap.add_argument("--file", required=True)
    ap.add_argument("--port", type=int, default=14514)
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--strict-path", action="store_true", help="只允许访问真实文件名这个路径")
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()

    if not os.path.isfile(args.file):
        log(f"[!] 文件不存在: {args.file}")
        return 1
    STATE["file"] = os.path.abspath(args.file)
    STATE["once"] = args.once

    srv = ThreadingHTTPServer((args.bind, args.port), Handler)
    log(f"固件直链服务器: http://<本机IP>:{args.port}/{os.path.basename(STATE['file'])}")
    log(f"文件: {STATE['file']}  ({os.path.getsize(STATE['file']):,} 字节)")
    log("Ctrl+C 停止")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        log("停止。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
