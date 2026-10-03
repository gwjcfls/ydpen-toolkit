#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""elf_strings.py —— 从 ELF/SO 里抽字符串并做主题聚类（用于逆向 APP_UPD 这类模块）

用法:
    python elf_strings.py <so文件> --near APP_UPD --window 40
    python elf_strings.py <so文件> --grep 'pms|expire|updateinfo'
    python elf_strings.py <so文件> --tags pms update expired store version
"""
import argparse
import re
import sys


def strings(path, minlen=4):
    data = open(path, "rb").read()
    out = []
    i = 0
    n = len(data)
    cur = bytearray()
    start = 0
    for i in range(n):
        b = data[i]
        if 0x20 <= b < 0x7F or b in (0x09,):
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
    ap.add_argument("--minlen", type=int, default=4)
    ap.add_argument("--near", default=None, help="以某关键字为中心，显示附近字符串")
    ap.add_argument("--window", type=int, default=30, help="near 模式前后各看多少条")
    ap.add_argument("--grep", default=None)
    ap.add_argument("--tags", nargs="*", default=None, help="按主题过滤并分组打印")
    ap.add_argument("--max", type=int, default=200)
    a = ap.parse_args()

    ss = strings(a.path, a.minlen)
    print("== %s：%d 条字符串 ==" % (a.path, len(ss)))

    if a.near:
        idxs = [i for i, (_, s) in enumerate(ss) if a.near in s]
        print("含 %r 的字符串 %d 条\n" % (a.near, len(idxs)))
        seen = set()
        for i in idxs[:a.max]:
            for j in range(max(0, i - a.window), min(len(ss), i + a.window + 1)):
                if j in seen:
                    continue
                seen.add(j)
                s = ss[j][1]
                if len(s) > 150:
                    s = s[:150] + "…"
                mark = "★" if j == i else " "
                print("%s %8d  %s" % (mark, ss[j][0], s))
            print("   " + "-" * 60)
        return 0

    if a.grep:
        pat = re.compile(a.grep, re.I)
        hits = [(o, s) for o, s in ss if pat.search(s)]
        print("匹配 %r：%d 条\n" % (a.grep, len(hits)))
        for o, s in hits[:a.max]:
            print("%8d  %s" % (o, s[:170]))
        return 0

    if a.tags:
        for t in a.tags:
            pat = re.compile(t, re.I)
            hits = [(o, s) for o, s in ss if pat.search(s)]
            print("\n########## 主题 %r：%d 条 ##########" % (t, len(hits)))
            for o, s in hits[:a.max]:
                print("%8d  %s" % (o, s[:170]))
        return 0

    for o, s in ss[:a.max]:
        print("%8d  %s" % (o, s))
    return 0


if __name__ == "__main__":
    sys.exit(main())
