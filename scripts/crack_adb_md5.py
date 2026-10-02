#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
crack_adb_md5.py —— 试着把词典笔 adbd_auth.sh 里的 md5 反查成明文

脚本逻辑是 `echo $PASSWD | md5sum`，所以真实哈希 = md5(密码 + "\n")。
（也顺带试一下不带换行的，以防固件写法不同。）

用法:
  python crack_adb_md5.py --hash 302af1d80be1106586775350cc0a2c92 --wordlist rockyou.txt
  python crack_adb_md5.py --hash ... --wordlist list.txt --mangle
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import os
import sys
import time

import _console  # noqa: F401

SUFFIXES = ["", "1", "12", "123", "1234", "12345", "123456", "2023", "2024", "2025", "@123", "!", "!!"]


def variants(word: str, mangle: bool):
    yield word
    yield word + "\n"
    if not mangle:
        return
    base = [word, word.capitalize(), word.upper(), word.lower()]
    for b in base:
        for s in SUFFIXES:
            yield b + s
            yield b + s + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hash", required=True, dest="target")
    ap.add_argument("--wordlist", required=True)
    ap.add_argument("--mangle", action="store_true")
    args = ap.parse_args()
    target = args.target.strip().lower()

    n = 0
    t0 = time.time()
    with open(args.wordlist, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            w = line.rstrip("\r\n")
            if not w:
                continue
            for cand in variants(w, args.mangle):
                n += 1
                if hashlib.md5(cand.encode("utf-8", "replace")).hexdigest() == target:
                    pw = cand[:-1] if cand.endswith("\n") else cand
                    print(f"\n[+++] 命中！密码 = {pw!r}   (试了 {n:,} 个，{time.time()-t0:.0f}s)")
                    return 0
            if n % 2000000 < len(SUFFIXES) + 2:
                el = max(time.time() - t0, 1e-6)
                print(f"    … 已试 {n:,} 个，{n/el/1000:.0f}k/s", flush=True)
    print(f"\n[-] 没命中（试了 {n:,} 个，{time.time()-t0:.0f}s）")
    return 1


if __name__ == "__main__":
    sys.exit(main())
