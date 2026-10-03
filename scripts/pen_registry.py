#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pen_registry.py —— 有道词典笔「应用注册表」巡检 / 备份 / 修复

背景：桌面显示哪些应用，取决于**注册表**（不是包在不在盘上）：

    /userdata/miniapp/data/mini_app/pkg/packages.json
    {"packages":[{appid,b,category,flag,icon,installPath,name,packageDir,props,version}, ...]}

应用包本身在（`installPath` 指向的目录），而笔自带应用的 `installPath` 也可能在
`/userdisk/secondary/miniapp/...`，所以跨根是正常的。
**如果某次安装/卸载被中断（或反复 uninstall/install），注册表条目可能丢 → 重启后桌面就不显示该应用**
（框架日志里表现为 `pm name=<名字> type=removed`）。这个工具就是应对这种情况的。

用法:
    python pen_registry.py check              # 巡检：注册表条目 + 盘上未注册的包
    python pen_registry.py backup             # 备份注册表（笔上 /userdisk + 本地 out/registry/）
    python pen_registry.py fix                # 把盘上未注册的包补进注册表（自动先备份）
    python pen_registry.py restore <本地备份>  # 用备份覆盖回去（自动先备份当前）

环境：需要 adb 可用、笔已 root、且已 `adb shell auth` 通过。
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADB = os.path.join(ROOT, "tools", "platform-tools", "adb.exe")
SERIAL = os.environ.get("PEN_SERIAL", "192.168.137.153:5555")
REG = "/userdata/miniapp/data/mini_app/pkg/packages.json"
BACKUP_DIR = "/userdisk/pen-registry-backup"
ROOTS = [
    "/userdata/miniapp/data/mini_app/pkg",
    "/userdisk/secondary/miniapp/data/mini_app/pkg",
    "/userdisk/miniapp/data/mini_app/pkg",
]


def adb(*args, stdin=None):
    cmd = [ADB, "-s", SERIAL] + list(args)
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore",
                       input=stdin, timeout=180)
    return p.returncode, (p.stdout or ""), (p.stderr or "")


def sh(script):
    return adb("shell", script)


SCAN = r'''
for r in %s; do
  for d in $r/*/; do
    id=$(basename "$d")
    case "$id" in packages.json|*.*) continue;; esac
    for s in a b; do
      m="$d$s/manifest.json"
      if [ -f "$m" ]; then
        nm=$(sed -n 's/.*"appName"[ ]*:[ ]*"\([^"]*\)".*/\1/p' "$m" | head -1)
        vs=$(sed -n 's/.*"version"[ ]*:[ ]*"\([^"]*\)".*/\1/p' "$m" | head -1)
        echo "$id|$s|$nm|$vs|$d$s/"
      fi
    done
  done
done
''' % " ".join(ROOTS)


def load_registry():
    rc, out, err = adb("shell", "cat " + REG)
    if rc != 0 or not out.strip():
        print("[!] 读不到注册表：%s %s" % (err.strip(), out.strip()[:120]))
        sys.exit(1)
    return json.loads(out)


def scan_disk():
    rc, out, err = sh(SCAN)
    found = []
    for line in out.splitlines():
        parts = line.strip().split("|")
        if len(parts) == 5 and re.fullmatch(r"\d{10,20}", parts[0]):
            found.append({"appid": parts[0], "slot": parts[1], "name": parts[2],
                          "version": parts[3], "installPath": parts[4]})
    return found


def backup(tag="manual"):
    name = "packages.json.%s.%s" % (time.strftime("%Y%m%d-%H%M%S"), tag)
    sh("mkdir -p %s; cp %s %s/%s" % (BACKUP_DIR, REG, BACKUP_DIR, name))
    local = os.path.join(ROOT, "out", "registry")
    os.makedirs(local, exist_ok=True)
    rc, out, _ = adb("shell", "cat " + REG)
    with open(os.path.join(local, name), "w", encoding="utf-8") as f:
        f.write(out)
    print("[+] 备份：笔上 %s/%s ；本地 out/registry/%s" % (BACKUP_DIR, name, name))
    return name


def cmd_check():
    reg = load_registry()
    have = {p["appid"]: p for p in reg.get("packages", [])}
    disk = {d["appid"]: d for d in scan_disk()}
    print("注册表条目：%d 个    盘上包：%d 个" % (len(have), len(disk)))
    missing = [d for a, d in disk.items() if a not in have]
    ghost = [p for a, p in have.items() if a not in disk]
    print("\n== 盘上有、注册表没有（重启后会不显示）==")
    for d in missing:
        print("   %s  %-16s v%-8s %s" % (d["appid"], d["name"], d["version"], d["installPath"]))
    if not missing:
        print("   （无）")
    print("\n== 注册表有、盘上没有（条目失效）==")
    for p in ghost:
        print("   %s  %-16s %s" % (p["appid"], p.get("name"), p.get("installPath")))
    if not ghost:
        print("   （无）")
    print("\n== 注册表里的第三方/自装应用（非笔自带 8001/8080 前缀以外也列出）==")
    for p in reg.get("packages", []):
        print("   %s  %-18s v%-8s %s" % (p["appid"], p.get("name"), p.get("version"),
                                         p.get("installPath")))
    return 0


def write_back(reg):
    tmp = os.path.join(ROOT, "tmp", "packages.fixed.json")
    os.makedirs(os.path.dirname(tmp), exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(reg, f, ensure_ascii=False)
    adb("push", tmp, "/tmp/packages.fixed.json")
    rc, out, err = sh("cp /tmp/packages.fixed.json %s && chown root:root %s && chmod 644 %s && "
                      "rm -f /tmp/packages.fixed.json && echo OK" % (REG, REG, REG))
    ok = "OK" in out
    print("[%s] 写回注册表%s" % ("✓" if ok else "!", "" if ok else "：" + (err or out).strip()[:120]))
    return ok


def cmd_normalize(appid=None):
    """把侧载条目（flag 含 16384）改成商店应用那种 flag=0 —— 应用更新器(APP_UPD)开机校验时
    对 flag=16384 的条目会去商店查，查不到就 rollingBack 移除；flag=0 的不会被动。"""
    reg = load_registry()
    changed = []
    for p in reg.get("packages", []):
        if appid and p.get("appid") != appid:
            continue
        if int(p.get("flag", 0)) & 16384:
            p["flag"] = 0
            changed.append("%s(%s)" % (p["appid"], p.get("name")))
    if not changed:
        print("[i] 没有需要归一化的条目（flag 里没有 16384）")
        return 0
    backup("before-normalize")
    print("[i] 将归一化 %d 个条目：%s" % (len(changed), ", ".join(changed)))
    return 0 if write_back(reg) else 1


def cmd_fix():
    reg = load_registry()
    have = {p["appid"] for p in reg.get("packages", [])}
    missing = [d for d in scan_disk() if d["appid"] not in have]
    if not missing:
        print("[i] 没有需要补的包")
        return 0
    backup("before-fix")
    for d in missing:
        entry = {
            "appid": d["appid"], "b": (d["slot"] == "b"), "category": "", "flag": 0,
            "icon": "app_icon.png", "installPath": d["installPath"],
            "name": d["name"] or d["appid"], "packageDir": os.path.dirname(d["installPath"].rstrip("/")) + "/",
            "props": None, "version": d["version"] or "0.0.0",
        }
        reg["packages"].append(entry)
        print("[+] 补入 %s %s" % (d["appid"], entry["name"]))
    return 0 if write_back(reg) else 1


def cmd_backup():
    backup("manual")
    return 0


def cmd_restore(path):
    backup("before-restore")
    adb("push", path, "/tmp/packages.restore.json")
    rc, out, err = sh("cp /tmp/packages.restore.json %s && chown root:root %s && chmod 644 %s && "
                      "rm -f /tmp/packages.restore.json && echo OK" % (REG, REG, REG))
    print("[%s] 恢复完成：%s" % ("✓" if "OK" in out else "!", (out or err).strip()[:120]))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["check", "backup", "fix", "normalize", "restore"])
    ap.add_argument("path", nargs="?")
    ap.add_argument("--appid", help="只处理某个 appid")
    a = ap.parse_args()
    if a.cmd == "check":
        return cmd_check()
    if a.cmd == "backup":
        return cmd_backup()
    if a.cmd == "fix":
        return cmd_fix()
    if a.cmd == "normalize":
        return cmd_normalize(a.appid)
    if a.cmd == "restore":
        if not a.path:
            print("用法：pen_registry.py restore <本地备份文件>")
            return 2
        return cmd_restore(a.path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
