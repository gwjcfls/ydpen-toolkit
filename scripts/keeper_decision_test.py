#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""keeper_t3_decision.py —— 测 keeper 的「cleaner 判定 → 退出/常驻」分支

场景 A：框架日志出现 cleaner 跳过 → keeper 做最终镜像、装 cron 镜像任务、**自行退出**
场景 B：框架日志出现 cleaner 清理（removing） → keeper 修复并**转入常驻轮询**

用假 crontab（沙箱里放一个记录调用的脚本）避免依赖真实 cron。
"""
import io
import os
import shutil
import subprocess
import sys
import time
import zipfile

ROOT = r"C:\Users\Sever\Documents\deepseek-harness\default-workspace\ydpen-adb"
TMP = os.path.join(ROOT, "tmp")
BB = os.path.join(ROOT, "tools", "busybox", "busybox.exe")
KEEPER = os.path.join(TMP, "keeper_lf.sh")
APPID = "8001999000000001"


def sh(cmd, env=None, timeout=90, cwd=None):
    e = dict(os.environ)
    if env:
        e.update(env)
    p = subprocess.run([BB, "sh", "-c", cmd], capture_output=True, text=True,
                       encoding="utf-8", errors="ignore", env=e, timeout=timeout, cwd=cwd)
    return (p.stdout or "") + (p.stderr or "")


def setup(tag):
    W = os.path.join(TMP, tag).replace("\\", "/")
    shutil.rmtree(W, ignore_errors=True)
    for d in ("amr", "pkg/%s/a" % APPID, "pkg/%s/data" % APPID, "bin"):
        os.makedirs(os.path.join(W, d), exist_ok=True)
    # 真 amr（zip，含 manifest.json）
    with zipfile.ZipFile(os.path.join(W, "amr", "PenTerm.amr"), "w") as z:
        z.writestr("manifest.json", '{"appid":"%s","appName":"PenTerm"}' % APPID)
        z.writestr("app.js.bin", b"x")
    io.open(os.path.join(W, "amr", "PenTerm.appid"), "w").write(APPID)
    io.open(os.path.join(W, "pkg", APPID, "a", "manifest.json"), "w").write('{"appid":"%s"}' % APPID)
    io.open(os.path.join(W, "pkg", APPID, "data", "user.txt"), "w").write("DATA")
    io.open(os.path.join(W, "reg.json"), "w").write('[{"appid":"%s"}]' % APPID)
    io.open(os.path.join(W, "fw.log"), "w").write("")          # 空的"本次开机日志"
    # 假 miniapp_cli
    io.open(os.path.join(W, "miniapp_cli"), "w", newline="\n").write(
        '#!/bin/sh\n[ "$1" = install ] || exit 0\nW="%s"; id=%s\n'
        'mkdir -p $W/pkg/$id/a\n'
        'printf \'%%s\' \'{"appid":"%s"}\' > $W/pkg/$id/a/manifest.json\n'
        'printf \'%%s\' \'[{"appid":"%s"}]\' > $W/reg.json\n'
        'echo \'{"appid": "%s", "ret": 0}\'\n' % (W, APPID, APPID, APPID, APPID))
    # 假 crontab（记录调用）
    io.open(os.path.join(W, "bin", "crontab.sh"), "w", newline="\n").write(
        '#!/bin/sh\nW="%s"\nif [ "$1" = "-l" ]; then cat $W/cron.current 2>/dev/null; exit 0; fi\n'
        'cat "$1" > $W/cron.current 2>/dev/null\necho installed >> $W/cron.calls\n' % W)
    return W


def run_case(tag, log_line, expect):
    W = setup(tag)
    env = {
        "KEEPER_AMRDIR": W + "/amr", "KEEPER_APPDIR": W + "/pkg", "KEEPER_REG": W + "/reg.json",
        "KEEPER_BAK": W + "/bak", "KEEPER_LOG": W + "/keeper.log", "KEEPER_LOCK": W + "/pid",
        "KEEPER_SEEN": W + "/seen", "KEEPER_MINIAPP": W + "/miniapp_cli",
        "KEEPER_FWLOG": W + "/fw.log", "KEEPER_WAIT_READY": "0", "KEEPER_WAIT_DECISION": "30",
        "KEEPER_MODE": "boot", "KEEPER_INTERVAL": "2",
        "PATH": W + "/bin:" + os.environ.get("PATH", ""),
        "KEEPER_CRONTAB": BB + " sh " + W + "/bin/crontab.sh",
    }
    print("\n===== %s =====" % tag)
    print("  场景：框架日志将出现 %r" % log_line[:48])

    # 后台：2 秒后往"本次开机日志"里追加 cleaner 的输出
    # 场景 B 要先把包删掉（模拟 cleaner 真的卸载了它）
    pre = ""
    if expect != "exit":
        # 真机上镜像来自上一次运行（cron/上次开机），先铺一个镜像，再让包被删
        os.makedirs(os.path.join(W, "bak", APPID), exist_ok=True)
        io.open(os.path.join(W, "bak", APPID, "user.txt"), "w").write("DATA")
        pre = 'rm -rf "%s/pkg/%s"; printf \'%%s\' \'[]\' > "%s/reg.json"; ' % (W, APPID, W)
    bg = subprocess.Popen(
        [BB, "sh", "-c", pre + 'sleep 2; printf "%s\\n" \'%s\' >> "%s"' % ("%s", log_line, W + "/fw.log")],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    t0 = time.time()
    p = subprocess.Popen([BB, "sh", KEEPER], env={**os.environ, **env},
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # 最多等 40s
    exited = False
    while time.time() - t0 < 40:
        if p.poll() is not None:
            exited = True
            break
        time.sleep(1)
    if not exited:
        p.kill()
        p.wait()
    bg.wait(timeout=20)
    dt = time.time() - t0

    log = io.open(W + "/keeper.log", encoding="utf-8", errors="ignore").read()
    for line in log.splitlines():
        print("    " + line)
    cron = io.open(W + "/cron.current", encoding="utf-8", errors="ignore").read() \
        if os.path.isfile(W + "/cron.current") else ""
    if cron:
        print("    [cron] " + cron.strip())
    print("    进程：%s（%.0fs）  期望：%s" % ("已退出" if exited else "仍在常驻", dt, expect))

    ok = True
    if expect == "exit":
        ok = exited and "cleaner 已跳过" in log and "mirror" in cron
        ok = ok and os.path.isfile(os.path.join(W, "bak", APPID, "user.txt"))
    else:  # resident + repaired
        ok = (not exited) and "检测到 cleaner 执行了清理" in log and "修复" in log
        ok = ok and os.path.isfile(os.path.join(W, "pkg", APPID, "data", "user.txt"))
    print("    结果：%s" % ("✔ 通过" if ok else "✗ 失败"))
    return ok


def main():
    a = run_case("kt3a", "[AppWhitelistCleaner] fetch overmind appWhitelist failed, code: undefined", "exit")
    b = run_case("kt3b", "[AppWhitelistCleaner] removing: %s flag: 16384" % APPID, "resident")
    print("\n=== 结论：%s ===" % ("两个分支都正确 ✓✓" if (a and b) else "有失败项 ✗"))
    return 0 if (a and b) else 1


if __name__ == "__main__":
    sys.exit(main())
