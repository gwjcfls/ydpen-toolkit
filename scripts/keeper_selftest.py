#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""keeper_t2_setup.py —— 用 Python 造测试夹具（真 amr = zip），再用 busybox 跑 keeper 自测

验证 keeper 能做到：
  ① 从真 amr 的 manifest.json 解析 appid（.appid 文件故意写坏）
  ② 应用健在时建立 data/ 硬链接镜像
  ③ 包被 APP_UPD 连数据一起删后，装回并恢复 data/（逐字节一致）
"""
import io
import os
import shutil
import subprocess
import sys
import zipfile

ROOT = r"C:\Users\Sever\Documents\deepseek-harness\default-workspace\ydpen-adb"
TMP = os.path.join(ROOT, "tmp")
BB = os.path.join(ROOT, "tools", "busybox", "busybox.exe")
KEEPER = os.path.join(TMP, "keeper_lf.sh")
W = os.path.join(TMP, "kt2").replace("\\", "/")
APPID = "8001999000000001"


def sh(cmd, env=None):
    e = dict(os.environ)
    if env:
        e.update(env)
    p = subprocess.run([BB, "sh", "-c", cmd], capture_output=True, text=True,
                       encoding="utf-8", errors="ignore", env=e)
    return (p.stdout or "") + (p.stderr or "")


def main():
    shutil.rmtree(W, ignore_errors=True)
    for d in ("amr", "pkg/%s/a" % APPID, "pkg/%s/data/resource" % APPID, "zip"):
        os.makedirs(os.path.join(W, d), exist_ok=True)

    # ---- 造真 amr（zip，含 manifest.json）----
    amr = os.path.join(W, "amr", "PenTerm.amr")
    with zipfile.ZipFile(amr, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json",
                   '{"appName":"PenTerm","version":"9.2.0","appid":"%s","icon":"app_icon.png"}' % APPID)
        z.writestr("app.js.bin", b"dummy")
    # ★ 故意写坏的 .appid（整段 JSON）—— 旧版脚本会拿它当 appid 用
    io.open(os.path.join(W, "amr", "PenTerm.appid"), "w", encoding="utf-8").write(
        '{"appid":"%s","appName":"PenTerm"}' % APPID)

    # ---- 应用现有状态：包在、且有宝贵数据 ----
    io.open(os.path.join(W, "pkg", APPID, "a", "manifest.json"), "w").write('{"appid":"%s"}' % APPID)
    io.open(os.path.join(W, "pkg", APPID, "data", "user.txt"), "w").write("PRECIOUS-DATA")
    big = "\n".join("line-%d-abcdefghijklmnopqrstuvwxyz" % i for i in range(1, 301)) + "\n"
    io.open(os.path.join(W, "pkg", APPID, "data", "resource", "big.txt"), "w").write(big)
    io.open(os.path.join(W, "reg.json"), "w").write('[{"appid":"%s","name":"PenTerm"}]' % APPID)

    # ---- 假 miniapp_cli ----
    cli = os.path.join(W, "miniapp_cli")
    io.open(cli, "w", newline="\n").write(
        "#!/bin/sh\n"
        '[ "$1" = install ] || exit 0\n'
        'W="%s"; id=%s\n'
        'mkdir -p $W/pkg/$id/a\n'
        'echo \'{"appid":"\'$id\'"}\' > $W/pkg/$id/a/manifest.json\n'
        'echo \'[{"appid":"\'$id\'"}]\' > $W/reg.json\n'
        'echo \'{"appid": "\'$id\'", "ret": 0}\'\n' % (W, APPID))

    env = {
        "KEEPER_AMRDIR": W + "/amr", "KEEPER_APPDIR": W + "/pkg",
        "KEEPER_REG": W + "/reg.json", "KEEPER_BAK": W + "/bak",
        "KEEPER_LOG": W + "/keeper.log", "KEEPER_LOCK": W + "/keeper.pid",
        "KEEPER_MINIAPP": cli, "KEEPER_WAIT_READY": "0", "KEEPER_WAIT_UPD": "0",
        "KEEPER_MODE": "oneshot", "KEEPER_SWEEP_ONLY": "0", "KEEPER_WAIT_DECISION": "0",
    }

    def run_keeper():
        try:
            os.remove(W + "/keeper.pid")
        except OSError:
            pass
        return sh('sh "%s"' % KEEPER.replace("\\", "/"), env)

    def plant(tag):
        print("\n===== %s =====" % tag)

    # ================= 第 1 轮 =================
    plant("第 1 轮：应用健在 + .appid 写坏 → 应自解析 appid 并建立镜像")
    run_keeper()
    log = io.open(W + "/keeper.log", encoding="utf-8", errors="ignore").read()
    for line in log.splitlines():
        if "解析" in line or "镜像" in line:
            print("  " + line)
    print("  解析后的 .appid 文件内容: %r" %
          io.open(W + "/amr/PenTerm.appid", encoding="utf-8").read().strip())
    mir = os.path.join(W, "bak", APPID, "resource", "big.txt")
    ok1 = os.path.isfile(mir) and io.open(mir, encoding="utf-8").read() == big
    print("  镜像 big.txt 与源逐字节一致: %s" % ("✓" if ok1 else "✗"))

    # ============ 模拟 APP_UPD 删包 ============
    plant("模拟 APP_UPD：连包带数据一起删")
    shutil.rmtree(os.path.join(W, "pkg", APPID), ignore_errors=True)
    io.open(W + "/reg.json", "w").write("[]")
    print("  包目录: %s" % ("已删" if not os.path.isdir(os.path.join(W, "pkg", APPID)) else "还在"))
    surv = os.path.isfile(os.path.join(W, "bak", APPID, "user.txt"))
    print("  镜像仍在（硬链接 inode 存活）: %s" % ("✓" if surv else "✗"))
    if surv:
        print("  镜像里的数据: %s" % io.open(os.path.join(W, "bak", APPID, "user.txt")).read())

    # ================= 第 2 轮 =================
    plant("第 2 轮：应用已被删 → 应装回并恢复 data/")
    run_keeper()
    log = io.open(W + "/keeper.log", encoding="utf-8", errors="ignore").read()
    for line in log.splitlines()[-5:]:
        print("  " + line)
    pkg = os.path.join(W, "pkg", APPID)
    print("  恢复后的包: %s" % sorted(os.listdir(pkg)) if os.path.isdir(pkg) else "  包没装回 ✗")
    d = os.path.join(pkg, "data")
    ok_user = os.path.isfile(os.path.join(d, "user.txt"))
    ok_big = os.path.isfile(os.path.join(d, "resource", "big.txt")) and \
        io.open(os.path.join(d, "resource", "big.txt"), encoding="utf-8").read() == big
    ok_reg = APPID in io.open(W + "/reg.json", encoding="utf-8").read()
    print("  user.txt 恢复: %s" % ("✓ " + io.open(os.path.join(d, "user.txt")).read() if ok_user else "✗"))
    print("  big.txt（300 行）逐字节一致: %s" % ("✓" if ok_big else "✗"))
    print("  注册表已重登记: %s" % ("✓" if ok_reg else "✗"))

    allok = ok1 and surv and ok_user and ok_big and ok_reg
    print("\n=== 结论：%s ===" % ("全部通过 ✓✓✓" if allok else "有失败项 ✗"))
    return 0 if allok else 1


if __name__ == "__main__":
    sys.exit(main())
