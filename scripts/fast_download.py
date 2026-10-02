#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
fast_download.py —— 多线程“小块 + 断点续传”下载器（专治官 CDN 会掐断长连接）

为什么不用大分片：
    官 CDN（iotdown-jd.mayitek.com）单条连接下十几 MB 就被掐断，
    大分片下载永远“提前结束”，每次重试又从头开始 → 永远下不完。
    所以改成 1MB 小块：块小到一定能在一次连接里下完，断了只重来 1MB。

特点：
  * 1MB 块 + Range 请求，块内断了还能从块内偏移继续
  * 进度记在 <文件>.blocks.json，随时 Ctrl+C / 重跑都能接着下
  * 32 线程并发（实测合计 ~4.5MB/s，比单线程 0.5MB/s 快 9 倍）
  * 下完自动校验 md5

用法:
  python fast_download.py --url http://.../xxx.img --out ..\firmware\xxx.img --md5 <官方md5>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request

import _console  # noqa: F401

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
BLOCK = 1024 * 1024


def make_req(url: str, start: int, end: int):
    r = urllib.request.Request(url)
    r.add_header("User-Agent", UA)
    r.add_header("Accept", "*/*")
    r.add_header("Accept-Encoding", "identity")
    r.add_header("Range", f"bytes={start}-{end}")
    return r


def head_size(url: str) -> tuple[int, bool]:
    r = urllib.request.Request(url)
    r.add_header("User-Agent", UA)
    with urllib.request.urlopen(r, timeout=30) as resp:
        size = int(resp.headers.get("Content-Length") or 0)
        ranges = (resp.headers.get("Accept-Ranges") or "").lower() == "bytes"
    return size, ranges


class Counter:
    def __init__(self, total: int, done_bytes: int):
        self.total = total
        self.done = done_bytes
        self.lock = threading.Lock()
        self.t0 = time.time()
        self.last = 0.0
        self.fails = 0

    def add(self, n: int):
        with self.lock:
            self.done += n
            now = time.time()
            if now - self.last >= 5:
                self.last = now
                el = now - self.t0
                rate = self.done / el / 1048576 if el > 0 else 0
                pct = self.done / self.total * 100 if self.total else 0
                left = (self.total - self.done) / (self.done / el) / 60 if self.done and el > 0 else 0
                print(
                    f"    {pct:5.1f}%  {self.done/1048576:7.1f}/{self.total/1048576:.1f} MiB  "
                    f"{rate:4.2f} MB/s  ETA {left:4.1f} min  失败重试={self.fails}",
                    flush=True,
                )

    def fail(self):
        with self.lock:
            self.fails += 1


def fetch_block(url: str, dest: str, idx: int, size: int, cnt: Counter, retries: int = 4) -> bool:
    start = idx * BLOCK
    end = min(start + BLOCK - 1, size - 1)
    got = start
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(make_req(url, got, end), timeout=45) as r, open(dest, "r+b") as f:
                f.seek(got)
                while got <= end:
                    buf = r.read(min(256 * 1024, end - got + 1))
                    if not buf:
                        break
                    f.write(buf)
                    got += len(buf)
                    cnt.add(len(buf))
                if got > end:
                    return True
            cnt.fail()
        except Exception:  # noqa: BLE001
            cnt.fail()
            time.sleep(0.5 + attempt)
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--threads", type=int, default=32)
    ap.add_argument("--md5", default=None)
    ap.add_argument("--retry-rounds", type=int, default=6)
    args = ap.parse_args()

    out = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    state_path = out + ".blocks.json"

    size, ranges = head_size(args.url)
    print(f"[*] 远端 {size:,} 字节  Range={ranges}")
    if not ranges:
        print("[!] 服务器不支持 Range，无法分块下载")
        return 1

    nblocks = (size + BLOCK - 1) // BLOCK
    done: set[int] = set()
    if os.path.isfile(state_path):
        try:
            st = json.load(open(state_path, encoding="utf-8"))
            if st.get("size") == size:
                done = set(st.get("blocks", []))
                print(f"[*] 续传：已有 {len(done)}/{nblocks} 块")
        except Exception:  # noqa: BLE001
            done = set()

    if not os.path.isfile(out) or os.path.getsize(out) != size:
        with open(out, "wb") as f:
            f.truncate(size)

    done_bytes = sum(min(BLOCK, size - i * BLOCK) for i in done)
    cnt = Counter(size, done_bytes)

    for rnd in range(args.retry_rounds):
        todo = [i for i in range(nblocks) if i not in done]
        if not todo:
            break
        print(f"[*] 第 {rnd+1} 轮：待下 {len(todo)}/{nblocks} 块，并发 {args.threads}")
        q = list(todo)
        qlock = threading.Lock()
        dlock = threading.Lock()
        newdone = 0

        def worker():
            nonlocal newdone
            while True:
                with qlock:
                    if not q:
                        return
                    idx = q.pop(0)
                if fetch_block(args.url, out, idx, size, cnt):
                    with dlock:
                        done.add(idx)
                        newdone += 1
                        if newdone % 20 == 0:
                            json.dump({"size": size, "blocks": sorted(done)}, open(state_path, "w", encoding="utf-8"))

        ts = [threading.Thread(target=worker, daemon=True) for _ in range(max(1, args.threads))]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        json.dump({"size": size, "blocks": sorted(done)}, open(state_path, "w", encoding="utf-8"))
        print(f"[*] 本轮结束：累计 {len(done)}/{nblocks} 块")

    missing = [i for i in range(nblocks) if i not in done]
    if missing:
        print(f"[!] 还有 {len(missing)} 块没下下来（前几个：{missing[:10]}）。重跑本命令继续补。")
        return 2

    print("[*] 全部块已下载，校验大小与 md5 …")
    if os.path.getsize(out) != size:
        print("[!] 大小不符")
        return 3
    if args.md5:
        h = hashlib.md5()
        with open(out, "rb") as f:
            for b in iter(lambda: f.read(8 * 1024 * 1024), b""):
                h.update(b)
        ok = h.hexdigest().lower() == args.md5.lower()
        print(f"[*] md5 {h.hexdigest()} {'✔ 与官方一致' if ok else '✗ 不一致（可能被截断/篡改）'}")
        if not ok:
            return 4
    os.remove(state_path)
    print("[+] 完成:", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
