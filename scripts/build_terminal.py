#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_terminal.py —— 一键构建 PenTerm 终端 miniapp（插件 + 页面 + 打包）

做四件事：
  1) zig 交叉编译原生插件 libjsapi_term.so
  2) 把 jsfmc 推上笔（持久目录 /userdisk/skip_re/tools），编译页面 JS → .js.bin
  3) 用 pack_amr.py 打成 .amr
  4) 可选 --install：uninstall → install → start，并同步 keeper 的保活 amr

用法：
    python tools\\build_terminal.py                    # 只构建
    python tools\\build_terminal.py --version 9.4.0
    python tools\\build_terminal.py --install          # 构建 + 装到笔上
"""
import argparse
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADB = os.path.join(ROOT, "tools", "platform-tools", "adb.exe")
SERIAL = os.environ.get("PEN_SERIAL", "192.168.137.100:5555")
SRC = os.path.join(ROOT, "miniapps", "terminal", "src")
BUILD = os.path.join(ROOT, "miniapps", "terminal", "build")
DIST = os.path.join(ROOT, "miniapps", "dist")
ZIG = os.path.join(ROOT, "tools", "build", "zig", "zig.exe")
QJS = os.path.join(os.path.expanduser("~"), ".dsh", "skills", "ydpen-toolkit", "assets", "quickjs")
APPID = "8001999000000001"
# jsfmc 链接了笔上的 yddal 库，必须带上这个搜索路径（踩过的坑）
PEN_LIB = "/oem/YoudaoDictPen/output/libs:/usr/lib:/lib"


def run(cmd, **kw):
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="ignore", **kw)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def adb(*args, timeout=180):
    return run([ADB, "-s", SERIAL] + list(args), timeout=timeout)[1]


def sh(cmd, timeout=180):
    return adb("shell", cmd, timeout=timeout)


def auth():
    out = sh("uptime")
    if "login" in out:
        subprocess.run([ADB, "-s", SERIAL, "shell", "auth"], input="ydpen2026\n",
                       capture_output=True, text=True, timeout=60)
        print("      [i] 已 ADB 认证")


def build_plugin():
    print("[1/4] 编译原生插件 libjsapi_term.so")
    env = dict(os.environ)
    env["ZIG_GLOBAL_CACHE_DIR"] = os.path.join(ROOT, "tools", "build", "zig-cache-global")
    env["ZIG_LOCAL_CACHE_DIR"] = os.path.join(ROOT, "tools", "build", "zig-cache")
    out = os.path.join(ROOT, "tools", "build", "libjsapi_term.so")
    cmd = [ZIG, "cc", "-target", "aarch64-linux-gnu.2.29", "-O2", "-fPIC", "-shared",
           "-fvisibility=hidden", "-I", QJS, os.path.join(SRC, "term.c"),
           os.path.join(ROOT, "out", "x7", "libquickjs.so"),
           os.path.join(ROOT, "out", "x7", "libz.so.1"),
           "-o", out, "-lpthread", "-lutil"]
    rc, log = run(cmd, env=env, cwd=os.path.join(ROOT, "tools", "build"))
    errs = [l for l in log.splitlines() if " error" in l or "error:" in l]
    if rc != 0 or not os.path.isfile(out):
        print("      [x] 编译失败:\n" + "\n".join(errs[:10] or log.splitlines()[-10:]))
        return False
    print("      [✓] %s (%.1f KB)%s" % (os.path.basename(out), os.path.getsize(out) / 1024.0,
                                        "" if not errs else "  警告 %d 条" % len(errs)))
    return True


def build_pages():
    print("[2/4] 编译页面 JS → .js.bin（在笔上编译）")
    auth()
    # jsfmc 放持久目录；LD_LIBRARY_PATH 必须带 yddal 库目录
    sh("mkdir -p /userdisk/skip_re/tools")
    adb("push", os.path.join(ROOT, "tools", "build", "jsfmc"),
        "/userdisk/skip_re/tools/jsfmc")
    sh("chmod +x /userdisk/skip_re/tools/jsfmc")
    pairs = [("component.js", "Component.js"), ("base-page.js", "BasePage.js"),
             ("page-index.js", "index.js"), ("page-index.js", "shell.js"),
             ("app.js", "app.js")]
    for src, mod in pairs:
        local = os.path.join(SRC, src)
        if not os.path.isfile(local):
            continue
        txt = open(local, encoding="utf-8").read().replace("\r\n", "\n")
        stem = mod[:-3] if mod.endswith(".js") else mod     # Component.js → Component
        tmp = os.path.join(ROOT, "tmp", "build_%s.js" % stem)
        os.makedirs(os.path.dirname(tmp), exist_ok=True)
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(txt)
        adb("push", tmp, "/tmp/build_%s.js" % stem)
        out = sh("export LD_LIBRARY_PATH=%s; /userdisk/skip_re/tools/jsfmc "
                 "-o /tmp/%s.bin -n %s /tmp/build_%s.js" % (PEN_LIB, stem, mod, stem))
        line = [l for l in out.splitlines() if "源码)" in l]
        print("      %s" % (line[0] if line else out.strip()[:100]))
        if not line:
            print("      [x] 编译 %s 失败" % mod)
            return False
        # 注意：模块名已含 .js，落盘必须是 <mod>.bin（早先写成 %s.js.bin 多了一个 .js）
        adb("pull", "/tmp/%s.bin" % stem, os.path.join(BUILD, "%s.bin" % mod))
    # pages/ 目录也放一份（框架按页面路径找时用得上）
    os.makedirs(os.path.join(BUILD, "pages", "index"), exist_ok=True)
    for n in ("index.js.bin", "shell.js.bin"):
        s = os.path.join(BUILD, n)
        if os.path.isfile(s):
            with open(s, "rb") as fi, open(os.path.join(BUILD, "pages", "index", n), "wb") as fo:
                fo.write(fi.read())
    return True


def pack(version):
    print("[3/4] 打包 .amr (版本 %s)" % version)
    out = os.path.join(DIST, "terminal-%s.amr" % version)
    rc, log = run([sys.executable, os.path.join(ROOT, "tools", "pack_amr.py"),
                   "--src", "miniapps/terminal/build", "--out", "miniapps/dist/terminal-%s.amr" % version,
                   "--appid", APPID, "--name", "终端", "--version", version,
                   "--lib", "arm64=tools/build/libjsapi_term.so",
                   "--lib", "arm64-orange=tools/build/libjsapi_term.so"], cwd=ROOT)
    line = [l for l in log.splitlines() if l.startswith("[+]")]
    print("      " + (line[0] if line else log.strip()[-120:]))
    return out if os.path.isfile(out) else None


def install(amr, version):
    print("[4/4] 安装到笔并启动")
    auth()
    adb("push", amr, "/tmp/penterm.amr")
    sh("miniapp_cli uninstall %s >/dev/null 2>&1" % APPID)
    time.sleep(2)
    print("      " + sh("miniapp_cli install /tmp/penterm.amr").strip()[:80])
    time.sleep(3)
    sh("miniapp_cli start %s index" % APPID)
    time.sleep(6)
    log = sh("grep -a 'appResumed' /data/applog/DictPen_*.log | tail -1")
    print("      " + log.strip()[:150])
    # 同步 keeper 的保活包，保证重启后被清理也能装回新版本
    adb("push", amr, "/userdisk/skip_re/amr/PenTerm.amr")
    print("      [✓] 已同步 keeper 保活包 /userdisk/skip_re/amr/PenTerm.amr")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default=None)
    ap.add_argument("--install", action="store_true")
    a = ap.parse_args()
    version = a.version or time.strftime("9.%m%d.%H%M")
    if not build_plugin():
        return 1
    if not build_pages():
        return 1
    amr = pack(version)
    if not amr:
        return 1
    if a.install and not install(amr, version):
        return 1
    print("\n完成：%s" % amr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
