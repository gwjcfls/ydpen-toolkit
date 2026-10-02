#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
ota_fetch.py —— 用已知 mid（= 笔的 SN / adb 序列号）直接向官方 OTA 服务器要全量包。

原理（本机实测）：
  * 接口是明文 HTTP：POST http://iotapi.abupdate.com/product/<productId>/<任意key>/ota/checkVersion
  * sign / timestamp **不校验**（旧请求重放照样 200）
  * 路径里的 key 段 **不校验**（全 0 也能过；缺段才 5000）
  * 唯一门槛：mid 必须在服务器注册过（否则 status 2103 Device not registered）
  * version 填 99.99.90 触发“返回最新全量包”

用法:
    python tools\ota_fetch.py --mid ME82300008900903 --product-id 1717746496
    python tools\ota_fetch.py --mid ME82300008900903 --product-id 1717746496 --save
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.abspath(os.path.join(HERE, "..", "out"))
HOST = "http://iotapi.abupdate.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
PATH_KEY = "3f57f6d234286e04"  # 旧笔抓到的 key 段；实测不校验，随便填都行


def ask(product_id: str, mid: str, version: str, sign: str = "0" * 32,
        timeout: int = 30) -> tuple[int, dict | str]:
    url = f"{HOST}/product/{product_id}/{PATH_KEY}/ota/checkVersion"
    body = {
        "timestamp": int(time.time()),
        "sign": sign,
        "mid": mid,
        "productId": str(product_id),
        "version": version,
        "networkType": "WIFI",
    }
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json;charset=UTF-8")
    req.add_header("Accept", "*/*")
    req.add_header("User-Agent", UA)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            txt = r.read().decode("utf-8", "replace")
            code = r.status
    except urllib.error.HTTPError as e:
        txt = e.read().decode("utf-8", "replace")
        code = e.code
    except Exception as e:  # noqa: BLE001
        return 0, f"<{type(e).__name__}: {e}>"
    try:
        return code, json.loads(txt)
    except Exception:  # noqa: BLE001
        return code, txt


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mid", required=True, help="笔的 SN / adb 序列号")
    ap.add_argument("--product-id", required=True, action="append", help="可重复传多个")
    ap.add_argument("--version", default="99.99.90")
    ap.add_argument("--save", action="store_true", help="把成功的那份响应存到 out\\real_response.json")
    args = ap.parse_args()

    ok = None
    for pid in args.product_id:
        code, obj = ask(pid, args.mid, args.version)
        print(f"\n=== mid={args.mid} productId={pid} ===")
        print(f"    HTTP {code}")
        if isinstance(obj, dict):
            status = obj.get("status")
            print(f"    status={status} msg={obj.get('msg') or obj.get('message')}")
            ver = (obj.get("data") or {}).get("version") or {}
            if ver:
                seg = ver.get("segmentMd5") or []
                print(f"    versionName = {ver.get('versionName')}")
                print(f"    fileSize    = {ver.get('fileSize'):,}" if isinstance(ver.get("fileSize"), int) else f"    fileSize    = {ver.get('fileSize')}")
                print(f"    md5sum      = {ver.get('md5sum')}")
                print(f"    sha         = {ver.get('sha')}")
                print(f"    deltaUrl    = {ver.get('deltaUrl')}")
                print(f"    bakUrl      = {ver.get('bakUrl')}")
                print(f"    segments    = {len(seg)}")
                ok = obj
        else:
            print(f"    {str(obj)[:400]}")
        if ok:
            break

    if ok and args.save:
        os.makedirs(OUT, exist_ok=True)
        dest = os.path.join(OUT, "real_response.json")
        if os.path.exists(dest):
            backup = dest + ".old_backup"
            os.replace(dest, backup)
            print(f"\n[*] 旧的 real_response.json 已备份为 {os.path.basename(backup)}")
        with open(dest, "w", encoding="utf-8") as f:
            json.dump(ok, f, ensure_ascii=False, indent=2)
        print(f"[+] 已写入 {dest}")
    return 0 if ok else 3


if __name__ == "__main__":
    sys.exit(main())
