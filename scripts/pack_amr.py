#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
pack_amr.py —— 通用 miniapp .amr 打包器（从零做一个 app，不依赖 aiot-vue-cli）

.amr 结构（实测）：
    manifest.json          必须有；含 appName/version/appid/icon/quickjs/cert
    <page>.js / .js.bin    每个页面一个文件，**页面名 = 文件名**（miniapp_cli start <appid> <page>）
    app.js / app.js.bin    应用入口（导出继承 $falcon.App 的类）
    libs/<abi>/libjsapi_*.so   原生插件；<abi> 随机型：arm64 / arm64-orange / arm64-cherry3566
    app_icon.png
    cert 覆盖除 manifest.json 与 app_icon.png 以外的所有文件（size + md5）

用法示例：
    python tools\pack_amr.py --src miniapps\terminal\src ^
        --out miniapps\dist\terminal-0.1.0.amr ^
        --appid 8001999000000001 --name "终端" --version 0.1.0 ^
        --lib arm64=tools\build\libjsapi_term.so ^
        --lib arm64-orange=tools\build\libjsapi_term.so
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
CERT_EXCLUDE = {"manifest.json", "app_icon.png"}


def md5(b: bytes) -> str:
    return hashlib.md5(b).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="应用源目录（相对工作区或绝对路径）")
    ap.add_argument("--out", required=True, help="输出 .amr 路径")
    ap.add_argument("--appid", required=True)
    ap.add_argument("--name", required=True, help="appName（显示名，可中文）")
    ap.add_argument("--version", default="0.1.0")
    ap.add_argument("--quickjs", default="20200705")
    ap.add_argument("--icon", default=None, help="app_icon.png（默认用 --src 里的 app_icon.png，或用现成的）")
    ap.add_argument("--lib", action="append", default=[],
                    help="ABI=so路径，可重复；例如 arm64=tools\\build\\libjsapi_term.so")
    ap.add_argument("--extra", action="append", default=[], help="额外塞进包里的 包内路径=本地路径")
    ap.add_argument("--no-libs", action="store_true", help="不写 libs/（纯 JS 应用）")
    args = ap.parse_args()

    src = args.src if os.path.isabs(args.src) else os.path.join(ROOT, args.src)
    out = args.out if os.path.isabs(args.out) else os.path.join(ROOT, args.out)
    if not os.path.isdir(src):
        print(f"[!] 源目录不存在: {src}")
        return 1

    # 1) 收集文件（包内路径统一用 /）
    files: dict[str, bytes] = {}
    for base, _dirs, names in os.walk(src):
        for n in names:
            p = os.path.join(base, n)
            rel = os.path.relpath(p, src).replace("\\", "/")
            if rel.startswith("libs/"):
                continue  # libs 由 --lib 决定，避免误打包源码目录里的东西
            files[rel] = open(p, "rb").read()

    # 2) 图标
    icon_name = "app_icon.png"
    if args.icon:
        ic = args.icon if os.path.isabs(args.icon) else os.path.join(ROOT, args.icon)
        files[icon_name] = open(ic, "rb").read()
    if icon_name not in files:
        print("[!] 包里没有 app_icon.png，请用 --icon 指定一张")
        return 2

    # 3) 原生插件
    for spec in args.lib:
        abi, _, p = spec.partition("=")
        p = p if os.path.isabs(p) else os.path.join(ROOT, p)
        if not os.path.isfile(p):
            print(f"[!] 插件不存在: {p}")
            return 3
        files[f"libs/{abi}/{os.path.basename(p)}"] = open(p, "rb").read()

    # 4) 额外文件
    for spec in args.extra:
        dest, _, p = spec.partition("=")
        p = p if os.path.isabs(p) else os.path.join(ROOT, p)
        files[dest] = open(p, "rb").read()

    # 5) manifest + cert
    cert = {}
    for rel, data in files.items():
        if rel in CERT_EXCLUDE:
            continue
        cert[rel] = {"size": len(data), "md5": md5(data)}
    manifest = {
        "appName": args.name,
        "version": args.version,
        "appid": str(args.appid),
        "icon": icon_name,
        "quickjs": {"version": args.quickjs, "bigNum": False},
        "cert": cert,
    }
    files["manifest.json"] = json.dumps(manifest, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    # 6) 写包（manifest.json 放最前，其余按名字排序，稳定可复现）
    os.makedirs(os.path.dirname(out), exist_ok=True)
    order = ["manifest.json"] + sorted(k for k in files if k != "manifest.json")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in order:
            z.writestr(rel, files[rel])

    print(f"[+] {out}  ({os.path.getsize(out):,} 字节)")
    print(f"    appid={args.appid} name={args.name} version={args.version}")
    print(f"    条目 {len(files)} 个，cert {len(cert)} 条:")
    for rel in order:
        print(f"      {rel:<48} {len(files[rel]):>9,}  {md5(files[rel])[:12]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
