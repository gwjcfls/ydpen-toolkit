#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
patch_policy.py —— 把伪造 OTA 响应里的安装策略改成“装完自动重启升级”

背景：ota_meta.py 只重算哈希/URL，不动 policy。官方响应里
      policy.install.rebootUpgrade = "false"，
      不改的话词典笔会卡在“正在安装 100.00%”不重启，补丁不生效。
      另外 policy.install.force 里的时间窗也可能挡住升级，一并清空。

用法:
    python tools\patch_policy.py out\patched_response.json
    python tools\patch_policy.py out\patched_response.json --reboot true --clear-force
"""

from __future__ import annotations

import argparse
import json
import os
import sys


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("response", help="patched_response.json 路径")
    ap.add_argument("--reboot", default="true", choices=["true", "false"])
    ap.add_argument("--clear-force", action="store_true", default=True)
    ap.add_argument("--keep-force", dest="clear_force", action="store_false")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    with open(args.response, "r", encoding="utf-8") as f:
        obj = json.load(f)

    data = obj.get("data") if isinstance(obj.get("data"), dict) else obj
    pol = data.get("policy") if isinstance(data, dict) else None
    if not isinstance(pol, dict):
        print("[!] 响应里没有 data.policy，无法改策略")
        return 1

    changes = []
    for grp, items in pol.items():
        if not isinstance(items, list):
            continue
        for it in items:
            if not isinstance(it, dict):
                continue
            name = it.get("key_name")
            if name == "rebootUpgrade":
                if it.get("key_value") != args.reboot:
                    changes.append(f"policy.{grp}.rebootUpgrade: {it.get('key_value')} -> {args.reboot}")
                    it["key_value"] = args.reboot
            elif name == "force" and args.clear_force and it.get("key_value"):
                changes.append(f"policy.{grp}.force: {it.get('key_value')!r} -> ''")
                it["key_value"] = ""
            elif name == "storageSize":
                pass

    if not changes:
        print("[=] 策略已经是对的，无需改动")
    else:
        print("[*] 改动：")
        for c in changes:
            print("    " + c)

    out = args.out or args.response
    tmp = out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, out)
    print(f"[+] 已写入 {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
