"""
机器人运行器 —— 命令行（main.py）和网页控制台（server.py）共用同一套启动逻辑。

职责：
- 建窗口/模板/任务，放到后台线程跑
- 对外提供 snapshot()：状态、进度、耗时、成功率
- 一轮跑完（或异常/手动停止）后写一条历史记录，并把本次日志单独存档

历史文件：history/runs.jsonl（每行一条 JSON）
本次日志：history/logs/<run_id>.log
"""
import os
import json
import time
import logging
import threading
import traceback
from collections import deque

from config import OROCHI_CONFIG
from core.window import WindowDevice
from core.matcher import Matcher
from core.logbuffer import LOG_BUFFER, install as install_log_buffer
from tasks.registry import get_task, DEFAULT_TASK_ID

log = logging.getLogger("OnmyojiPilot")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HISTORY_DIR = os.path.join(BASE_DIR, "history")
LOG_DIR = os.path.join(HISTORY_DIR, "logs")
HISTORY_FILE = os.path.join(HISTORY_DIR, "runs.jsonl")
MAX_HISTORY = 200          # 历史记录最多保留条数


def _ensure_dirs():
    os.makedirs(LOG_DIR, exist_ok=True)


def _load_history(limit=50):
    """读历史记录（最新的在前）"""
    if not os.path.exists(HISTORY_FILE):
        return []
    rows = deque(maxlen=limit)
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return list(reversed(rows))


def _append_history(record):
    _ensure_dirs()
    with open(HISTORY_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    _trim_history()


def _trim_history():
    """历史文件太大时只留最近 MAX_HISTORY 条"""
    try:
        if not os.path.exists(HISTORY_FILE):
            return
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            lines = [ln for ln in f if ln.strip()]
        if len(lines) <= MAX_HISTORY:
            return
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            f.writelines(lines[-MAX_HISTORY:])
    except OSError:
        pass


class BotRunner:
    """一次运行的生命周期管理者"""

    def __init__(self):
        self._lock = threading.Lock()
        self._thread = None
        self.task = None
        self.device = None
        self.matcher = None
        self.run_id = None
        self.error = ""
        self.requested_limit = 0
        self.task_id = DEFAULT_TASK_ID
        self.log_start_seq = 0
        self._stop_requested = False

    # ---------- 状态查询 ----------
    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def _task_info(self):
        spec = get_task(self.task_id)
        return {
            "task_id": spec.id,
            "task_name": spec.name,
            "task_group": spec.group,
            "task_icon": spec.icon,
            "task_accent": spec.accent,
        }

    def snapshot(self):
        """给前端用的完整状态快照（只读，不阻塞）"""
        task = self.task
        now = time.time()
        info = self._task_info()

        if task is None:
            return {
                "running": False,
                "state": "idle",
                "state_text": "空闲",
                "limit": self.requested_limit or get_task(self.task_id).default_limit,
                "done": 0, "failed": 0, "retries": 0,
                "elapsed": 0.0, "avg": 0.0, "last": 0.0,
                "success_rate": None, "message": "",
                "run_id": None, "log_seq": LOG_BUFFER.latest_seq,
                "window": "", "durations": [],
                **info,
            }

        stats = task.stats
        durations = list(stats.get("durations", []))
        done = int(stats.get("done", 0))
        failed = int(stats.get("failed", 0))

        started = task.started_at
        finished = task.finished_at
        elapsed = (finished or now) - started if started else 0.0

        state = stats.get("state", "starting")
        if self.running and state in ("finished", "stopped", "error"):
            state = "stopping"      # 线程还没退，但逻辑已结束

        return {
            "running": self.running,
            "state": state,
            "state_text": {
                "starting": "准备中",
                "running": "运行中",
                "stopping": "收尾中",
                "finished": "已完成",
                "stopped": "已停止",
                "error": "出错",
                "idle": "空闲",
            }.get(state, state),
            "limit": int(stats.get("limit", self.requested_limit)),
            "done": done,
            "failed": failed,
            "retries": int(stats.get("retries", 0)),
            "elapsed": round(elapsed, 1),
            "avg": round(sum(durations) / len(durations), 2) if durations else 0.0,
            "last": durations[-1] if durations else 0.0,
            "success_rate": round(done / (done + failed) * 100, 1) if (done + failed) else None,
            "message": stats.get("message", "") or self.error,
            "run_id": self.run_id,
            "log_seq": LOG_BUFFER.latest_seq,
            "window": f"{self.device.width}x{self.device.height}" if self.device else "",
            # 每轮耗时（只带最近 60 个，够画曲线又不至于让状态包变大）
            "durations": durations[-60:],
            **info,
        }

    # ---------- 控制 ----------
    def start(self, limit, task_id=None):
        with self._lock:
            if self.running:
                return False, "已经在运行中"
            spec = get_task(task_id)
            if not spec.available or spec.cls is None:
                return False, f"「{spec.name}」还没实现"
            self.task_id = spec.id
            self.requested_limit = max(1, int(limit))
            self.error = ""
            self._stop_requested = False
            self.run_id = time.strftime("%Y%m%d_%H%M%S")
            self.log_start_seq = LOG_BUFFER.latest_seq
            self.task = None
            self.device = None
            self._thread = threading.Thread(target=self._run, name="OnmyojiPilot", daemon=True)
            self._thread.start()
            return True, "已开始"

    def stop(self):
        with self._lock:
            if not self.running:
                return False, "当前没有在运行"
            self._stop_requested = True
            if self.task is not None:
                self.task.stop_flag = True
            return True, "正在停止（当前这一轮会先走完）"

    def wait(self, timeout=None):
        """命令行模式用：阻塞到运行结束"""
        if self._thread is not None:
            self._thread.join(timeout)

    # ---------- 真正的执行体 ----------
    def _run(self):
        install_log_buffer()
        started_wall = time.time()
        task = None
        ok = False
        spec = get_task(self.task_id)
        try:
            log.info("=" * 55)
            log.info("任务：%s（%s）  目标 %d 次", spec.name, spec.group, self.requested_limit)

            device = WindowDevice(click_mode=spec.config.get("click_mode", "message"))
            if not device.find_window():
                raise RuntimeError("无法定位 ZenLink 窗口，请确认已打开并正在投屏")
            self.device = device
            # find_window 内部已经打过一条带 hwnd 的日志，这里不再重复

            matcher = Matcher(spec.template_dir)
            matcher.load_config(spec.templates)
            loaded = sum(1 for t in matcher.templates.values() if t.image is not None)
            log.info("已加载 %d/%d 个模板", loaded, len(matcher.templates))
            self.matcher = matcher

            cfg = dict(spec.config)
            cfg["limit_count"] = self.requested_limit
            cfg["click_regions"] = spec.click_regions
            cfg["close_regions"] = spec.close_regions
            cfg["settle_exclude"] = spec.settle_exclude
            cfg["timing"] = spec.timing

            task = spec.cls(device, matcher, cfg)
            self.task = task
            if self._stop_requested:
                task.stop_flag = True
            task.run()
            ok = task.stats.get("state") in ("finished", "stopped")

        except Exception as e:
            self.error = str(e)
            log.exception("运行异常: %s", e)
            if task is not None:
                task.set_state("error", str(e))

        finally:
            # 线程结束后把状态从"收尾中"落到终态
            if task is not None:
                state = task.stats.get("state")
                if state in ("starting", "running"):
                    task.set_state("stopped" if self._stop_requested else "error",
                                   "" if self._stop_requested else "线程意外退出")
            self._save_history(task, started_wall, ok)

    def _save_history(self, task, started_wall, ok):
        try:
            _ensure_dirs()
            finished_wall = time.time()
            stats = task.stats if task else {}
            durations = list(stats.get("durations", []))
            done = int(stats.get("done", 0))
            failed = int(stats.get("failed", 0))
            state = stats.get("state", "error")

            spec = get_task(self.task_id)
            record = {
                "run_id": self.run_id,
                "task_id": spec.id,
                "task_name": spec.name,
                "group": spec.group,
                "start": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(started_wall)),
                "end": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(finished_wall)),
                "limit": int(stats.get("limit", self.requested_limit)),
                "done": done,
                "failed": failed,
                "retries": int(stats.get("retries", 0)),
                "total_sec": round(finished_wall - started_wall, 1),
                "avg_sec": round(sum(durations) / len(durations), 2) if durations else 0,
                "success_rate": round(done / (done + failed) * 100, 1) if (done + failed) else None,
                "state": state,
                "message": stats.get("message", "") or self.error,
            }
            _append_history(record)

            # 把本次日志单独存一份，方便回看
            lines = LOG_BUFFER.since(self.log_start_seq)
            with open(os.path.join(LOG_DIR, f"{self.run_id}.log"), "w", encoding="utf-8") as f:
                f.write(f"# run {self.run_id}  {spec.name}  目标 {record['limit']} 次  "
                        f"结果 {state}  完成 {done}/{record['limit']}\n")
                for item in lines:
                    f.write(item["text"] + "\n")
        except Exception:
            log.warning("写历史记录失败:\n%s", traceback.format_exc())


# 全局单例（同一进程只允许一个运行实例）
RUNNER = BotRunner()
