"""
内存日志缓冲 —— 供网页控制台实时拉取日志

设计要点：
- 用带自增序号的 deque，前端只需传"我已有的最大序号"，服务端返回增量。
  比每次返回全量日志省带宽，也不会因为日志多了拖慢页面。
- 容量上限 3000 条，超出自动丢弃最旧的（长时间挂机不会吃内存）。
"""
import logging
import threading
from collections import deque

LOG_FORMAT = "%(asctime)s  %(message)s"
DATE_FORMAT = "%H:%M:%S"

LEVEL_NAMES = {
    logging.DEBUG: "debug",
    logging.INFO: "info",
    logging.WARNING: "warn",
    logging.ERROR: "error",
    logging.CRITICAL: "error",
}


class MemoryLogHandler(logging.Handler):
    """把日志同时存进内存队列，供 HTTP 接口增量读取"""

    def __init__(self, maxlen=3000):
        super().__init__()
        self.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
        self._buffer = deque(maxlen=maxlen)
        self._seq = 0
        self._lock = threading.Lock()

    def emit(self, record):
        try:
            text = self.format(record)
        except Exception:
            return
        level = LEVEL_NAMES.get(record.levelno, "info")
        with self._lock:
            self._seq += 1
            self._buffer.append({"seq": self._seq, "level": level, "text": text})

    @property
    def latest_seq(self):
        with self._lock:
            return self._seq

    def since(self, seq):
        """返回序号大于 seq 的所有日志"""
        with self._lock:
            return [item for item in self._buffer if item["seq"] > seq]

    def clear(self):
        with self._lock:
            self._buffer.clear()
            # 注意：不重置 _seq，否则前端会重复拿旧日志


# 全局单例：main.py（命令行）和 server.py（网页）都往同一个 handler 写
LOG_BUFFER = MemoryLogHandler()


def install():
    """把内存 handler 挂到项目 logger 上（重复调用不会重复挂）"""
    logger = logging.getLogger("OnmyojiPilot")
    # 关键：必须显式把级别降到 INFO。
    # 网页模式下没有任何 basicConfig，根 logger 默认是 WARNING，
    # log.info() 在进入 handler 之前就被 isEnabledFor 拦掉了 ——
    # 表现为页面日志一直是空的。命令行模式因为 basicConfig(level=INFO) 才碰巧正常。
    if logger.getEffectiveLevel() > logging.INFO:
        logger.setLevel(logging.INFO)
    for handler in logger.handlers:
        if isinstance(handler, MemoryLogHandler):
            return LOG_BUFFER
    logger.addHandler(LOG_BUFFER)
    return LOG_BUFFER
