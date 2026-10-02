#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
test_upload.py —— 端到端验证自制 fileServer 插件（页面 / 上传 / 落盘校验 / 下载 / Range / 建目录）

用法:
  python test_upload.py --base http://192.168.137.73:18080 --adb <adb路径> --serial 192.168.137.73:5555
"""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
import urllib.parse

import requests

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FAIL = []


def check(label: str, ok: bool, extra: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"   {extra}" if extra else ""))
    if not ok:
        FAIL.append(label)


def adb(adb_path: str, serial: str, args: str, timeout: int = 60) -> str:
    env = dict(os.environ)
    env.setdefault("ANDROID_USER_HOME", os.path.join(os.path.dirname(adb_path), "..", ".android"))
    r = subprocess.run([adb_path, "-s", serial, "shell", args],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, env=env)
    return (r.stdout or "") + (r.stderr or "")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--adb", required=True)
    ap.add_argument("--serial", required=True)
    ap.add_argument("--size", type=int, default=8 * 1024 * 1024)
    ap.add_argument("--dir", default="/userdisk/Favorite/uploadtest")
    args = ap.parse_args()

    base = args.base.rstrip("/")
    tmpdir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tmp")
    os.makedirs(tmpdir, exist_ok=True)
    local = os.path.abspath(os.path.join(tmpdir, "上传测试.bin"))
    if not os.path.exists(local) or os.path.getsize(local) != args.size:
        with open(local, "wb") as f:
            f.write(os.urandom(args.size))
    data = open(local, "rb").read()
    md5 = hashlib.md5(data).hexdigest()
    print(f"[*] 本地测试文件: {local}  {len(data)} 字节  md5={md5}")

    print(f"\n[1] GET /   （目录页面）")
    r = requests.get(base + "/", timeout=20)
    check("HTTP 200", r.status_code == 200, f"status={r.status_code}")
    html = r.text
    check("页面含上传控件", 'type="file"' in html and "/upload" in html and "XMLHttpRequest" in html)
    check("页面含共享目录信息", "共享目录" in html)

    print(f"\n[2] POST /upload?path=uploadtest  （multipart, 中文文件名, {args.size//1048576}MB）")
    with open(local, "rb") as fh:
        r = requests.post(base + "/upload?path=uploadtest",
                          files={"files": (os.path.basename(local), fh, "application/octet-stream")},
                          timeout=180)
    check("HTTP 200", r.status_code == 200, f"status={r.status_code}")
    saved = None
    try:
        j = r.json()
        saved = (j.get("files") or [None])[0]
        check("返回 ok=true", bool(j.get("ok")), r.text[:200])
    except Exception as e:  # noqa: BLE001
        check("返回可解析 JSON", False, f"{e} :: {r.text[:200]}")

    print(f"\n[3] 笔上落盘校验  （{args.dir}）")
    ls = adb(args.adb, args.serial, f"ls -l '{args.dir}'")
    print("      " + ls.strip().replace("\n", "\n      "))
    check("文件出现在笔上", bool(saved) and saved in ls, f"saved={saved}")
    if saved:
        remote_md5 = adb(args.adb, args.serial, f"md5sum '{args.dir}/{saved}'").strip().split()[0] if adb(
            args.adb, args.serial, f"md5sum '{args.dir}/{saved}'").strip() else ""
        check("笔上文件 md5 与本地一致", remote_md5.lower() == md5, f"remote={remote_md5} local={md5}")

    print(f"\n[4] GET /files/...  （下载回来再比一次）")
    if saved:
        url = base + "/files/uploadtest/" + urllib.parse.quote(saved)
        r2 = requests.get(url, timeout=120)
        check("HTTP 200", r2.status_code == 200, f"status={r2.status_code}")
        check("下载内容一致", hashlib.md5(r2.content).hexdigest() == md5,
              f"收到 {len(r2.content)} 字节")

    print(f"\n[5] Range 请求  （视频拖动/断点续传依赖它）")
    if saved:
        r3 = requests.get(base + "/files/uploadtest/" + urllib.parse.quote(saved),
                          headers={"Range": "bytes=1000-1999"}, timeout=30)
        check("HTTP 206", r3.status_code == 206, f"status={r3.status_code} cr={r3.headers.get('Content-Range')}")
        check("返回 1000 字节", len(r3.content) == 1000, f"len={len(r3.content)}")

    print(f"\n[6] 新建文件夹 + 子目录列表")
    r4 = requests.post(base + "/mkdir?path=uploadtest&name=" + urllib.parse.quote("新建目录"), timeout=20)
    check("mkdir 返回 200", r4.status_code == 200, r4.text[:80])
    r5 = requests.get(base + "/?path=uploadtest", timeout=20)
    check("列表页含新目录", "新建目录" in r5.text)
    check("列表页含刚上传的文件", bool(saved) and saved in r5.text)

    print("\n" + "=" * 60)
    if FAIL:
        print(f"结果：{len(FAIL)} 项失败 -> {FAIL}")
        return 1
    print("结果：全部通过 ✔  上传/下载/续传/建目录都正常")
    return 0


if __name__ == "__main__":
    sys.exit(main())
