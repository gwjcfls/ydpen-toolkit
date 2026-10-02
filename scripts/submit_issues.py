#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
submit_issues.py —— 把 issues\*.md 草稿通过 GitHub API 提交成 issue

用法：
    # 1) 先预演（不发，只显示会发什么）
    python tools\submit_issues.py --dry-run

    # 2) 设好 token 后正式提交（token 只从环境变量读，不写日志、不出现在命令行）
    $env:GITHUB_TOKEN = "github_pat_..."        # 或 ghp_...
    python tools\submit_issues.py

可选：
    --only 01,02,03        只提交指定编号的草稿
    --repo owner/name=file 覆盖某份草稿的目标仓库（可多次）

草稿文件约定：第一行是 `# 标题`，其余为正文。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
ISSUES_DIR = os.path.join(ROOT, "issues")

# 草稿编号 → (目标仓库, 说明)
TARGETS = {
    "01": ("56dz/PenBili", "PenBili 在 X6 Pro 上无法运行（32 位插件 + 无 /dev/fb0）"),
    "02": ("Mxzsan/file-manager-miniapp", "文件互传只实现 GET，无法上传文件"),
    "03a": ("adogecheems/doge-reader", "PenOS 4.3.5 缺 fs jsapi（阅读器）"),
    "03b": ("adogecheems/doge-comic", "PenOS 4.3.5 缺 fs jsapi（漫画）"),
}
# 03 那份草稿要发到两个仓库
MULTI = {"03": ["03a", "03b"]}

FOOTER = ("\n\n---\n\n*本 issue 由一台有道词典笔 **YDPX6-2（X6 Pro / RK3562 / aarch64 / PenOS 4.3.5）** "
          "实机复现整理；文中的命令与日志均为该机实测输出。*")


def load_draft(path: str):
    txt = open(path, encoding="utf-8").read()
    m = re.match(r"^#\s+(.+?)\s*$", txt, re.M)
    if not m:
        return None, None
    title = m.group(1).strip()
    body = txt[m.end():].lstrip("\n")
    return title, body


def api(method: str, url: str, token: str, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "ydpen-issue-reporter",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            return json.loads(r.read().decode()), None
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode())
            msg = detail.get("message", "")
            errs = detail.get("errors")
            if errs:
                msg += " | " + json.dumps(errs, ensure_ascii=False)[:300]
        except Exception:  # noqa: BLE001
            msg = str(e)
        return None, f"HTTP {e.code}: {msg}"
    except Exception as e:  # noqa: BLE001
        return None, str(e)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", default="")
    ap.add_argument("--repo", action="append", default=[])
    args = ap.parse_args()

    overrides = {}
    for kv in args.repo:
        if "=" in kv:
            k, v = kv.split("=", 1)
            overrides[k] = v

    files = sorted(f for f in os.listdir(ISSUES_DIR) if re.match(r"^0\d.*\.md$", f))
    # --only 支持 "01,02,03" / "1,2,3" / "01 02 03" 三种写法
    wanted = set()
    if args.only:
        for x in re.split(r"[,\s]+", args.only.strip()):
            if x:
                wanted.add(x.lstrip("0") or "0")
    tasks = []
    for f in files:
        num = f[:2]
        if wanted and (num.lstrip("0") or "0") not in wanted:
            continue
        title, body = load_draft(os.path.join(ISSUES_DIR, f))
        if not title:
            print(f"[!] {f} 里没找到 `# 标题`，跳过")
            continue
        keys = MULTI.get(num, [num])
        for k in keys:
            repo = overrides.get(k) or overrides.get(num) or (TARGETS.get(k) or TARGETS.get(num, ("?", "")))[0]
            tasks.append((f, k, repo, title, body))

    print(f"待提交 {len(tasks)} 个 issue：\n")
    for f, k, repo, title, body in tasks:
        print(f"  [{k}] {repo}")
        print(f"       标题: {title}")
        print(f"       正文: {len(body)} 字符，{body.count(chr(10))} 行")
        print()

    if args.dry_run:
        print("这不是 --dry-run 的提交，什么都没发。要真发就去掉 --dry-run 并设好 GITHUB_TOKEN。")
        return 0

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        print("[!] 没找到 GITHUB_TOKEN 环境变量。")
        print('    设置方法（PowerShell，仅当前会话）：  $env:GITHUB_TOKEN="github_pat_..."')
        return 2
    print(f"[*] 已读到 token（长度 {len(token)}，前缀 {token[:7]}…，不会打印完整值）\n")

    ok = 0
    for f, k, repo, title, body in tasks:
        if repo == "?":
            print(f"[!] {k}: 目标仓库未知，跳过")
            continue
        print(f"[*] 提交到 {repo} …")
        res, err = api("POST", f"https://api.github.com/repos/{repo}/issues", token,
                       {"title": title, "body": body + FOOTER})
        if err:
            print(f"    [失败] {err}")
            if "410" in err or "disabled" in err.lower():
                print("    （提示：该仓库可能关闭了 Issues）")
        else:
            ok += 1
            print(f"    [成功] #{res.get('number')}  {res.get('html_url')}")

    print(f"\n完成：成功 {ok} / {len(tasks)}")
    return 0 if ok == len(tasks) else 1


if __name__ == "__main__":
    sys.exit(main())
