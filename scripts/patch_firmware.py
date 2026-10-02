#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
patch_firmware.py —— 有道词典笔固件 adb 鉴权哈希“等长原地替换”工具

原理
----
词典笔 OTA 全量包 (.img) 里 /usr/bin/adb_auth.sh (某些固件叫 adbd_auth.sh) 负责
校验 ADB 密码，形如：

    PASSWD=$(echo -n ${PASSWD} | sha256sum - | awk '{print $1}')
    if [ "$PASSWD" = "$(tail -n 1 /usr/bin/adb_auth.sh | awk -F# '{print $2}' | awk '{print $1}')" ]
    ...
    # 9de0341eb0ac432ecf39b72a0ddf4ac9a5dfb01828c0728dee474a573810a51f -

把最后那个哈希换成“我们自己密码的哈希”即可，且长度完全一样，
所以文件大小、偏移、其它分区都不受影响。

本工具做的事
-----------
1. 顺序扫描 img，定位 adb_auth.sh / adbd_auth.sh / .adb_auth_verified 等特征串；
2. 抽出脚本的可打印文本，自动判断：
     - 算法：脚本里出现 sha256sum -> sha256；出现 md5sum -> md5；
     - 换行：脚本里出现 `echo -n` -> 不加换行；否则 echo 会补一个 \n（教程里最坑的点）；
3. 计算新密码哈希，**等长原地写入**（不改变文件大小）；
4. 复查：新哈希已出现、旧哈希已消失、文件大小一致，并写 patch_report.json。

用法
----
  # 1) 先只看，不改：打印识别到的脚本、算法、原哈希
  python patch_firmware.py scan  ..\\firmware\\xxx.img

  # 2) 改成新密码（原地修改，同长度覆盖）
  python patch_firmware.py patch ..\\firmware\\xxx.img --password mypass123

  # 3) 想保留原始包时先自己复制一份，或加 --copy-to D:\\orig.img（需要额外磁盘空间）

可选参数
--------
  --algo auto|md5|sha256     强制算法（默认 auto）
  --newline auto|yes|no      密码后是否补 \n（默认 auto，按脚本里的 echo -n 判断）
  --old-hash <hex>           手动指定要替换的原哈希（32/64 位十六进制）
  --dump-script DIR          把所有抽到的脚本文本保存到 DIR，便于人工核对
  --dry-run                  只计算不写入
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

MARKERS = [
    b"adbd_auth.sh",
    b"adb_auth.sh",
    b".adb_auth_verified",
    b"password incorrect!",
    b"password incorrect",
]

PRINTABLE = set(range(32, 127)) | {9, 10, 13}
HEX_RE = re.compile(rb"(?<![0-9a-fA-F])([0-9a-fA-F]{32}|[0-9a-fA-F]{64})(?![0-9a-fA-F])")

CHUNK = 8 * 1024 * 1024


# ----------------------------------------------------------------------------- 扫描
def _read_at(f, off: int, size: int) -> bytes:
    f.seek(off)
    return f.read(size)


def printable_run(f, pos: int, max_len: int = 65536, step: int = 8192) -> tuple[bytes, int]:
    """从 pos 向左右扩展，返回包含 pos 的最长可打印 ASCII 连续段及其绝对起始偏移。"""
    f.seek(0, os.SEEK_END)
    fsize = f.tell()
    start = max(0, pos - step)
    end = min(fsize, pos + step)
    buf = _read_at(f, start, end - start)
    i = pos - start
    j = pos - start
    while True:
        moved = False
        while i > 0 and buf[i - 1] in PRINTABLE:
            i -= 1
        while j < len(buf) and buf[j] in PRINTABLE:
            j += 1
        if i == 0 and start > 0 and len(buf) < max_len:
            new_start = max(0, start - step)
            buf = _read_at(f, new_start, end - new_start)
            j += start - new_start
            i = start - new_start
            start = new_start
            moved = True
        if j == len(buf) and end < fsize and len(buf) < max_len:
            new_end = min(fsize, end + step)
            buf = buf + _read_at(f, end, new_end - end)
            end = new_end
            moved = True
        if not moved:
            break
    return buf[i:j], start + i


def analyze_script(text: bytes) -> dict:
    """从脚本文本中推断算法 / 换行 / 候选哈希。"""
    low = text.lower()
    algo = None
    if b"sha256sum" in low or b"sha256" in low:
        algo = "sha256"
    elif b"md5sum" in low or b"md5" in low:
        algo = "md5"

    # echo -n -> 不追加换行；否则 echo 会追加 \n
    if re.search(rb"echo\s+-n", low):
        newline = False
        newline_reason = "脚本里出现 `echo -n`，密码后不补换行"
    elif re.search(rb"\becho\b", low):
        newline = True
        newline_reason = "脚本里是普通 `echo $PASSWD`，会追加一个 \\n 参与哈希"
    else:
        newline = True
        newline_reason = "未发现 echo 用法，按教程默认补一个 \\n（可用 --newline 覆盖）"

    candidates = []
    for m in HEX_RE.finditer(text):
        h = m.group(1).decode("ascii")
        candidates.append(
            {
                "hash": h.lower(),
                "offset": m.start(),
                "algo": "md5" if len(h) == 32 else "sha256",
            }
        )
    return {"algo": algo, "newline": newline, "newline_reason": newline_reason, "candidates": candidates}


def scan_file(path: str) -> list[dict]:
    """扫描固件，返回所有识别到的鉴权脚本条目。"""
    findings: list[dict] = []
    seen_ranges: list[tuple[int, int]] = []
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        tail = b""
        base = 0
        while True:
            block = f.read(CHUNK)
            if not block:
                break
            buf = tail + block
            buf_base = base - len(tail)
            for marker in MARKERS:
                start = 0
                while True:
                    k = buf.find(marker, start)
                    if k < 0:
                        break
                    start = k + 1
                    abs_pos = buf_base + k
                    # 已在这个脚本范围内就跳过
                    if any(a <= abs_pos < b for a, b in seen_ranges):
                        continue
                    text, text_off = printable_run(f, abs_pos)
                    if not text:
                        continue
                    seen_ranges.append((text_off, text_off + len(text)))
                    info = analyze_script(text)
                    findings.append(
                        {
                            "marker": marker.decode(),
                            "marker_offset": abs_pos,
                            "script_offset": text_off,
                            "script_len": len(text),
                            "algo": info["algo"],
                            "newline": info["newline"],
                            "newline_reason": info["newline_reason"],
                            "candidates": info["candidates"],
                            "script_text": text.decode("utf-8", "replace"),
                        }
                    )
            base += len(block)
            tail = buf[-64:]
            if base >= size:
                break
    return findings


def pick_target(finding: dict, algo_override: str | None, old_hash: str | None) -> dict | None:
    """在脚本里挑出真正被比对的那个哈希，并确定算法。"""
    algo = algo_override or finding["algo"]
    cands = finding["candidates"]
    if old_hash:
        for c in cands:
            if c["hash"] == old_hash.lower():
                return {"algo": "md5" if len(old_hash) == 32 else "sha256", **c}
        return None
    if algo:
        same = [c for c in cands if c["algo"] == algo]
        if same:
            # 取脚本里最后一个（教程中的写法是放在文件末尾注释里）
            return {"algo": algo, **same[-1]}
    if cands:
        last = cands[-1]
        return {"algo": last["algo"], **last}
    return None


def new_hash_for(password: str, algo: str, newline: bool) -> str:
    data = password.encode("utf-8") + (b"\n" if newline else b"")
    h = hashlib.new(algo)
    h.update(data)
    return h.hexdigest()


# ----------------------------------------------------------------------------- 替换
def find_all_occurrences(path: str, needle: bytes) -> list[int]:
    """返回 needle 在文件中的所有偏移（顺序扫描，支持跨块匹配）。"""
    if not needle:
        return []
    offs: list[int] = []
    overlap = len(needle) - 1
    with open(path, "rb") as f:
        base = 0
        tail = b""
        while True:
            block = f.read(CHUNK)
            if not block:
                break
            buf = tail + block
            buf_base = base - len(tail)
            start = 0
            while True:
                k = buf.find(needle, start)
                if k < 0:
                    break
                offs.append(buf_base + k)
                start = k + 1
            base += len(block)
            tail = buf[-overlap:] if overlap > 0 else b""
            if len(block) < CHUNK:
                break
    return offs


def replace_at(path: str, offsets: list[int], data: bytes) -> None:
    with open(path, "r+b") as f:
        for off in offsets:
            f.seek(off)
            f.write(data)
        f.flush()
        os.fsync(f.fileno())


# ----------------------------------------------------------------------------- 命令
def cmd_scan(args) -> int:
    t0 = time.time()
    print(f"[*] 扫描 {args.img} ({os.path.getsize(args.img):,} 字节) …")
    findings = scan_file(args.img)
    if not findings:
        print("[!] 没找到 adb_auth.sh / adbd_auth.sh 等特征串。")
        print("    可能原因：固件已加密、是 hdiffpatch 差分补丁、或鉴权脚本改了名字。")
        print("    建议：把整包交给 https://github.com/PenUniverse 群友或人工 binwalk 解包确认。")
        return 2
    report = []
    for i, fd in enumerate(findings, 1):
        tgt = pick_target(fd, args.algo, args.old_hash)
        print("=" * 78)
        print(f"[{i}] 特征串 {fd['marker']} @ 0x{fd['marker_offset']:x}")
        print(f"    脚本范围 0x{fd['script_offset']:x} .. 0x{fd['script_offset']+fd['script_len']:x}")
        print(f"    算法={fd['algo']}  换行={fd['newline']}  ({fd['newline_reason']})")
        print(f"    候选哈希: {[c['hash'] for c in fd['candidates']] or '（无）'}")
        print(f"    选定哈希: {tgt['hash'] if tgt else '（无）'}  @ 0x{tgt['offset'] + fd['script_offset']:x}" if tgt else "    选定哈希: （无）")
        print("-" * 78)
        print(fd["script_text"][:2000])
        report.append({k: v for k, v in fd.items() if k != "script_text"})
        if args.dump_script:
            os.makedirs(args.dump_script, exist_ok=True)
            p = os.path.join(args.dump_script, f"script_{i}_{fd['marker_offset']:x}.sh")
            with open(p, "w", encoding="utf-8") as fh:
                fh.write(fd["script_text"])
            print(f"    脚本已保存: {p}")
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
        print(f"[*] 报告: {args.json_out}")
    print(f"[*] 完成，用时 {time.time()-t0:.1f}s")
    return 0


def cmd_patch(args) -> int:
    if not args.password:
        print("[!] patch 需要 --password")
        return 1
    size_before = os.path.getsize(args.img)
    findings = scan_file(args.img)
    if not findings:
        print("[!] 没找到鉴权脚本，无法 patch。先跑 scan 看看。")
        return 2

    plan = []
    for fd in findings:
        tgt = pick_target(fd, args.algo, args.old_hash)
        if not tgt:
            print(f"[!] 跳过 {fd['marker']} @0x{fd['marker_offset']:x}：没找到候选哈希")
            continue
        newline = fd["newline"]
        if args.newline != "auto":
            newline = args.newline == "yes"
        nh = new_hash_for(args.password, tgt["algo"], newline)
        if nh.lower() == tgt["hash"]:
            print(f"[=] {tgt['hash']} 已经是新密码的哈希，跳过")
            continue
        abs_off = fd["script_offset"] + tgt["offset"]
        plan.append(
            {
                "marker": fd["marker"],
                "marker_offset": fd["marker_offset"],
                "script_offset": fd["script_offset"],
                "hash_offset": abs_off,
                "algo": tgt["algo"],
                "newline": newline,
                "old_hash": tgt["hash"],
                "new_hash": nh,
            }
        )

    if not plan:
        print("[!] 没有需要替换的哈希。")
        return 3

    # 去重（同一 old_hash 只替换一次）
    uniq: dict[str, dict] = {}
    for p in plan:
        uniq.setdefault(p["old_hash"], p)
    plan = list(uniq.values())

    print("[*] 替换计划：")
    for p in plan:
        print(
            f"    {p['old_hash']}  ->  {p['new_hash']}"
            f"   ({p['algo']}, {'带\\n' if p['newline'] else '不带\\n'}, @0x{p['hash_offset']:x})"
        )
    if args.newline == "auto":
        print("[i] 换行判定：密码 + \\n 参与哈希的那些脚本已按脚本里的 echo 用法自动决定。")
    if args.dry_run:
        print("[*] --dry-run，未写入。")
        return 0

    for p in plan:
        old = p["old_hash"].encode()
        new = p["new_hash"].encode()
        assert len(old) == len(new)
        offs = find_all_occurrences(args.img, old)
        if not offs:
            print(f"[!] 找不到 {p['old_hash']} 的原始字节，跳过")
            continue
        if p["hash_offset"] not in offs:
            print(f"[i] 额外发现 {len(offs)-1} 处相同哈希，一并替换（通常是同一脚本的副本/校验文件）")
        replace_at(args.img, offs, new)
        p["replaced_offsets"] = [hex(o) for o in offs]
        print(f"[+] 已替换 {p['old_hash']} -> {p['new_hash']}，共 {len(offs)} 处")

    size_after = os.path.getsize(args.img)
    if size_before != size_after:
        print(f"[!!] 文件大小变了！{size_before} -> {size_after}（不应该发生，请立刻用备份恢复）")
        return 4

    # 复查
    print("[*] 复查 …")
    after = scan_file(args.img)
    ok = True
    for p in plan:
        still_old = bool(find_all_occurrences(args.img, p["old_hash"].encode()))
        has_new = bool(find_all_occurrences(args.img, p["new_hash"].encode()))
        print(f"    旧哈希残留={still_old}  新哈希存在={has_new}")
        ok = ok and has_new and not still_old
        p["verified"] = bool(has_new and not still_old)

    report = {
        "img": os.path.abspath(args.img),
        "size": size_after,
        "password_len": len(args.password),
        "patched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "entries": plan,
        "findings": [{k: v for k, v in fd.items() if k != "script_text"} for fd in after],
        "verified": ok,
    }
    out = args.report or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "out", "patch_report.json")
    out = os.path.abspath(out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    print(f"[*] patch 报告: {out}")
    print(f"[*] 大小不变: {size_before:,} 字节" if size_before == size_after else "[!] 大小异常")
    return 0 if ok else 5


def main() -> int:
    ap = argparse.ArgumentParser(description="有道词典笔固件 adb 鉴权哈希等长替换")
    sub = ap.add_subparsers(dest="cmd", required=True)

    for name in ("scan", "patch"):
        p = sub.add_parser(name)
        p.add_argument("img")
        p.add_argument("--password", default=None)
        p.add_argument("--algo", choices=["auto", "md5", "sha256"], default="auto")
        p.add_argument("--newline", choices=["auto", "yes", "no"], default="auto")
        p.add_argument("--old-hash", default=None)
        p.add_argument("--dump-script", default=None)
        p.add_argument("--json-out", default=None)
        p.add_argument("--report", default=None)
        p.add_argument("--dry-run", action="store_true")
        p.set_defaults(func=cmd_scan if name == "scan" else cmd_patch)

    args = ap.parse_args()
    if getattr(args, "algo", "auto") == "auto":
        args.algo = None
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
