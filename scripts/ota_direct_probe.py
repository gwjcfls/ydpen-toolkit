#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
ota_direct_probe.py —— 直接从电脑向官方 OTA 服务器发 checkVersion，看能不能白拿全量包。

动机：社区教程里"改 version 重发抓到的请求"就能拿到全量包链接 —— 说明服务器对
timestamp/sign 校验很松（sign 很可能不覆盖 version）。那么只要能凑出
productId + 路径里的那一段 key，就完全不需要动笔。

用法:
    python tools\ota_direct_probe.py                # 跑内置的几组探测
    python tools\ota_direct_probe.py --mid XXX --sign YYY --url <完整URL>
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
HOST = "http://iotapi.abupdate.com"

# 旧笔（YDPX6-2）真实抓到的一份请求，作为"已知可用样本"
KNOWN = {
    "mid": "9DA2600007503031",
    "productId": "1687695728",
    "sign": "77e115d2a2c86779126d44155d0afe15",
    "timestamp": 1790844918,
    "path_key": "3f57f6d234286e04",
    "version": "4.3.5",
}


def ask(url: str, body: dict, timeout: int = 25) -> tuple[int, str]:
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json;charset=UTF-8")
    req.add_header("Accept", "*/*")
    req.add_header("User-Agent", UA)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return 0, f"<{type(e).__name__}: {e}>"


def summarize(text: str, limit: int = 1200) -> str:
    text = text.strip()
    try:
        obj = json.loads(text)
    except Exception:  # noqa: BLE001
        return text[:limit]
    ver = ((obj.get("data") or {}).get("version") or {})
    if ver:
        keep = {k: ver.get(k) for k in ("versionName", "fileSize", "md5sum", "sha", "deltaUrl", "bakUrl")}
        seg = ver.get("segmentMd5")
        if isinstance(seg, list):
            keep["segmentMd5_len"] = len(seg)
            keep["segmentMd5_tail"] = seg[-1]
        return json.dumps({"status": obj.get("status"), "msg": obj.get("msg"), "version": keep},
                          ensure_ascii=False, indent=1)[:limit]
    return json.dumps(obj, ensure_ascii=False)[:limit]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mid", default=None)
    ap.add_argument("--sign", default=None)
    ap.add_argument("--product-id", default=None)
    ap.add_argument("--path-key", default=None)
    ap.add_argument("--url", default=None)
    ap.add_argument("--version", default="99.99.90")
    args = ap.parse_args()

    cases: list[tuple[str, str, dict]] = []

    if args.url:
        body = {
            "timestamp": KNOWN["timestamp"],
            "sign": args.sign or KNOWN["sign"],
            "mid": args.mid or KNOWN["mid"],
            "productId": args.product_id or KNOWN["productId"],
            "version": args.version,
            "networkType": "WIFI",
        }
        cases.append(("命令行指定", args.url, body))
    else:
        pid_old = KNOWN["productId"]
        key_old = KNOWN["path_key"]
        # 1) 已知旧笔样本重放（验证"服务器还吃不吃老请求 / HTTP 洞还在不在"）
        for ver in ("4.3.5", "99.99.90"):
            cases.append((
                f"[已知样本] 旧笔 productId={pid_old} version={ver}",
                f"{HOST}/product/{pid_old}/{key_old}/ota/checkVersion",
                {"timestamp": KNOWN["timestamp"], "sign": KNOWN["sign"], "mid": KNOWN["mid"],
                 "productId": pid_old, "version": ver, "networkType": "WIFI"},
            ))
        # 2) 教程里出现的 x7pro productId —— 试探路径 key 是否真的被校验
        pid_x7 = "1717746496"
        variants = [
            ("沿用旧 key", f"{HOST}/product/{pid_x7}/{key_old}/ota/checkVersion"),
            ("全 0 key", f"{HOST}/product/{pid_x7}/0000000000000000/ota/checkVersion"),
            ("无 key 段", f"{HOST}/product/{pid_x7}/ota/checkVersion"),
        ]
        for label, url in variants:
            cases.append((
                f"[x7pro 猜测] productId={pid_x7} ({label})",
                url,
                {"timestamp": KNOWN["timestamp"], "sign": KNOWN["sign"], "mid": KNOWN["mid"],
                 "productId": pid_x7, "version": args.version, "networkType": "WIFI"},
            ))

    for label, url, body in cases:
        print(f"\n=== {label} ===", flush=True)
        print(f"    POST {url}", flush=True)
        print(f"    body {json.dumps(body, ensure_ascii=False)}", flush=True)
        code, text = ask(url, body)
        print(f"    -> HTTP {code}", flush=True)
        print("    " + summarize(text).replace("\n", "\n    "), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
