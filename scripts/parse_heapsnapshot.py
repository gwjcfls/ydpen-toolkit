#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
parse_heapsnapshot.py —— 解析框架导出的 V8 格式堆快照，还原出 app 内存里"看到"的软件包对象

用法:
    python tools\parse_heapsnapshot.py out\pstore\httpdump.heapsnapshot [--filter app_name] [--json out.json]

原理:
    快照是 Chrome DevTools 的 .heapsnapshot 格式：
      snapshot.meta.node_fields 描述每个 node 的字段
      nodes 是扁平数组；edges 也是扁平数组（每个 node 的 edge 紧接其后，按 edge_count 个数）
      edge.name_or_index 对 property 类型来说就是 strings 数组的下标
    于是可以把每个对象节点的属性完整读出来，找到带 app_name / download_url 的对象即为"软件包"。
"""

from __future__ import annotations

import json
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FIELDS = ("app_name", "appid", "latest_version", "category", "developer",
          "download_url", "icon_url", "size", "billing_type", "price", "update_description")


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    path = sys.argv[1]
    out_json = None
    if "--json" in sys.argv:
        out_json = sys.argv[sys.argv.index("--json") + 1]

    snap = json.load(open(path, encoding="utf-8", errors="replace"))
    meta = snap["snapshot"]["meta"]
    strings = snap["strings"]
    nf, ef = meta["node_fields"], meta["edge_fields"]
    ntypes, etypes = meta["node_types"][0], meta["edge_types"][0]
    nodes, edges = snap["nodes"], snap["edges"]
    ncount = snap["snapshot"]["node_count"]
    NS, ES = len(nf), len(ef)
    I_TYPE, I_NAME, I_EDGES = nf.index("type"), nf.index("name"), nf.index("edge_count")
    print(f"节点 {ncount} 个，边 {len(edges)//ES} 条，字符串 {len(strings)} 个")

    # 每个 node 的 edge 起始偏移
    starts = []
    off = 0
    for i in range(ncount):
        starts.append(off)
        off += nodes[i * NS + I_EDGES] * ES

    def node_name(idx: int):
        t = ntypes[nodes[idx * NS + I_TYPE]]
        raw = nodes[idx * NS + I_NAME]
        if t == "string":
            return strings[raw] if 0 <= raw < len(strings) else None
        if t == "number":
            return raw
        return strings[raw] if 0 <= raw < len(strings) else None

    def props(idx: int):
        ec = nodes[idx * NS + I_EDGES]
        out = {}
        base = starts[idx]
        for k in range(ec):
            e = base + k * ES
            et = etypes[edges[e]]
            nm = edges[e + 1]
            to = edges[e + 2] // NS
            if et != "property":
                continue
            key = strings[nm] if 0 <= nm < len(strings) else str(nm)
            out.setdefault(key, []).append(to)
        return out

    # 找出所有带 app_name 属性的对象
    found = []
    for i in range(ncount):
        if ntypes[nodes[i * NS + I_TYPE]] != "object":
            continue
        p = props(i)
        if "app_name" in p or ("appid" in p and "download_url" in p):
            rec = {}
            for f in FIELDS:
                if f in p:
                    rec[f] = node_name(p[f][0])
            if rec.get("app_name") or rec.get("appid"):
                found.append(rec)

    # 去重（同一个包可能有多份对象）
    seen, uniq = set(), []
    for r in found:
        k = (r.get("appid"), r.get("latest_version"), r.get("download_url"))
        if k in seen:
            continue
        seen.add(k)
        uniq.append(r)

    print(f"\n还原出软件包对象 {len(uniq)} 个\n")
    print(f"{'名称':<26}{'appid':<20}{'版本':<10}{'分类':<8}{'开发者':<18}download_url")
    print("-" * 130)
    for r in sorted(uniq, key=lambda x: str(x.get("app_name"))):
        print(f"{str(r.get('app_name'))[:24]:<26}{str(r.get('appid')):<20}{str(r.get('latest_version')):<10}"
              f"{str(r.get('category') or '-')[:6]:<8}{str(r.get('developer'))[:16]:<18}{r.get('download_url')}")

    appids = {r.get("appid") for r in uniq if r.get("appid")}
    print(f"\n唯一 appid: {len(appids)}")

    if out_json:
        json.dump(uniq, open(out_json, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"[+] 已存 {out_json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
