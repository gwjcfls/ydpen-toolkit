#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
pstore_catalog.py —— 汇总 PStore（store.posc.net）上可安装的全部 amr 包

数据来源（两个互补）：
  1) /userdisk/pstore/.catalog_cache_v1 —— app 落盘的目录缓存（第一页 20 条，字段权威）
  2) QuickJS 堆快照（miniapp_cli dumpMemory）的 strings 数组 —— 含全部 34 条

用法:
    python tools\pstore_catalog.py out\pstore\.catalog_cache_v1 out\pstore\httpdump.heapsnapshot \
        --md out\pstore\PStore目录.md --json out\pstore\pstore_catalog.json
"""

from __future__ import annotations

import json
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HEX64 = re.compile(r"^[0-9a-f]{64}$")
APPID = re.compile(r"^8\d{15}$")
VER = re.compile(r"^\d+\.\d+\.\d+$")
TOKEN = re.compile(r"^/api/device/download/([a-z0-9]{10,20})$")


def from_cache(path):
    d = json.load(open(path, encoding="utf-8"))
    out = []
    for s in d.get("software", []):
        m = TOKEN.match(s.get("download_url") or "")
        out.append({
            "appid": s.get("appid"),
            "name": s.get("app_name"),
            "version": s.get("latest_version"),
            "developer": s.get("developer"),
            "category": s.get("category") or "",
            "size": s.get("size"),
            "icon": s.get("icon_url"),
            "token": m.group(1) if m else "",
            "checksum": s.get("checksum"),
            "desc": (s.get("software_description") or "").replace("\r\n", " / ")[:200],
            "billing": s.get("billing_type"),
            "src": "cache",
        })
    meta = {"sku": d.get("sku"), "total": d.get("totalItems"), "categories": d.get("categories"),
            "saved_at": d.get("saved_at")}
    return out, meta


def from_heap(path, known_ids):
    snap = json.load(open(path, encoding="utf-8", errors="replace"))
    S = snap["strings"]
    # 定位目录区域：第一个 token 的 strings 下标 ~ 最后一个 token + 少量尾部
    idxs = [i for i, s in enumerate(S) if TOKEN.match(s) or s.startswith("/api/device/download/")]
    if not idxs:
        return []
    lo, hi = min(idxs) - 200, max(idxs) + 3
    region = S[lo:hi]

    blocks, cur = [], []
    for s in region:
        cur.append(s)
        if s.startswith("/api/device/download/"):
            blocks.append(cur)
            cur = []
    out = []
    for b in blocks:
        rec = {"src": "heap"}
        token = ""
        for s in b:
            if s.startswith("/api/device/download/"):
                token = s.rsplit("/", 1)[-1]
        if not token:
            continue
        # appid / 名称 / 版本 / 开发者
        appid = next((s for s in b if APPID.match(s)), None)
        name = None
        for s in b:
            if APPID.match(s) or s.startswith("http") or s.startswith("/api/") or HEX64.match(s):
                continue
            if VER.match(s) or s.startswith("<prop>") or len(s) > 60:
                continue
            name = s
            break
        ver = next((s for s in b if VER.match(s)), "")
        hexes = [k for k, s in enumerate(b) if HEX64.match(s)]
        dev = ""
        if hexes:
            k = hexes[0] - 1
            if k >= 0:
                cand = b[k]
                if not cand.startswith(("http", "/api/")) and not APPID.match(cand) and not VER.match(cand):
                    dev = cand
        icon = next((s for s in b if s.startswith("http")), "")
        desc = max((s for s in b if len(s) > 25 and not s.startswith(("http", "/api/", "<prop>"))
                    and not HEX64.match(s)), key=len, default="")
        rec.update({"appid": appid, "name": name, "version": ver, "developer": dev,
                    "category": "", "size": None, "icon": icon, "token": token,
                    "checksum": next((s for s in b if HEX64.match(s)), ""), "desc": desc.replace("\n", " / ")[:200],
                    "billing": "free"})
        # 仅有"同名同 appid 的多个版本"这一种情况会被堆快照整体去重：
        # 此时该块既没有名字也没有 appid —— 才继承上一条，避免误填。
        if out and not rec.get("name") and not rec.get("appid"):
            prev = out[-1]
            for k in ("appid", "name", "developer"):
                rec[k] = prev.get(k)
        out.append(rec)

    # 只保留缓存里没有的；并丢掉没有名称也没有 appid 的碎片块
    extra = [r for r in out
             if (r.get("appid") not in known_ids) and r["token"] not in known_ids
             and (r.get("name") or r.get("appid"))]

    # 堆快照会去重相同字符串，个别 appid 会缺失 —— 用"名称→已知 appid"补全
    KNOWN_NAME2ID = {
        "元素周期表": "8001749644971193",
        "背单词": "8000000000765868",
    }
    for r in extra:
        if not r.get("appid") and r.get("name") in KNOWN_NAME2ID:
            r["appid"] = KNOWN_NAME2ID[r["name"]]
    return extra


def main() -> int:
    cache_path, heap_path = sys.argv[1], sys.argv[2]
    md_path = sys.argv[sys.argv.index("--md") + 1] if "--md" in sys.argv else None
    json_path = sys.argv[sys.argv.index("--json") + 1] if "--json" in sys.argv else None

    cached, meta = from_cache(cache_path)
    known_tokens = {c["token"] for c in cached}
    heap_extra = from_heap(heap_path, known_tokens)

    allpkgs = cached + heap_extra
    # 去重（按 token）
    seen, uniq = set(), []
    for p in allpkgs:
        if p["token"] in seen:
            continue
        seen.add(p["token"])
        uniq.append(p)

    print(f"SKU={meta['sku']}  服务器 totalItems={meta['total']}  缓存 {len(cached)} 条  堆里补充 {len(heap_extra)} 条  →  合计 {len(uniq)}")
    print("=" * 120)
    print(f"{'#':<4}{'名称':<24}{'appid':<19}{'版本':<9}{'开发者':<16}{'分类':<7}来源")
    print("-" * 120)
    for i, p in enumerate(sorted(uniq, key=lambda x: str(x.get("name"))), 1):
        print(f"{i:<4}{str(p.get('name'))[:22]:<24}{str(p.get('appid')):<19}{str(p.get('version')):<9}"
              f"{str(p.get('developer'))[:14]:<16}{str(p.get('category') or '-'):<7}{p.get('src')}")

    if json_path:
        json.dump({"meta": meta, "packages": uniq}, open(json_path, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        print(f"\n[+] JSON → {json_path}")

    if md_path:
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(f"# PStore 应用商店可安装的 amr 包（{meta['sku']}）\n\n")
            f.write(f"- 服务器：`https://store.posc.net`\n")
            f.write(f"- 设备 SKU：`{meta['sku']}`\n")
            f.write(f"- 服务器总量：**{meta['total']}** 个；本文列出 **{len(uniq)}** 个\n")
            f.write(f"- 分类：{' / '.join(meta.get('categories') or [])}\n\n")
            f.write("| # | 名称 | appid | 版本 | 开发者 | 分类 | 大小 | 说明 |\n|---|---|---|---|---|---|---|---|\n")
            for i, p in enumerate(sorted(uniq, key=lambda x: str(x.get("name"))), 1):
                size = f"{p['size']/1024:.0f} KB" if p.get("size") else ""
                desc = (p.get("desc") or "").replace("|", "/")[:70]
                f.write(f"| {i} | {p.get('name')} | `{p.get('appid') or '?'}` | {p.get('version') or ''} | "
                        f"{p.get('developer') or ''} | {p.get('category') or ''} | {size} | {desc} |\n")
            f.write("\n## 下载地址（需 app 的签名头，浏览器直连会 401）\n\n")
            for p in sorted(uniq, key=lambda x: str(x.get("name"))):
                f.write(f"- **{p.get('name')}** `{p.get('appid') or '?'}` v{p.get('version') or '?'}"
                        f" → `https://store.posc.net{p.get('download_url') if p.get('download_url') else '/api/device/download/' + str(p.get('token'))}`\n")
        print(f"[+] Markdown → {md_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
