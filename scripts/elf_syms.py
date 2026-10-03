#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""elf_syms.py —— 解析 ELF 的符号表（.symtab/.dynsym），按关键字过滤

libfalcon.so 没被 strip，符号名能直接看出模块结构（命名空间/类/方法），
比啃反汇编快得多。

用法:
    python elf_syms.py <so> --grep 'pm|upd'
    python elf_syms.py <so> --grep 'PackageManager'
    python elf_syms.py <so> --namespaces
"""
import argparse
import re
import struct
import sys


def parse_elf_syms(path):
    data = open(path, "rb").read()
    if data[:4] != b"\x7fELF":
        raise SystemExit("[!] 不是 ELF")
    is64 = data[4] == 2
    if not is64:
        raise SystemExit("[!] 只支持 64 位 ELF")
    e_shoff = struct.unpack_from("<Q", data, 0x28)[0]
    e_shentsize = struct.unpack_from("<H", data, 0x3A)[0]
    e_shnum = struct.unpack_from("<H", data, 0x3C)[0]
    e_shstrndx = struct.unpack_from("<H", data, 0x3E)[0]

    secs = []
    for i in range(e_shnum):
        off = e_shoff + i * e_shentsize
        name, typ, flags, addr, offset, size, link, info, align, entsize = \
            struct.unpack_from("<IIQQQQIIQQ", data, off)
        secs.append(dict(name=name, type=typ, offset=offset, size=size,
                         link=link, entsize=entsize, addr=addr))

    shstr = secs[e_shstrndx]
    shstrtab = data[shstr["offset"]:shstr["offset"] + shstr["size"]]

    def cstr(tab, off):
        end = tab.find(b"\x00", off)
        return tab[off:end].decode("utf-8", "replace")

    syms = []
    for s in secs:
        if s["type"] not in (2, 11):      # SYMTAB=2, DYNSYM=11
            continue
        strtab = data[secs[s["link"]]["offset"]:secs[s["link"]]["offset"] + secs[s["link"]]["size"]]
        ent = s["entsize"] or 24
        n = s["size"] // ent
        for i in range(n):
            off = s["offset"] + i * ent
            st_name, st_info, st_other, st_shndx, st_value, st_size = \
                struct.unpack_from("<IBBHQQ", data, off)
            if st_name == 0:
                continue
            nm = cstr(strtab, st_name)
            if nm:
                syms.append((nm, st_value, st_size, s["type"]))
    return syms


def demangle(nm):
    """把 Itanium ABI 名字粗略还原成可读形式（够看结构）"""
    if not nm.startswith("_Z"):
        return nm
    body = nm[2:]
    # 去掉长度前缀的常见模式
    def repl(m):
        ln = int(m.group(1))
        return m.group(2)[:ln]
    try:
        return re.sub(r"(\d+)([A-Za-z_][A-Za-z0-9_]*)", repl, body)
    except Exception:
        return nm


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--grep", default=None)
    ap.add_argument("--namespaces", action="store_true", help="按顶层命名空间统计")
    ap.add_argument("--max", type=int, default=300)
    ap.add_argument("--raw", action="store_true", help="打印原始 mangled 名（含完整方法名，便于搜索）")
    a = ap.parse_args()

    syms = parse_elf_syms(a.path)
    print("== %s：%d 个符号 ==" % (a.path, len(syms)))

    if a.namespaces:
        cnt = {}
        for nm, _, _, _ in syms:
            d = demangle(nm)
            ns = d.split("::")[0] if "::" in d else (d[:12] if d.startswith("_Z") else "(C)")
            cnt[ns] = cnt.get(ns, 0) + 1
        for k, v in sorted(cnt.items(), key=lambda x: -x[1])[:40]:
            print("  %6d  %s" % (v, k))
        return 0

    if a.grep:
        pat = re.compile(a.grep, re.I)
        hits = [(nm, v, sz) for nm, v, sz, _ in syms if pat.search(demangle(nm)) or pat.search(nm)]
        print("匹配 %r：%d 个\n" % (a.grep, len(hits)))
        seen = set()
        for nm, v, sz in hits:
            key = nm if a.raw else demangle(nm)
            if key in seen:
                continue
            seen.add(key)
            if a.raw:
                print("  %-110s size=%-6d 0x%x" % (nm[:110], sz, v))
            else:
                print("  %-90s size=%-6d 0x%x" % (key[:90], sz, v))
            if len(seen) >= a.max:
                break
        return 0

    for nm, v, sz, _ in syms[:a.max]:
        print("  %-90s size=%-6d" % (demangle(nm)[:90], sz))
    return 0


if __name__ == "__main__":
    sys.exit(main())
