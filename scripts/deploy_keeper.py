#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""deploy_keeper.py —— 一键把 sideload-keeper 部署到词典笔（幂等，可反复运行）

做四件事：
  1) 部署/更新 /userdisk/skip_re/skip_login.sh（开机钩子会拉起它）
  2) 把指定 .amr 放进 /userdisk/skip_re/amr/（keeper 据此保活 + 数据镜像）
  3) 停掉旧实例（用 pid 文件，**不用 pkill -f**）并启动新实例
  4) 打印状态：进程 / 镜像目录 / 日志

用法:
    python deploy_keeper.py                       # 默认部署终端 + 文件管理器
    python deploy_keeper.py --amr a.amr b.amr     # 指定要保活的包
    python deploy_keeper.py --restart-only        # 只重启 keeper
"""
import argparse
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADB = os.path.join(ROOT, "tools", "platform-tools", "adb.exe")
SERIAL = os.environ.get("PEN_SERIAL", "192.168.137.153:5555")
KEEPER_SRC = os.path.join(ROOT, "tools", "sideload-keeper.sh")
KEEPER_DST = "/userdisk/skip_re/skip_login.sh"
AMRDIR = "/userdisk/skip_re/amr"
DEFAULT_AMRS = [
    os.path.join(ROOT, "miniapps", "dist", "terminal-9.2.0.amr"),
    os.path.join(ROOT, "miniapps", "dist", "file-manager-1.2.0-upload.amr"),
]


def adb(*args, timeout=120):
    p = subprocess.run([ADB, "-s", SERIAL] + list(args), capture_output=True,
                       text=True, encoding="utf-8", errors="ignore", timeout=timeout)
    return (p.stdout or "") + (p.stderr or "")


def sh(cmd, timeout=120):
    return adb("shell", cmd, timeout=timeout)


def ensure_auth():
    out = sh("uptime")
    if "login" in out:
        print("[i] 需要 ADB 认证…")
        p = subprocess.run([ADB, "-s", SERIAL, "shell", "auth"], input="ydpen2026\n",
                           capture_output=True, text=True, encoding="utf-8",
                           errors="ignore", timeout=60)
        out = (p.stdout or "") + (p.stderr or "")
        if "success" not in out:
            print("[!] 认证失败：%s" % out.strip()[:200])
            return False
        print("[+] 认证成功")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--amr", nargs="*", help="要保活的 .amr（默认：终端 + 文件管理器）")
    ap.add_argument("--restart-only", action="store_true", help="只重启 keeper，不重传文件")
    a = ap.parse_args()

    print("[1/5] 连接 %s" % SERIAL)
    print("      " + adb("connect", SERIAL).strip().splitlines()[-1])
    time.sleep(2)
    if not ensure_auth():
        return 1

    if not a.restart_only:
        print("[2/5] 部署 keeper 脚本 → %s" % KEEPER_DST)
        txt = open(KEEPER_SRC, encoding="utf-8").read().replace("\r\n", "\n")
        tmp = os.path.join(ROOT, "tmp", "skip_login.sh")
        os.makedirs(os.path.dirname(tmp), exist_ok=True)
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(txt)
        sh("mkdir -p /userdisk/skip_re/amr")
        print("      " + adb("push", tmp, KEEPER_DST).strip().splitlines()[-1])
        r = sh("chmod +x %s; sh -n %s && echo SYNTAX_OK" % (KEEPER_DST, KEEPER_DST))
        print("      语法检查：%s" % ("OK" if "SYNTAX_OK" in r else "失败 → " + r.strip()[:160]))
        if "SYNTAX_OK" not in r:
            return 1

        print("[3/5] 放入要保活的 .amr")
        amrs = a.amr or [p for p in DEFAULT_AMRS if os.path.isfile(p)]
        if not amrs:
            print("[!] 找不到任何 .amr")
            return 1
        for p in amrs:
            if not os.path.isfile(p):
                print("      跳过（不存在）：%s" % p)
                continue
            # 统一成短名字，便于在笔上辨认；appid 由 keeper 自己从包内 manifest.json 解析
            base = os.path.basename(p)
            if base.startswith("terminal-"):
                name = "PenTerm.amr"
            elif base.startswith("file-manager"):
                name = "FileManager.amr"
            else:
                name = base
            print("      " + adb("push", p, "%s/%s" % (AMRDIR, name)).strip().splitlines()[-1])
    else:
        print("[2/5][3/5] 跳过（--restart-only）")

    print("[4/5] 重启 keeper（按 pid 停，避免 pkill -f 误杀 adb 会话）")
    sh("P=$(cat /tmp/sideload-keeper.pid 2>/dev/null); "
       "[ -n \"$P\" ] && kill $P 2>/dev/null; "
       "for p in $(ps | grep skip_login | grep -v grep | awk '{print $1}'); do "
       "[ \"$p\" != \"$$\" ] && kill -9 $p 2>/dev/null; done; "
       "rm -f /tmp/sideload-keeper.pid; sleep 2; "
       "nohup sh %s >/dev/null 2>&1 & sleep 4; echo started" % KEEPER_DST)

    print("[5/5] 状态")
    print(sh("echo '--- 进程 ---'; ps | grep skip_login | grep -v grep; "
             "echo '--- pid ---'; cat /tmp/sideload-keeper.pid 2>/dev/null; "
             "echo '--- amr 目录 ---'; ls -la %s; "
             "echo '--- 数据镜像 ---'; ls %s/data-backup 2>/dev/null; "
             "echo '--- 日志 ---'; tail -12 /userdisk/skip_re/sideload-keeper.log 2>/dev/null"
             % (AMRDIR, "/userdisk/skip_re")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
