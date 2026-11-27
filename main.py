"""
OnmyojiPilot 主入口 - 单人刷御魂（巫女大蛇·悲鸣）

用法：
    python main.py              # 开始刷，次数由 config.py 的 limit_count 决定
    python main.py 50           # 刷 50 次
    Ctrl+C                      # 停止

图形界面（推荐日常使用）：
    双击  启动控制台.bat    或    python server.py
"""
import os
import sys
import time
import signal
import logging

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import OROCHI_CONFIG
from bot_runner import RUNNER
from core.logbuffer import install as install_log_buffer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(os.path.join(BASE_DIR, "onmyojipilot.log"), encoding="utf-8"),
    ],
)
log = logging.getLogger("OnmyojiPilot")
install_log_buffer()


def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else OROCHI_CONFIG["limit_count"]
    task_id = sys.argv[2] if len(sys.argv) > 2 else None

    log.info("=" * 60)
    log.info("OnmyojiPilot - 单人刷御魂（巫女大蛇·悲鸣）")
    log.info("目标次数: %d  点击方式: %s", limit, OROCHI_CONFIG["click_mode"])
    log.info("=" * 60)

    ok, msg = RUNNER.start(limit, task_id)
    if not ok:
        log.error("启动失败: %s", msg)
        return 1

    # Ctrl+C 优雅停止
    def handle_signal(signum, frame):
        log.info("收到停止信号，正在停止...")
        RUNNER.stop()

    signal.signal(signal.SIGINT, handle_signal)

    # 阻塞等运行结束
    while RUNNER.running:
        time.sleep(0.5)

    snap = RUNNER.snapshot()
    log.info("-" * 60)
    log.info("结果: %s  完成 %d/%d  失败 %d  重试 %d  总用时 %.1fs",
             snap["state_text"], snap["done"], snap["limit"],
             snap["failed"], snap["retries"], snap["elapsed"])
    if snap["state"] == "error":
        log.error("%s", snap["message"])
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
