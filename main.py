"""
作业辅助工具 v3 —— 主服务
运行： python main.py
电脑浏览器访问 http://localhost:8000
"""
import os
import uuid
import json
from typing import List, Optional, Dict, Any

from fastapi import FastAPI, File, UploadFile, Form, HTTPException, Path, BackgroundTasks
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import config
from modules import (
    question_detector,
    grader,
    rag_system,
    short_term_bank,
    image_utils,
    applogger,
    llm_config,
    llm_providers,
    student_manager,
    error_book,
    knowledge_graph,
    ability_analyzer,
    problem_generator,
    teacher_analytics,
)

logger = applogger.get_logger(__name__)

app = FastAPI(title="作业辅助工具 v3")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
# 头像单独挂载，允许前端直接访问 /avatars/xxx.png
app.mount("/avatars", StaticFiles(directory=config.AVATAR_DIR), name="avatars")


def _require_student(sid: str) -> Dict:
    s = student_manager.get(sid)
    if not s:
        raise HTTPException(404, f"学生不存在: {sid}")
    return s


@app.get("/", response_class=HTMLResponse)
async def index():
    with open(os.path.join(STATIC_DIR, "index.html"), "r", encoding="utf-8") as f:
        return f.read()


# ====================================================================
#                           学生档案
# ====================================================================
class StudentIn(BaseModel):
    name: str
    avatar: Optional[str] = ""
    grade: Optional[str] = ""
    subject: Optional[str] = ""
    note: Optional[str] = ""


class StudentPatch(BaseModel):
    name: Optional[str] = None
    avatar: Optional[str] = None
    grade: Optional[str] = None
    subject: Optional[str] = None
    note: Optional[str] = None


@app.get("/api/students")
async def api_students_list():
    return {"ok": True, "items": student_manager.list_all()}


@app.post("/api/students")
async def api_students_create(body: StudentIn):
    item = student_manager.create(
        name=body.name, avatar=body.avatar or "",
        grade=body.grade or "", subject=body.subject or "",
        note=body.note or "",
    )
    return {"ok": True, "item": item}


@app.get("/api/students/{sid}")
async def api_students_get(sid: str):
    s = _require_student(sid)
    s["stats"] = student_manager._compute_stats(sid)
    return {"ok": True, "item": s}


@app.put("/api/students/{sid}")
async def api_students_update(sid: str, body: StudentPatch):
    _require_student(sid)
    item = student_manager.update(sid, body.model_dump(exclude_unset=True))
    return {"ok": True, "item": item}


@app.delete("/api/students/{sid}")
async def api_students_delete(sid: str):
    _require_student(sid)
    try:
        rag_system.drop_student(sid)
    except Exception:
        pass
    student_manager.delete(sid)
    return {"ok": True}


@app.post("/api/students/{sid}/avatar")
async def api_student_avatar(sid: str, file: UploadFile = File(...)):
    _require_student(sid)
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "文件为空")
    # 强制转为 JPEG 以统一
    try:
        jpg = image_utils.load_and_resize(raw, max_size=256)
    except Exception as e:
        raise HTTPException(400, f"不是有效图片: {e}")
    fname = f"{sid}_{uuid.uuid4().hex[:6]}.jpg"
    path = os.path.join(config.AVATAR_DIR, fname)
    with open(path, "wb") as f:
        f.write(jpg)
    url = f"/avatars/{fname}"
    student_manager.update(sid, {"avatar": url})
    return {"ok": True, "avatar": url}


# ====================================================================
#                           题目检测 / 批改
# ====================================================================
@app.post("/api/detect")
async def api_detect(file: UploadFile = File(...)):
    try:
        raw = await file.read()
        img_bytes = image_utils.load_and_resize(raw)
        questions = question_detector.detect_questions(img_bytes)
        return {"ok": True, "questions": questions}
    except Exception as e:
        logger.error("api_detect 失败: %s", e)
        raise HTTPException(500, str(e))


@app.post("/api/grade")
async def api_grade(file: UploadFile = File(...),
                    questions_json: str = Form(...),
                    student_id: Optional[str] = Form(None)):
    try:
        if student_id:
            _require_student(student_id)
        questions = json.loads(questions_json)
        raw = await file.read()
        img_bytes = image_utils.load_and_resize(raw)
        results = grader.grade_all(
            img_bytes, questions,
            sid=student_id, source="batch",
        )
        teacher_analytics.invalidate_cache()
        return {"ok": True, "results": results}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("api_grade 失败: %s", e)
        raise HTTPException(500, str(e))


@app.post("/api/realtime")
async def api_realtime(file: UploadFile = File(...),
                       last_hash: Optional[str] = Form(None),
                       student_id: Optional[str] = Form(None)):
    try:
        if student_id:
            _require_student(student_id)
        raw = await file.read()
        img_bytes = image_utils.load_and_resize(raw, max_size=1024)
        h = image_utils.image_hash(img_bytes)
        changed = True
        if last_hash:
            d = image_utils.hamming_distance(last_hash, h)
            if d < 5:
                changed = False
        if not changed:
            return {"ok": True, "hash": h, "changed": False}
        questions = question_detector.detect_questions(img_bytes)
        if not questions:
            return {"ok": True, "hash": h, "changed": True,
                    "questions": [], "results": []}
        results = grader.grade_all(
            img_bytes, questions,
            sid=student_id, source="realtime",
        )
        return {
            "ok": True, "hash": h, "changed": True,
            "questions": questions, "results": results,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("api_realtime 失败: %s", e)
        raise HTTPException(500, str(e))


# ====================================================================
#                  流式批改（SSE / NDJSON）—— v4 新增
#   前端先拿到对错，再拿到详解 / 知识点，统计数据放后台入库
# ====================================================================
import json as _json_for_stream


def _sse(event: dict) -> bytes:
    return (f"data: {_json_for_stream.dumps(event, ensure_ascii=False)}\n\n"
            ).encode("utf-8")


@app.post("/api/grade_stream")
async def api_grade_stream(file: UploadFile = File(...),
                           questions_json: str = Form(...),
                           student_id: Optional[str] = Form(None),
                           source: Optional[str] = Form("batch")):
    """
    SSE 流式批改：使用方式
      const es = new EventSource/ fetch+reader
    每事件 data: JSON
      {type:"start", total:N}
      {type:"verdict", index, result}
      {type:"enriching"}
      {type:"enriched", items: [{index, knowledge_points, error_categories}]}
      {type:"done"}
    """
    if student_id:
        _require_student(student_id)
    try:
        questions = _json_for_stream.loads(questions_json)
    except Exception as e:
        raise HTTPException(400, f"questions_json 解析失败: {e}")
    raw = await file.read()
    img_bytes = image_utils.load_and_resize(raw)

    def gen():
        try:
            for ev in grader.grade_all_stream(
                img_bytes, questions,
                sid=student_id, source=source or "batch",
            ):
                yield _sse(ev)
            # 触发教师面板缓存失效（下次拉取时重建）
            teacher_analytics.invalidate_cache()
        except Exception as e:
            logger.error("grade_stream 异常: %s", e)
            yield _sse({"type": "error", "message": str(e)})

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/realtime_stream")
async def api_realtime_stream(file: UploadFile = File(...),
                              last_hash: Optional[str] = Form(None),
                              student_id: Optional[str] = Form(None)):
    """实时批改的流式版：检测 + 流式批改合二为一"""
    if student_id:
        _require_student(student_id)
    raw = await file.read()
    img_bytes = image_utils.load_and_resize(raw, max_size=1024)
    h = image_utils.image_hash(img_bytes)
    changed = True
    if last_hash:
        d = image_utils.hamming_distance(last_hash, h)
        if d < 5:
            changed = False

    def gen():
        # 先发 hash / changed
        yield _sse({"type": "hash", "hash": h, "changed": changed})
        if not changed:
            yield _sse({"type": "done"})
            return
        try:
            questions = question_detector.detect_questions(img_bytes)
            yield _sse({"type": "detected", "questions": questions})
            if not questions:
                yield _sse({"type": "done"})
                return
            for ev in grader.grade_all_stream(
                img_bytes, questions,
                sid=student_id, source="realtime",
            ):
                yield _sse(ev)
            teacher_analytics.invalidate_cache()
        except Exception as e:
            logger.error("realtime_stream 异常: %s", e)
            yield _sse({"type": "error", "message": str(e)})

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/extract_for_bank")
async def api_extract_for_bank(file: UploadFile = File(...)):
    try:
        raw = await file.read()
        img_bytes = image_utils.load_and_resize(raw)
        data = grader.extract_for_bank(img_bytes)
        return {"ok": True, "extracted": data}
    except Exception as e:
        logger.error("extract_for_bank 失败: %s", e)
        raise HTTPException(500, str(e))


# ====================================================================
#                    短期题库 / 长期题库 —— 按学生
# ====================================================================
class BankItemIn(BaseModel):
    question: str
    solution: str = ""
    note: str = ""


@app.get("/api/students/{sid}/short_term")
async def api_st_list(sid: str):
    _require_student(sid)
    return {"ok": True, "items": short_term_bank.list_all(sid)}


@app.post("/api/students/{sid}/short_term")
async def api_st_add(sid: str, item: BankItemIn):
    _require_student(sid)
    return {"ok": True, "item":
            short_term_bank.add(sid, item.question, item.solution, item.note)}


@app.delete("/api/students/{sid}/short_term/{item_id}")
async def api_st_del(sid: str, item_id: str):
    _require_student(sid)
    return {"ok": short_term_bank.delete(sid, item_id)}


@app.delete("/api/students/{sid}/short_term")
async def api_st_clear(sid: str):
    _require_student(sid)
    short_term_bank.clear(sid)
    return {"ok": True}


@app.get("/api/students/{sid}/rag")
async def api_rag_list(sid: str):
    _require_student(sid)
    return {"ok": True, "items": rag_system.list_all(sid)}


@app.post("/api/students/{sid}/rag")
async def api_rag_add(sid: str, item: BankItemIn):
    _require_student(sid)
    try:
        return {"ok": True,
                "item": rag_system.add(sid, item.question, item.solution, item.note)}
    except Exception as e:
        logger.error("rag add 失败: %s", e)
        raise HTTPException(500, str(e))


@app.delete("/api/students/{sid}/rag/{item_id}")
async def api_rag_del(sid: str, item_id: str):
    _require_student(sid)
    return {"ok": rag_system.delete(sid, item_id)}


@app.delete("/api/students/{sid}/rag")
async def api_rag_clear(sid: str):
    _require_student(sid)
    rag_system.clear(sid)
    return {"ok": True}


# ====================================================================
#               全局题库 —— 短期 & 长期（对所有学生生效）
# ====================================================================
@app.get("/api/global/short_term")
async def api_global_st_list():
    return {"ok": True, "items": short_term_bank.list_global()}


@app.post("/api/global/short_term")
async def api_global_st_add(item: BankItemIn):
    return {"ok": True, "item":
            short_term_bank.add_global(item.question, item.solution, item.note)}


@app.delete("/api/global/short_term/{item_id}")
async def api_global_st_del(item_id: str):
    return {"ok": short_term_bank.delete_global(item_id)}


@app.delete("/api/global/short_term")
async def api_global_st_clear():
    short_term_bank.clear_global()
    return {"ok": True}


@app.get("/api/global/rag")
async def api_global_rag_list():
    return {"ok": True, "items": rag_system.list_all_global()}


@app.post("/api/global/rag")
async def api_global_rag_add(item: BankItemIn):
    try:
        return {"ok": True, "item":
                rag_system.add_global(item.question, item.solution, item.note)}
    except Exception as e:
        logger.error("global rag add 失败: %s", e)
        raise HTTPException(500, str(e))


@app.delete("/api/global/rag/{item_id}")
async def api_global_rag_del(item_id: str):
    return {"ok": rag_system.delete_global(item_id)}


@app.delete("/api/global/rag")
async def api_global_rag_clear():
    rag_system.clear_global()
    return {"ok": True}


# ====================================================================
#                      错题本
# ====================================================================
class ErrorAddIn(BaseModel):
    question_text: str
    question_type: str = "normal"
    student_answer: str = ""
    correct_answer: str = ""
    error_reason: str = ""
    explanation: str = ""
    knowledge_points: List[str] = []
    error_categories: List[str] = []


@app.get("/api/students/{sid}/errors")
async def api_err_list(sid: str, include_mastered: bool = True):
    _require_student(sid)
    return {
        "ok": True,
        "items": error_book.list_all(sid, include_mastered=include_mastered),
        "summary": {
            "frequent_knowledge_points": error_book.frequent_knowledge_points(sid),
            "frequent_error_categories": error_book.frequent_error_categories(sid),
            "frequent_question_types": error_book.frequent_question_types(sid),
        },
    }


@app.post("/api/students/{sid}/errors")
async def api_err_add(sid: str, body: ErrorAddIn):
    _require_student(sid)
    return {"ok": True, "item": error_book.add(sid, body.model_dump())}


@app.get("/api/students/{sid}/errors/{eid}")
async def api_err_get(sid: str, eid: str):
    _require_student(sid)
    it = error_book.get(sid, eid)
    if not it:
        raise HTTPException(404, "错题不存在")
    return {"ok": True, "item": it}


@app.delete("/api/students/{sid}/errors/{eid}")
async def api_err_del(sid: str, eid: str):
    _require_student(sid)
    return {"ok": error_book.delete(sid, eid)}


class MasteredIn(BaseModel):
    mastered: bool


@app.put("/api/students/{sid}/errors/{eid}/mastered")
async def api_err_mastered(sid: str, eid: str, body: MasteredIn):
    _require_student(sid)
    return {"ok": error_book.set_mastered(sid, eid, body.mastered)}


@app.delete("/api/students/{sid}/errors")
async def api_err_clear(sid: str):
    _require_student(sid)
    error_book.clear(sid)
    return {"ok": True}


# ====================================================================
#                        知识图谱
# ====================================================================
@app.get("/api/students/{sid}/knowledge")
async def api_kg_graph(sid: str):
    _require_student(sid)
    return {
        "ok": True,
        "graph": knowledge_graph.get_graph(sid),
        "weak_points": knowledge_graph.weak_points(sid),
    }


@app.get("/api/students/{sid}/knowledge/{name}/explain")
async def api_kg_explain(sid: str, name: str):
    _require_student(sid)
    text = knowledge_graph.explain_point(sid, name)
    return {"ok": True, "name": name, "markdown": text}


@app.delete("/api/students/{sid}/knowledge/{name}")
async def api_kg_remove(sid: str, name: str):
    _require_student(sid)
    return {"ok": knowledge_graph.remove_point(sid, name)}


class RelationIn(BaseModel):
    source: str
    target: str
    type: str = "related"


@app.post("/api/students/{sid}/knowledge/relation")
async def api_kg_relation(sid: str, body: RelationIn):
    _require_student(sid)
    knowledge_graph.add_relation(sid, body.source, body.target, body.type)
    return {"ok": True}


@app.delete("/api/students/{sid}/knowledge")
async def api_kg_clear(sid: str):
    _require_student(sid)
    knowledge_graph.clear(sid)
    return {"ok": True}


# ====================================================================
#                        能力雷达
# ====================================================================
@app.get("/api/students/{sid}/ability")
async def api_ability(sid: str):
    _require_student(sid)
    return {"ok": True, "radar": ability_analyzer.radar(sid)}


@app.post("/api/students/{sid}/ability/advice")
async def api_ability_advice(sid: str):
    _require_student(sid)
    try:
        md = ability_analyzer.generate_advice(sid)
        return {"ok": True, "markdown": md}
    except Exception as e:
        logger.error("生成建议失败: %s", e)
        raise HTTPException(500, str(e))


@app.delete("/api/students/{sid}/ability")
async def api_ability_reset(sid: str):
    _require_student(sid)
    ability_analyzer.reset(sid)
    return {"ok": True}


# ====================================================================
#                        练习生成
# ====================================================================
class GenFromErrorIn(BaseModel):
    error_id: str
    count: int = 3
    difficulty: str = "same"  # easy / same / hard


class GenFromKpIn(BaseModel):
    point: str
    count: int = 3
    difficulty: str = "same"
    qtype: str = "normal"


@app.get("/api/students/{sid}/practice")
async def api_practice_list(sid: str):
    _require_student(sid)
    return {"ok": True, "items": problem_generator.list_all(sid)}


@app.post("/api/students/{sid}/practice/from_error")
async def api_practice_from_error(sid: str, body: GenFromErrorIn):
    _require_student(sid)
    try:
        batch = problem_generator.generate_from_error(
            sid, body.error_id, count=body.count, difficulty=body.difficulty
        )
        return {"ok": True, "batch": batch}
    except ValueError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        logger.error("practice_from_error 失败: %s", e)
        raise HTTPException(500, str(e))


@app.post("/api/students/{sid}/practice/from_kp")
async def api_practice_from_kp(sid: str, body: GenFromKpIn):
    _require_student(sid)
    try:
        batch = problem_generator.generate_from_knowledge_point(
            sid, body.point, count=body.count,
            difficulty=body.difficulty, qtype=body.qtype,
        )
        return {"ok": True, "batch": batch}
    except Exception as e:
        logger.error("practice_from_kp 失败: %s", e)
        raise HTTPException(500, str(e))


@app.delete("/api/students/{sid}/practice/{pid}")
async def api_practice_delete(sid: str, pid: str):
    _require_student(sid)
    return {"ok": problem_generator.delete(sid, pid)}


@app.delete("/api/students/{sid}/practice")
async def api_practice_clear(sid: str):
    _require_student(sid)
    problem_generator.clear(sid)
    return {"ok": True}


# ====================================================================
#                       历史记录
# ====================================================================
@app.get("/api/students/{sid}/history")
async def api_history(sid: str, limit: int = 200):
    _require_student(sid)
    return {"ok": True, "items": student_manager.get_history(sid, limit)}


# ====================================================================
#                        日志
# ====================================================================
@app.get("/api/logs")
async def api_logs(limit: int = 200):
    return {"ok": True, "logs": applogger.recent_logs(limit)}


@app.delete("/api/logs")
async def api_logs_clear():
    applogger.clear_logs()
    return {"ok": True}


# ====================================================================
#                        LLM 供应商配置
# ====================================================================
class ProviderUpdate(BaseModel):
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    vision_model: Optional[str] = None
    text_model: Optional[str] = None
    embed_model: Optional[str] = None


class GlobalLLMUpdate(BaseModel):
    active_provider: Optional[str] = None
    fallback_vision_to_ollama: Optional[bool] = None
    fallback_embed_to_ollama: Optional[bool] = None


@app.get("/api/llm_config")
async def api_llm_config():
    return {"ok": True, "config": llm_config.masked_config()}


@app.put("/api/llm_config/provider/{provider}")
async def api_update_provider(provider: str, patch: ProviderUpdate):
    if provider not in llm_config.PROVIDERS_META:
        raise HTTPException(400, f"未知供应商: {provider}")
    raw = patch.model_dump(exclude_unset=True)
    if "api_key" in raw:
        v = raw["api_key"]
        if v is None or v == "":
            raw.pop("api_key")
        elif v == "__clear__":
            raw["api_key"] = ""
    try:
        llm_config.update_provider_config(provider, raw)
    except Exception as e:
        raise HTTPException(500, str(e))
    return {"ok": True, "config": llm_config.masked_config()}


@app.put("/api/llm_config")
async def api_update_global(patch: GlobalLLMUpdate):
    raw = patch.model_dump(exclude_unset=True)
    if "active_provider" in raw and raw["active_provider"] is not None:
        try:
            llm_config.set_active(raw["active_provider"])
        except ValueError as e:
            raise HTTPException(400, str(e))
    other = {k: v for k, v in raw.items()
             if k != "active_provider" and v is not None}
    if other:
        full = llm_config.load()
        full.update(other)
        llm_config.save(full)
    return {"ok": True, "config": llm_config.masked_config()}


class TestProviderIn(BaseModel):
    provider: str
    capability: str = "text"


@app.post("/api/llm_config/test")
async def api_test_provider(body: TestProviderIn):
    if body.provider not in llm_config.PROVIDERS_META:
        raise HTTPException(400, f"未知供应商: {body.provider}")
    r = llm_providers.test_provider(body.provider, body.capability)
    return {"ok": r.get("ok", False), "result": r}


# ====================================================================
#                     教师面板 —— v4 新增
# ====================================================================
@app.get("/api/teacher/overview")
async def api_teacher_overview(refresh: int = 0):
    """班级全貌。带 8 秒缓存；refresh=1 强刷。"""
    try:
        data = teacher_analytics.class_overview(force=bool(refresh))
        return {"ok": True, "data": data}
    except Exception as e:
        logger.error("teacher/overview 失败: %s", e)
        raise HTTPException(500, str(e))


@app.post("/api/teacher/advice")
async def api_teacher_advice():
    """让 AI 基于班级学情给出整体教学建议"""
    try:
        md = teacher_analytics.generate_class_advice()
        return {"ok": True, "markdown": md}
    except Exception as e:
        logger.error("teacher/advice 失败: %s", e)
        raise HTTPException(500, str(e))


@app.get("/api/teacher/student/{sid}")
async def api_teacher_student_detail(sid: str):
    """单学生在班级视角下的完整快照"""
    _require_student(sid)
    try:
        snap = teacher_analytics._one_student_snapshot(sid)
        # 减小体积：只回最近 50 条历史
        hist = snap.get("history") or []
        snap["history_recent"] = hist[-50:][::-1]
        snap.pop("history", None)
        return {"ok": True, "data": snap}
    except Exception as e:
        logger.error("teacher/student 失败: %s", e)
        raise HTTPException(500, str(e))


# v5：教师面板完整视图（单独详情页用）
@app.get("/api/teacher/student/{sid}/full")
async def api_teacher_student_full(sid: str,
                                   history_limit: int = 500):
    """
    给「学生详情页」用的完整数据：
    - 全量已掌握/未掌握错题本
    - 全量搜题/批改历史记录（按最新在前）
    - 能力六维
    - 知识点列表
    """
    _require_student(sid)
    try:
        snap = teacher_analytics._one_student_snapshot(sid)
        hist = snap.get("history") or []
        # 最新在前，可按 limit 截取
        snap["history_all"] = hist[::-1][:max(0, int(history_limit or 500))]
        snap.pop("history", None)
        return {"ok": True, "data": snap}
    except Exception as e:
        logger.error("teacher/student/full 失败: %s", e)
        raise HTTPException(500, str(e))


# v5：为单个学生生成 AI 学情建议（老师视角）
@app.post("/api/teacher/student/{sid}/advice")
async def api_teacher_student_advice(sid: str):
    _require_student(sid)
    try:
        md = ability_analyzer.generate_advice(sid)
        return {"ok": True, "markdown": md}
    except Exception as e:
        logger.error("teacher/student/advice 失败: %s", e)
        raise HTTPException(500, str(e))


# ====================================================================
#                         健康检查
# ====================================================================
@app.get("/api/health")
async def api_health():
    cfg = llm_config.load()
    active = cfg.get("active_provider", "ollama")
    provider_cfg = cfg.get("providers", {}).get(active, {})
    return {
        "ok": True,
        "active_provider": active,
        "vision_model": provider_cfg.get("vision_model", ""),
        "text_model": provider_cfg.get("text_model", ""),
        "embed_model": provider_cfg.get("embed_model", ""),
        "base_url": provider_cfg.get("base_url", ""),
        "fallback_vision_to_ollama": cfg.get("fallback_vision_to_ollama", True),
        "fallback_embed_to_ollama": cfg.get("fallback_embed_to_ollama", True),
        "student_count": len(student_manager.list_all()),
        "global_short_term_count": len(short_term_bank.list_global()),
        "global_rag_count": rag_system.count_global(),
    }


if __name__ == "__main__":
    import uvicorn
    logger.info("启动服务: http://%s:%d", config.HOST, config.PORT)
    _cfg = llm_config.load()
    logger.info("当前供应商: %s", _cfg.get("active_provider"))
    uvicorn.run(app, host=config.HOST, port=config.PORT)
