#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
analyze_heapsnapshot.py —— 从框架的 QuickJS 堆快照里挖出 app 当前"看到"的全部软件包

用法: python tools\analyze_heapsnapshot.py out\pstore\httpdump.heapsnapshot [out\pstore\.catalog_cache_v1]
"""

from __future__ import annotations

import json
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    snap = sys.argv[1]
    d = open(snap, "rb").read()
    print(f"快照 {len(d)/1048576:.1f} MB : {snap}")

    toks = sorted({m.group(1).decode() for m in re.finditer(rb"/api/device/download/([a-z0-9]{10,20})", d)})
    print(f"\ndownload token: {len(toks)}")

    names = sorted({m.group(1).decode("utf-8", "replace")
                    for m in re.finditer(rb'"app_name":"([^"]{1,40})"', d)})
    print(f"app_name: {len(names)}")
    for n in names:
        print(f"   {n}")

    # 尝试把完整的 JSON 对象抠出来（app_name + appid + download_url 同一对象）
    pkgs = []
    for m in re.finditer(rb'\{"app_name".{0,3000}?\}', d):
        try:
            o = json.loads(m.group().decode("utf-8", "replace"))
        except Exception:  # noqa: BLE001
            continue
        if isinstance(o, dict) and o.get("appid"):
            pkgs.append(o)
    # 去重
    seen, uniq = set(), []
    for p in pkgs:
        if p["appid"] in seen:
            continue
        seen.add(p["appid"])
        uniq.append(p)
    print(f"\n从快照里抠出完整包信息: {len(uniq)} 个")
    if uniq:
        print(f"{'名称':<24}{'appid':<20}{'版本':<10}{'分类':<8}{'大小':>10}  download_url")
        for p in sorted(uniq, key=lambda x: x.get("app_name", "")):
            print(f"{str(p.get('app_name'))[:22]:<24}{p.get('appid'):<20}{str(p.get('latest_version')):<10}"
                  f"{str(p.get('category') or '-')[:6]:<8}{p.get('size', 0):>10}  {p.get('download_url')}")

    # 与缓存合并
    if len(sys.argv) > 2 and os.path.isfile(sys.argv[2]):
        cat = json.load(open(sys.argv[2], encoding="utf-8"))
        known = {p["appid"] for p in cat.get("software", [])}
        print(f"\n目录缓存里有 {len(known)} 个；快照里新增：")
        extra = [p for p in uniq if p["appid"] not in known]
        for p in extra:
            print(f"   + {p.get('app_name')}  {p.get('appid')}  v{p.get('latest_version')}  {p.get('download_url')}")
        if not extra:
            print("   (没有新增)")
        allids = sorted(known | {p["appid"] for p in uniq})
        print(f"\n合计唯一下载条目: {len(allids)}  (服务器 totalItems={cat.get('totalItems')})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
