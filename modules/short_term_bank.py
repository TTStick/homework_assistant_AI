"""
短期题库 —— 两层结构
- 全局（global）：所有学生共用，存于 data/global_short_term.json
- 按学生：每个学生目录下独立 short_term.json
- as_prompt_snippet(sid) 会把两层合并拼成 system prompt 片段
- 均受 SHORT_TERM_MAX 容量限制，超出淘汰最旧
"""
import os
import json
import uuid
import threading
from datetime import datetime
from typing import List, Dict, Optional

import config
from modules import student_manager
from modules.applogger import get_logger

logger = get_logger(__name__)

_LOCK = threading.Lock()

FILE_NAME = "short_term.json"
GLOBAL_FILE = os.path.join(config.DATA_DIR, "global_short_term.json")


# ================================================================
#                   通用 read/write
# ================================================================
def _load_global() -> List[Dict]:
    if not os.path.exists(GLOBAL_FILE):
        return []
    try:
        with open(GLOBAL_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error("短期全局题库读取失败: %s", e)
        return []


def _save_global(items: List[Dict]):
    tmp = GLOBAL_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    os.replace(tmp, GLOBAL_FILE)


def _load_student(sid: str) -> List[Dict]:
    return student_manager.read_json(sid, FILE_NAME, [])


def _save_student(sid: str, items: List[Dict]):
    student_manager.write_json(sid, FILE_NAME, items)


# ================================================================
#                   按学生 API
# ================================================================
def list_all(sid: str) -> List[Dict]:
    with _LOCK:
        return _load_student(sid)


def add(sid: str, question: str, solution: str, note: str = "") -> Dict:
    with _LOCK:
        items = _load_student(sid)
        item = {
            "id": uuid.uuid4().hex[:12],
            "question": question.strip(),
            "solution": solution.strip(),
            "note": note.strip(),
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "scope": "student",
        }
        items.append(item)
        if len(items) > config.SHORT_TERM_MAX:
            items = items[-config.SHORT_TERM_MAX:]
        _save_student(sid, items)
        logger.info("short_term 添加 [%s]: %s", sid, item["id"])
        return item


def delete(sid: str, item_id: str) -> bool:
    with _LOCK:
        items = _load_student(sid)
        new_items = [x for x in items if x["id"] != item_id]
        changed = len(new_items) != len(items)
        if changed:
            _save_student(sid, new_items)
            logger.info("short_term 删除 [%s]: %s", sid, item_id)
        return changed


def clear(sid: str):
    with _LOCK:
        _save_student(sid, [])
    logger.info("short_term 清空 [%s]", sid)


# ================================================================
#                   全局 API
# ================================================================
def list_global() -> List[Dict]:
    with _LOCK:
        return _load_global()


def add_global(question: str, solution: str, note: str = "") -> Dict:
    with _LOCK:
        items = _load_global()
        item = {
            "id": uuid.uuid4().hex[:12],
            "question": question.strip(),
            "solution": solution.strip(),
            "note": note.strip(),
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "scope": "global",
        }
        items.append(item)
        if len(items) > config.SHORT_TERM_MAX:
            items = items[-config.SHORT_TERM_MAX:]
        _save_global(items)
        logger.info("short_term 全局添加: %s", item["id"])
        return item


def delete_global(item_id: str) -> bool:
    with _LOCK:
        items = _load_global()
        new_items = [x for x in items if x["id"] != item_id]
        changed = len(new_items) != len(items)
        if changed:
            _save_global(new_items)
            logger.info("short_term 全局删除: %s", item_id)
        return changed


def clear_global():
    with _LOCK:
        _save_global([])
    logger.info("short_term 全局清空")


# ================================================================
#              拼进 system prompt 的片段（学生+全局合并）
# ================================================================
def as_prompt_snippet(sid: Optional[str]) -> str:
    """把全局+该学生的短期题库一并拼成 prompt 片段。
    全局条目先出，学生条目后出；各自最多 10 条以控制长度。"""
    parts: List[str] = []

    g_items = list_global()
    if g_items:
        lines = ["【全局短期题库（所有学生共用的参考题）】"]
        for i, it in enumerate(g_items[-10:], 1):
            lines.append(f"{i}. 题目：{it['question']}")
            if it["solution"]:
                lines.append(f"   参考解答：{it['solution']}")
            if it["note"]:
                lines.append(f"   备注：{it['note']}")
        parts.append("\n".join(lines))

    if sid:
        s_items = list_all(sid)
        if s_items:
            lines = ["【该学生专属的短期题库】"]
            for i, it in enumerate(s_items[-10:], 1):
                lines.append(f"{i}. 题目：{it['question']}")
                if it["solution"]:
                    lines.append(f"   参考解答：{it['solution']}")
                if it["note"]:
                    lines.append(f"   备注：{it['note']}")
            parts.append("\n".join(lines))

    return "\n\n".join(parts)
