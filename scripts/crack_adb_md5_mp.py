#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
crack_adb_md5_mp.py —— 多进程 + 变形规则，暴力破解 adbd_auth.sh 里的 md5

真实校验是 `echo $PASSWD | md5sum`，所以目标 = md5(密码 + "\n")（也顺带试不带换行的）。

策略（按性价比排序，边跑边打日志）：
  1. youdao 主题词 × 分隔符 × 年份/常见后缀 × 大小写（几千个，秒出）
  2. rockyou 1434 万词 × 常见变形规则（大小写、年份、数字后缀、@/!、简单 leet）
  3. 纯数字 9~10 位（8 位及以下之前已排除）
  4. 常见前缀数字模式 a123456 / abc123456

用法:
  python crack_adb_md5_mp.py --hash 302af1d80be1106586775350cc0a2c92 --rockyou out\wl\rockyou.txt --seconds 300
"""

from __future__ import annotations

import argparse
import hashlib
import multiprocessing as mp
import os
import sys
import time

import _console  # noqa: F401

YEAR_SUFFIX = ["", "1", "12", "123", "1234", "12345", "123456", "!", "@", "#", "@123", "123!", "@2023", "@2024", "@2025", "2023", "2024", "2025", "2026", "2020", "2019", "2021", "2022"]
THEME = [
    "youdao", "Youdao", "YOUDAO", "yd", "ydp", "ydpx6", "ydpx62", "x6", "x6pro", "X6Pro", "dictpen", "DictPen",
    "dictionarypen", "netease", "Netease", "youdaodict", "ydict", "ydictpen", "pen", "Pen", "youdaopen",
    "YoudaoDictionaryPen", "youdaoai", "aiyoudao", "有道", "词典笔", "有道词典笔", "youdaobiji", "ydb",
]
SEP = ["", "@", "_", "-", ".", "#", "!", "123"]


def hit(pw: str, target: str) -> bool:
    for cand in (pw, pw + "\n"):
        if hashlib.md5(cand.encode("utf-8", "replace")).hexdigest() == target:
            return True
    return False


def leet(w: str) -> list[str]:
    out = [w]
    table = [("a", "@"), ("o", "0"), ("i", "1"), ("e", "3"), ("s", "$")]
    for a, b in table:
        out += [x.replace(a, b) for x in list(out)]
        out += [x.replace(a.upper(), b) for x in list(out)]
    return out[:64]


def rule_variants(word: str):
    bases = {word, word.capitalize(), word.upper(), word.lower()}
    for b in list(bases):
        bases.update(leet(b))
    for b in bases:
        for s in YEAR_SUFFIX:
            yield b + s
            yield s + b if s.isalpha() else b + s  # 少量前缀式


def phase1(target: str) -> str | None:
    t0 = time.time()
    n = 0
    for w in THEME:
        for sep in SEP:
            for suf in YEAR_SUFFIX:
                for cand in {w + sep + suf, (w + sep + suf).capitalize(), (w + sep + suf).upper()}:
                    n += 1
                    if hit(cand, target):
                        return cand
    print(f"  [1] youdao 主题词试了 {n:,} 个，没中（{time.time()-t0:.1f}s）", flush=True)
    return None


def worker(proc_id: int, words: list[str], target: str, queue):
    n = 0
    t0 = time.time()
    for w in words:
        w = w.rstrip("\r\n")
        if not w:
            continue
        for cand in rule_variants(w):
            n += 1
            if hit(cand, target):
                queue.put(cand)
                return
        if time.time() - t0 > 30:
            queue.put(("progress", proc_id, n))
            t0 = time.time()
            n = 0
    queue.put(("done", proc_id))


def phase2(rockyou: str, target: str, seconds: float, procs: int) -> str | None:
    if not os.path.isfile(rockyou):
        print("  [2] 没有 rockyou 词表，跳过", flush=True)
        return None
    with open(rockyou, "r", encoding="utf-8", errors="replace") as f:
        words = [l.rstrip("\r\n") for l in f if l.strip()]
    print(f"  [2] rockyou {len(words):,} 词 × 变形规则，{procs} 进程，上限 {seconds:.0f}s", flush=True)
    chunks = [words[i::procs] for i in range(procs)]
    q: mp.Queue = mp.Queue()
    ps = [mp.Process(target=worker, args=(i, chunks[i], target, q), daemon=True) for i in range(procs)]
    t0 = time.time()
    total = 0
    for p in ps:
        p.start()
    try:
        remaining = seconds
        while time.time() - t0 < remaining:
            try:
                item = q.get(timeout=1.0)
            except Exception:  # noqa: BLE001
                continue
            if isinstance(item, tuple):
                if item[0] == "progress":
                    total += item[2]
                    print(f"      … 速度 {total/(time.time()-t0)/1000:.0f}k/s  已试 {total:,}", flush=True)
                    total = 0
                elif item[0] == "done":
                    pass
            else:
                for p in ps:
                    p.terminate()
                return item
    finally:
        for p in ps:
            p.terminate()
    print(f"  [2] rockyou 变形跑满 {seconds:.0f}s 没中", flush=True)
    return None


def phase3(target: str, lo: int, hi: int, procs: int, queue) -> str | None:
    def w(pid, a, b):
        for n in range(a, b):
            s = str(n)
            if hit(s, target):
                queue.put(s)
                return
        queue.put(("done", pid))

    step = (hi - lo) // procs
    ps = []
    for i in range(procs):
        a = lo + i * step
        b = hi if i == procs - 1 else a + step
        p = mp.Process(target=w, args=(i, a, b), daemon=True)
        p.start()
        ps.append(p)
    t0 = time.time()
    res = None
    while any(p.is_alive() for p in ps):
        try:
            item = queue.get(timeout=1)
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(item, tuple):
            res = item
            break
        if time.time() - t0 > 240:
            break
    for p in ps:
        p.terminate()
    print(f"  [3] 数字 {lo}..{hi} 跑完 {time.time()-t0:.0f}s" + (f" 命中 {res}" if res else " 没中"), flush=True)
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hash", required=True, dest="target")
    ap.add_argument("--rockyou", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "out", "wl", "rockyou.txt"))
    ap.add_argument("--seconds", type=float, default=300)
    ap.add_argument("--procs", type=int, default=max(2, (os.cpu_count() or 4)))
    ap.add_argument("--skip-numbers", action="store_true")
    args = ap.parse_args()
    target = args.target.strip().lower()
    print(f"[*] 目标 md5 = {target}   进程数 = {args.procs}")

    r = phase1(target)
    if r:
        print(f"\n[+++] 命中！密码 = {r!r}")
        return 0
    r = phase2(args.rockyou, target, args.seconds, args.procs)
    if r:
        print(f"\n[+++] 命中！密码 = {r!r}")
        return 0
    if not args.skip_numbers:
        q: mp.Queue = mp.Queue()
        r = phase3(target, 100_000_000, 10_000_000_000, args.procs, q)
        if r:
            print(f"\n[+++] 命中！密码 = {r!r}")
            return 0
    print("\n[-] 全部策略都没命中。基本可以认定是随机字符串 → 走改固件路线。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
