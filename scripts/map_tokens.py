#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
map_tokens.py —— 把堆快照里的 34 个 download token 与其邻近字符串对应起来，找出缓存里没有的包

用法: python tools\map_tokens.py out\pstore\httpdump.heapsnapshot [out\pstore\.catalog_cache_v1]
"""

from __future__ import annotations

import json
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    snap = sys.argv[1]
    d = open(snap, "rb").read()

    cached = {}
    if len(sys.argv) > 2 and os.path.isfile(sys.argv[2]):
        for p in json.load(open(sys.argv[2], encoding="utf-8")).get("software", []):
            m = re.search(r"/api/device/download/([a-z0-9]+)", p.get("download_url", "") or "")
            if m:
                cached[m.group(1)] = p

    toks = []
    for m in re.finditer(rb"/api/device/download/([a-z0-9]{10,20})", d):
        t = m.group(1).decode()
        if t not in toks:
            toks.append(t)

    print(f"快照里 {len(toks)} 个 token，缓存已知 {len(cached)} 个")
    print("=" * 100)

    # 打印每个 token 周边 400 字节内的可读串（取前 6 个较长的）
    for t in toks:
        off = d.find(b"/api/device/download/" + t.encode())
        seg = d[max(0, off - 500):off + 500]
        strs = [s.decode("utf-8", "replace") for s in re.findall(rb"[ -~]{4,60}", seg)]
        interesting = [s for s in strs
                       if not re.match(r"^[\x20-\x7e]*$", s) or True]
        # 过滤掉纯技术串
        keep = []
        for s in interesting:
            if re.search(r"^(\{|\[|\"|\d+$|https?://)", s):
                continue
            if re.search(r"(app|App|version|Version|name|url|icon|price|size|category|dev)", s):
                keep.append(s)
        tag = cached.get(t, {}).get("app_name", "★未知★")
        print(f"\n[{t}]  {tag}")
        for s in keep[:8]:
            print(f"      {s[:100]}")

    extra = [t for t in toks if t not in cached]
    print("\n" + "=" * 100)
    print(f"缓存里没有的 token（{len(extra)} 个）:")
    for t in extra:
        print(f"   https://store.posc.net/api/device/download/{t}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
