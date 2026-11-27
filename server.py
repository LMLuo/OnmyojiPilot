"""
OnmyojiPilot 本地网页控制台

纯标准库实现（http.server），不依赖 Flask 等框架，双击 启动控制台.bat 即可用。

接口：
  GET  /                     控制台页面
  GET  /api/tasks            可挂机任务列表（由 tasks/registry.py 驱动）
  GET  /api/status           运行状态 + 统计
  GET  /api/logs?since=N     增量日志
  GET  /api/history?limit=N  历史运行记录
  GET  /api/history/log      某次运行的完整日志（?run_id=xxx）
  POST /api/start            {"task": "orochi", "limit": 30}
  POST /api/stop
  POST /api/shutdown         关闭本地服务

只用 127.0.0.1 监听，不对外暴露。
"""
import os
import io
import sys
import json
import time
import logging
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from bot_runner import RUNNER, _load_history, LOG_DIR, BASE_DIR
from tasks.registry import public_tasks
from core.logbuffer import LOG_BUFFER, install as install_log_buffer

WEB_DIR = os.path.join(BASE_DIR, "web")
DEFAULT_PORT = 8765

# 关闭服务用的开关
_SHUTDOWN = threading.Event()


class Handler(BaseHTTPRequestHandler):
    server_version = "OnmyojiPilot/1.0"

    # 默认的请求日志太吵，静音（出错时我们自己打）
    def log_message(self, fmt, *args):
        pass

    # ---------- 工具 ----------
    def _send_json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path, content_type):
        if not os.path.exists(path):
            self._send_json({"ok": False, "error": "文件不存在"}, 404)
            return
        with open(path, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                return {}
            raw = self.rfile.read(length)
            return json.loads(raw.decode("utf-8")) or {}
        except Exception:
            return {}

    # ---------- 路由 ----------
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path in ("/", "/index.html"):
            self._send_file(os.path.join(WEB_DIR, "index.html"), "text/html; charset=utf-8")
            return

        if path == "/api/status":
            self._send_json({"ok": True, "data": RUNNER.snapshot()})
            return

        if path == "/api/tasks":
            self._send_json({"ok": True, "items": public_tasks()})
            return

        if path == "/api/logs":
            try:
                since = int(query.get("since", ["0"])[0])
            except ValueError:
                since = 0
            items = LOG_BUFFER.since(since)
            self._send_json({"ok": True, "latest": LOG_BUFFER.latest_seq, "items": items})
            return

        if path == "/api/history":
            try:
                limit = int(query.get("limit", ["50"])[0])
            except ValueError:
                limit = 50
            self._send_json({"ok": True, "items": _load_history(limit)})
            return

        if path == "/api/history/log":
            run_id = (query.get("run_id", [""])[0] or "").strip()
            # 只允许字母数字下划线，避免路径穿越
            if not run_id or not all(c.isalnum() or c in "_-" for c in run_id):
                self._send_json({"ok": False, "error": "run_id 非法"}, 400)
                return
            path_log = os.path.join(LOG_DIR, f"{run_id}.log")
            if not os.path.exists(path_log):
                self._send_json({"ok": False, "error": "找不到该次运行日志"}, 404)
                return
            with open(path_log, "r", encoding="utf-8", errors="replace") as f:
                self._send_json({"ok": True, "text": f.read()})
            return

        self._send_json({"ok": False, "error": "未知接口"}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        payload = self._read_json()

        if path == "/api/start":
            try:
                limit = int(payload.get("limit", 30))
            except (TypeError, ValueError):
                limit = 30
            task_id = (payload.get("task") or "").strip() or None
            if limit < 1:
                self._send_json({"ok": False, "error": "次数必须大于 0"})
                return
            if limit > 9999:
                self._send_json({"ok": False, "error": "次数过大（上限 9999）"})
                return
            ok, msg = RUNNER.start(limit, task_id)
            self._send_json({"ok": ok, "message": msg})
            return

        if path == "/api/stop":
            ok, msg = RUNNER.stop()
            self._send_json({"ok": ok, "message": msg})
            return

        if path == "/api/shutdown":
            ok, msg = RUNNER.stop()
            self._send_json({"ok": True, "message": "服务即将关闭" + ("" if ok else "（未在运行）")})
            _SHUTDOWN.set()
            return

        self._send_json({"ok": False, "error": "未知接口"}, 404)


def _port_in_use(port):
    """端口上是不是真的有个东西在监听（不是"能不能 bind"这种虚的判定）"""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


class _ExclusiveServer(ThreadingHTTPServer):
    """
    必须关掉地址复用。原因在 Windows 上：

    socketserver 默认 `allow_reuse_address = 1`，也就是给 socket 设 SO_REUSEADDR。
    但这个选项在 Windows 上的语义是"**允许抢占已经被别人监听的端口**"，
    跟 POSIX 的"允许复用 TIME_WAIT 的端口"完全不是一回事。
    后果：本程序开第二个实例时能**成功绑上同一个端口**，两个进程各自
    起一个机器人线程去点同一个 ZenLink 窗口 —— 点出来的效果是乱的。
    实测撞到过：`netstat` 里同一个 8765 挂着两个 LISTENING。

    之前那个 `_pick_port()` 也自己设了 SO_REUSEADDR 来"测试端口是否可用"，
    在 Windows 上这个测试永远返回"可用"，等于没测。那个函数已经删掉，
    换成 `_choose_port()` + `_port_in_use()` 这套真检测。
    """

    allow_reuse_address = False
    daemon_threads = True


def _choose_port():
    """
    决定监听端口。默认端口已被占用时**直接退出**，绝不静默换一个。

    为什么不"找下一个空端口"：两个控制台各自带一个机器人线程，
    同时跑会互相抢着点同一台设备。偷偷换端口会让用户以为没事，
    然后在游戏里看到莫名其妙的点击。宁可不启动。
    想强行换端口可以设环境变量 YYS_PORT。
    """
    env_port = os.environ.get("YYS_PORT", "").strip()
    if env_port:
        try:
            return int(env_port)
        except ValueError:
            print(f"YYS_PORT={env_port!r} 不是数字，忽略")

    if _port_in_use(DEFAULT_PORT):
        url = f"http://127.0.0.1:{DEFAULT_PORT}/"
        print("=" * 56, flush=True)
        print(f" 端口 {DEFAULT_PORT} 上已经有服务在跑，启动中止", flush=True)
        print("=" * 56, flush=True)
        print(f" 很可能你已经开着一个控制台，直接用它就好：{url}", flush=True)
        print(" 如果那个是残留进程（关窗口没关干净），先结束它：", flush=True)
        print(f"     netstat -ano | findstr {DEFAULT_PORT}", flush=True)
        print("     taskkill /PID <上面查到的PID> /F", flush=True)
        print(" 两个控制台同时开，会互相抢着点同一台设备，所以这里不做自动改端口。", flush=True)
        print(" 确实需要换端口：set YYS_PORT=8766 再启动。", flush=True)
        raise SystemExit(1)

    return DEFAULT_PORT


def main():
    # 终端里也打一份日志，方便直接看（页面看日志是主路径，这里是备份）
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )
    install_log_buffer()

    port = _choose_port()
    try:
        server = _ExclusiveServer(("127.0.0.1", port), Handler)
    except OSError as e:
        print(f"端口 {port} 绑定失败：{e}", flush=True)
        print("换个端口再试：set YYS_PORT=8766", flush=True)
        raise SystemExit(1)
    url = f"http://127.0.0.1:{port}/"

    print("=" * 56, flush=True)
    print(" OnmyojiPilot 控制台已启动", flush=True)
    print(f" 地址：{url}", flush=True)
    print(" 关闭：在这个窗口按 Ctrl+C，或在页面上点\"退出\"", flush=True)
    print("=" * 56, flush=True)

    # YYS_NO_BROWSER=1 时不自动开浏览器（截图/调试用）
    if not os.environ.get("YYS_NO_BROWSER"):
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    thread = threading.Thread(target=server.serve_forever, name="http", daemon=True)
    thread.start()

    try:
        while not _SHUTDOWN.is_set():
            time.sleep(0.3)
    except KeyboardInterrupt:
        print("\n正在关闭服务...")
    finally:
        # 服务退出前先把机器人停掉，避免线程悬空去点游戏
        try:
            if RUNNER.running:
                RUNNER.stop()
                RUNNER.wait(timeout=20)
        except Exception:
            pass
        server.shutdown()
        server.server_close()
        print("已退出")


if __name__ == "__main__":
    main()
