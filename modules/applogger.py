"""日志模块 —— 既写文件也保留最近 N 条供前端展示"""
import logging
from collections import deque
from datetime import datetime
from typing import List, Dict
import threading

import config

_LOCK = threading.Lock()
_RECENT: deque = deque(maxlen=500)      # 前端显示用的环形缓冲

# 文件 + 控制台
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(config.LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)


def _push(level: str, msg: str, extra: Dict = None):
    with _LOCK:
        _RECENT.append({
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "level": level,
            "message": msg,
            "extra": extra or {},
        })


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)

    # 封装一层，顺便写入 _RECENT
    orig_info = logger.info
    orig_warn = logger.warning
    orig_err = logger.error
    orig_dbg = logger.debug

    def info(msg, *args, extra=None, **kwargs):
        orig_info(msg, *args, **kwargs)
        _push("INFO", msg % args if args else msg, extra)

    def warn(msg, *args, extra=None, **kwargs):
        orig_warn(msg, *args, **kwargs)
        _push("WARN", msg % args if args else msg, extra)

    def error(msg, *args, extra=None, **kwargs):
        orig_err(msg, *args, **kwargs)
        _push("ERROR", msg % args if args else msg, extra)

    def debug(msg, *args, extra=None, **kwargs):
        orig_dbg(msg, *args, **kwargs)
        _push("DEBUG", msg % args if args else msg, extra)

    logger.info = info
    logger.warning = warn
    logger.error = error
    logger.debug = debug
    return logger


def recent_logs(limit: int = 200) -> List[Dict]:
    with _LOCK:
        return list(_RECENT)[-limit:][::-1]   # 最新在前


def clear_logs():
    with _LOCK:
        _RECENT.clear()
