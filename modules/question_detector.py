"""
题目检测模块 v3.1
- 更强的提示词（针对中文作业页 + 多填空题混排）
- 结果校验：检测"单大框覆盖全图"、"question_text 里混入 JSON"这类失败模式
- 失败时使用更保守的提示词重试一次
- 选择题会额外输出 options 列表与 student_choice
- 填空题会额外输出 blanks 列表（每空一项）
- 所有坐标归一化到 0-1000
"""
from typing import List, Dict, Any, Optional

from modules import llm_providers
from modules.applogger import get_logger

logger = get_logger(__name__)


SUPPORTED_TYPES = {"multiple_choice", "fill_blank", "normal"}


DETECT_SYSTEM = """你是一位非常细致、经验丰富的中小学老师，正在批改作业。
你的任务是先把作业照片里的题目**逐个拆分**出来，再用结构化 JSON 输出。
务必：
1) 严格按题目编号（1.、2.、3. 或 (1)(2)(3) 等）一题一题拆分，**不要把多道题合并**为一道；
2) 中文作业中的下划线（例如 ____、_________ 或题干末尾的长横线）**几乎一定是填空**，type 必须填 fill_blank；
3) 只输出 JSON，绝对不要加 markdown 代码块围栏（不要出现 ```json），也不要任何解释文字。"""


# ========== 主提示词（针对密排填空/选择混合页优化） ==========
DETECT_PROMPT = """仔细看图，把图片里的**每一道题**单独列出来。返回严格 JSON（不要包含任何 markdown 围栏、不要加解释）。

==== 识别规则 ====

【第一步：数清题号】先找出图上所有的题号：`1.` `2.` `3.` …… 或 `(1)` `(2)` …… 一道题对应一个题号。**这张图有多少个题号，你就必须返回多少道题**，不允许合并。

【第二步：判题型】
- 出现 `A. B. C. D.` 并列选项 → type = "multiple_choice"
- 题干里有下划线 `____` / 空括号 `( )` / 方框 `□` / 或末尾留空要学生填答 → type = "fill_blank"
- 计算题、解答题、证明题、应用题等 → type = "normal"
- **重要**：中文数学作业里，"……的值为 ___" / "……等于____" / "……的度数为____" 全部都是 **填空题**。

【第三步：提取正文】把题干原文完整抄下来（含数字、角度、符号），用 `____` 占位表示空处（不要用别的符号）。保留分数写成 `3/4`，平方写成 `x^2`，根号写成 `√2` 或 `sqrt(2)`。

【第四步：识别学生作答】
- 学生在横线上写的内容 → 对应空的 student_fill
- 学生圈/勾的字母 → student_choice（只填 A/B/C/D，未选就留空）
- 看不清的写 ""（空字符串），不要瞎猜

【第五步：bbox】每道题一个 bbox = `[x1, y1, x2, y2]`，归一化到 0–1000；包括题干 + 作答区域；**不同题的 bbox 不能完全重叠**。

==== 输出格式 ====

整体必须是：
{"questions": [ 题1, 题2, ... ]}

每道题的字段：
- 普通题(normal)：{"index":N, "type":"normal", "question_text":"...", "student_answer":"...", "bbox":[...]}
- 选择题(multiple_choice)：多加 "options":[{"label":"A","text":"..."},...] 和 "student_choice":"A"
- 填空题(fill_blank)：多加 "blanks":[{"index":1,"student_fill":"..."},{"index":2,"student_fill":"..."}]

==== 示范 ====

假设图上有 3 道填空题：
"1. 若 a+b=3，则 2a+2b=___"（学生填 "6"）
"2. √4 = ___"（学生没填）
"3. 三角形内角和为 ___ 度"（学生填 "180"）

就应该返回：
{"questions":[
 {"index":1,"type":"fill_blank","question_text":"若 a+b=3，则 2a+2b=____","student_answer":"6","bbox":[50,60,950,130],"blanks":[{"index":1,"student_fill":"6"}]},
 {"index":2,"type":"fill_blank","question_text":"√4 = ____","student_answer":"","bbox":[50,140,950,210],"blanks":[{"index":1,"student_fill":""}]},
 {"index":3,"type":"fill_blank","question_text":"三角形内角和为 ____ 度","student_answer":"180","bbox":[50,220,950,290],"blanks":[{"index":1,"student_fill":"180"}]}
]}

==== 最后检查 ====
在输出前自问：图上有几个题号？我是否返回了同样多的题？若答案是否定的，请补齐。

现在开始。"""


# ========== 备用提示词（第一次失败时用） ==========
DETECT_PROMPT_RETRY = """上一次识别失败了。请再次仔细看图，**只输出 JSON**（不要 markdown、不要解释）。

重点要求：
1. 图上每个以"数字."开头的编号（1. 2. 3. 4. ...）都是一道独立的题，必须各占一条记录；
2. 题干末尾有下划线 / 空格 / 横线要学生填答的一律是 fill_blank；
3. 只要包含并列 A/B/C/D 选项就是 multiple_choice；
4. 没把握的字段就填空字符串 ""，不要瞎编；
5. 绝对不要嵌套代码块，不要加 ```json。

格式：
{"questions":[{"index":1,"type":"fill_blank|multiple_choice|normal","question_text":"...","student_answer":"...","bbox":[x1,y1,x2,y2],"blanks":[...可选],"options":[...可选],"student_choice":"...可选"}, ...]}

请直接开始输出 JSON。"""


# ============================================================
#                     结果规范化辅助
# ============================================================
def _normalize_bbox(bbox: Any) -> List[int]:
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        return [0, 0, 1000, 1000]
    try:
        vals = [max(0, min(1000, int(float(v)))) for v in bbox]
    except (TypeError, ValueError):
        return [0, 0, 1000, 1000]
    x1, y1, x2, y2 = vals
    if x2 < x1: x1, x2 = x2, x1
    if y2 < y1: y1, y2 = y2, y1
    if x2 - x1 < 10: x2 = min(1000, x1 + 10)
    if y2 - y1 < 10: y2 = min(1000, y1 + 10)
    return [x1, y1, x2, y2]


def _normalize_options(raw_opts: Any) -> List[Dict[str, str]]:
    if not isinstance(raw_opts, list):
        return []
    out = []
    for i, opt in enumerate(raw_opts):
        if isinstance(opt, dict):
            label = str(opt.get("label", "")).strip().upper()
            text = str(opt.get("text", "")).strip()
        elif isinstance(opt, str):
            label, text = "", opt.strip()
        else:
            continue
        if not label:
            label = chr(ord("A") + i) if i < 26 else str(i + 1)
        out.append({"label": label[:2], "text": text})
    return out


def _normalize_blanks(raw_blanks: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw_blanks, list):
        return []
    out = []
    for i, b in enumerate(raw_blanks):
        if isinstance(b, dict):
            idx = b.get("index", i + 1)
            fill = str(b.get("student_fill", "")).strip()
        elif isinstance(b, str):
            idx, fill = i + 1, b.strip()
        else:
            continue
        try:
            idx = int(idx)
        except (TypeError, ValueError):
            idx = i + 1
        out.append({"index": idx, "student_fill": fill})
    return out


def _normalize_one(q: Dict, default_index: int) -> Dict:
    qtype = str(q.get("type", "normal")).strip().lower()
    if qtype not in SUPPORTED_TYPES:
        qtype = "normal"

    item = {
        "index": int(q.get("index", default_index) or default_index),
        "type": qtype,
        "question_text": str(q.get("question_text", "")).strip(),
        "student_answer": str(q.get("student_answer", "")).strip(),
        "bbox": _normalize_bbox(q.get("bbox")),
    }

    if qtype == "multiple_choice":
        item["options"] = _normalize_options(q.get("options"))
        choice = str(q.get("student_choice", "")).strip().upper()
        item["student_choice"] = choice[:2] if choice else ""
    elif qtype == "fill_blank":
        item["blanks"] = _normalize_blanks(q.get("blanks"))
        if not item["blanks"] and item["student_answer"]:
            item["blanks"] = [{"index": 1, "student_fill": item["student_answer"]}]

    # 根据题干自动推断题型 (当模型把填空题标成 normal 时兜底)
    if qtype == "normal":
        qt = item["question_text"]
        if qt and _looks_like_fill_blank(qt):
            item["type"] = "fill_blank"
            # 如果没 blanks，构造一个默认的
            if not item.get("blanks"):
                n = max(1, qt.count("____") or qt.count("___") or 1)
                item["blanks"] = [
                    {"index": i + 1,
                     "student_fill": item["student_answer"] if i == 0 else ""}
                    for i in range(n)
                ]

    return item


def _looks_like_fill_blank(text: str) -> bool:
    """启发式判断：文本里有下划线/横线占位符 → 填空题"""
    if not text:
        return False
    return ("____" in text or "___" in text
            or "　　　" in text or "＿＿" in text)


# ============================================================
#             质量检测：判断识别结果是否"可疑"
# ============================================================
_JSON_MARKERS = ('"questions"', '"index":', '"bbox":', '"type":', "```json", "```")


def _text_looks_like_raw_json(text: str) -> bool:
    """question_text 里混入了 JSON 结构/代码块围栏 → 明显是识别失败"""
    if not text:
        return False
    s = text.strip()
    if s.startswith("```") or s.startswith("{") or s.startswith("["):
        return True
    head = s[:200]
    hits = sum(1 for m in _JSON_MARKERS if m in head)
    return hits >= 2


def _bbox_fills_image(bbox: List[int]) -> bool:
    """bbox 覆盖 >= 70% 面积 → 可能是单框套整页（失败模式）"""
    if not bbox or len(bbox) != 4:
        return False
    w = max(0, bbox[2] - bbox[0])
    h = max(0, bbox[3] - bbox[1])
    return w * h >= 1000 * 1000 * 0.7


def _detect_is_suspicious(questions: List[Dict]) -> Optional[str]:
    """
    返回 None = 正常；否则返回疑似失败的原因字符串
    触发条件：
    - 任何一题的 question_text 里混入了 JSON 结构
    - 只有 1 道题且 bbox 几乎覆盖整图
    - 全部题的 type 都是 normal，但 question_text 合在一起看包含很多 '____'
    """
    if not questions:
        return "未识别到任何题目"

    for q in questions:
        if _text_looks_like_raw_json(q.get("question_text", "")):
            return "题目文本里混入了 JSON 代码，视觉模型未按格式返回"

    if len(questions) == 1 and _bbox_fills_image(questions[0].get("bbox", [])):
        qt = questions[0].get("question_text", "")
        if len(qt) > 80:   # 文本较长但只识别成一道 → 可能合并了
            return "只识别出 1 道题且覆盖整页，疑似多题被合并"

    return None


# ============================================================
#                     检测主流程
# ============================================================
def _do_detect(image_bytes: bytes, prompt: str) -> List[Dict]:
    raw = llm_providers.vision_chat(
        prompt=prompt, image_bytes=image_bytes,
        system=DETECT_SYSTEM, temperature=0.1,
    )
    data = llm_providers.parse_json(raw)
    if not data:
        return []
    # 容错：模型可能直接返回 {"index":1,...} 一道题而不是 {"questions":[...]}
    if isinstance(data, dict) and "questions" not in data and data.get("type"):
        data = {"questions": [data]}
    # 或者直接返回一个列表
    if isinstance(data, list):
        data = {"questions": data}
    if not isinstance(data, dict):
        return []
    raw_list = data.get("questions", []) or []
    clean = []
    for i, q in enumerate(raw_list):
        if not isinstance(q, dict):
            continue
        n = _normalize_one(q, i + 1)
        # 过滤掉 question_text 里混入 JSON 的"坏题"
        if _text_looks_like_raw_json(n.get("question_text", "")):
            continue
        clean.append(n)
    clean.sort(key=lambda x: x["index"])
    for i, q in enumerate(clean, 1):
        q["index"] = i
    return clean


def detect_questions(image_bytes: bytes) -> List[Dict]:
    """
    对外入口：识别一张图上的全部题目。
    内部流程：
      1) 用主提示词识别
      2) 若结果可疑（只识别出 1 大框 / 文本混入 JSON / 空列表），用备用提示词再试一次
      3) 仍失败则返回单题占位，但 question_text 不会包含垃圾 JSON
    """
    try:
        first = _do_detect(image_bytes, DETECT_PROMPT)
    except Exception as e:
        logger.error("detect_questions 第一次调用异常: %s", e)
        first = []

    reason = _detect_is_suspicious(first)
    if reason:
        logger.warning("首次识别可疑 (%s)，重试一次", reason)
        try:
            second = _do_detect(image_bytes, DETECT_PROMPT_RETRY)
        except Exception as e:
            logger.error("detect_questions 重试异常: %s", e)
            second = []

        second_reason = _detect_is_suspicious(second)
        # 哪个结果"更好"：失败原因是 None 的胜出；都失败则选题数更多的
        if not second_reason:
            first = second
        elif not reason and second_reason:
            pass  # first 更好
        elif len(second) > len(first):
            first = second

    if not first:
        logger.warning("detect_questions 最终无结果")
        return [{
            "index": 1,
            "type": "normal",
            "question_text": "（识别失败：视觉模型未能返回有效题目。请检查图片清晰度，或在设置里切换更强的视觉模型，如 qwen-vl-max / doubao-vision，或用「自定义」接入其它视觉模型。）",
            "student_answer": "",
            "bbox": [0, 0, 1000, 1000],
            "_detect_failed": True,
        }]

    logger.info("detect_questions: 识别出 %d 道题 （类型分布：%s）",
                len(first), _type_summary(first))
    return first


def _type_summary(items: List[Dict]) -> str:
    from collections import Counter
    c = Counter(x.get("type", "normal") for x in items)
    return ", ".join(f"{k}={v}" for k, v in c.items())
