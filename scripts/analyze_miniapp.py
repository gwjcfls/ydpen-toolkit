#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
analyze_miniapp.py —— 分析笔上某个 miniapp 的服务器接口 / 商店链接 / 依赖

用法:
    python tools\analyze_miniapp.py <app目录（含 .js.bin 的目录）>
    # 例：python tools\analyze_miniapp.py out\app_8001782559140681\a

输出：
    1) manifest 概要
    2) 所有 http(s) URL（按出现次数排序，标注来源文件）
    3) 疑似 API 路径 / 接口名
    4) 与 store / pstore / license / 服务器 有关的关键字符串及上下文
    5) 原生插件清单（libs/*.so 与其导出符号线索）
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter, defaultdict

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

URL_RE = re.compile(rb"https?://[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]{4,200}")
API_RE = re.compile(rb"/(?:api|v\d|store|app|miniapp|pstore|license|plan|study|report|sync)[A-Za-z0-9\-._/{}$]{0,80}")


def printable(b: bytes) -> str:
    return "".join(chr(c) if 32 <= c < 127 or c in (9,) else "." for c in b)


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    appdir = os.path.abspath(sys.argv[1])
    if not os.path.isdir(appdir):
        print(f"[!] 目录不存在: {appdir}")
        return 1

    print("=" * 78)
    print(f"分析目录: {appdir}")
    print("=" * 78)

    # 1) manifest
    mf = os.path.join(appdir, "manifest.json")
    if os.path.isfile(mf):
        m = json.load(open(mf, encoding="utf-8"))
        print(f"\n[1] manifest: appName={m.get('appName')}  version={m.get('version')}  appid={m.get('appid')}")
        print(f"    quickjs={m.get('quickjs')}")
        props = m.get("props") or {}
        if props:
            keys = list(props.keys())
            print(f"    props 键: {keys[:12]}{' …' if len(keys) > 12 else ''}")
            for k in keys:
                v = props[k]
                if isinstance(v, (str, int, bool)) and str(v) not in ("", "None"):
                    print(f"      {k} = {str(v)[:120]}")
        cert = m.get("cert") or {}
        so = [k for k in cert if k.endswith(".so")]
        print(f"    cert 条目 {len(cert)} 个，其中 .so {len(so)} 个")
        for s in so:
            print(f"      {s}")

    # 2) 收集所有 js.bin / 文本里的 URL
    urls: dict[str, Counter] = defaultdict(Counter)
    api_paths: Counter = Counter()
    for root, _dirs, files in os.walk(appdir):
        for fn in files:
            if not fn.endswith((".js.bin", ".js", ".json", ".bin")):
                continue
            p = os.path.join(root, fn)
            try:
                d = open(p, "rb").read()
            except OSError:
                continue
            for mm in URL_RE.finditer(d):
                u = mm.group().decode("utf-8", "replace")
                urls[u][fn] += 1
            for mm in API_RE.finditer(d):
                s = mm.group().decode("utf-8", "replace")
                if len(s) > 3:
                    api_paths[s] += 1

    print(f"\n[2] 发现的 http(s) URL（{len(urls)} 个不同）")
    for u, cnt in sorted(urls.items(), key=lambda kv: -sum(kv[1].values())):
        total = sum(cnt.values())
        srcs = ",".join(sorted(cnt)[:3])
        print(f"    x{total:<3} {u[:150]}")
        print(f"          来源: {srcs}")

    print(f"\n[3] 疑似接口路径（top 30）")
    for s, c in api_paths.most_common(30):
        print(f"    x{c:<3} {s[:120]}")

    # 4) 关健上下文
    print(f"\n[4] 关键词上下文（store / pstore / license / server）")
    kw = [b"pstore", b"PStore", b"PSTORE", b"store_url", b"storeUrl", b"baseUrl", b"BASE_URL",
          b"license", b"License", b"server", b"Server", b"appstore", b"app_store"]
    for root, _dirs, files in os.walk(appdir):
        for fn in files:
            if not fn.endswith(".js.bin"):
                continue
            d = open(os.path.join(root, fn), "rb").read()
            hits = []
            for k in kw:
                for mm in re.finditer(re.escape(k), d):
                    seg = d[max(0, mm.start() - 90):mm.start() + 110]
                    t = printable(seg)
                    if t not in hits:
                        hits.append(t)
            if hits:
                print(f"\n  --- {fn} （{len(hits)} 处）---")
                for t in hits[:12]:
                    print(f"    …{t}…")

    # 5) 原生插件
    print(f"\n[5] 原生插件（libs/）")
    libdir = os.path.join(appdir, "libs")
    if os.path.isdir(libdir):
        for fn in sorted(os.listdir(libdir)):
            p = os.path.join(libdir, fn)
            size = os.path.getsize(p)
            d = open(p, "rb").read()
            strs = {printable(s) for s in re.findall(rb"[ -~]{6,60}", d)}
            hints = sorted(s for s in strs if re.search(r"https?://|registerCModuleLoader|license|pstore|store", s, re.I))[:6]
            print(f"    {fn}  {size/1024:.0f} KB")
            for h in hints:
                print(f"        {h[:110]}")
    else:
        print("    (没有 libs 目录)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
