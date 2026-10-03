#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""elf_range.py —— 导出 ELF 里某个文件偏移区间的所有字符串（看一个模块的完整字符串表）

用法: python elf_range.py <so> <start> <end> [--minlen 3]
"""
import argparse
import sys


def strings(path, minlen=3):
    data = open(path, "rb").read()
    out = []
    cur = bytearray()
    start = 0
    for i, b in enumerate(data):
        if 0x20 <= b < 0x7F or b == 0x09:
            if not cur:
                start = i
            cur.append(b)
        else:
            if len(cur) >= minlen:
                out.append((start, cur.decode("ascii", "replace")))
            cur = bytearray()
    if len(cur) >= minlen:
        out.append((start, cur.decode("ascii", "replace")))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("start", type=lambda x: int(x, 0))
    ap.add_argument("end", type=lambda x: int(x, 0))
    ap.add_argument("--minlen", type=int, default=3)
    a = ap.parse_args()
    ss = strings(a.path, a.minlen)
    sel = [(o, s) for o, s in ss if a.start <= o <= a.end]
    print("区间 [0x%x, 0x%x] 内 %d 条字符串：\n" % (a.start, a.end, len(sel)))
    for o, s in sel:
        print("%8d  %s" % (o, s[:170]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
