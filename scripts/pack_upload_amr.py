#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
pack_upload_amr.py —— 把自研的 fileServer 插件打进 file-manager 的 .amr（新设备一次装好，自带上传）

做的事：
  1) 用原始 file-manager-1.2.0.amr 为底（**所有原有文件一字节不改**）
  2) 把 libs/arm64/libjsapi_fileserver.so 与 libs/arm64-cherry3566/libjsapi_fileserver.so
     换成自研插件（支持上传）
  3) 同步 manifest.json 里这两个条目的 size / md5
  4) 校验：除这两个外，其余每个文件都与 cert 里的 md5 一致

用法: python tools\pack_upload_amr.py
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import zipfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
SRC = os.path.join(ROOT, "miniapps", "file-manager-1.2.0.amr")
OUT = os.path.join(ROOT, "miniapps", "dist", "file-manager-1.2.0-upload.amr")
NEW_SO = os.path.join(ROOT, "tools", "build", "libjsapi_fileserver_9000000001.so")
REPLACE = [
    "libs/arm64/libjsapi_fileserver.so",
    "libs/arm64-cherry3566/libjsapi_fileserver.so",
]


def main() -> int:
    if not os.path.isfile(NEW_SO):
        print(f"[!] 缺少自研插件: {NEW_SO}")
        return 1
    so = open(NEW_SO, "rb").read()
    so_md5 = hashlib.md5(so).hexdigest()
    print(f"[*] 自研插件 {len(so)} 字节 md5={so_md5}")

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    shutil.copy2(SRC, OUT)

    # 读原包
    zin = zipfile.ZipFile(SRC)
    names = zin.namelist()
    manifest = json.loads(zin.read("manifest.json"))
    cert = manifest["cert"]
    for p in REPLACE:
        if p not in names:
            print(f"[!] 原包里没有 {p}")
            return 2
        cert[p] = {"size": len(so), "md5": so_md5}

    manifest_bytes = json.dumps(manifest, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    # 重写包（保持压缩方式为 deflate，条目顺序不变）
    tmp = OUT + ".tmp"
    zout = zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED)
    for n in names:
        if n == "manifest.json":
            zout.writestr(n, manifest_bytes)
        elif n in REPLACE:
            zout.writestr(n, so)
        else:
            zout.writestr(n, zin.read(n))
    zout.close()
    zin.close()
    os.replace(tmp, OUT)
    print(f"[*] 已生成 {OUT}  ({os.path.getsize(OUT)} 字节)")

    # 校验
    z = zipfile.ZipFile(OUT)
    m = json.loads(z.read("manifest.json"))
    bad, new_ok = [], 0
    for n in z.namelist():
        if n == "manifest.json":
            continue
        d = z.read(n)
        want = m["cert"].get(n, {}).get("md5")
        got = hashlib.md5(d).hexdigest()
        if n in REPLACE:
            if got == so_md5 and m["cert"][n]["size"] == len(d):
                new_ok += 1
            else:
                bad.append(n)
        elif want and got != want:
            bad.append(n)
    print(f"[*] 替换的 2 个 64 位插件校验: {new_ok}/2")
    print(f"[*] 其余原有文件校验: {'全部一致 ✔' if not bad else '不一致: ' + str(bad[:5])}")
    print(f"[*] appName={m.get('appName')} version={m.get('version')} appid={m.get('appid')}")
    print(f"[*] 32 位 arm 目录仍是原版（32 位老机型没有上传功能）: {m['cert']['libs/arm/libjsapi_fileserver.so']['md5'][:12]}…")
    return 0 if not bad and new_ok == 2 else 3


if __name__ == "__main__":
    sys.exit(main())
