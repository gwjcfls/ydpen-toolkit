#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ota_meta.py —— 根据抓到的官方 checkVersion 响应 + 改过的固件，生成“喂给词典笔”的伪造响应

做三件事
--------
1. 一趟顺序读盘算出：整包 MD5 / SHA1 / SHA256 / 每个分片(endpos 分段)的 MD5；
2. 把官方 JSON 里的 segmentMd5 / md5sum / sha / sha256 / fileSize / storageSize
   换成上面算出来的值，把 bakUrl / deltaUrl 换成本机固件直链；
3. 输出 status=1000 的新 JSON（写到 out/patched_response.json），供 fake_ota_server.py 使用。

用法
----
  python ota_meta.py --img firmware\\xxx.img \\
                     --response out\\real_response.json \\
                     --url http://192.168.137.1:14514/xxx.img \\
                     --out out\\patched_response.json

  # 只想看算出来的值，不写文件：
  python ota_meta.py --img ... --response ... --url ... --dry-run
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time

import _console  # noqa: F401  (把控制台切到 UTF-8)

BLOCK = 4 * 1024 * 1024


def find_segment_md5(obj) -> tuple[dict, str] | tuple[None, None]:
    """在响应 JSON 里找到 version 对象和 segmentMd5 字符串。"""
    data = obj.get("data", obj)
    ver = data.get("version") if isinstance(data, dict) else None
    if isinstance(ver, dict) and "segmentMd5" in ver:
        return ver, "data.version.segmentMd5"
    # 兜底：递归找第一个带 segmentMd5 的 dict
    stack = [obj]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            if "segmentMd5" in cur:
                return cur, "?(递归找到)"
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)
    return None, None


def compute_hashes(img: str, endpos: list[int]) -> dict:
    """一趟读盘：整包 md5/sha1/sha256 + 每个分片 md5。"""
    total_md5 = hashlib.md5()
    total_sha1 = hashlib.sha1()
    total_sha256 = hashlib.sha256()
    seg_md5: list[str] = []
    seg_idx = 0
    seg_hasher = hashlib.md5()
    seg_end = endpos[0] if endpos else None
    pos = 0
    size = os.path.getsize(img)
    t0 = time.time()
    report_at = 0
    with open(img, "rb") as f:
        while True:
            buf = f.read(BLOCK)
            if not buf:
                break
            view = memoryview(buf)
            total_md5.update(view)
            total_sha1.update(view)
            total_sha256.update(view)
            if endpos:
                off = 0
                while off < len(buf):
                    if seg_end is None:
                        break
                    take = min(len(buf) - off, seg_end - (pos + off))
                    if take <= 0:
                        seg_md5.append(seg_hasher.hexdigest())
                        seg_idx += 1
                        seg_hasher = hashlib.md5()
                        seg_end = endpos[seg_idx] if seg_idx < len(endpos) else None
                        continue
                    seg_hasher.update(view[off : off + take])
                    off += take
            pos += len(buf)
            if time.time() - report_at > 5:
                report_at = time.time()
                pct = pos / size * 100 if size else 100
                print(f"    … {pct:5.1f}%  ({pos/1048576:.0f}/{size/1048576:.0f} MiB)", flush=True)
        if endpos and seg_end is not None:
            seg_md5.append(seg_hasher.hexdigest())
    if endpos and len(seg_md5) != len(endpos):
        print(f"[!] 分片数不一致: 算出 {len(seg_md5)}，endpos {len(endpos)}（末尾可能有空洞，已按实际填充）")
    return {
        "md5": total_md5.hexdigest(),
        "sha1": total_sha1.hexdigest(),
        "sha256": total_sha256.hexdigest(),
        "segment_md5": seg_md5,
        "size": size,
        "seconds": round(time.time() - t0, 1),
    }


def update_fields(obj: dict, img_url: str, hashes: dict, segments: list[dict], force_version: str | None) -> list[str]:
    """递归替换 JSON 里所有需要改的字段，返回改动清单。"""
    changes: list[str] = []

    def walk(node, path=""):
        if isinstance(node, dict):
            for k in list(node.keys()):
                v = node[k]
                p = f"{path}.{k}" if path else k
                kl = k.lower()
                if isinstance(v, str):
                    if kl == "md5sum" and len(v) == 32:
                        node[k] = hashes["md5"]
                        changes.append(f"{p}: {v} -> {hashes['md5']}")
                    elif kl == "sha256" and len(v) in (32, 64):
                        node[k] = hashes["sha256"]
                        changes.append(f"{p}: {v} -> {hashes['sha256']}")
                    elif kl == "sha" and len(v) in (40, 64):
                        new = hashes["sha1"] if len(v) == 40 else hashes["sha256"]
                        node[k] = new
                        changes.append(f"{p}: {v} -> {new}")
                    elif kl in ("bakurl", "deltaurl", "url"):
                        if isinstance(v, str) and re.match(r"^https?://", v):
                            node[k] = img_url
                            changes.append(f"{p}: {v} -> {img_url}")
                    elif kl == "storagesize":
                        node[k] = str(hashes["size"])
                        changes.append(f"{p}: {v} -> {hashes['size']}")
                    elif kl == "segmentmd5":
                        node[k] = segments_text(segments, v)
                        changes.append(f"{p}: 已按新分片 MD5 重写")
                elif isinstance(v, int) and kl == "filesize":
                    node[k] = hashes["size"]
                    changes.append(f"{p}: {v} -> {hashes['size']}")
                else:
                    walk(v, p)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")

    walk(obj)

    if force_version:
        data = obj.get("data", obj)
        rn = data.get("releaseNotes") if isinstance(data, dict) else None
        if isinstance(rn, dict) and "version" in rn:
            changes.append(f"data.releaseNotes.version: {rn['version']} -> {force_version}")
            rn["version"] = force_version
        ver = data.get("version") if isinstance(data, dict) else None
        if isinstance(ver, dict) and "versionName" in ver:
            changes.append(f"data.version.versionName: {ver['versionName']} -> {force_version}")
            ver["versionName"] = force_version
    return changes


def segments_text(segments: list[dict], original: str) -> str:
    """按原字符串风格（紧凑/带空格）序列化分片数组。"""
    spaced = bool(re.search(r"[:,]\s", original))
    if spaced:
        return json.dumps(segments, ensure_ascii=False)
    return json.dumps(segments, ensure_ascii=False, separators=(",", ":"))


def main() -> int:
    ap = argparse.ArgumentParser(description="生成伪造 OTA 响应 JSON")
    ap.add_argument("--img", required=True)
    ap.add_argument("--response", required=True, help="官方 checkVersion 的真实响应 JSON（抓包/转发的原始返回）")
    ap.add_argument("--url", required=True, help="本机固件直链，例如 http://192.168.137.1:14514/xxx.img")
    ap.add_argument("--out", default=None)
    ap.add_argument("--force-version", default="99.99.91", help="写进响应的假版本号，默认 99.99.91（教程做法）；传 empty 表示不改")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not os.path.isfile(args.img):
        print(f"[!] 固件不存在: {args.img}")
        return 1
    with open(args.response, "r", encoding="utf-8") as f:
        raw = f.read()
    obj = json.loads(raw)
    ver, where = find_segment_md5(obj)
    if ver is None:
        print("[!] 官方响应里没找到 segmentMd5，无法计算分片。先看 out/real_response.json 结构。")
        return 2

    seg_src = ver["segmentMd5"]
    try:
        seg_list = json.loads(seg_src) if isinstance(seg_src, str) else seg_src
    except json.JSONDecodeError as e:
        print(f"[!] segmentMd5 解析失败: {e}")
        return 2
    endpos = [int(s["endpos"]) for s in seg_list]
    print(f"[*] 分片数={len(endpos)}  末尾={endpos[-1]:,}  固件大小={os.path.getsize(args.img):,}")
    print(f"[*] 正在计算哈希（约 {os.path.getsize(args.img)/1048576:.0f} MiB，顺序读一遍）…")
    hashes = compute_hashes(args.img, endpos)
    print(f"[*] 整包 MD5    = {hashes['md5']}")
    print(f"[*] 整包 SHA1   = {hashes['sha1']}")
    print(f"[*] 整包 SHA256 = {hashes['sha256']}")
    for i, h in enumerate(hashes["segment_md5"], 1):
        print(f"    分片 {i:>3}: {h}")

    new_segments = []
    for i, s in enumerate(seg_list):
        ns = dict(s)
        if i < len(hashes["segment_md5"]):
            ns["md5"] = hashes["segment_md5"][i]
        new_segments.append(ns)

    force = None if args.force_version.lower() in ("", "empty", "none") else args.force_version
    changes = update_fields(obj, args.url, hashes, new_segments, force)
    obj["status"] = 1000
    if "msg" in obj:
        obj["msg"] = "success"

    print("[*] 字段改动：")
    for c in changes:
        print("    " + c)

    out = args.out or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "out", "patched_response.json")
    out = os.path.abspath(out)
    if args.dry_run:
        print("[*] --dry-run，未写文件。")
        return 0
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    # 同时留一份计算结果，方便排查
    with open(os.path.join(os.path.dirname(out), "hashes.json"), "w", encoding="utf-8") as f:
        json.dump({"img": os.path.abspath(args.img), "url": args.url, **hashes}, f, ensure_ascii=False, indent=2)
    print(f"[*] 伪造响应已写入: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
