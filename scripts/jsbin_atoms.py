#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""jsbin_atoms.py —— 从 .js.bin（QuickJS 字节码容器）里抽出字符串原子表

容器格式（实测）：
    [0x01][varint 原子数][N × ( varint(len<<1|wide) + bytes )][QuickJS 字节码]

用法:
    python jsbin_atoms.py <file.js.bin>                 # 全部列出
    python jsbin_atoms.py <file.js.bin> --grep upd      # 只列匹配的（大小写不敏感）
    python jsbin_atoms.py <file.js.bin> --grep upd --context 3   # 带周边原子（看调用链）
"""
import argparse
import re
import sys


def read_varint(buf, i):
    v = 0
    shift = 0
    while True:
        b = buf[i]
        i += 1
        v |= (b & 0x7F) << shift
        if not (b & 0x80):
            break
        shift += 7
    return v, i


def parse_atoms(path):
    buf = open(path, "rb").read()
    if not buf or buf[0] != 0x01:
        raise SystemExit("[!] 不是预期的 .js.bin 容器（首字节应 0x01，实际 0x%02x）" % (buf[0] if buf else 0))
    n, i = read_varint(buf, 1)
    atoms = []
    for _ in range(n):
        v, i = read_varint(buf, i)
        wide = v & 1
        ln = v >> 1
        if wide:
            ln *= 2
        raw = buf[i:i + ln]
        i += ln
        if wide:
            try:
                atoms.append(raw.decode("utf-16-le", "replace"))
            except Exception:
                atoms.append(repr(raw))
        else:
            atoms.append(raw.decode("utf-8", "replace"))
    return atoms, i, len(buf)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--grep", default=None, help="只显示匹配的原子（忽略大小写）")
    ap.add_argument("--context", type=int, default=0, help="显示命中项前后各 N 个原子")
    ap.add_argument("--max", type=int, default=400, help="最多显示多少行")
    a = ap.parse_args()

    atoms, off, total = parse_atoms(a.path)
    print("文件 %s：%d 个原子，头 %d 字节，总 %d 字节" % (a.path, len(atoms), off, total))

    if not a.grep:
        for idx, s in enumerate(atoms[:a.max]):
            print("%5d  %s" % (idx, s))
        return 0

    pat = re.compile(a.grep, re.I)
    hits = [i for i, s in enumerate(atoms) if pat.search(s)]
    print("匹配 %r 的原子：%d 个\n" % (a.grep, len(hits)))
    shown = set()
    for h in hits[:a.max]:
        lo = max(0, h - a.context)
        hi = min(len(atoms), h + a.context + 1)
        for i in range(lo, hi):
            if i in shown:
                continue
            shown.add(i)
            mark = "★" if i == h else " "
            s = atoms[i]
            if len(s) > 160:
                s = s[:160] + "…"
            print("%s%5d  %s" % (mark, i, s))
        print("      ---")
    return 0


if __name__ == "__main__":
    sys.exit(main())
