#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
hc_campaign.py —— 用 hashcat(RTX 2080 Ti) 分阶段爆破 adbd_auth.sh 的 md5

目标哈希是 md5(密码 + "\n")（脚本里是 `echo $PASSWD | md5sum`），
所以每条候选都要在末尾补一个 0x0a —— 用规则文件 nl.rule 里的 `$\x0a` 实现。

阶段（从最可能的开始，命中即停）：
  1. rockyou × best64 规则
  2. rockyou × OneRuleToRuleThemAll（几万条规则，GPU 也只要一两分钟）
  3. youdao 主题词表 × best64
  4. 掩码：9~12 位纯数字、6~8 位小写字母、常见 词+数字 组合
用法: python hc_campaign.py [--hash ...] [--budget 900]
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
import sys
import time
import urllib.request

import _console  # noqa: F401

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
HC_DIR = os.path.join(ROOT, "tools", "hashcat", "hashcat-6.2.6")
HC = os.path.join(HC_DIR, "hashcat.exe")
WL = os.path.join(ROOT, "out", "wl")
WORK = os.path.join(ROOT, "out", "hc")

THEME = [
    "youdao", "Youdao", "YOUDAO", "yd", "ydp", "ydpx6", "x6", "x6pro", "X6Pro", "dictpen", "DictPen",
    "dictionarypen", "netease", "Netease", "youdaodict", "ydict", "ydictpen", "pen", "Pen", "youdaopen",
    "YoudaoDictionaryPen", "youdaoai", "aiyoudao", "youdaobiji", "ydb", "Youdao2024", "youdao2024",
]


def sh(cmd: list[str], log: str, timeout: float | None = None) -> int:
    """跑 hashcat，输出写日志，同时把关键行打到屏幕。"""
    with open(log, "w", encoding="utf-8", errors="replace") as lf:
        p = subprocess.Popen(cmd, cwd=HC_DIR, stdout=lf, stderr=subprocess.STDOUT, text=True)
        t0 = time.time()
        while p.poll() is None:
            if timeout and time.time() - t0 > timeout:
                p.terminate()
                try:
                    p.wait(10)
                except Exception:  # noqa: BLE001
                    p.kill()
                print(f"      (超时 {timeout:.0f}s，终止)", flush=True)
                break
            time.sleep(1)
    try:
        txt = open(log, encoding="utf-8", errors="replace").read()
    except Exception:  # noqa: BLE001
        return -1
    for line in txt.splitlines():
        if re.search(r"Status|Speed|Recovered|Progress|Time.Started|Cracked|Exhausted|Hash.Target", line):
            if "Status" in line or "Speed" in line or "Recovered" in line or "Cracked" in line:
                print("      " + line.strip(), flush=True)
    return p.returncode or 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hash", default="302af1d80be1106586775350cc0a2c92")
    ap.add_argument("--budget", type=float, default=1200, help="总时间上限（秒）")
    args = ap.parse_args()

    os.makedirs(WORK, exist_ok=True)
    target = args.hash.strip().lower()
    hashfile = os.path.join(WORK, "target.txt")
    pot = os.path.join(WORK, "campaign.pot")
    open(hashfile, "w").write(target + "\n")

    # nl.rule：给每条候选补一个换行
    nl_rule = os.path.join(WORK, "nl.rule")
    with open(nl_rule, "wb") as f:
        f.write(b"$\x5cx0a\n")

    # 主题词表
    theme = os.path.join(WORK, "theme.txt")
    with open(theme, "w", encoding="utf-8") as f:
        f.write("\n".join(THEME) + "\n")

    # OneRuleToRuleThemAll
    big_rule = os.path.join(WORK, "OneRuleToRuleThemAll.rule")
    if not os.path.isfile(big_rule):
        for u in (
            "https://raw.githubusercontent.com/NotSoSecure/password_cracking_rules/master/OneRuleToRuleThemAll.rule",
            "https://raw.githubusercontent.com/stealthsploit/OneRuleToRuleThemStill/main/OneRuleToRuleThemStill.rule",
        ):
            try:
                req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
                data = urllib.request.urlopen(req, timeout=120).read()
                open(big_rule, "wb").write(data)
                print(f"[*] 规则表下载 OK: {os.path.basename(u)}  {len(data)/1048576:.1f} MB", flush=True)
                break
            except Exception as e:  # noqa: BLE001
                print(f"[!] 规则表下载失败 {u}: {e}", flush=True)

    best64 = os.path.join(HC_DIR, "rules", "best64.rule")
    rockyou = os.path.join(WL, "rockyou.txt")
    top100k = os.path.join(WL, "100k-most-used-passwords-NCSC.txt")

    # rockyou 前 1,000,000 行（按词频排序，性价比最高的一段）
    rockyou_1m = os.path.join(WORK, "rockyou-top1m.txt")
    if os.path.isfile(rockyou) and not os.path.isfile(rockyou_1m):
        with open(rockyou, "r", encoding="utf-8", errors="replace") as src, open(rockyou_1m, "w", encoding="utf-8", errors="replace") as dst:
            for i, line in enumerate(src):
                if i >= 1_000_000:
                    break
                dst.write(line)
        print(f"[*] 生成 rockyou 前 100 万词: {rockyou_1m}", flush=True)

    # 主题词 × 大规则表
    theme_big = os.path.join(WORK, "theme_all.txt")
    with open(theme_big, "w", encoding="utf-8") as f:
        toks = THEME + ["ydict2023", "youdao2023", "Youdao@123", "youdao123", "ydpx", "ydpx6-2", "YDPX6", "YDPX62"]
        f.write("\n".join(toks) + "\n")

    stages: list[tuple[str, list[str], float]] = []
    base = ["-m", "0", "--potfile-path", pot, "--quiet", "--force", "--status", "--status-timer", "20", "-o", os.path.join(WORK, "found.txt"), "--outfile-format", "2"]
    if os.path.isfile(big_rule):
        stages.append(("youdao 主题词 × OneRule + 换行", ["-a", "0", hashfile, theme_big, "-r", big_rule, "-r", nl_rule], 60))
        if os.path.isfile(top100k):
            stages.append(("top100k × OneRule + 换行", ["-a", "0", hashfile, top100k, "-r", big_rule, "-r", nl_rule], 240))
    if os.path.isfile(rockyou):
        stages.append(("rockyou + best64 + 换行", ["-a", "0", hashfile, rockyou, "-r", best64, "-r", nl_rule], 180))
        if os.path.isfile(big_rule):
            stages.append(("rockyou 前100万 × OneRule + 换行", ["-a", "0", hashfile, rockyou_1m, "-r", big_rule, "-r", nl_rule], 400))
    stages.append(("youdao 主题词 + best64 + 换行", ["-a", "0", hashfile, theme_big, "-r", best64, "-r", nl_rule], 60))
    # 掩码：数字（换行用自定义字符集 ?1 = 0x0a）
    for n in (9, 10, 11, 12):
        stages.append((f"{n} 位纯数字 + 换行", ["-a", "3", hashfile, "-1", "0a", "--hex-charset", "?d" * n + "?1"], 150))
    # 掩码：纯小写字母
    for n in (6, 7, 8):
        stages.append((f"{n} 位小写字母 + 换行", ["-a", "3", hashfile, "-1", "0a", "--hex-charset", "?l" * n + "?1"], 200))
    # 掩码：小写/大写开头 + 字母 + 数字（常见人工密码形状）
    for n in (3, 4, 5):
        stages.append((f"小写{n}位+数字4位 + 换行", ["-a", "3", hashfile, "-1", "0a", "--hex-charset", "?l" * n + "?d" * 4 + "?1"], 200))
    for n in (3, 4):
        stages.append((f"首字母大写+小写{n}位+数字4位 + 换行", ["-a", "3", hashfile, "-1", "0a", "--hex-charset", "?u" + "?l" * n + "?d" * 4 + "?1"], 200))

    t0 = time.time()
    for name, extra, budget in stages:
        if os.path.isfile(pot) and target in open(pot, encoding="utf-8", errors="replace").read():
            break
        if time.time() - t0 > args.budget:
            print("[*] 总时间用完，停止", flush=True)
            break
        print(f"\n[*] 阶段：{name}（上限 {budget:.0f}s）", flush=True)
        rc = sh([HC] + base + extra, os.path.join(WORK, "last.log"), timeout=min(budget, max(10, args.budget - (time.time() - t0))))
        if os.path.isfile(pot):
            for line in open(pot, encoding="utf-8", errors="replace"):
                if line.startswith(target):
                    pw = line.split(":", 1)[1].rstrip("\n")
                    if pw.startswith("$HEX["):
                        pw = bytes.fromhex(pw[5:-1]).decode("utf-8", "replace")
                    pw = pw.rstrip("\n")
                    print(f"\n[+++] 破解成功！ADB 密码 = {pw!r}", flush=True)
                    return 0
    print("\n[-] 所有阶段都没破出来。这个密码基本可以确定是随机字符串，走改固件刷机路线。", flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())
