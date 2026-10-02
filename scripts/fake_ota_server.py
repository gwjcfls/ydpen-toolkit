#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fake_ota_server.py —— 伪装成有道 OTA 接口的本地服务器（替代 Wireshark 抓包 + 手工重发请求）

它同时干三件事：
  1. 记录：把词典笔发来的每一个请求（方法/路径/头/体）原样写进 logs/；
  2. 探测：收到 checkVersion 时，自己拿同样的 sign/mid/productId，
     把 version 改成 99.99.90 再向真服务器发一次，把返回的“全量包信息”
     存成 out/real_response.json（这就是教程里手工在 SOAP/SOJSON 上做的事）；
  3. 应答：
     - 还没有 out/patched_response.json 时（probe 阶段）：把词典笔自己的请求原样转发给真服务器，
       把真实返回转给它 —— 词典笔看到的就是官方答案，不会有副作用；
     - 已经有了 out/patched_response.json 时（serve 阶段）：直接回我们伪造的 status=1000 响应，
       词典笔就会去我们指定的地址下载改过的固件。

监听 80 端口（有道 OTA 是明文 HTTP），另外可选监听 443 用来判断固件是否已经改成 HTTPS。

用法
----
  python fake_ota_server.py                        # 自动模式：有 patched_response.json 就 serve，否则 probe
  python fake_ota_server.py --mode probe           # 只探测
  python fake_ota_server.py --mode serve --response ..\\out\\patched_response.json
  python fake_ota_server.py --tls-detect           # 同时监听 443，检测是否已上 HTTPS
  python fake_ota_server.py --port 8080            # 改端口（配合端口转发时用）
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import socket
import ssl
import sys
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import _console  # noqa: F401  (把控制台切到 UTF-8)

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
LOGDIR = os.path.join(ROOT, "logs")
OUTDIR = os.path.join(ROOT, "out")
REAL_HOST = "iotapi.abupdate.com"


def now() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(msg: str) -> None:
    print(f"[{now()}] {msg}", flush=True)


def save_json(path: str, obj) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


class State:
    def __init__(self, args):
        self.args = args
        self.lock = threading.Lock()
        self.probe_done = False
        self.serve_obj = None
        self.reload_response()


class Handler(BaseHTTPRequestHandler):
    server_version = "nginx/1.20.1"
    protocol_version = "HTTP/1.1"

    # ---- 基础 ----
    def log_message(self, fmt, *a):
        log("    " + fmt % a)

    def _read_body(self) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def _reply_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json;charset=UTF-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Server", "nginx/1.20.1")
        self.end_headers()
        self.wfile.write(body)

    def _handle(self, method: str):
        st: State = self.server.state  # type: ignore[attr-defined]
        body = self._read_body()
        peer = self.client_address[0]
        log(f"{method} {self.path} from {peer}  ({len(body)} bytes body)")
        rec = {
            "time": now(),
            "peer": peer,
            "method": method,
            "path": self.path,
            "headers": {k: v for k, v in self.headers.items()},
            "body": body.decode("utf-8", "replace"),
        }
        os.makedirs(LOGDIR, exist_ok=True)
        with open(os.path.join(LOGDIR, "requests.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

        if "checkversion" not in self.path.lower():
            # 其它接口（/register/ 等）原样转发给真服务器，保证词典笔的注册/激活/上报都正常，
            # 否则这些请求收到 404 有可能让升级流程卡住。
            try:
                status, resp = forward(self.path, body, self.headers, override_version=None)
                log(f"    -> 透明转发 {self.path}: HTTP {status}，{len(resp)} bytes")
                self.send_response(status)
                self.send_header("Content-Type", "application/json;charset=UTF-8")
                data = resp.encode("utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except Exception as e:  # noqa: BLE001
                log(f"    [!] 转发 {self.path} 失败: {e}")
                self._reply_json({"status": 5000, "msg": "proxy error", "data": None}, 200)
            return

        # 1) 先做一次“探测”：拿全量包信息
        if st.args.mode in ("probe", "auto") and not (st.args.once and st.probe_done):
            try:
                self.do_probe(body)
            except Exception as e:  # noqa: BLE001
                log(f"    [!] 探测失败: {e}")

        # 2) 决定怎么回
        if st.serve_obj is not None and st.args.mode in ("serve", "auto"):
            log("    -> 回伪造响应 (status=1000，指向本机固件)")
            self._reply_json(st.serve_obj)
            return

        # probe 阶段：把词典笔自己的请求原样转发，返回官方回答（无副作用）
        if st.args.mode == "log":
            self._reply_json({"status": 2101, "msg": "no update (log mode)", "data": None})
            return
        try:
            status, resp = forward(self.path, body, self.headers, override_version=None)
            log(f"    -> 原样转发官方接口: HTTP {status}，{len(resp)} bytes")
            self._reply_json(json.loads(resp) if resp.strip().startswith(("{", "[")) else {"raw": resp})
        except Exception as e:  # noqa: BLE001
            log(f"    [!] 转发失败: {e}；回 2101 保证词典笔不动作")
            self._reply_json({"status": 2101, "msg": "no update", "data": None})

    def do_probe(self, body: bytes) -> None:
        st: State = self.server.state  # type: ignore[attr-defined]
        with st.lock:
            if st.probe_done and st.args.once:
                return
            req = {}
            try:
                req = json.loads(body.decode("utf-8"))
            except Exception:  # noqa: BLE001
                log("    [!] 请求体不是 JSON，跳过探测")
                return
            status, resp = forward(self.path, body, self.headers, override_version=st.args.probe_version)
            try:
                obj = json.loads(resp)
            except Exception:  # noqa: BLE001
                obj = {"raw": resp}
            # 只有真的是“全量包信息”才算探测成功；否则（比如 MID/sign 校验失败）继续等下一个请求
            ver = {}
            if isinstance(obj, dict) and obj.get("status") == 1000 and isinstance(obj.get("data"), dict):
                ver = obj["data"].get("version") or {}
            if not (ver.get("deltaUrl") or ver.get("bakUrl")):
                save_json(os.path.join(LOGS, "probe_failed.json"), obj)
                log(f"    [!] 探测没拿到全量包: {obj.get('msg', obj) if isinstance(obj, dict) else obj}")
                log("        （正常：如果你刚才是用假 sign/mid 自测的，这个报错是预期的）")
                return
            path = os.path.join(OUTDIR, "real_response.json")
            save_json(path, obj)
            save_json(os.path.join(OUTDIR, "pen_request.json"), req)
            log(f"    [+] 官方全量包信息已保存: {path}  (HTTP {status}, 版本 {ver.get('versionName','')})")
            log(f"        最新包: {ver.get('fileSize')} bytes  {ver.get('deltaUrl') or ver.get('bakUrl')}")
            st.probe_done = True

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")


def forward(path: str, body: bytes, headers, override_version: str | None):
    """把请求转发到真·有道接口；override_version 不为空则改写 body 里的 version。"""
    url = f"http://{REAL_HOST}{path}"
    payload = body
    if override_version and body:
        try:
            obj = json.loads(body.decode("utf-8"))
            obj["version"] = override_version
            payload = json.dumps(obj).encode("utf-8")
        except Exception:  # noqa: BLE001
            pass
    req = urllib.request.Request(url, data=payload, method="POST")
    for k, v in headers.items():
        if k.lower() in ("host", "content-length", "connection", "transfer-encoding"):
            continue
        req.add_header(k, v)
    req.add_header("Content-Type", headers.get("Content-Type", "application/json;charset=UTF-8"))
    req.add_header("Content-Length", str(len(payload)))
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


class TlsDetector(threading.Thread):
    """监听 443，只为判断词典笔是不是已经改用 HTTPS（若是，改哈希这条路就走不通了）。"""

    daemon = True

    def __init__(self, port: int = 443):
        super().__init__(name="tls-detect")
        self.port = port

    def run(self):
        try:
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind(("0.0.0.0", self.port))
            srv.listen(16)
        except OSError as e:
            log(f"[tls] 无法监听 {self.port}: {e} (可能已被占用，忽略)")
            return
        log(f"[tls] 正在监听 {self.port} 用于检测 HTTPS …")
        while True:
            try:
                conn, addr = srv.accept()
            except OSError:
                return
            with conn:
                try:
                    conn.settimeout(5)
                    head = conn.recv(1024)
                except OSError:
                    head = b""
            if head[:3] == b"\x16\x03\x01" or head[:1] == b"\x16":
                sni = ""
                try:
                    sni = head.split(b"\x00\x00")[-1][2:].split(b"\x00")[0].decode("ascii", "replace")
                except Exception:  # noqa: BLE001
                    pass
                log("!" * 70)
                log(f"[tls] 词典笔 {addr[0]} 在用 HTTPS 连接！(SNI≈{sni})")
                log("[tls] 说明该固件已经修掉了明文 HTTP 漏洞 —— 改哈希这条路走不通。")
                log("[tls] 备选：改用 RKDevTool dump 分区法（见 README「路线二」）。")
                log("!" * 70)
            elif head:
                log(f"[tls] {addr[0]} 明文连了 443：{head[:60]!r}")


def main() -> int:
    ap = argparse.ArgumentParser(description="有道词典笔 OTA 欺骗服务器")
    ap.add_argument("--port", type=int, default=80)
    ap.add_argument("--mode", choices=["auto", "probe", "serve", "log"], default="auto")
    ap.add_argument("--response", default=os.path.join(OUTDIR, "patched_response.json"))
    ap.add_argument("--probe-version", default="99.99.90")
    ap.add_argument("--once", action="store_true", help="探测只做一次")
    ap.add_argument("--tls-detect", action="store_true")
    args = ap.parse_args()

    if args.tls_detect:
        TlsDetector().start()

    httpd = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    st = State(args)
    httpd.state = st  # type: ignore[attr-defined]
    log(f"伪装 OTA 服务器已启动: 0.0.0.0:{args.port}  模式={args.mode}")
    if st.serve_obj is not None:
        log(f"已加载伪造响应 {args.response} (status={st.serve_obj.get('status')})，词典笔会下载本机固件")
    else:
        log("尚未生成 patched_response.json：当前为 probe 模式，词典笔检查更新会触发一次全量包查询")
    log("记得把 iotapi.abupdate.com 在 hosts 里指到本机 IP（tools\\hosts_tool.ps1 -Add <本机IP>）")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log("停止。")
    return 0


def _reload(self):
    p = self.args.response
    if os.path.isfile(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                self.serve_obj = json.load(f)
        except Exception as e:  # noqa: BLE001
            log(f"[!] 读取 {p} 失败: {e}")
            self.serve_obj = None
    else:
        self.serve_obj = None


State.reload_response = _reload  # type: ignore[attr-defined]

if __name__ == "__main__":
    sys.exit(main())
