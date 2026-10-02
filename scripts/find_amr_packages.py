#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
find_amr_packages.py —— 在全网（GitHub 为主）搜集可安装的有道词典笔 .amr 小程序包

做法：
  1) 用 GitHub 搜索 API 找相关仓库（多组关键词）
  2) 逐个仓库看：Releases 附件里有没有 .amr / .zip、仓库文件树里有没有 .amr
  3) 顺便枚举 PenUniverse 组织的全部仓库及其 amr 附件
  4) 结果汇总成表 + 存 out\amr_sources.json

用法: python tools\find_amr_packages.py
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
OUT = os.path.join(ROOT, "out", "amr_sources.json")

UA = {"User-Agent": "Mozilla/5.0 (pen-toolkit)", "Accept": "application/vnd.github+json"}
TOKEN = os.environ.get("GITHUB_TOKEN", "")
if TOKEN:
    UA["Authorization"] = f"Bearer {TOKEN}"


def api(url: str, retries: int = 3):
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            if e.code in (403, 429):
                time.sleep(6 + i * 6)
                continue
            return {"__error__": f"HTTP {e.code}"}
        except Exception as e:  # noqa: BLE001
            time.sleep(2)
            if i == retries - 1:
                return {"__error__": str(e)}
    return {"__error__": "rate limited"}


def amr_assets(releases):
    """从 releases 里挑出 .amr / .zip 附件"""
    found = []
    for rel in releases or []:
        for a in rel.get("assets", []):
            name = a.get("name", "")
            if re.search(r"\.(amr|zip|apk)$", name, re.I):
                found.append({
                    "release": rel.get("tag_name"),
                    "name": name,
                    "size": a.get("size"),
                    "downloads": a.get("download_count"),
                    "url": a.get("browser_download_url"),
                    "published": (rel.get("published_at") or "")[:10],
                })
    return found


def main() -> int:
    print("=" * 78)
    print("1) GitHub 仓库搜索")
    print("=" * 78)
    queries = [
        "youdao pen miniapp",
        "词典笔 amr",
        "youdao dictionary pen amr",
        "有道词典笔 小程序",
        "youdao miniapp",
        "dictionary pen miniapp amr",
    ]
    repos: dict[str, dict] = {}
    for q in queries:
        d = api(f"https://api.github.com/search/repositories?q={urllib.parse.quote(q)}&per_page=20&sort=updated")
        items = d.get("items", []) if isinstance(d, dict) else []
        print(f"  「{q}」→ {len(items)} 个结果")
        for it in items:
            full = it["full_name"]
            repos.setdefault(full, {
                "full_name": full,
                "desc": (it.get("description") or "")[:110],
                "stars": it.get("stargazers_count"),
                "pushed": (it.get("pushed_at") or "")[:10],
                "url": it.get("html_url"),
                "why": q,
            })
        time.sleep(2.5)

    # 补充：PenUniverse 组织全部仓库
    print("\n  PenUniverse 组织仓库：")
    org = api("https://api.github.com/orgs/PenUniverse/repos?per_page=100&sort=updated")
    if isinstance(org, list):
        for it in org:
            full = it["full_name"]
            print(f"    - {full}  ⭐{it.get('stargazers_count')}  {(it.get('description') or '')[:70]}")
            repos.setdefault(full, {
                "full_name": full,
                "desc": (it.get("description") or "")[:110],
                "stars": it.get("stargazers_count"),
                "pushed": (it.get("pushed_at") or "")[:10],
                "url": it.get("html_url"),
                "why": "PenUniverse org",
            })
    else:
        print("    (取不到)", org)

    print("\n" + "=" * 78)
    print(f"2) 逐个仓库找 .amr（共 {len(repos)} 个仓库，只保留有 amr 或像笔的项目的）")
    print("=" * 78)

    results = []
    for full, info in sorted(repos.items(), key=lambda kv: -(kv[1].get("stars") or 0)):
        rels = api(f"https://api.github.com/repos/{full}/releases?per_page=30")
        assets = amr_assets(rels if isinstance(rels, list) else [])
        tree_amr = []
        if not assets:
            tr = api(f"https://api.github.com/repos/{full}/git/trees/HEAD?recursive=1")
            if isinstance(tr, dict) and "tree" in tr:
                tree_amr = [t["path"] for t in tr["tree"] if re.search(r"\.amr$", t.get("path", ""), re.I)][:10]
        if assets or tree_amr:
            info["assets"] = assets
            info["repo_files"] = tree_amr
            results.append(info)
            print(f"\n  ★ {full}  ⭐{info['stars']}  更新 {info['pushed']}")
            print(f"      {info['desc']}")
            for a in assets:
                print(f"      [Release {a['release']}] {a['name']}  {a['size']/1048576:.2f} MB  下载{a['downloads']}次")
                print(f"          {a['url']}")
            for f in tree_amr:
                print(f"      [仓库文件] {f}")
        time.sleep(1.2)

    print("\n" + "=" * 78)
    print(f"3) 汇总：{len(results)} 个仓库带 amr 包  |  共 {sum(len(r.get('assets', [])) for r in results)} 个 release 附件")
    print("=" * 78)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"repos": results, "all_searched": list(repos.values())}, f, ensure_ascii=False, indent=2)
    print(f"[+] 明细已存 {OUT}")
    return 0


if __name__ == "__main__":
    import urllib.parse  # noqa: E402
    sys.exit(main())
