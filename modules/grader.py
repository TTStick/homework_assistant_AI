"""
批改模块 v5 —— 并行 + 流式 token + 后台化
相比 v4 的增量：
- 视觉批改走 provider.vision_chat_stream()，一边生成一边把 token 推到前端
- 结果用 【思路】 + 【结论】JSON 两段式，前段是可读推导，后段解析出结构化结果
- enrich（知识点抽取）完全后台化，不阻塞 done 事件，前端可立刻关页/翻页
- RAG search 已在 rag_system 层做了空集合早退
"""
from typing import List, Dict, Any, Optional, Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
import threading
import queue as _queue_mod

import config
from modules import (
    llm_providers, rag_system, short_term_bank,
    error_book, knowledge_graph, ability_analyzer, student_manager,
)
from modules.applogger import get_logger

logger = get_logger(__name__)


# 并发批改线程数：视觉模型通常是瓶颈；太高会把 Ollama 打挂
MAX_GRADE_WORKERS = 4
# 后台后处理线程池：复用，避免频繁起线程
_BG_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="grader-bg")


GRADE_SYSTEM_BASE = """你是一位细致、耐心的中小学老师，正在批改学生的作业。
要求：
1) 认真结合图片上学生的实际作答进行判断；
2) 如果错误，分析错因（计算错、概念错、审题错等）；
3) 给出完整正确解答和关键思路；
4) 用简洁明了的中文表达；
5) 输出必须严格按以下两段式：
   先输出一段 `【思路】` 推导（3~6 句白话，读起来像你在口述思考过程，不要太长），
   再输出 `【结论】` 后面紧跟一个合法 JSON。
   JSON 之外不要有任何多余文字。"""


def _build_system_prompt(sid: Optional[str]) -> str:
    parts = [GRADE_SYSTEM_BASE]
    if sid:
        student = student_manager.get(sid)
        if student:
            s_info = (f"【当前正在批改的学生】姓名：{student.get('name','')}；"
                      f"年级：{student.get('grade','') or '未填'}；"
                      f"学科：{student.get('subject','') or '不限'}")
            parts.append(s_info)
        snippet = short_term_bank.as_prompt_snippet(sid)
        if snippet:
            parts.append(snippet)
    return "\n\n".join(parts)


# =====================================================================
#                       针对不同题型的 user prompt
# =====================================================================
def _build_user_prompt_normal(q: Dict, rag: str) -> str:
    q_text = q.get("question_text") or "（无法识别题目文本）"
    ans = q.get("student_answer") or "（未识别到作答）"
    extra = f"\n\n{rag}" if rag else ""
    return f"""请批改下面这道题（图中第 {q.get('index')} 题，题型：普通题）：

【题目】
{q_text}

【学生作答】
{ans}
{extra}

请结合图片里学生的实际作答来判断。按如下两段输出：

【思路】
在这里用 3~6 句白话写出你的推导过程：先看题目要求，再看学生答案，判断对错并说明关键思路。像口述一样自然，不要列表。

【结论】
{{
  "index": {q.get('index')},
  "type": "normal",
  "is_correct": true 或 false,
  "student_answer": "图片里学生实际写的答案（简要）",
  "correct_answer": "正确答案",
  "error_reason": "错误原因（正确则填空字符串）",
  "explanation": "解题思路或关键分析，2~5 句话"
}}"""


def _build_user_prompt_mc(q: Dict, rag: str) -> str:
    idx = q.get("index")
    q_text = q.get("question_text") or "（无法识别题目文本）"
    options = q.get("options") or []
    opt_lines = [f"{o.get('label','?')}. {o.get('text','')}" for o in options]
    opts_str = "\n".join(opt_lines) if opt_lines else "（未识别到选项）"
    stu = q.get("student_choice") or q.get("student_answer") or "（未识别到选择）"
    extra = f"\n\n{rag}" if rag else ""
    return f"""请批改下面这道选择题（图中第 {idx} 题）：

【题目】
{q_text}

【选项】
{opts_str}

【学生所选】
{stu}
{extra}

任务：
- 判断学生的选择是否正确；
- 给出正确选项（只填字母，如 A/B/C/D）；
- 简明解释为什么这个选项是正确的，其他为什么错。

按如下两段输出：

【思路】
在这里用 3~5 句白话口述分析：先扫一下每个选项，再判断哪个对、学生选的对不对。

【结论】
{{
  "index": {idx},
  "type": "multiple_choice",
  "is_correct": true 或 false,
  "student_choice": "学生所选字母（大写），未选则空字符串",
  "correct_choice": "正确选项字母（大写）",
  "correct_answer": "与 correct_choice 相同（用于兼容显示）",
  "student_answer": "与 student_choice 相同（用于兼容显示）",
  "error_reason": "错误原因；正确时填空字符串",
  "explanation": "对正确选项的解析以及为何其他选项错，2~5 句话"
}}"""


def _build_user_prompt_fill(q: Dict, rag: str) -> str:
    idx = q.get("index")
    q_text = q.get("question_text") or "（无法识别题目文本）"
    blanks = q.get("blanks") or []
    blanks_lines = []
    for b in blanks:
        blanks_lines.append(
            f"第{b.get('index')}空：{b.get('student_fill') or '（空）'}"
        )
    blanks_str = "\n".join(blanks_lines) if blanks_lines else "（未识别到学生填入的内容）"
    extra = f"\n\n{rag}" if rag else ""
    n = len(blanks) or 1
    sample_blanks = ",\n    ".join([
        f'{{"index": {i+1}, "student_fill": "...", "correct_fill": "...", "is_correct": true 或 false, "note": ""}}'
        for i in range(n)
    ])
    return f"""请批改下面这道填空题（图中第 {idx} 题，共 {n} 空）：

【题目】
{q_text}

【学生填写】
{blanks_str}
{extra}

任务：
- 逐空判断是否正确；
- 每空给出正确填写内容；
- 整题只有所有空全对才算 is_correct=true。

按如下两段输出：

【思路】
在这里用 3~5 句白话逐空说你的判断：这个空应该填什么，学生填的对不对，为什么。

【结论】
{{
  "index": {idx},
  "type": "fill_blank",
  "is_correct": true 或 false,
  "blanks": [
    {sample_blanks}
  ],
  "student_answer": "把各空答案用分号拼接的概要",
  "correct_answer": "把各空正确答案用分号拼接的概要",
  "error_reason": "错误原因（正确则空字符串）",
  "explanation": "整体解析，2~5 句话"
}}"""


def _build_user_prompt(q: Dict, rag_snippet: str) -> str:
    t = q.get("type", "normal")
    if t == "multiple_choice":
        return _build_user_prompt_mc(q, rag_snippet)
    if t == "fill_blank":
        return _build_user_prompt_fill(q, rag_snippet)
    return _build_user_prompt_normal(q, rag_snippet)


# =====================================================================
#                        结果规范化
# =====================================================================
def _coerce_bool(v) -> Any:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("true", "yes", "1", "对", "正确"): return True
        if s in ("false", "no", "0", "错", "错误"): return False
    if isinstance(v, (int, float)):
        return bool(v)
    return None


def _normalize_result(data: Dict, q: Dict) -> Dict:
    qtype = q.get("type", "normal")
    result = {
        "index": int(data.get("index", q.get("index", 0)) or q.get("index", 0)),
        "type": qtype,
        "is_correct": _coerce_bool(data.get("is_correct")),
        "student_answer": str(data.get("student_answer", "")).strip(),
        "correct_answer": str(data.get("correct_answer", "")).strip(),
        "error_reason": str(data.get("error_reason", "")).strip(),
        "explanation": str(data.get("explanation", "")).strip(),
    }

    if qtype == "multiple_choice":
        sc = str(data.get("student_choice",
                          data.get("student_answer", ""))).strip().upper()[:2]
        cc = str(data.get("correct_choice",
                          data.get("correct_answer", ""))).strip().upper()[:2]
        result["student_choice"] = sc
        result["correct_choice"] = cc
        if not result["student_answer"]: result["student_answer"] = sc
        if not result["correct_answer"]: result["correct_answer"] = cc
        if result["is_correct"] is None and sc and cc:
            result["is_correct"] = (sc == cc)

    elif qtype == "fill_blank":
        raw_blanks = data.get("blanks") or []
        norm_blanks = []
        for i, b in enumerate(raw_blanks):
            if not isinstance(b, dict):
                continue
            norm_blanks.append({
                "index": int(b.get("index", i + 1) or (i + 1)),
                "student_fill": str(b.get("student_fill", "")).strip(),
                "correct_fill": str(b.get("correct_fill", "")).strip(),
                "is_correct": _coerce_bool(b.get("is_correct")),
                "note": str(b.get("note", "")).strip(),
            })
        result["blanks"] = norm_blanks
        if result["is_correct"] is None and norm_blanks:
            bools = [b["is_correct"] for b in norm_blanks if b["is_correct"] is not None]
            if bools:
                result["is_correct"] = all(bools)
        if not result["student_answer"] and norm_blanks:
            result["student_answer"] = "；".join(b["student_fill"] for b in norm_blanks)
        if not result["correct_answer"] and norm_blanks:
            result["correct_answer"] = "；".join(b["correct_fill"] for b in norm_blanks)

    return result


# =====================================================================
#               v5：流式输出切分 —— 【思路】 / 【结论】
# =====================================================================
class _ThinkSplitter:
    """
    增量式地把视觉模型的 token 流拆成两部分：
      - thinking_chunk: 属于 【思路】 段，可以立刻吐给前端
      - (final) 全量文本中的 【结论】 后面那段，用于最终 JSON 解析

    使用方式：
      sp = _ThinkSplitter()
      for piece in stream:
          visible = sp.feed(piece)   # 只返回可以给前端看的 思路增量
          if visible: yield_token(visible)
      final_text = sp.full_text()
      data = parse_json_after_conclusion(final_text)
    """

    # 结论分隔符的各种写法（宽松匹配）
    _CONCLUSION_MARKERS = ["【结论】", "[结论]", "【结论】:", "【结论】：",
                            "结论：", "结论:"]
    _THINK_HEADERS = ["【思路】", "[思路]", "思路：", "思路:"]

    def __init__(self):
        self._buf = []            # 完整历史
        self._in_thinking = False
        self._saw_conclusion = False
        self._pending = ""        # 尚未判断归属的尾部

    def feed(self, piece: str) -> str:
        if not piece:
            return ""
        self._buf.append(piece)
        # 已进入结论段：全部忽略（思路段结束）
        if self._saw_conclusion:
            return ""

        # 合并到 pending 去检测结论标记
        self._pending += piece

        # 检测结论标记
        lowest = -1
        marker_len = 0
        for m in self._CONCLUSION_MARKERS:
            idx = self._pending.find(m)
            if idx >= 0 and (lowest < 0 or idx < lowest):
                lowest = idx
                marker_len = len(m)
        if lowest >= 0:
            # 结论标记前的内容仍可视为思路可见部分
            visible_tail = self._pending[:lowest]
            self._pending = ""
            self._saw_conclusion = True
            # 若从未检测到思路头，把可见部分整个作为思路
            return self._strip_think_header(visible_tail)

        # 为了避免标记被切断（比如「【结」与「论】」分两个包到），
        # 保留最后 6 个字符作为 pending，其余 flush 出去
        if len(self._pending) > 6:
            flush = self._pending[:-6]
            self._pending = self._pending[-6:]
            return self._strip_think_header(flush)
        return ""

    def _strip_think_header(self, s: str) -> str:
        """把最开头的 【思路】 等字样剥掉，避免重复显示"""
        if not s:
            return s
        stripped = s.lstrip()
        for h in self._THINK_HEADERS:
            if stripped.startswith(h):
                # 保留原始的前导空白
                lead_len = len(s) - len(stripped)
                return s[:lead_len] + stripped[len(h):].lstrip("\n :：")
        return s

    def full_text(self) -> str:
        return "".join(self._buf)


def _extract_conclusion_json(full_text: str):
    """
    从完整文本里找到结论段，然后走 parse_json。
    若没有结论标记，就对整段原文走 parse_json（兼容旧式纯 JSON 输出）。
    """
    if not full_text:
        return None, ""
    markers = _ThinkSplitter._CONCLUSION_MARKERS
    lowest = -1
    marker_len = 0
    for m in markers:
        idx = full_text.find(m)
        if idx >= 0 and (lowest < 0 or idx < lowest):
            lowest = idx
            marker_len = len(m)
    if lowest >= 0:
        thinking_part = full_text[:lowest]
        json_part = full_text[lowest + marker_len:]
    else:
        thinking_part = ""
        json_part = full_text
    data = llm_providers.parse_json(json_part)
    # 思路段去头
    t = thinking_part.strip()
    for h in _ThinkSplitter._THINK_HEADERS:
        if t.startswith(h):
            t = t[len(h):].strip()
            break
    return data, t


# =====================================================================
#                             单题批改
# =====================================================================
def grade_one(image_bytes: bytes, question: Dict,
              sid: Optional[str] = None) -> Dict:
    if question.get("_detect_failed"):
        return {
            "index": question.get("index"),
            "type": question.get("type", "normal"),
            "is_correct": None,
            "student_answer": "",
            "correct_answer": "",
            "error_reason": "",
            "explanation": question.get("question_text", "识别失败"),
            "_detect_failed": True,
        }

    q_text = question.get("question_text", "")
    rag_snippet = rag_system.search_as_prompt_snippet(sid or "", q_text) if q_text else ""

    system = _build_system_prompt(sid)
    user = _build_user_prompt(question, rag_snippet)

    raw = llm_providers.vision_chat(
        prompt=user, image_bytes=image_bytes,
        system=system, temperature=0.2,
    )
    data = llm_providers.parse_json(raw)
    if not data:
        logger.warning("grade_one 解析失败, raw=%s", (raw or "")[:200])
        return {
            "index": question.get("index"),
            "type": question.get("type", "normal"),
            "is_correct": None,
            "student_answer": question.get("student_answer", ""),
            "correct_answer": "",
            "error_reason": "",
            "explanation": (raw or "").strip()[:800] or "批改结果解析失败",
            "raw": raw,
        }
    return _normalize_result(data, question)


def _grade_one_safe(image_bytes: bytes, question: Dict,
                    sid: Optional[str]) -> Dict:
    """异常安全版：永远返回结果，不抛异常"""
    try:
        return grade_one(image_bytes, question, sid=sid)
    except Exception as e:
        logger.error("grade_one 异常 (idx=%s): %s", question.get("index"), e)
        return {
            "index": question.get("index"),
            "type": question.get("type", "normal"),
            "is_correct": None,
            "student_answer": question.get("student_answer", ""),
            "correct_answer": "",
            "error_reason": "",
            "explanation": f"批改过程出错: {e}",
        }


# =====================================================================
#                    v5：单题"流式"批改 —— 核心
# =====================================================================
def _grade_one_streaming(image_bytes: bytes, question: Dict,
                         sid: Optional[str],
                         q_idx: int,
                         out_q: "_queue_mod.Queue"):
    """
    把一道题的 token 流源源不断推进 out_q；最后推一个 verdict 事件。
    事件格式：
      {"type": "token", "q_idx": q_idx, "index": question_index, "delta": "..."}
      {"type": "thinking_done", "q_idx": q_idx, "index": question_index}
      {"type": "verdict", "q_idx": q_idx, "index": question_index, "result": {...}}
    失败也会推 verdict（带占位结果），保证主循环能收齐。
    """
    question_index = question.get("index")
    # 识别失败的直接回一个壳
    if question.get("_detect_failed"):
        out_q.put({
            "type": "verdict",
            "q_idx": q_idx,
            "index": question_index,
            "result": {
                "index": question_index,
                "type": question.get("type", "normal"),
                "is_correct": None,
                "student_answer": "",
                "correct_answer": "",
                "error_reason": "",
                "explanation": question.get("question_text", "识别失败"),
                "_detect_failed": True,
            }
        })
        return

    q_text = question.get("question_text", "")
    try:
        rag_snippet = (rag_system.search_as_prompt_snippet(sid or "", q_text)
                       if q_text else "")
    except Exception as e:
        logger.warning("rag snippet 失败 idx=%s: %s", question_index, e)
        rag_snippet = ""

    system = _build_system_prompt(sid)
    user = _build_user_prompt(question, rag_snippet)

    splitter = _ThinkSplitter()
    try:
        for piece in llm_providers.vision_chat_stream(
            prompt=user, image_bytes=image_bytes,
            system=system, temperature=0.2,
        ):
            visible = splitter.feed(piece)
            if visible:
                out_q.put({
                    "type": "token", "q_idx": q_idx,
                    "index": question_index, "delta": visible,
                })
    except Exception as e:
        logger.error("流式批改失败 idx=%s: %s", question_index, e)
        out_q.put({
            "type": "verdict", "q_idx": q_idx, "index": question_index,
            "result": {
                "index": question_index,
                "type": question.get("type", "normal"),
                "is_correct": None,
                "student_answer": question.get("student_answer", ""),
                "correct_answer": "",
                "error_reason": "",
                "explanation": f"批改过程出错: {e}",
            }
        })
        return

    # 思路段结束：通知前端可以把占位收起来了
    out_q.put({"type": "thinking_done", "q_idx": q_idx,
               "index": question_index})

    # 解析结论 JSON
    full_text = splitter.full_text()
    data, thinking_text = _extract_conclusion_json(full_text)
    if not data:
        logger.warning("grade_stream 解析失败 idx=%s, raw=%s",
                       question_index, (full_text or "")[:200])
        out_q.put({
            "type": "verdict", "q_idx": q_idx, "index": question_index,
            "result": {
                "index": question_index,
                "type": question.get("type", "normal"),
                "is_correct": None,
                "student_answer": question.get("student_answer", ""),
                "correct_answer": "",
                "error_reason": "",
                "explanation": (thinking_text or full_text).strip()[:800]
                               or "批改结果解析失败",
                "thinking": thinking_text,
                "raw": full_text,
            }
        })
        return

    result = _normalize_result(data, question)
    # 把思路带给前端：哪怕前端没订阅 token 也能展开看
    if thinking_text:
        result["thinking"] = thinking_text
    out_q.put({"type": "verdict", "q_idx": q_idx,
               "index": question_index, "result": result})


# =====================================================================
#                        并行批改（一次性）
# =====================================================================
def grade_all(image_bytes: bytes, questions: List[Dict],
              sid: Optional[str] = None,
              source: str = "batch",
              wait_post_process: bool = False) -> List[Dict]:
    """
    并行批改整张图里的所有题，保持结果顺序与 questions 一致。
    后处理（知识点抽取 / 错题本 / 雷达 / 图谱）默认丢到后台线程，不阻塞返回。
    """
    if not questions:
        return []

    results: List[Optional[Dict]] = [None] * len(questions)
    workers = max(1, min(MAX_GRADE_WORKERS, len(questions)))

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers,
                            thread_name_prefix="grade") as pool:
        futs = {
            pool.submit(_grade_one_safe, image_bytes, q, sid): i
            for i, q in enumerate(questions)
        }
        for fut in as_completed(futs):
            i = futs[fut]
            results[i] = fut.result()
    logger.info("grade_all 并行完成: %d 题, %.2fs, workers=%d",
                len(questions), time.time() - t0, workers)

    final_results: List[Dict] = [r for r in results if r is not None]

    if sid:
        if wait_post_process:
            try:
                _post_process(sid, questions, final_results, source=source)
            except Exception as e:
                logger.error("后处理失败: %s", e)
        else:
            _BG_POOL.submit(_post_process_safe, sid,
                            list(questions), list(final_results), source)

    return final_results


def _post_process_safe(sid, questions, results, source):
    try:
        _post_process(sid, questions, results, source=source)
    except Exception as e:
        logger.error("后台后处理失败 [%s]: %s", sid, e)


# =====================================================================
#                 v5：流式批改 —— token 级，后台 enrich
# =====================================================================
# 哨兵对象，提示 worker 已退出
_WORKER_DONE = object()


def grade_all_stream(image_bytes: bytes, questions: List[Dict],
                     sid: Optional[str] = None,
                     source: str = "batch") -> Iterator[Dict]:
    """
    产出的事件流：
      {"type": "start", "total": N}
      {"type": "token", "q_idx": i, "index": qidx, "delta": "..."}
      {"type": "thinking_done", "q_idx": i, "index": qidx}
      {"type": "verdict", "q_idx": i, "index": qidx, "result": {...}}
      {"type": "verdicts_done"}            ← 全部 verdict 已发完，enrich 还在后台
      {"type": "enriched", "items": [...]} ← 知识点/错因抽取完毕（可能晚于 done）
      {"type": "done"}                      ← 整体完成（不等 enriched，前端可先继续交互）

    为什么发完 verdicts 就 "done"：
    知识点抽取是一次纯文本调用，用户体感不需要等它完成；它的结果会通过独立事件
    异步追加。前端只需在已渲染的卡片上再打一圈 tag 即可。
    """
    yield {"type": "start", "total": len(questions)}
    if not questions:
        yield {"type": "done"}
        return

    workers = max(1, min(MAX_GRADE_WORKERS, len(questions)))
    out_q: "_queue_mod.Queue" = _queue_mod.Queue()
    results: List[Optional[Dict]] = [None] * len(questions)

    # 启动工作线程
    t0 = time.time()
    pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="gstream")
    pending = len(questions)
    futs = []
    for i, q in enumerate(questions):
        def _runner(idx=i, qq=q):
            try:
                _grade_one_streaming(image_bytes, qq, sid, idx, out_q)
            finally:
                out_q.put(_WORKER_DONE)
        futs.append(pool.submit(_runner))

    remaining_workers = len(questions)
    # 主循环：从队列取事件，转发
    while remaining_workers > 0:
        ev = out_q.get()
        if ev is _WORKER_DONE:
            remaining_workers -= 1
            continue
        if ev.get("type") == "verdict":
            qi = ev["q_idx"]
            results[qi] = ev["result"]
            pending -= 1
            # 只把必要字段发给前端
            yield {"type": "verdict", "q_idx": qi,
                   "index": ev.get("index"), "result": ev["result"]}
        else:
            yield ev
    pool.shutdown(wait=True)

    logger.info("grade_all_stream(v5) 完成: %d 题, %.2fs",
                len(questions), time.time() - t0)

    final_results: List[Dict] = [r for r in results if r is not None]

    # 告诉前端：verdict 已发完，可以切图 / 翻页；enrich 等后台
    yield {"type": "verdicts_done"}

    if not sid or not final_results:
        yield {"type": "done"}
        return

    # ================== 把 enrich 放到一个后台线程，继续流给前端 ==================
    enrich_q: "_queue_mod.Queue" = _queue_mod.Queue()

    def _enrich_worker():
        try:
            student = student_manager.get(sid) or {}
            pairs = [(q, r) for q, r in zip(questions, final_results)
                     if not (q.get("_detect_failed") or r.get("_detect_failed"))]
            if not pairs:
                enrich_q.put({"type": "_stop"})
                return
            vqs = [p[0] for p in pairs]
            vrs = [p[1] for p in pairs]
            extracted = extract_knowledge_and_categories(
                vqs, vrs, subject=student.get("subject", ""))
            by_idx = {e["index"]: e for e in extracted}
            enrich_out = []
            for r in final_results:
                ex = by_idx.get(r.get("index"), {})
                kps = ex.get("knowledge_points") or []
                cats = ex.get("error_categories") or []
                r["knowledge_points"] = kps
                r["error_categories"] = cats
                enrich_out.append({
                    "index": r.get("index"),
                    "knowledge_points": kps,
                    "error_categories": cats,
                })
            enrich_q.put({"type": "enriched", "items": enrich_out})
            # 归档（错题本 / 知识图谱 / 雷达 / history）完全后台
            _BG_POOL.submit(
                _archive_after_enrich, sid, vqs, vrs, extracted, source
            )
        except Exception as e:
            logger.error("流式后处理失败: %s", e)
            enrich_q.put({"type": "enrich_error", "message": str(e)})
        finally:
            enrich_q.put({"type": "_stop"})

    threading.Thread(target=_enrich_worker,
                     name="gstream-enrich", daemon=True).start()

    # enrich 期间先出 done，再继续 yield enrich 结果
    # 但 SSE 约定 done 之后前端不继续读也可以关连接；所以反过来：
    # 先把 enrich 排队（最多等 25 秒），然后发 done
    yield {"type": "enriching"}
    enrich_deadline = time.time() + 25.0
    while True:
        remaining = enrich_deadline - time.time()
        if remaining <= 0:
            yield {"type": "enrich_timeout"}
            break
        try:
            ev = enrich_q.get(timeout=remaining)
        except _queue_mod.Empty:
            yield {"type": "enrich_timeout"}
            break
        if ev.get("type") == "_stop":
            break
        yield ev

    yield {"type": "done"}


def _archive_after_enrich(sid, valid_questions, valid_results,
                          extracted, source):
    try:
        _archive(sid, valid_questions, valid_results, extracted, source)
    except Exception as e:
        logger.error("归档失败 [%s]: %s", sid, e)


# =====================================================================
#                    后处理 —— 知识点抽取 + 归档
# =====================================================================
def _post_process(sid: str, questions: List[Dict],
                  results: List[Dict], source: str = "batch"):
    if not questions or not results:
        return
    pairs = [(q, r) for q, r in zip(questions, results)
             if not (q.get("_detect_failed") or r.get("_detect_failed"))]
    if not pairs:
        return
    valid_questions = [p[0] for p in pairs]
    valid_results = [p[1] for p in pairs]

    student = student_manager.get(sid) or {}
    extracted = extract_knowledge_and_categories(
        valid_questions, valid_results, subject=student.get("subject", ""))

    by_idx = {e["index"]: e for e in extracted}
    for r in results:
        ex = by_idx.get(r.get("index"), {})
        r["knowledge_points"] = ex.get("knowledge_points") or []
        r["error_categories"] = ex.get("error_categories") or []

    _archive(sid, valid_questions, valid_results, extracted, source)


def _archive(sid, valid_questions, valid_results, extracted, source):
    student = student_manager.get(sid) or {}

    # 1) history
    history_records = []
    for q, r, ex in zip(valid_questions, valid_results, extracted):
        history_records.append({
            "question_text": q.get("question_text", ""),
            "question_type": q.get("type", "normal"),
            "is_correct": r.get("is_correct"),
            "knowledge_points": ex.get("knowledge_points") or [],
            "error_categories": ex.get("error_categories") or [],
            "created_at": ex.get("created_at", ""),
            "source": source,
        })
    student_manager.append_history(sid, history_records)

    # 2) 错题本
    for q, r, ex in zip(valid_questions, valid_results, extracted):
        if r.get("is_correct") is False:
            error_book.add(sid, {
                "question_text": q.get("question_text", ""),
                "question_type": q.get("type", "normal"),
                "student_answer": r.get("student_answer", ""),
                "correct_answer": r.get("correct_answer", ""),
                "error_reason": r.get("error_reason", ""),
                "explanation": r.get("explanation", ""),
                "knowledge_points": ex.get("knowledge_points") or [],
                "error_categories": ex.get("error_categories") or [],
                "source": source,
            })

    # 3) 知识图谱
    kg_records = []
    for r, ex in zip(valid_results, extracted):
        kg_records.append({
            "knowledge_points": ex.get("knowledge_points") or [],
            "is_correct": r.get("is_correct"),
            "subject": student.get("subject", ""),
        })
    knowledge_graph.ingest_graded(sid, kg_records)

    # 4) 能力雷达
    ab_records = []
    for r, ex in zip(valid_results, extracted):
        ab_records.append({
            "is_correct": r.get("is_correct"),
            "error_categories": ex.get("error_categories") or [],
        })
    ability_analyzer.ingest_graded(sid, ab_records)


# =====================================================================
#             知识点 & 错因维度批量抽取（一次文本 LLM 调用）
# =====================================================================
EXTRACT_KP_SYSTEM = """你是一位中小学教师，擅长识别题目考察的知识点，并能诊断错因。
输出必须是合法 JSON，不要有任何额外解释或代码块围栏。"""


def extract_knowledge_and_categories(questions: List[Dict],
                                     results: List[Dict],
                                     subject: str = "") -> List[Dict]:
    from datetime import datetime
    now = datetime.now().isoformat(timespec="seconds")
    base_out: List[Dict] = [
        {"index": q.get("index"), "knowledge_points": [],
         "error_categories": [], "created_at": now}
        for q in questions
    ]
    if not questions:
        return base_out

    lines = []
    for q, r in zip(questions, results):
        is_ok = r.get("is_correct")
        ok_str = "对" if is_ok is True else ("错" if is_ok is False else "未判断")
        lines.append(
            f"第{q.get('index')}题 ({q.get('type','normal')}, {ok_str}):\n"
            f"  题目: {q.get('question_text','')[:300]}\n"
            f"  学生答: {r.get('student_answer','') or ''}\n"
            f"  正确答: {r.get('correct_answer','') or ''}\n"
            f"  错因: {r.get('error_reason','') or '（无）'}"
        )
    questions_block = "\n\n".join(lines)
    dims_str = "、".join(config.ABILITY_DIMENSIONS)

    prompt = f"""下面是刚刚批改完的一批题目及其结果，请为每道题提取以下信息：
1) **knowledge_points**: 这道题考察的 1~3 个知识点（用尽量规范、可复用的名称，例如「一元二次方程求根公式」「勾股定理」「三角形内角和」；不要过于宽泛如「数学」）；
2) **error_categories**: 如果这道题做错了，从下列维度中挑 1~3 个最相关的：{dims_str}；
   如果做对了，此字段填 [] 空数组。
学科：{subject or '不限'}

【题目与批改结果】
{questions_block}

请严格按如下 JSON 返回，不要任何多余文字和代码围栏：
{{
  "items": [
    {{"index": 1, "knowledge_points": ["..."], "error_categories": ["..."]}},
    ...
  ]
}}"""

    try:
        raw = llm_providers.text_chat(
            prompt=prompt, system=EXTRACT_KP_SYSTEM, temperature=0.2
        )
        data = llm_providers.parse_json(raw)
    except Exception as e:
        logger.warning("知识点抽取失败: %s", e)
        return base_out

    if not (data and isinstance(data, dict)
            and isinstance(data.get("items"), list)):
        return base_out

    idx_map = {o["index"]: o for o in base_out}
    for item in data["items"]:
        if not isinstance(item, dict):
            continue
        try:
            idx = int(item.get("index"))
        except (TypeError, ValueError):
            continue
        if idx not in idx_map:
            continue
        kps = item.get("knowledge_points") or []
        cats = item.get("error_categories") or []
        kps = [str(x).strip() for x in kps if str(x).strip()]
        cats = [str(x).strip() for x in cats
                if str(x).strip() in config.ABILITY_DIMENSIONS]
        idx_map[idx]["knowledge_points"] = kps[:5]
        idx_map[idx]["error_categories"] = cats[:3]

    return base_out


# =====================================================================
#                     题库录入：从照片提取题目 + 解答
# =====================================================================
EXTRACT_SYSTEM = """你是一位老师，擅长从作业照片中提取题目及其标准答案。"""

EXTRACT_PROMPT = """请从图片中提取一道题，按 JSON 返回：
{
  "question": "题目原文（保留原格式与符号）",
  "solution": "标准解答或正确答案（如果图片里能看到就填，否则留空）",
  "note": "易错点或补充说明（可选，可为空）"
}
图片里如果有多道题，只取最清晰、最完整的一道。
只返回 JSON，不要任何解释文字。"""


def extract_for_bank(image_bytes: bytes) -> Dict:
    raw = llm_providers.vision_chat(
        prompt=EXTRACT_PROMPT, image_bytes=image_bytes,
        system=EXTRACT_SYSTEM, temperature=0.1,
    )
    data = llm_providers.parse_json(raw)
    if not data:
        return {"question": (raw or "").strip()[:500], "solution": "", "note": ""}
    return {
        "question": str(data.get("question", "")).strip(),
        "solution": str(data.get("solution", "")).strip(),
        "note": str(data.get("note", "")).strip(),
    }
