"""
学生档案管理
- students_index.json 保存所有学生的概要（用于登录选择）
- 每个学生在 data/students/{sid}/ 下有独立目录
- 头像以文件形式存放在 data/avatars/ 下（或用 emoji）
"""
import json
import os
import shutil
import threading
import uuid
from datetime import datetime
from typing import List, Dict, Optional

import config
from modules.applogger import get_logger

logger = get_logger(__name__)

_LOCK = threading.Lock()


def _new_sid() -> str:
    return uuid.uuid4().hex[:10]


def _load_index() -> List[Dict]:
    if not os.path.exists(config.STUDENTS_INDEX_FILE):
        return []
    try:
        with open(config.STUDENTS_INDEX_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error("读取学生索引失败: %s", e)
        return []


def _save_index(items: List[Dict]):
    tmp = config.STUDENTS_INDEX_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    os.replace(tmp, config.STUDENTS_INDEX_FILE)


def _student_dir(sid: str) -> str:
    d = os.path.join(config.STUDENTS_DIR, sid)
    os.makedirs(d, exist_ok=True)
    return d


def list_all() -> List[Dict]:
    """返回全部学生概要"""
    with _LOCK:
        items = _load_index()
    # 带上统计信息
    for it in items:
        try:
            it["stats"] = _compute_stats(it["id"])
        except Exception:
            it["stats"] = {}
    return items


def get(sid: str) -> Optional[Dict]:
    with _LOCK:
        items = _load_index()
        for it in items:
            if it["id"] == sid:
                return it
        return None


def create(name: str, avatar: str = "", grade: str = "",
           subject: str = "", note: str = "") -> Dict:
    """创建新学生。avatar 可以是 emoji，或 /static/avatars/xxx.png 这样的路径"""
    sid = _new_sid()
    now = datetime.now().isoformat(timespec="seconds")
    item = {
        "id": sid,
        "name": name.strip() or "未命名",
        "avatar": avatar.strip() or "🧑‍🎓",
        "grade": grade.strip(),
        "subject": subject.strip(),
        "note": note.strip(),
        "created_at": now,
    }
    with _LOCK:
        items = _load_index()
        items.append(item)
        _save_index(items)
    _student_dir(sid)   # 初始化学生目录
    logger.info("创建学生 %s (%s)", sid, name)
    return item


def update(sid: str, patch: Dict) -> Optional[Dict]:
    allowed = {"name", "avatar", "grade", "subject", "note"}
    with _LOCK:
        items = _load_index()
        for it in items:
            if it["id"] == sid:
                for k, v in (patch or {}).items():
                    if k in allowed and v is not None:
                        it[k] = str(v).strip()
                it["updated_at"] = datetime.now().isoformat(timespec="seconds")
                _save_index(items)
                logger.info("更新学生 %s", sid)
                return it
    return None


def delete(sid: str) -> bool:
    """彻底删除学生：索引 + 学生目录 + 相关 RAG collection（由调用方处理）"""
    with _LOCK:
        items = _load_index()
        new_items = [x for x in items if x["id"] != sid]
        if len(new_items) == len(items):
            return False
        _save_index(new_items)
    sdir = os.path.join(config.STUDENTS_DIR, sid)
    if os.path.exists(sdir):
        shutil.rmtree(sdir, ignore_errors=True)
    logger.info("删除学生 %s", sid)
    return True


# ==============================================================
#                         统计摘要
# ==============================================================
def _compute_stats(sid: str) -> Dict:
    """给学生列表页用的快速概览"""
    from modules import error_book, knowledge_graph
    stats = {
        "error_count": 0,
        "history_count": 0,
        "knowledge_count": 0,
        "accuracy": None,
    }
    try:
        hist = _load_history(sid)
        stats["history_count"] = len(hist)
        if hist:
            cor = sum(1 for h in hist if h.get("is_correct") is True)
            total = sum(1 for h in hist if h.get("is_correct") in (True, False))
            if total > 0:
                stats["accuracy"] = round(cor / total * 100, 1)
        stats["error_count"] = error_book.count(sid)
        stats["knowledge_count"] = knowledge_graph.count_points(sid)
    except Exception as e:
        logger.warning("计算学生 %s 统计失败: %s", sid, e)
    return stats


# ==============================================================
#             学生级 JSON 文件读写（通用工具）
# ==============================================================
def student_file(sid: str, name: str) -> str:
    return os.path.join(_student_dir(sid), name)


def read_json(sid: str, name: str, default):
    path = student_file(sid, name)
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error("读 %s/%s 失败: %s", sid, name, e)
        return default


def write_json(sid: str, name: str, data):
    path = student_file(sid, name)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# ==============================================================
#                        历史记录（所有批改过的题）
# ==============================================================
def _load_history(sid: str) -> List[Dict]:
    return read_json(sid, "history.json", [])


def append_history(sid: str, records: List[Dict]):
    """把一次批改的结果追加到 history.json（限制长度避免无限增长）"""
    if not records:
        return
    hist = _load_history(sid)
    hist.extend(records)
    if len(hist) > 5000:
        hist = hist[-5000:]
    write_json(sid, "history.json", hist)


def get_history(sid: str, limit: int = 200) -> List[Dict]:
    hist = _load_history(sid)
    return hist[-limit:][::-1]
