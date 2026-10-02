#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
jsfm_inspect.py —— 解析有道 miniapp 的 .js.bin（"jsfm"）容器

实测出的格式（用 66B/219B/2KB/78KB 多个样本交叉验证）：

    [0x01]                    格式版本
    varint  N                 字符串表条数
    N × (varint len|wide + bytes)
        长度字段是 (字节长 << 1) | wide；wide=1 表示内容是 UTF-16LE（中文等）
    [QuickJS 字节码]           JS_WriteObject(ctx, ..., JS_WRITE_OBJ_BYTECODE) 的原始输出

用法:
    python tools\jsfm_inspect.py <file.js.bin> [--strings] [--head N]
    python tools\jsfm_inspect.py --amr <app.amr>        # 批量看包里所有 .js.bin
"""

from __future__ import annotations

import argparse
import sys
import zipfile


def read_varint(d: bytes, i: int) -> tuple[int, int]:
    """无符号 LEB128。"""
    v = 0
    shift = 0
    while True:
        if i >= len(d):
            raise ValueError("varint 越界")
        b = d[i]
        i += 1
        v |= (b & 0x7F) << shift
        if not (b & 0x80):
            return v, i
        shift += 7


def parse(d: bytes) -> dict:
    ver = d[0]
    n, i = read_varint(d, 1)
    strs: list[str] = []
    for _ in range(n):
        l2, i = read_varint(d, i)
        wide = l2 & 1
        units = l2 >> 1
        nbytes = units * 2 if wide else units
        raw = d[i : i + nbytes]
        i += nbytes
        if wide:
            strs.append(raw.decode("utf-16-le", "replace"))
        else:
            try:
                strs.append(raw.decode("utf-8"))
            except UnicodeDecodeError:
                strs.append(raw.decode("latin-1"))
    return {"version": ver, "count": n, "strings": strs, "payload": d[i:], "payload_off": i}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("file", nargs="?")
    ap.add_argument("--amr")
    ap.add_argument("--strings", action="store_true", help="打印全部字符串")
    ap.add_argument("--head", type=int, default=24, help="打印字节码头部字节数")
    args = ap.parse_args()

    items: list[tuple[str, bytes]] = []
    if args.amr:
        z = zipfile.ZipFile(args.amr)
        for n in z.namelist():
            if n.endswith(".js.bin"):
                items.append((n, z.read(n)))
    elif args.file:
        items.append((args.file, open(args.file, "rb").read()))
    else:
        ap.error("需要 file 或 --amr")

    for name, d in items:
        try:
            r = parse(d)
        except Exception as e:  # noqa: BLE001
            print(f"=== {name}: 解析失败 {e}")
            continue
        print(f"=== {name}  {len(d)} 字节  ver={r['version']} 字符串={r['count']} 字节码={len(r['payload'])}B @0x{r['payload_off']:x}")
        if args.strings:
            for s in r["strings"]:
                print("    ", s)
        else:
            head = r["strings"][:10]
            print("    前几个字符串:", head, "…" if len(r["strings"]) > 10 else "")
        print("    字节码头:", r["payload"][: args.head].hex(" "))
    return 0


if __name__ == "__main__":
    sys.exit(main())
