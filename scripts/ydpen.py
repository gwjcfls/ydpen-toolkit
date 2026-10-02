#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
ydpen.py —— 有道词典笔 (YDPX6-2 / X6 Pro CHN PLUS) 获取 ADB 权限 一条龙编排器

    全程走教程主线：劫持 OTA -> 下载官方全量包 -> 把 adb_auth 的哈希换成我们的密码
    -> 本地伪装升级服务器 -> 词典笔升级 -> adb shell 用新密码登录。

子命令
------
  env                     环境体检（IP / adb / 热点 / 端口 / 防火墙）
  adb                     连 USB，跑 adb devices，并轮流试已知老密码
  probe                   起欺骗服务器（探测模式），等词典笔“检查更新”
  download                从 out\real_response.json 里的 deltaUrl 下载全量包（可断点续传）
  patch --password XXX    改固件里的 adb 哈希 + 算出分段/整包 MD5，生成 patched_response.json
  serve                   起“欺骗服务器 + 固件直链服务器”，等词典笔下载安装
  all  --password XXX     上面几步按顺序全自动跑（缺哪步补哪步）

例子
----
  python ydpen.py env
  python ydpen.py adb
  python ydpen.py probe
  python ydpen.py download
  python ydpen.py patch --password tqn123456
  python ydpen.py serve
  python ydpen.py all --password tqn123456
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.request

if os.path.isdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools")):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools"))
import _console  # noqa: E402,F401  (把控制台切到 UTF-8)

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(HERE, "tools")
FW = os.path.join(HERE, "firmware")
OUT = os.path.join(HERE, "out")
LOGS = os.path.join(HERE, "logs")
ADB = os.path.join(TOOLS, "platform-tools", "adb.exe")
PY = sys.executable
KNOWN_PASSWORDS = ["CherryYoudao", "x3sbrY1d2@dictpen", "youdao", "123456"]
IS_WIN = platform.system() == "Windows"


def log(m: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def ensure_dirs() -> None:
    for d in (FW, OUT, LOGS, TOOLS):
        os.makedirs(d, exist_ok=True)


def ps(cmd: str, timeout: int = 30) -> str:
    """执行 PowerShell 片段并返回输出（用于取 IP、热点状态等）。"""
    if not IS_WIN:
        return ""
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        return (r.stdout or "") + (r.stderr or "")
    except Exception as e:  # noqa: BLE001
        return f"<ps error: {e}>"


def ipv4_list() -> list[tuple[str, str]]:
    out = ps("Get-NetIPAddress -AddressFamily IPv4 | ForEach-Object { $_.IPAddress + '|' + $_.InterfaceAlias }")
    res = []
    for line in out.splitlines():
        line = line.strip()
        if "|" in line and not line.startswith("<"):
            ip, _, alias = line.partition("|")
            if ip and not ip.startswith("127.") and not ip.startswith("169.254."):
                res.append((ip.strip(), alias.strip()))
    return res


def pick_ip(prefer_hotspot: bool = True) -> str:
    ips = ipv4_list()
    if prefer_hotspot:
        for ip, _ in ips:
            if ip == "192.168.137.1":  # Windows 移动热点(ICS) 默认网关
                return ip
        for ip, alias in ips:
            if ip.startswith("192.168.") and ("本地连接" in alias or "Local Area Connection" in alias) and ip.endswith(".1"):
                return ip
    for ip, _ in ips:
        if ip.startswith("192.168.") or ip.startswith("10."):
            return ip
    if ips:
        return ips[0][0]
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:  # noqa: BLE001
        return "127.0.0.1"
    finally:
        s.close()


def find_firmware() -> str | None:
    if not os.path.isdir(FW):
        return None
    imgs = [os.path.join(FW, f) for f in os.listdir(FW) if f.lower().endswith((".img", ".bin"))]
    imgs.sort(key=os.path.getsize, reverse=True)
    return imgs[0] if imgs else None


def start_bg(args: list[str], logname: str) -> subprocess.Popen:
    ensure_dirs()
    lf = open(os.path.join(LOGS, logname), "a", encoding="utf-8", buffering=1)
    lf.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} {' '.join(args)} =====\n")
    p = subprocess.Popen(args, stdout=lf, stderr=subprocess.STDOUT)
    return p


def hosts_has_domain(domain: str = "iotapi.abupdate.com") -> str | None:
    """读 hosts（不需要提权）看看劫持是不是已经写好了，避免每次都弹 UAC。"""
    p = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "drivers", "etc", "hosts")
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split()
                if len(parts) >= 2 and domain in parts[1:]:
                    return parts[0]
    except Exception:  # noqa: BLE001
        return None
    return None


def ensure_hosts(ip: str) -> None:
    cur = hosts_has_domain()
    if cur == ip:
        log(f"[=] hosts 已经是 {ip} iotapi.abupdate.com，跳过提权")
        return
    log(f"[*] 正在写 hosts（{cur or '无'} -> {ip}），会弹 UAC，请点“是”…")
    log(ps(f"& '{os.path.join(TOOLS,'hosts_tool.ps1')}' -Add '{ip}'").strip())
    got = hosts_has_domain()
    log(f"[+] hosts 现在: {got or '(仍然没有！)'} iotapi.abupdate.com")


# --------------------------------------------------------------------------- env
def cmd_env(args) -> int:
    ensure_dirs()
    log("=== 环境体检 ===")
    log(f"Python : {sys.version.split()[0]}  ({PY})")
    adb_ok = os.path.isfile(ADB)
    log(f"adb    : {'已就绪 ' + ADB if adb_ok else '未安装（tools\\platform-tools 缺失）'}")
    if adb_ok:
        code, ver = adb_run(["version"])
        log("         " + (ver.strip().splitlines()[0] if ver.strip() else f"adb 退出码 {code}"))
        code, dev = adb_run(["devices", "-l"])
        log("adb devices:\n" + dev.strip())
    log("本机 IPv4：")
    for ip, alias in ipv4_list():
        mark = "  <- 移动热点网关，优先用它" if ip == "192.168.137.1" else ""
        log(f"    {ip:<16} {alias}{mark}")
    log(f"建议固件直链主机: {pick_ip()}")
    log("热点服务: " + ps("Get-Service icssvc,WlanSvc | ForEach-Object { $_.Name + '=' + $_.Status }").strip())
    log("防火墙: " + ps("(Get-NetFirewallProfile | ForEach-Object { $_.Name + '=' + $_.Enabled }) -join ', '").strip())
    log("端口占用(80/14514/53): " + ps("netstat -ano | Select-String ':80\\s|:14514\\s|:53\\s' | Select-Object -First 6").strip())
    log(f"固件目录: {FW}  ->  " + (find_firmware() or "（还没有固件）"))
    log(f"探测结果: {'有 out\\real_response.json' if os.path.isfile(os.path.join(OUT,'real_response.json')) else '还没抓到官方响应'}")
    log(f"伪造响应: {'已生成 out\\patched_response.json' if os.path.isfile(os.path.join(OUT,'patched_response.json')) else '未生成'}")
    return 0


def cmd_watch(args) -> int:
    """盯着 adb 设备状态，一旦从 offline 变成 device 就自动开始试密码。"""
    ensure_dirs()
    last = None
    log("=== 盯着 adb 设备状态（你现在去词典笔上打开 ADB 开关）===")
    log("    词典笔: 设置 → 法律监管 → 连点文本 10~15 下，直到提示“ADB 调试已打开”")
    while True:
        code, out = adb_run(["devices"])
        lines = [l.strip() for l in out.splitlines()[1:] if l.strip()]
        state = " / ".join(lines) if lines else "(无设备)"
        if state != last:
            log(f"    状态: {state}")
            last = state
        if lines and any("\tdevice" in l or l.endswith("device") for l in lines):
            log("[+] 设备已就绪！")
            return cmd_adb(argparse.Namespace(passwords=args.passwords))
        time.sleep(args.interval)


# --------------------------------------------------------------------------- usb
def cmd_usb(args) -> int:
    """列出当前 USB 设备，用来判断词典笔插上后有没有认出来 / 缺驱动。"""
    ensure_dirs()
    log("=== 当前 USB 设备（插笔前后各跑一次，看多了哪个）===")
    out = ps(
        "Get-PnpDevice -PresentOnly | Where-Object { $_.InstanceId -like 'USB\\*' } | "
        "ForEach-Object { $_.Status + ' | ' + $_.Class + ' | ' + $_.FriendlyName + ' | ' + $_.InstanceId }"
    )
    for line in out.splitlines():
        line = line.strip()
        if line and not line.startswith("<"):
            flag = ""
            low = line.lower()
            if "error" in low or "unknown" in low or "未知" in line:
                flag = "   <== 可能是缺驱动的词典笔"
            if any(k in low for k in ("android", "adb", "youdao", "rockchip", "2207", "mtp", "便携", "portable")):
                flag = "   <== 像词典笔/安卓设备"
            log("  " + line + flag)
    log("")
    log("如果插上后出现“未知设备/其他设备”且带感叹号：告诉我它的 VID/PID，我给你装驱动（会弹 UAC）。")


# --------------------------------------------------------------------------- adb
def adb_run(argv: list[str], stdin: str | None = None, timeout: int = 25) -> tuple[int, str]:
    if not os.path.isfile(ADB):
        return 127, "adb 不存在"
    tmpdir = os.path.join(HERE, "tmp")
    os.makedirs(tmpdir, exist_ok=True)
    env = dict(
        os.environ,
        ANDROID_USER_HOME=os.path.join(HERE, ".android"),  # 免得写 C:\Users\xx\.android 被拒
        TEMP=tmpdir,  # 免得写 %TEMP%\adb.log 被拒
        TMP=tmpdir,
    )
    r = subprocess.run(
        [ADB] + argv,
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=env,
    )
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def cmd_adb(args) -> int:
    ensure_dirs()
    log("=== 1) 检查设备 ===")
    code, out = adb_run(["devices", "-l"])
    print(out.strip())
    if "device " not in out and "\tdevice" not in out:
        log("[!] 还没看到设备。请确认：")
        log("    1. 词典笔上 设置 → 法律监管 → 连点文本 10~15 下，提示“ADB 调试已打开”")
        log("    2. 用数据线插到这台电脑（不是只充电的线）")
        log("    3. 若设备管理器里出现黄色感叹号，需要你点一下 UAC 让我装驱动")
        log("    然后重新运行: python ydpen.py adb")
        return 2

    log("=== 2) 直接试老密码（历史版本用过的固定密码）===")
    pws = args.passwords.split(",") if args.passwords else KNOWN_PASSWORDS
    for pw in pws:
        log(f"  试 {pw!r} …")
        code, out = adb_run(["shell", "auth"], stdin=pw + "\n")
        txt = out.strip().replace("\r", "")
        log("    " + (txt.replace("\n", " | ") if txt else "(无输出)"))
        if "success" in txt.lower():
            log(f"[+] 密码是 {pw!r}！")
            code, out = adb_run(["shell", "id"])
            log("    " + out.strip())
            return 0
    log("[!] 老密码都不行 —— 需要走“改固件哈希”路线：python ydpen.py probe")
    return 3


# --------------------------------------------------------------------------- probe
def real_response_ok(path: str) -> bool:
    """判断 out\\real_response.json 是不是真的全量包信息（而不是 MID/sign 报错）。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            obj = json.load(f)
        ver = (obj.get("data") or {}).get("version") or {}
        return bool(ver.get("deltaUrl") or ver.get("bakUrl"))
    except Exception:  # noqa: BLE001
        return False


def cmd_probe(args) -> int:
    ensure_dirs()
    ip = args.host or pick_ip()
    real = os.path.join(OUT, "real_response.json")
    if os.path.isfile(real) and not args.force:
        log(f"[=] 已有 {real}，跳过探测（--force 可重新抓）")
        return 0
    log("=== 启动 OTA 欺骗服务器（探测模式）===")
    p = start_bg([PY, os.path.join(TOOLS, "fake_ota_server.py"), "--mode", "probe", "--once"] + (["--tls-detect"] if args.tls_detect else []), "ota_probe.log")
    log(f"    服务器 PID={p.pid}，日志 logs\\ota_probe.log")
    log("    hosts 劫持检查 …")
    ensure_hosts(ip)
    log("")
    log(">>> 现在请在词典笔上：连接电脑开的热点 → 设置 → 检查更新 <<<")
    log("    （第一次可能没反应，反复点几次；实在没动静就恢复出厂设置后联网激活再试）")
    log(f"    我在等 {real} 出现，最多 {args.timeout} 秒 …")
    t0 = time.time()
    warned = False
    while time.time() - t0 < args.timeout:
        if os.path.isfile(real):
            if real_response_ok(real):
                log("[+] 抓到了！")
                with open(real, "r", encoding="utf-8") as f:
                    try:
                        data = json.load(f)["data"]["version"]
                        log(f"    版本 {data.get('versionName')}  大小 {data.get('fileSize')}")
                        log(f"    下载地址 {data.get('deltaUrl') or data.get('bakUrl')}")
                    except Exception:  # noqa: BLE001
                        log("    (响应结构非预期，请人工看 out\\real_response.json)")
                p.terminate()
                log("[*] 下一步: python ydpen.py download")
                return 0
            if not warned:
                warned = True
                try:
                    with open(real, "r", encoding="utf-8") as f:
                        obj = json.load(f)
                    log(f"[!] {real} 里是报错响应（{obj.get('status')}: {obj.get('msg')}），继续等真正的请求…")
                    log("    这通常是自测用的假 sign/mid 留下的，忽略即可。")
                except Exception:  # noqa: BLE001
                    log(f"[!] {real} 不是有效的全量包 JSON，继续等…")
        time.sleep(2)
    log(f"[-] {args.timeout}s 内没收到请求。可能原因：")
    log("    1. 词典笔没连上电脑热点 / 没真正发出更新请求（界面上“正在检查更新”可能是假的）")
    log("    2. hosts 没生效或没提权成功（看 tools\\hosts_tool.ps1 -List）")
    log("    3. 词典笔已经改用 HTTPS —— 看 logs\\ota_probe.log 里有没有 [tls] 报警")
    log("    4. 请求走了别的域名：可以开 Wireshark 抓一次，把域名告诉我，我加进 hosts")
    p.terminate()
    return 4


# --------------------------------------------------------------------------- download
def http_get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def download(url: str, dest: str) -> str:
    """支持断点续传的下载（带浏览器 UA —— 官 CDN 会 403 掉 python-urllib）。"""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    pos = os.path.getsize(dest) if os.path.isfile(dest) else 0
    req = urllib.request.Request(url)
    req.add_header("User-Agent", UA)
    req.add_header("Accept", "*/*")
    req.add_header("Accept-Encoding", "identity")
    if pos:
        req.add_header("Range", f"bytes={pos}-")
        log(f"    断点续传，从 {pos/1048576:.1f} MiB 继续")
    with urllib.request.urlopen(req, timeout=60) as r:
        total = r.headers.get("Content-Length")
        total = (int(total) + pos) if total else None
        mode = "ab" if pos and r.status == 206 else "wb"
        if mode == "wb":
            pos = 0
        with open(dest, mode) as f:
            last = 0.0
            got = pos
            while True:
                buf = r.read(1024 * 1024)
                if not buf:
                    break
                f.write(buf)
                got += len(buf)
                if time.time() - last > 3:
                    last = time.time()
                    pct = f"{got/total*100:5.1f}%" if total else "  ?  "
                    log(f"    {pct}  {got/1048576:.0f} MiB" + (f" / {total/1048576:.0f} MiB" if total else ""))
    return dest


def cmd_download(args) -> int:
    ensure_dirs()
    real = args.response or os.path.join(OUT, "real_response.json")
    if not os.path.isfile(real):
        log(f"[!] 没有 {real}，先跑 python ydpen.py probe")
        return 2
    with open(real, "r", encoding="utf-8") as f:
        obj = json.load(f)
    ver = obj.get("data", {}).get("version", {})
    url = args.url or ver.get("deltaUrl") or ver.get("bakUrl")
    if not url:
        log("[!] 响应里没有 deltaUrl/bakUrl，请人工打开 out\\real_response.json 看结构")
        return 2
    name = os.path.basename(url.split("?")[0]) or "firmware.img"
    dest = os.path.join(FW, name)
    log(f"=== 下载全量包 ===")
    log(f"    {url}")
    log(f" -> {dest}")
    try:
        download(url, dest)
    except Exception as e:  # noqa: BLE001
        log(f"[!] 下载失败: {e}")
        if ver.get("bakUrl") and ver["bakUrl"] != url:
            log("[*] 换 bakUrl 重试 …")
            try:
                download(ver["bakUrl"], dest)
            except Exception as e2:  # noqa: BLE001
                log(f"[!] 仍然失败: {e2}")
                return 3
    size = os.path.getsize(dest) if os.path.isfile(dest) else 0
    if not size:
        log("[!] 下载没成功（文件不存在），把上面的报错发我。")
        return 3
    expect = ver.get("fileSize")
    log(f"[+] 完成 {size:,} 字节" + (f"  (期望 {expect:,})" + (" ✔" if int(expect) == size else "  ✗ 大小不符，别慌，先看 md5") if expect else ""))
    if args.verify and ver.get("md5sum"):
        import hashlib

        h = hashlib.md5()
        with open(dest, "rb") as f:
            for b in iter(lambda: f.read(8 * 1024 * 1024), b""):
                h.update(b)
        ok = h.hexdigest() == ver["md5sum"]
        log(f"    md5 {h.hexdigest()} {'✔ 与原厂一致' if ok else '✗ 不一致（固件可能已加密/被改）'}")
        if not ok:
            return 4
    log("[*] 下一步: python ydpen.py patch --password 你的新密码")
    return 0


# --------------------------------------------------------------------------- patch
def cmd_patch(args) -> int:
    ensure_dirs()
    img = args.img or find_firmware()
    if not img:
        log("[!] firmware\\ 下没有 .img，先 python ydpen.py download")
        return 2
    real = args.response or os.path.join(OUT, "real_response.json")
    host = args.host or pick_ip()
    url = args.url or f"http://{host}:14514/{os.path.basename(img)}"
    log(f"=== 1) 改固件里的 adb 密码哈希 ===")
    r = subprocess.run(
        [PY, os.path.join(TOOLS, "patch_firmware.py"), "patch", img, "--password", args.password]
        + (["--algo", args.algo] if args.algo else [])
        + (["--newline", args.newline] if args.newline else [])
        + (["--dump-script", os.path.join(OUT, "scripts")]),
        text=True,
    )
    if r.returncode != 0:
        log(f"[!] patch_firmware 返回 {r.returncode}，请把上面的输出发我。固件未被破坏（等长替换，失败时不写入）。")
        return 3
    log("=== 2) 计算分段 MD5 / 整包 MD5 / SHA256，生成伪造响应 ===")
    if not os.path.isfile(real):
        log(f"[!] 缺少 {real}：需要先 probe 抓到官方响应，否则拿不到分片 endpos")
        return 4
    r = subprocess.run(
        [PY, os.path.join(TOOLS, "ota_meta.py"), "--img", img, "--response", real, "--url", url],
        text=True,
    )
    if r.returncode != 0:
        log("[!] ota_meta 失败，把输出发我。")
        return 5
    log(f"[+] 完成。固件: {img}")
    log(f"[+] 直链: {url}")
    log(f"[+] 伪造响应: {os.path.join(OUT,'patched_response.json')}")
    log("[*] 下一步: python ydpen.py serve")
    return 0


# --------------------------------------------------------------------------- serve
def cmd_serve(args) -> int:
    ensure_dirs()
    img = args.img or find_firmware()
    if not img:
        log("[!] 没找到固件")
        return 2
    patched = os.path.join(OUT, "patched_response.json")
    if not os.path.isfile(patched):
        log(f"[!] 还没有 {patched}，先 patch")
        return 3
    host = args.host or pick_ip()
    log(f"=== 启动固件直链服务器 (14514) ===")
    p1 = start_bg([PY, os.path.join(TOOLS, "file_server.py"), "--file", img, "--port", "14514"], "file_server.log")
    log(f"    PID={p1.pid}  http://{host}:14514/{os.path.basename(img)}")
    log(f"=== 启动 OTA 欺骗服务器 (80) ===")
    p2 = start_bg([PY, os.path.join(TOOLS, "fake_ota_server.py"), "--mode", "serve"], "ota_serve.log")
    log(f"    PID={p2.pid}")
    log("=== 写 hosts（要 UAC）===")
    ensure_hosts(host)
    log("")
    log(">>> 现在在词典笔上：连电脑热点 → 检查更新 → 会看到很大的更新包 → 下载并安装 <<<")
    log("    下载/安装期间别关窗口。进度看 logs\\file_server.log 和 logs\\ota_serve.log")
    if args.wait:
        try:
            while True:
                time.sleep(5)
        except KeyboardInterrupt:
            pass
        p1.terminate()
        p2.terminate()
    return 0


# --------------------------------------------------------------------------- all
def cmd_all(args) -> int:
    ensure_dirs()
    if not args.password:
        log("[!] all 需要 --password 新密码")
        return 1
    rc = cmd_env(args)
    if not os.path.isfile(ADB):
        log("[!] 先把 platform-tools 放到 tools\\ 下")
        return 2

    if not os.path.isfile(os.path.join(OUT, "real_response.json")):
        log("[[ 第 1 步：先试老密码 / 抓官方更新响应 ]]")
        cmd_adb(argparse.Namespace(passwords=None))
        log("如果老密码成功就结束了；否则继续抓包路线。")
        rc = cmd_probe(argparse.Namespace(host=None, timeout=args.timeout, force=False, tls_detect=True))
        if rc != 0:
            return rc

    img = find_firmware()
    if not img:
        rc = cmd_download(argparse.Namespace(response=None, url=None, verify=True))
        if rc != 0:
            return rc
        img = find_firmware()

    if not os.path.isfile(os.path.join(OUT, "patched_response.json")) or args.repatch:
        rc = cmd_patch(
            argparse.Namespace(
                img=img,
                response=None,
                host=None,
                url=None,
                password=args.password,
                algo=None,
                newline="auto",
                repatch=True,
            )
        )
        if rc != 0:
            return rc

    log("[[ 最后一步：让词典笔升级改过的固件 ]]")
    return cmd_serve(argparse.Namespace(img=img, host=None, wait=True))


def main() -> int:
    ap = argparse.ArgumentParser(description="有道词典笔 ADB 提权一条龙", formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("env").set_defaults(func=cmd_env)
    sub.add_parser("usb").set_defaults(func=cmd_usb)

    a = sub.add_parser("adb")
    a.add_argument("--passwords", default=None, help="逗号分隔，默认试几个历史密码")
    a.set_defaults(func=cmd_adb)

    w = sub.add_parser("watch")
    w.add_argument("--passwords", default=None)
    w.add_argument("--interval", type=float, default=2.0)
    w.set_defaults(func=cmd_watch)

    p = sub.add_parser("probe")
    p.add_argument("--host", default=None, help="hosts 指向的本机 IP，默认自动（优先 192.168.137.1）")
    p.add_argument("--timeout", type=int, default=300)
    p.add_argument("--force", action="store_true")
    p.add_argument("--tls-detect", action="store_true", default=True)
    p.set_defaults(func=cmd_probe)

    d = sub.add_parser("download")
    d.add_argument("--response", default=None)
    d.add_argument("--url", default=None)
    d.add_argument("--verify", action="store_true", default=True)
    d.set_defaults(func=cmd_download)

    q = sub.add_parser("patch")
    q.add_argument("--password", required=True, help="你想设置的新 ADB 密码（明文，登录时输入它）")
    q.add_argument("--img", default=None)
    q.add_argument("--response", default=None)
    q.add_argument("--url", default=None)
    q.add_argument("--host", default=None)
    q.add_argument("--algo", default=None, choices=[None, "auto", "md5", "sha256"])
    q.add_argument("--newline", default=None, choices=[None, "auto", "yes", "no"])
    q.add_argument("--repatch", action="store_true")
    q.set_defaults(func=cmd_patch)

    s = sub.add_parser("serve")
    s.add_argument("--img", default=None)
    s.add_argument("--host", default=None)
    s.add_argument("--wait", action="store_true", default=True)
    s.set_defaults(func=cmd_serve)

    al = sub.add_parser("all")
    al.add_argument("--password", required=True)
    al.add_argument("--timeout", type=int, default=300)
    al.add_argument("--repatch", action="store_true")
    al.set_defaults(func=cmd_all)

    args = ap.parse_args()
    try:
        return args.func(args)
    except KeyboardInterrupt:
        log("用户中断。")
        return 130


if __name__ == "__main__":
    sys.exit(main())
