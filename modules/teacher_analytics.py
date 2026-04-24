"""
教师面板 —— 班级级聚合
使用多线程并行读取各学生档案，最大程度减少响应延迟。
"""
from typing import List, Dict, Any, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import time

import config
from modules import (
    student_manager, error_book, knowledge_graph,
    ability_analyzer, llm_providers,
)
from modules.applogger import get_logger

logger = get_logger(__name__)

# 缓存聚合结果：避免频繁 IO
_CACHE: Dict[str, Any] = {"data": None, "ts": 0.0}
_CACHE_TTL = 8.0   # 秒
_CACHE_LOCK = threading.Lock()


# =====================================================================
#                       逐学生摘要（并行读取）
# =====================================================================
def _one_student_snapshot(sid: str) -> Dict:
    """读一个学生的所有关键数据 —— 只读 JSON，轻量快速"""
    try:
        stu = student_manager.get(sid) or {}
        hist = student_manager.read_json(sid, "history.json", [])
        errs = student_manager.read_json(sid, "errors.json", [])
        kg = student_manager.read_json(sid, "knowledge.json",
                                        {"points": {}, "relations": []})
        ab = student_manager.read_json(sid, "ability.json", None)

        total_graded = len(hist)
        correct_cnt = sum(1 for h in hist if h.get("is_correct") is True)
        judged_cnt = sum(1 for h in hist if h.get("is_correct") in (True, False))
        accuracy = round(correct_cnt / judged_cnt * 100, 1) if judged_cnt else None

        unmastered = [e for e in errs if not e.get("mastered")]
        mastered_cnt = sum(1 for e in errs if e.get("mastered"))

        points = (kg or {}).get("points", {}) or {}
        # 计算当前 mastery —— 如果没存 mastery 字段就 recompute
        point_list = []
        for name, info in points.items():
            c = int(info.get("correct", 0))
            w = int(info.get("wrong", 0))
            t = c + w
            m = info.get("mastery")
            if m is None:
                m = 100 * c / t if t > 0 else 50
            point_list.append({
                "name": name,
                "mastery": round(float(m), 1),
                "correct": c, "wrong": w, "total": t,
                "subject": info.get("subject", ""),
            })

        # 能力雷达维度
        dims = {}
        if ab and isinstance(ab, dict):
            dim_map = (ab.get("dimensions") or {})
            for name in config.ABILITY_DIMENSIONS:
                d = dim_map.get(name, {"correct": 0, "wrong": 0})
                c = int(d.get("correct", 0))
                w = int(d.get("wrong", 0))
                total = c + w
                score = 50.0 if total == 0 else round(100.0 * c / total, 1)
                dims[name] = {"score": score, "correct": c, "wrong": w,
                              "total": total}
        else:
            for name in config.ABILITY_DIMENSIONS:
                dims[name] = {"score": 50.0, "correct": 0, "wrong": 0, "total": 0}

        return {
            "id": sid,
            "name": stu.get("name") or "未命名",
            "avatar": stu.get("avatar") or "🧑‍🎓",
            "grade": stu.get("grade") or "",
            "subject": stu.get("subject") or "",
            "note": stu.get("note") or "",
            "accuracy": accuracy,
            "graded_count": total_graded,
            "correct_count": correct_cnt,
            "judged_count": judged_cnt,
            "error_count": len(unmastered),
            "error_total": len(errs),
            "error_mastered": mastered_cnt,
            "knowledge_points": point_list,
            "knowledge_count": len(point_list),
            "ability_dimensions": dims,
            "history": hist,
            "errors": errs,
        }
    except Exception as e:
        logger.error("聚合学生 %s 失败: %s", sid, e)
        return {"id": sid, "name": "(加载失败)", "error": str(e)}


def load_all_students(parallel: bool = True) -> List[Dict]:
    sids = [s["id"] for s in student_manager.list_all()]
    if not sids:
        return []
    if not parallel or len(sids) <= 1:
        return [_one_student_snapshot(s) for s in sids]
    out = [None] * len(sids)
    with ThreadPoolExecutor(max_workers=min(8, len(sids)),
                            thread_name_prefix="teacher") as pool:
        futs = {pool.submit(_one_student_snapshot, s): i for i, s in enumerate(sids)}
        for fut in as_completed(futs):
            i = futs[fut]
            out[i] = fut.result()
    return [x for x in out if x]


# =====================================================================
#                       班级聚合
# =====================================================================
def _frequency(items, key_fn) -> List[Dict]:
    c: Dict[str, int] = {}
    for it in items:
        key = key_fn(it)
        if not key:
            continue
        c[key] = c.get(key, 0) + 1
    return [{"name": k, "count": v} for k, v in
            sorted(c.items(), key=lambda x: -x[1])]


def build_class_overview(parallel: bool = True) -> Dict:
    t0 = time.time()
    students = load_all_students(parallel=parallel)

    total_students = len(students)
    total_graded = sum(s.get("graded_count", 0) for s in students)
    total_correct = sum(s.get("correct_count", 0) for s in students)
    total_judged = sum(s.get("judged_count", 0) for s in students)
    class_accuracy = round(100.0 * total_correct / total_judged, 1) if total_judged else None
    total_errors = sum(s.get("error_count", 0) for s in students)
    students_with_data = sum(1 for s in students if s.get("graded_count", 0) > 0)

    # 分数分布（5 分段正确率分布）
    buckets = [
        {"label": "0~50", "lo": 0, "hi": 50, "count": 0},
        {"label": "50~70", "lo": 50, "hi": 70, "count": 0},
        {"label": "70~85", "lo": 70, "hi": 85, "count": 0},
        {"label": "85~100", "lo": 85, "hi": 101, "count": 0},
    ]
    for s in students:
        acc = s.get("accuracy")
        if acc is None:
            continue
        for b in buckets:
            if b["lo"] <= acc < b["hi"]:
                b["count"] += 1
                break

    # 班级能力雷达：每维度取所有学生均值
    class_radar = []
    for name in config.ABILITY_DIMENSIONS:
        scores = [s["ability_dimensions"][name]["score"]
                  for s in students
                  if s.get("ability_dimensions")
                  and s["ability_dimensions"][name]["total"] > 0]
        avg = round(sum(scores) / len(scores), 1) if scores else 50.0
        class_radar.append({
            "name": name,
            "score": avg,
            "student_count": len(scores),
            "weakest_students": sorted(
                [{"id": s["id"], "name": s["name"],
                  "score": s["ability_dimensions"][name]["score"]}
                 for s in students
                 if s.get("ability_dimensions")
                 and s["ability_dimensions"][name]["total"] > 0],
                key=lambda x: x["score"]
            )[:5],
        })

    # 知识点热力图：汇总所有学生所有知识点
    kp_agg: Dict[str, Dict] = {}
    for s in students:
        for p in s.get("knowledge_points", []):
            name = p["name"]
            if name not in kp_agg:
                kp_agg[name] = {
                    "name": name,
                    "correct": 0, "wrong": 0,
                    "student_count": 0,
                    "total_attempts": 0,
                    "mastery_list": [],   # 每个接触过此点的学生的掌握度
                }
            a = kp_agg[name]
            a["correct"] += p["correct"]
            a["wrong"] += p["wrong"]
            a["student_count"] += 1
            a["total_attempts"] += p["total"]
            a["mastery_list"].append(p["mastery"])
    kp_heatmap = []
    for name, a in kp_agg.items():
        total = a["correct"] + a["wrong"]
        mastery = round(100.0 * a["correct"] / total, 1) if total else 50.0
        avg_mastery = round(sum(a["mastery_list"]) / len(a["mastery_list"]), 1) \
            if a["mastery_list"] else 50.0
        kp_heatmap.append({
            "name": name,
            "mastery": mastery,
            "avg_student_mastery": avg_mastery,
            "correct": a["correct"],
            "wrong": a["wrong"],
            "total": total,
            "student_count": a["student_count"],
        })
    kp_heatmap.sort(key=lambda x: x["mastery"])   # 最弱在前
    weak_kps = [k for k in kp_heatmap if k["total"] >= 2][:12]   # 班级薄弱点
    strong_kps = sorted([k for k in kp_heatmap if k["total"] >= 2],
                        key=lambda x: -x["mastery"])[:6]

    # 错题错因维度分布（班级级）
    cat_counter: Dict[str, int] = {}
    type_counter: Dict[str, int] = {"normal": 0, "multiple_choice": 0,
                                     "fill_blank": 0}
    for s in students:
        for e in s.get("errors", []):
            if e.get("mastered"):
                continue
            for c in e.get("error_categories", []) or []:
                cat_counter[c] = cat_counter.get(c, 0) + 1
            t = e.get("question_type") or "normal"
            type_counter[t] = type_counter.get(t, 0) + 1
    error_categories = sorted(
        [{"name": k, "count": v} for k, v in cat_counter.items()],
        key=lambda x: -x["count"]
    )
    question_types = [{"name": k, "count": v} for k, v in type_counter.items()]

    # 学生排行榜（准确率高 / 错题多 等）
    leaderboard_accuracy = sorted(
        [{"id": s["id"], "name": s["name"], "avatar": s["avatar"],
          "grade": s["grade"], "subject": s["subject"],
          "accuracy": s["accuracy"],
          "graded": s["graded_count"], "errors": s["error_count"],
          "knowledge": s["knowledge_count"]}
         for s in students if s["accuracy"] is not None],
        key=lambda x: (-x["accuracy"], -x["graded"])
    )
    leaderboard_errors = sorted(
        [{"id": s["id"], "name": s["name"], "avatar": s["avatar"],
          "errors": s["error_count"], "graded": s["graded_count"],
          "accuracy": s["accuracy"]}
         for s in students],
        key=lambda x: -x["errors"]
    )

    # 近期活跃 —— 近 7 天批改次数
    from datetime import datetime, timedelta
    now = datetime.now()
    week_ago = now - timedelta(days=7)

    def _parse_ts(s):
        try:
            return datetime.fromisoformat(s)
        except Exception:
            return None

    activity = []
    for s in students:
        recent = 0
        recent_errors = 0
        for h in s.get("history", []):
            ts = _parse_ts(h.get("created_at", ""))
            if ts and ts >= week_ago:
                recent += 1
                if h.get("is_correct") is False:
                    recent_errors += 1
        activity.append({
            "id": s["id"], "name": s["name"], "avatar": s["avatar"],
            "recent_graded": recent, "recent_errors": recent_errors,
        })
    activity.sort(key=lambda x: -x["recent_graded"])

    # 7 日作业分布（班级级）
    day_bins = [0] * 7
    day_errs = [0] * 7
    day_labels = []
    for i in range(7):
        d = (now - timedelta(days=6 - i)).date()
        day_labels.append(d.strftime("%m-%d"))
    for s in students:
        for h in s.get("history", []):
            ts = _parse_ts(h.get("created_at", ""))
            if not ts:
                continue
            delta = (now.date() - ts.date()).days
            if 0 <= delta < 7:
                idx = 6 - delta
                day_bins[idx] += 1
                if h.get("is_correct") is False:
                    day_errs[idx] += 1

    # 精简输出（去掉 history/errors 这些大字段，减小响应体积）
    students_lite = [{
        "id": s["id"], "name": s["name"], "avatar": s["avatar"],
        "grade": s.get("grade", ""), "subject": s.get("subject", ""),
        "accuracy": s.get("accuracy"),
        "graded_count": s.get("graded_count", 0),
        "error_count": s.get("error_count", 0),
        "error_total": s.get("error_total", 0),
        "error_mastered": s.get("error_mastered", 0),
        "knowledge_count": s.get("knowledge_count", 0),
        "ability_dimensions": {
            name: {"score": d["score"], "total": d["total"]}
            for name, d in (s.get("ability_dimensions") or {}).items()
        },
        "top_weak_points": sorted(
            [p for p in s.get("knowledge_points", []) if p["total"] > 0],
            key=lambda x: x["mastery"]
        )[:3],
    } for s in students]

    elapsed = int((time.time() - t0) * 1000)
    logger.info("班级聚合完成: %d 学生, %d ms", total_students, elapsed)

    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "elapsed_ms": elapsed,
        "summary": {
            "total_students": total_students,
            "students_with_data": students_with_data,
            "total_graded": total_graded,
            "total_correct": total_correct,
            "total_judged": total_judged,
            "class_accuracy": class_accuracy,
            "total_errors": total_errors,
        },
        "accuracy_buckets": buckets,
        "class_radar": class_radar,
        "kp_heatmap": kp_heatmap[:40],
        "weak_kps": weak_kps,
        "strong_kps": strong_kps,
        "error_categories": error_categories,
        "question_types": question_types,
        "leaderboard_accuracy": leaderboard_accuracy,
        "leaderboard_errors": leaderboard_errors,
        "activity": activity,
        "day_trend": {
            "labels": day_labels,
            "graded": day_bins,
            "errors": day_errs,
        },
        "students": students_lite,
    }


# =====================================================================
#                       带缓存的班级聚合
# =====================================================================
def class_overview(force: bool = False) -> Dict:
    now = time.time()
    with _CACHE_LOCK:
        if (not force and _CACHE["data"] is not None
                and now - _CACHE["ts"] < _CACHE_TTL):
            cached = dict(_CACHE["data"])
            cached["_cache_hit"] = True
            return cached
    data = build_class_overview(parallel=True)
    with _CACHE_LOCK:
        _CACHE["data"] = data
        _CACHE["ts"] = now
    data = dict(data)
    data["_cache_hit"] = False
    return data


def invalidate_cache():
    with _CACHE_LOCK:
        _CACHE["data"] = None
        _CACHE["ts"] = 0.0


# =====================================================================
#                      AI 班级级建议
# =====================================================================
CLASS_ADVICE_SYSTEM = """你是一位经验丰富的班主任和学科组长，擅长根据班级学情做整体诊断并提出教学建议。
语言要具体、可执行，避免空话套话；不要开场白，直接按要求的 Markdown 结构输出。"""


def generate_class_advice() -> str:
    data = class_overview(force=False)
    summ = data.get("summary", {})
    weak_kps = data.get("weak_kps", []) or []
    strong_kps = data.get("strong_kps", []) or []
    cats = data.get("error_categories", []) or []
    radar = data.get("class_radar", []) or []
    acc_buckets = data.get("accuracy_buckets", []) or []
    leaderboard_errors = (data.get("leaderboard_errors") or [])[:5]

    def _lines(rows, fmt):
        return "\n".join(fmt(r) for r in rows) or "（无）"

    weak_lines = _lines(
        weak_kps[:8],
        lambda p: f"- {p['name']}: 班级掌握度 {p['mastery']}/100 "
                  f"（涉及 {p['student_count']} 名学生, 共 {p['total']} 次作答）"
    )
    strong_lines = _lines(
        strong_kps[:4],
        lambda p: f"- {p['name']}: 班级掌握度 {p['mastery']}/100"
    )
    cat_lines = _lines(
        cats[:6],
        lambda c: f"- {c['name']}: 班级共错 {c['count']} 次"
    )
    radar_lines = _lines(
        radar,
        lambda d: f"- {d['name']}: 班级平均 {d['score']}/100 "
                  f"（{d['student_count']} 名学生有数据）"
    )
    bucket_lines = _lines(
        acc_buckets,
        lambda b: f"- {b['label']}%: {b['count']} 名学生"
    )
    err_stu_lines = _lines(
        leaderboard_errors,
        lambda s: f"- {s['name']}: 未掌握错题 {s['errors']} 道, "
                  f"正确率 {s['accuracy'] if s['accuracy'] is not None else '-'}%"
    )

    prompt = f"""请基于以下班级学情数据，为任课老师写一份「班级学情简报 + 教学建议」。

【班级总览】
- 学生总数: {summ.get('total_students', 0)}
- 有作答数据的学生: {summ.get('students_with_data', 0)}
- 累计已批改题数: {summ.get('total_graded', 0)}
- 班级总体正确率: {summ.get('class_accuracy')}%
- 未掌握错题总数: {summ.get('total_errors', 0)}

【正确率分布】
{bucket_lines}

【班级能力雷达（六维）】
{radar_lines}

【班级最薄弱知识点】
{weak_lines}

【班级最稳固知识点】
{strong_lines}

【高频错因维度】
{cat_lines}

【错题最多的学生】
{err_stu_lines}

请严格按以下 Markdown 结构输出，不要加开场白、不要包含代码块围栏：

## 一、班级整体画像
（不超过 4 句话，概括本班当前最突出的 1~2 个学情特征。）

## 二、优先攻坚知识点
（列 3~5 个班级层面应优先突破的知识点，每条给出「为什么优先 + 建议的教学切入方式」。）

## 三、共性错因 & 习惯提醒
（针对错因分布，给出 2~3 条面向全班的课堂提醒或答题习惯建议。）

## 四、分层教学建议
（把班级分成 3 档：优秀 / 中游 / 薄弱，分别给出 1~2 句具体做法。）

## 五、需重点关注的学生
（指出 2~4 名需要个别辅导或面谈的学生姓名及原因，1 句话一人。）

## 六、一周教学行动计划
（给出 5 条可执行的本周任务，动词 + 量词开头，例如「安排 1 次..」「布置..」。）"""

    try:
        out = llm_providers.text_chat(
            prompt=prompt, system=CLASS_ADVICE_SYSTEM, temperature=0.4
        )
        return (out or "").strip()
    except Exception as e:
        logger.error("班级建议生成失败: %s", e)
        return f"*（生成失败：{e}）*"
