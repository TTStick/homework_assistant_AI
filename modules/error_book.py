"""
错题本 —— 按学生独立存储
每条错题记录:
  id, question_text, question_type, student_answer, correct_answer,
  error_reason, explanation, knowledge_points (list of str),
  error_categories (list of str, 来自 ABILITY_DIMENSIONS),
  source ('batch'/'realtime'/'manual'),
  created_at, mastered (bool, 是否已掌握)
"""
import threading
import uuid
from datetime import datetime
from typing import List, Dict, Optional

import config
from modules import student_manager
from modules.applogger import get_logger

logger = get_logger(__name__)

_LOCK = threading.Lock()

FILE_NAME = "errors.json"


def _load(sid: str) -> List[Dict]:
    return student_manager.read_json(sid, FILE_NAME, [])


def _save(sid: str, items: List[Dict]):
    student_manager.write_json(sid, FILE_NAME, items)


def list_all(sid: str, include_mastered: bool = True) -> List[Dict]:
    with _LOCK:
        items = _load(sid)
    if not include_mastered:
        items = [x for x in items if not x.get("mastered")]
    items.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return items


def count(sid: str) -> int:
    with _LOCK:
        return len(_load(sid))


def get(sid: str, eid: str) -> Optional[Dict]:
    with _LOCK:
        for it in _load(sid):
            if it["id"] == eid:
                return it
        return None


def add(sid: str, record: Dict) -> Dict:
    """添加一条错题；record 必须至少含 question_text"""
    item = {
        "id": uuid.uuid4().hex[:12],
        "question_text": record.get("question_text", "").strip(),
        "question_type": record.get("question_type", "normal"),
        "student_answer": record.get("student_answer", "").strip(),
        "correct_answer": record.get("correct_answer", "").strip(),
        "error_reason": record.get("error_reason", "").strip(),
        "explanation": record.get("explanation", "").strip(),
        "knowledge_points": record.get("knowledge_points", []) or [],
        "error_categories": record.get("error_categories", []) or [],
        "source": record.get("source", "manual"),
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "mastered": False,
    }
    with _LOCK:
        items = _load(sid)
        items.append(item)
        _save(sid, items)
    logger.info("错题本 +1 [%s] %s", sid, item["question_text"][:40])
    return item


def delete(sid: str, eid: str) -> bool:
    with _LOCK:
        items = _load(sid)
        new_items = [x for x in items if x["id"] != eid]
        if len(new_items) == len(items):
            return False
        _save(sid, new_items)
    logger.info("错题本删除 [%s] %s", sid, eid)
    return True


def set_mastered(sid: str, eid: str, mastered: bool) -> bool:
    with _LOCK:
        items = _load(sid)
        for it in items:
            if it["id"] == eid:
                it["mastered"] = bool(mastered)
                it["mastered_at"] = datetime.now().isoformat(timespec="seconds") \
                    if mastered else ""
                _save(sid, items)
                return True
    return False


def clear(sid: str):
    with _LOCK:
        _save(sid, [])
    logger.info("错题本清空 [%s]", sid)


# ==================================================================
#                       统计 / 聚合
# ==================================================================
def frequent_knowledge_points(sid: str, top_n: int = 10) -> List[Dict]:
    """返回错题中出现最频繁的知识点"""
    with _LOCK:
        items = _load(sid)
    counter: Dict[str, int] = {}
    for it in items:
        if it.get("mastered"):
            continue
        for kp in it.get("knowledge_points", []):
            if not kp:
                continue
            counter[kp] = counter.get(kp, 0) + 1
    ranked = sorted(counter.items(), key=lambda x: -x[1])
    return [{"name": k, "count": v} for k, v in ranked[:top_n]]


def frequent_question_types(sid: str) -> List[Dict]:
    with _LOCK:
        items = _load(sid)
    counter: Dict[str, int] = {}
    for it in items:
        if it.get("mastered"):
            continue
        t = it.get("question_type") or "normal"
        counter[t] = counter.get(t, 0) + 1
    ranked = sorted(counter.items(), key=lambda x: -x[1])
    return [{"type": k, "count": v} for k, v in ranked]


def frequent_error_categories(sid: str) -> List[Dict]:
    """错因维度分布（对应能力雷达）"""
    with _LOCK:
        items = _load(sid)
    counter: Dict[str, int] = {}
    for it in items:
        if it.get("mastered"):
            continue
        for c in it.get("error_categories", []):
            if not c:
                continue
            counter[c] = counter.get(c, 0) + 1
    ranked = sorted(counter.items(), key=lambda x: -x[1])
    return [{"category": k, "count": v} for k, v in ranked]
