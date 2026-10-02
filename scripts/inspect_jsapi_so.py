#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
inspect_jsapi_so.py —— 看一个 jsapi 原生插件 .so 的"能力"：
  * ELF64 动态符号表里导出的东西（custom_init_jsapis / jsapi_check_unload / 方法名）
  * 关键字命中（pty / forkpty / exec / vterm / ansi / ssh / socket / pthread ...）
  * 内嵌的字符串常量里像"模块名/导出名/JSON 键"的东西
用途：摸清别人插件提供了哪些 JS 侧方法，照着抄 API 设计。

用法: python tools\inspect_jsapi_so.py <x.so> [--grep 关键字]
"""

from __future__ import annotations

import argparse
import re
import struct
import sys

KEYS = [
    "pty", "forkpty", "openpty", "posix_spawn", "fork", "execvp", "execve", "system",
    "popen", "pipe", "dup2", "ioctl", "TIOCSWINSZ", "setsid", "waitpid",
    "ansi", "escape", "vterm", "termios", "curses", "terminal",
    "ssh", "socket", "connect", "pthread_create", "libquickjs", "registerCModuleLoader",
    "custom_init_jsapis", "jsapi_check_unload", "on(", "emit", "server_started",
]


def elf64_dynsyms(path: str) -> tuple[list[str], list[str]]:
    """返回 (导出符号, 依赖库)。纯手写解析，不依赖 pyelftools。"""
    d = open(path, "rb").read()
    if d[:4] != b"\x7fELF" or d[4] != 2:
        return [], []
    e_shoff, = struct.unpack_from("<Q", d, 0x28)
    e_shentsize, e_shnum, e_shstrndx = struct.unpack_from("<HHH", d, 0x3A)

    secs = []
    for i in range(e_shnum):
        off = e_shoff + i * e_shentsize
        name, typ, flags, addr, offset, size, link, info, align, entsize = struct.unpack_from("<IIQQQQIIQQ", d, off)
        secs.append(dict(name=name, type=typ, offset=offset, size=size, link=link, entsize=entsize))

    if e_shstrndx >= len(secs):
        return [], []
    shstr = secs[e_shstrndx]
    strtab_blob = d[shstr["offset"]: shstr["offset"] + shstr["size"]]

    def secname(n: int) -> str:
        end = strtab_blob.find(b"\0", n)
        return strtab_blob[n:end].decode("latin-1")

    exports: list[str] = []
    needed: list[str] = []
    for s in secs:
        nm = secname(s["name"])
        if nm == ".dynstr":
            dynstr = d[s["offset"]: s["offset"] + s["size"]]
        if nm == ".dynamic":
            entsize = s["entsize"] or 16
            for off in range(s["offset"], s["offset"] + s["size"], entsize):
                tag, val = struct.unpack_from("<Qq", d, off)
                if tag == 0:
                    break
                if tag == 1:  # DT_NEEDED
                    end = dynstr.find(b"\0", val)
                    needed.append(dynstr[val:end].decode("latin-1"))
        if nm == ".dynsym":
            entsize = s["entsize"] or 24
            for off in range(s["offset"], s["offset"] + s["size"], entsize):
                nameoff, info, other, shndx, value, size = struct.unpack_from("<IBBHQQ", d, off)
                if nameoff == 0:
                    continue
                end = dynstr.find(b"\0", nameoff)
                name = dynstr[nameoff:end].decode("latin-1")
                bind = info >> 4
                typ = info & 0xF
                if shndx != 0 and bind in (1, 2) and typ in (1, 2):  # GLOBAL/WEAK, OBJECT/FUNC
                    exports.append(name)
    return sorted(set(exports)), needed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("so")
    ap.add_argument("--grep", default=None)
    ap.add_argument("--strings", type=int, default=0, help="打印前 N 条长字符串")
    args = ap.parse_args()

    exp, needed = elf64_dynsyms(args.so)
    print(f"=== {args.so} ===")
    print(f"NEEDED: {needed}")
    print(f"导出符号 {len(exp)} 个:")
    for e in exp:
        print("   ", e)

    d = open(args.so, "rb").read()
    ss = sorted({m.group(0).decode("latin-1") for m in re.finditer(rb"[\x20-\x7e]{4,120}", d)})
    print(f"\n字符串常量 {len(ss)} 条；关键字命中：")
    for k in KEYS:
        hits = [s for s in ss if k.lower() in s.lower()]
        if hits:
            print(f"  [{k}] {len(hits)} 条，例：")
            for h in hits[:4]:
                print("       ", h[:110])

    if args.grep:
        pat = re.compile(args.grep, re.I)
        print(f"\n匹配 /{args.grep}/:")
        for s in ss:
            if pat.search(s):
                print("   ", s[:140])

    if args.strings:
        print(f"\n前 {args.strings} 条字符串:")
        for s in ss[: args.strings]:
            print("   ", s[:140])
    return 0


if __name__ == "__main__":
    sys.exit(main())
