"""
相似题 / 练习题 生成
- 基于一道错题 或一组知识点，让 LLM 生成若干同知识点同题型的新题（带答案 + 解析）
- 结果保存在学生目录下 generated.json
"""
import json
import threading
import uuid
from datetime import datetime
from typing import Dict, List, Optional

from modules import student_manager, llm_providers, error_book
from modules.applogger import get_logger

logger = get_logger(__name__)

_LOCK = threading.Lock()

FILE_NAME = "generated.json"


# ===================================================================
#                     存储
# ===================================================================
def _load(sid: str) -> List[Dict]:
    return student_manager.read_json(sid, FILE_NAME, [])


def _save(sid: str, items: List[Dict]):
    student_manager.write_json(sid, FILE_NAME, items)


def list_all(sid: str) -> List[Dict]:
    with _LOCK:
        items = _load(sid)
    items.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return items


def get(sid: str, pid: str) -> Optional[Dict]:
    with _LOCK:
        for it in _load(sid):
            if it["id"] == pid:
                return it
    return None


def _store(sid: str, batch: Dict):
    with _LOCK:
        items = _load(sid)
        items.append(batch)
        if len(items) > 200:
            items = items[-200:]
        _save(sid, items)


def delete(sid: str, pid: str) -> bool:
    with _LOCK:
        items = _load(sid)
        new_items = [x for x in items if x["id"] != pid]
        if len(new_items) == len(items):
            return False
        _save(sid, new_items)
    return True


def clear(sid: str):
    with _LOCK:
        _save(sid, [])


# ===================================================================
#                     LLM 生成
# ===================================================================
GENERATE_SYSTEM = """你是一位命题老师，擅长根据学生的薄弱知识点，
生成和原题同类型、同知识点但数值/情境不同的变式题。
你的输出必须是合法的 JSON，不要有任何额外文字、不要 markdown 代码块围栏。"""


def _build_prompt(base: Dict, count: int, difficulty: str) -> str:
    qtype = base.get("question_type", "normal")
    kps = base.get("knowledge_points") or []
    kp_str = "、".join(kps) if kps else "（未指定）"
    q_text = base.get("question_text", "")
    err = base.get("error_reason", "")
    qtype_str = {
        "multiple_choice": "选择题（含 A/B/C/D 四个选项）",
        "fill_blank": "填空题",
        "normal": "解答/计算题",
    }.get(qtype, "解答题")

    diff_hint = {
        "easy": "难度略低，重点巩固基础",
        "same": "难度与原题相当",
        "hard": "难度略高，加入综合或变式",
    }.get(difficulty, "难度与原题相当")

    schema_hint = ""
    if qtype == "multiple_choice":
        schema_hint = """每道题的 JSON 结构：
{
  "question": "题干",
  "options": [{"label":"A","text":"..."},{"label":"B","text":"..."},{"label":"C","text":"..."},{"label":"D","text":"..."}],
  "answer": "正确选项字母（大写）",
  "explanation": "2~4 句解析"
}"""
    elif qtype == "fill_blank":
        schema_hint = """每道题的 JSON 结构：
{
  "question": "题干（用 ___ 表示空位）",
  "answer": "各空答案，若多空用「；」分隔",
  "explanation": "2~4 句解析"
}"""
    else:
        schema_hint = """每道题的 JSON 结构：
{
  "question": "题干",
  "answer": "标准答案或最终结果",
  "explanation": "完整解题过程，步骤分明"
}"""

    return f"""请基于下面这道**原题**，生成 {count} 道**变式练习题**。

【原题】
{q_text}

【原题类型】{qtype_str}
【相关知识点】{kp_str}
【学生当时的错因】{err or '（无）'}
【难度要求】{diff_hint}

要求：
1. 生成的题目必须考察相同的知识点；
2. 题型保持一致（都是{qtype_str}）；
3. 题目表述与原题**不能完全一样**，数值/情境要有变化；
4. 每题必须给出答案与解析；
5. 用中文表述。

{schema_hint}

最终整体按如下 JSON 结构返回（不要任何多余文字，不要代码块围栏）：
{{
  "items": [ ... {count} 道题 ... ]
}}"""


def generate_from_error(sid: str, error_id: str,
                        count: int = 3, difficulty: str = "same") -> Dict:
    """基于错题本里的某一道题生成练习"""
    err = error_book.get(sid, error_id)
    if not err:
        raise ValueError(f"错题不存在: {error_id}")
    return _generate_and_store(sid, base=err, count=count,
                               difficulty=difficulty,
                               source_label=f"error:{error_id}")


def generate_from_knowledge_point(sid: str, point: str,
                                  count: int = 3,
                                  difficulty: str = "same",
                                  qtype: str = "normal") -> Dict:
    """基于某个知识点（没有具体错题做参考）生成练习"""
    base = {
        "question_text": f"请考察下述知识点：{point}",
        "question_type": qtype,
        "knowledge_points": [point],
        "error_reason": "",
    }
    return _generate_and_store(sid, base=base, count=count,
                               difficulty=difficulty,
                               source_label=f"kp:{point}")


def _generate_and_store(sid: str, base: Dict, count: int,
                        difficulty: str, source_label: str) -> Dict:
    count = max(1, min(int(count or 3), 8))
    prompt = _build_prompt(base, count, difficulty)
    logger.info("generate [%s] count=%d diff=%s base=%s",
                sid, count, difficulty, (base.get("question_text", "") or "")[:50])
    raw = llm_providers.text_chat(
        prompt=prompt, system=GENERATE_SYSTEM, temperature=0.6
    )
    data = llm_providers.parse_json(raw)
    items: List[Dict] = []
    if data and isinstance(data, dict) and isinstance(data.get("items"), list):
        for q in data["items"]:
            if not isinstance(q, dict):
                continue
            items.append({
                "question": str(q.get("question", "")).strip(),
                "options": q.get("options") or [],
                "answer": str(q.get("answer", "")).strip(),
                "explanation": str(q.get("explanation", "")).strip(),
            })

    batch = {
        "id": uuid.uuid4().hex[:12],
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "source_base_question": base.get("question_text", ""),
        "source_question_type": base.get("question_type", "normal"),
        "source_knowledge_points": base.get("knowledge_points") or [],
        "source_label": source_label,
        "difficulty": difficulty,
        "count": len(items),
        "items": items,
        "raw": "" if items else (raw or "")[:500],  # 解析失败时保留原文
    }
    _store(sid, batch)
    return batch
