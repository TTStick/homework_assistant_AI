"""
能力分析 —— 多维雷达
- 维度来自 config.ABILITY_DIMENSIONS
- 每道题批改后，LLM 会标注命中的几个维度（在 grader 的提取步骤里完成）
- 维度得分 = 该维度上「对」的次数 / 总次数 * 100；没数据时 = 50 (中性)
- 同时给出 AI 生成的改进建议
"""
import threading
from datetime import datetime
from typing import Dict, List

import config
from modules import student_manager, llm_providers
from modules.applogger import get_logger

logger = get_logger(__name__)

_LOCK = threading.Lock()

FILE_NAME = "ability.json"


def _empty() -> Dict:
    base = {d: {"correct": 0, "wrong": 0} for d in config.ABILITY_DIMENSIONS}
    return {
        "dimensions": base,
        "last_updated": "",
        "advice": "",
        "advice_at": "",
    }


def _load(sid: str) -> Dict:
    data = student_manager.read_json(sid, FILE_NAME, _empty())
    if not isinstance(data, dict) or "dimensions" not in data:
        return _empty()
    # 补全新增维度
    for d in config.ABILITY_DIMENSIONS:
        data["dimensions"].setdefault(d, {"correct": 0, "wrong": 0})
    return data


def _save(sid: str, data: Dict):
    student_manager.write_json(sid, FILE_NAME, data)


# ------------------------------------------------------------------
#                       雷达图数据
# ------------------------------------------------------------------
def radar(sid: str) -> Dict:
    with _LOCK:
        data = _load(sid)
    dims = []
    for name in config.ABILITY_DIMENSIONS:
        d = data["dimensions"].get(name, {"correct": 0, "wrong": 0})
        c = int(d.get("correct", 0))
        w = int(d.get("wrong", 0))
        total = c + w
        score = 50.0 if total == 0 else round(100.0 * c / total, 1)
        dims.append({
            "name": name,
            "score": score,
            "correct": c,
            "wrong": w,
            "total": total,
        })
    return {
        "dimensions": dims,
        "last_updated": data.get("last_updated", ""),
        "advice": data.get("advice", ""),
        "advice_at": data.get("advice_at", ""),
    }


# ------------------------------------------------------------------
#                       批改结果并入
# ------------------------------------------------------------------
def ingest_graded(sid: str, extracted: List[Dict]):
    """
    extracted 条目需含: is_correct (bool), error_categories (list of dim name)
    规则:
      - 正确: 认为每个相关维度都算一次 "correct"；若 LLM 没给 categories，则
              计入所有维度 (正确答题，全面加分)
      - 错误: 把 LLM 标注的错因维度各记一次 "wrong"
    """
    if not extracted:
        return
    with _LOCK:
        data = _load(sid)
        dims = data["dimensions"]
        for rec in extracted:
            ok = rec.get("is_correct")
            cats = [c for c in (rec.get("error_categories") or []) if c in dims]
            if ok is True:
                target = cats if cats else list(dims.keys())
                for d in target:
                    dims[d]["correct"] = int(dims[d].get("correct", 0)) + 1
            elif ok is False:
                target = cats if cats else ["概念理解"]  # 没给就扣到概念理解
                for d in target:
                    dims[d]["wrong"] = int(dims[d].get("wrong", 0)) + 1
        data["last_updated"] = datetime.now().isoformat(timespec="seconds")
        _save(sid, data)


# ------------------------------------------------------------------
#                      AI 生成改进建议
# ------------------------------------------------------------------
ADVICE_SYSTEM = """你是一位细心、耐心的中小学老师。根据学生近期的错题和能力画像，
给出针对性的改进建议。语言具体、温和、鼓励，不要空话套话。"""


def generate_advice(sid: str) -> str:
    from modules import error_book, knowledge_graph
    radar_data = radar(sid)
    student = student_manager.get(sid) or {}

    wp = knowledge_graph.weak_points(sid, limit=5)
    fq_kp = error_book.frequent_knowledge_points(sid, top_n=5)
    fq_cat = error_book.frequent_error_categories(sid)
    fq_type = error_book.frequent_question_types(sid)

    # 整理雷达概要
    dim_lines = "\n".join(
        f"- {d['name']}: {d['score']}/100 （对{d['correct']} 错{d['wrong']}）"
        for d in radar_data["dimensions"]
    )
    wp_lines = "\n".join(
        f"- {p['name']}: 掌握度 {p['mastery']}/100, 对{p['correct']} 错{p['wrong']}"
        for p in wp
    ) or "（暂无数据）"
    fq_kp_lines = "\n".join(f"- {x['name']} (错 {x['count']} 次)" for x in fq_kp) or "（暂无数据）"
    fq_cat_lines = "\n".join(f"- {x['category']}: 错 {x['count']} 次" for x in fq_cat) or "（暂无数据）"
    fq_type_lines = "\n".join(f"- {x['type']}: 错 {x['count']} 次" for x in fq_type) or "（暂无数据）"

    prompt = f"""请基于下述数据，给出针对学生「{student.get('name', '同学')}」的具体改进建议。

【年级】{student.get('grade') or '未填'}
【学科】{student.get('subject') or '未填'}

【能力雷达（六维 0~100）】
{dim_lines}

【最薄弱知识点】
{wp_lines}

【错题里高频知识点】
{fq_kp_lines}

【错因分布】
{fq_cat_lines}

【题型分布】
{fq_type_lines}

请以 Markdown 输出，包含以下 4 个小节，不要废话：
## 一、总体画像
（不超过 3 句话，点出最突出的 1~2 个问题）

## 二、优先突破
（列 2~3 个应优先攻克的知识点，各给 1 句具体的练习方向）

## 三、习惯建议
（针对错因分布，给出 2~3 条做题/检查习惯上的建议，每条 1~2 句）

## 四、一周行动计划
（给出最多 5 条可执行的本周任务，带动词 + 量词，如"每天抽 10 分钟..."）"""
    try:
        out = llm_providers.text_chat(
            prompt=prompt, system=ADVICE_SYSTEM, temperature=0.4
        )
        advice = out.strip()
    except Exception as e:
        logger.error("generate_advice 失败: %s", e)
        advice = f"*（生成失败：{e}）*"

    with _LOCK:
        data = _load(sid)
        data["advice"] = advice
        data["advice_at"] = datetime.now().isoformat(timespec="seconds")
        _save(sid, data)
    return advice


def reset(sid: str):
    with _LOCK:
        _save(sid, _empty())
    logger.info("能力雷达重置 [%s]", sid)
