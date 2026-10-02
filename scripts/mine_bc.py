#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
mine_bc.py —— 从 QuickJS 字节码里挖字符串，当"API 文档"用

为什么需要：框架的 JS 核心（Vue + Falcon UI + 组件注册）是字节码
/etc/miniapp/resources/framework/js-framework.min.bin，源码不可得；
但字节码里的**字符串常量**保留了组件名、API 名、样式属性名，足够反推用法。

用法:
    python tools\mine_bc.py <xxx.js.bin|xxx.bin> [--out strings.txt] [--grep 关键字]
"""

from __future__ import annotations

import argparse
import os
import re
import sys

PRINT = re.compile(rb"[\x20-\x7e]{3,}")
# 宽字符（UTF-16LE）也常见于 QuickJS 的字符串常量表
PRINT16 = re.compile((r"(?:[\x20-\x7e]\x00){3,}").encode())


def strings_of(path: str) -> list[str]:
    d = open(path, "rb").read()
    out: list[str] = []
    for m in PRINT.finditer(d):
        s = m.group(0).decode("latin-1")
        if len(s) >= 3:
            out.append(s)
    for m in PRINT16.finditer(d):
        s = m.group(0).decode("utf-16-le", "replace")
        if len(s) >= 3:
            out.append(s)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("bc")
    ap.add_argument("--out", default=None)
    ap.add_argument("--grep", default=None)
    ap.add_argument("--dump", type=int, default=0, help="打印前 N 条")
    args = ap.parse_args()

    ss = strings_of(args.bc)
    uniq = sorted(set(ss))
    print(f"[*] {os.path.basename(args.bc)}: 原始 {len(ss)} 条, 去重 {len(uniq)} 条")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write("\n".join(uniq))
        print(f"[*] 已写出 {args.out}")

    if args.grep:
        pat = re.compile(args.grep, re.I)
        hits = [s for s in uniq if pat.search(s)]
        print(f"[*] 匹配 /{args.grep}/ 共 {len(hits)} 条:")
        for h in hits[:400]:
            print("   ", h)

    if args.dump:
        print(f"[*] 前 {args.dump} 条:")
        for s in uniq[: args.dump]:
            print("   ", s)
    return 0


if __name__ == "__main__":
    sys.exit(main())
