"""
知识图谱 —— 按学生独立存储
- 每个知识点: {name, subject, correct, wrong, mastery (0-100), last_seen, related}
- relations: [{source, target, type}]  type = 'prereq' (前置) / 'related' (相关)
- 知识点及关系从题目批改结果中增量抽取（LLM 文本调用）
- 支持让 AI 对某个知识点做详细讲解
"""
import threading
from datetime import datetime
from typing import Dict, List, Optional

import config
from modules import student_manager, llm_providers
from modules.applogger import get_logger

logger = get_logger(__name__)

_LOCK = threading.Lock()

FILE_NAME = "knowledge.json"


def _empty_graph() -> Dict:
    return {"points": {}, "relations": []}


def _load(sid: str) -> Dict:
    data = student_manager.read_json(sid, FILE_NAME, _empty_graph())
    if not isinstance(data, dict):
        return _empty_graph()
    data.setdefault("points", {})
    data.setdefault("relations", [])
    return data


def _save(sid: str, data: Dict):
    student_manager.write_json(sid, FILE_NAME, data)


def get_graph(sid: str) -> Dict:
    with _LOCK:
        g = _load(sid)
    # 组装适合前端渲染的结构
    nodes = []
    for name, info in g["points"].items():
        correct = int(info.get("correct", 0))
        wrong = int(info.get("wrong", 0))
        total = correct + wrong
        mastery = info.get("mastery")
        if mastery is None:
            mastery = 100 * correct / total if total > 0 else 50
        nodes.append({
            "id": name,
            "name": name,
            "subject": info.get("subject", ""),
            "correct": correct,
            "wrong": wrong,
            "total": total,
            "mastery": round(float(mastery), 1),
            "last_seen": info.get("last_seen", ""),
        })
    # 去重 relations
    seen = set()
    edges = []
    for r in g["relations"]:
        key = (r.get("source"), r.get("target"), r.get("type", "related"))
        if key in seen or None in key:
            continue
        seen.add(key)
        edges.append({
            "source": key[0], "target": key[1], "type": key[2],
        })
    return {"nodes": nodes, "edges": edges}


def count_points(sid: str) -> int:
    with _LOCK:
        g = _load(sid)
    return len(g.get("points", {}))


def weak_points(sid: str, limit: int = 10) -> List[Dict]:
    """按掌握度升序返回薄弱知识点（只考虑至少练过一次的）"""
    g = get_graph(sid)
    nodes = [n for n in g["nodes"] if n["total"] > 0]
    nodes.sort(key=lambda n: (n["mastery"], -n["wrong"]))
    return nodes[:limit]


# ====================================================================
#             核心：把批改结果并入知识图谱
# ====================================================================
def ingest_graded(sid: str, extracted: List[Dict]):
    """
    extracted 是一系列 {knowledge_points: [...], is_correct: bool, subject: ""} 结构
    每条对应一道题，把对/错计数累加到对应知识点
    同时根据 LLM 给出的 related 关系累加 relations
    """
    if not extracted:
        return
    now = datetime.now().isoformat(timespec="seconds")
    with _LOCK:
        g = _load(sid)
        points = g["points"]
        relations = g["relations"]
        existing_rel = {(r.get("source"), r.get("target"), r.get("type", "related"))
                        for r in relations}
        for rec in extracted:
            kps = rec.get("knowledge_points") or []
            ok = rec.get("is_correct")
            subj = rec.get("subject", "")
            for kp in kps:
                kp = (kp or "").strip()
                if not kp:
                    continue
                node = points.setdefault(kp, {
                    "subject": subj, "correct": 0, "wrong": 0,
                    "last_seen": now,
                })
                if ok is True:
                    node["correct"] = int(node.get("correct", 0)) + 1
                elif ok is False:
                    node["wrong"] = int(node.get("wrong", 0)) + 1
                node["last_seen"] = now
                # 计算掌握度 (指数平滑)
                c, w = int(node.get("correct", 0)), int(node.get("wrong", 0))
                node["mastery"] = round(100.0 * c / (c + w), 1) if (c + w) else 50.0
                if subj and not node.get("subject"):
                    node["subject"] = subj
            # 同一道题里的知识点两两互为 related
            for i in range(len(kps)):
                for j in range(i + 1, len(kps)):
                    a, b = kps[i], kps[j]
                    if not a or not b or a == b:
                        continue
                    key = (a, b, "related")
                    if key not in existing_rel:
                        existing_rel.add(key)
                        relations.append({"source": a, "target": b, "type": "related"})
        _save(sid, g)


def add_relation(sid: str, source: str, target: str, rtype: str = "related"):
    with _LOCK:
        g = _load(sid)
        for r in g["relations"]:
            if r.get("source") == source and r.get("target") == target \
                    and r.get("type") == rtype:
                return
        g["relations"].append({"source": source, "target": target, "type": rtype})
        _save(sid, g)


# ====================================================================
#                 AI 讲解某个知识点
# ====================================================================
EXPLAIN_SYSTEM = """你是一位资深的中小学教师，正在给学生讲解知识点。
语言要简洁、清晰、有条理，适合中小学生理解。输出严格用 Markdown 格式。"""


def explain_point(sid: str, point_name: str, context: str = "") -> str:
    """让 AI 对某个知识点做详细讲解（可携带学生层面的上下文，例如它的掌握度）"""
    student = student_manager.get(sid) or {}
    grade = student.get("grade", "")
    subject = student.get("subject", "")

    # 拿到该知识点的统计
    g = _load(sid)
    info = g["points"].get(point_name, {})
    mastery = info.get("mastery", None)
    correct = info.get("correct", 0)
    wrong = info.get("wrong", 0)

    stat_line = ""
    if correct or wrong:
        stat_line = f"（该学生在此知识点上: 做对 {correct} 次，做错 {wrong} 次，当前掌握度约 {mastery}/100）"

    prompt = f"""请面向学生「{student.get('name', '同学')}」讲解知识点：**{point_name}**
学生年级/学段：{grade or '不限'}
学科：{subject or '不限'}
{stat_line}

请按以下结构给出讲解（Markdown 格式）：
1. **核心定义**（一两句话说清是什么）
2. **关键要点**（列出 3~5 条必须掌握的要点）
3. **典型例题**（给出 1 道典型题并给出完整解法）
4. **常见易错**（列出 2~3 条常见的错误或陷阱）
5. **练习建议**（给出一条进一步练习的建议）

{context}

请开始讲解，不要添加多余的开场白，直接从「## {point_name}」开始。"""
    try:
        out = llm_providers.text_chat(
            prompt=prompt, system=EXPLAIN_SYSTEM, temperature=0.3
        )
        return out.strip()
    except Exception as e:
        logger.error("explain_point 失败: %s", e)
        return f"## {point_name}\n\n*（生成讲解失败：{e}）*"


# ====================================================================
#              手动清理（比如学生不想要某个节点）
# ====================================================================
def remove_point(sid: str, name: str) -> bool:
    with _LOCK:
        g = _load(sid)
        if name in g["points"]:
            del g["points"][name]
            g["relations"] = [r for r in g["relations"]
                              if r.get("source") != name and r.get("target") != name]
            _save(sid, g)
            return True
    return False


def clear(sid: str):
    with _LOCK:
        _save(sid, _empty_graph())
    logger.info("知识图谱清空 [%s]", sid)
