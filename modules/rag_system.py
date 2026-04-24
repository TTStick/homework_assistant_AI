"""
长期题库 (RAG) —— 两层结构
- 全局 collection: long_term_global
- 按学生: student_{sid}  (一个学生一个 collection)
- 检索时两者都查，合并 top_k 按 distance 排序
"""
import uuid
from datetime import datetime
from typing import Dict, List, Optional
import threading

import chromadb
from chromadb.config import Settings

import config
from modules import llm_providers
from modules.applogger import get_logger

logger = get_logger(__name__)

_LOCK = threading.Lock()
_client: Optional[chromadb.PersistentClient] = None
_collections: Dict[str, object] = {}

_GLOBAL_COLL_NAME = "long_term_global"


# ================================================================
#                    Chroma 客户端
# ================================================================
def _get_client():
    global _client
    if _client is None:
        _client = chromadb.PersistentClient(
            path=config.CHROMA_DIR,
            settings=Settings(anonymized_telemetry=False),
        )
    return _client


def _student_coll_name(sid: str) -> str:
    return f"student_{sid}"


def _get_collection(name: str):
    if name in _collections:
        return _collections[name]
    coll = _get_client().get_or_create_collection(
        name=name, metadata={"hnsw:space": "cosine"},
    )
    _collections[name] = coll
    return coll


def _get_student_collection(sid: str):
    if not sid:
        raise ValueError("rag_system: 需要 student_id")
    return _get_collection(_student_coll_name(sid))


def _get_global_collection():
    return _get_collection(_GLOBAL_COLL_NAME)


# ================================================================
#                    按学生 API
# ================================================================
def add(sid: str, question: str, solution: str, note: str = "") -> Dict:
    with _LOCK:
        col = _get_student_collection(sid)
        text_for_embed = f"{question}\n{solution}\n{note}".strip()
        vec = llm_providers.embed(text_for_embed)
        item_id = uuid.uuid4().hex[:12]
        now = datetime.now().isoformat(timespec="seconds")
        col.add(
            ids=[item_id],
            embeddings=[vec],
            documents=[question],
            metadatas=[{"solution": solution, "note": note,
                        "created_at": now, "scope": "student"}],
        )
        logger.info("rag 添加 [%s]: %s", sid, item_id)
        return {"id": item_id, "question": question, "solution": solution,
                "note": note, "created_at": now, "scope": "student"}


def delete(sid: str, item_id: str) -> bool:
    with _LOCK:
        col = _get_student_collection(sid)
        try:
            col.delete(ids=[item_id])
            logger.info("rag 删除 [%s]: %s", sid, item_id)
            return True
        except Exception as e:
            logger.error("rag 删除失败: %s", e)
            return False


def list_all(sid: str, limit: int = 200) -> List[Dict]:
    with _LOCK:
        col = _get_student_collection(sid)
        data = col.get(limit=limit)
    return _format_list(data, scope="student")


def clear(sid: str):
    global _collections
    with _LOCK:
        name = _student_coll_name(sid)
        try:
            _get_client().delete_collection(name=name)
        except Exception as e:
            logger.warning("rag clear [%s]: %s", sid, e)
        _collections.pop(name, None)
        logger.info("rag 清空 [%s]", sid)


def drop_student(sid: str):
    try:
        clear(sid)
    except Exception as e:
        logger.warning("drop_student 失败: %s", e)


def count(sid: str) -> int:
    try:
        with _LOCK:
            return _get_student_collection(sid).count()
    except Exception:
        return 0


# ================================================================
#                    全局 API
# ================================================================
def add_global(question: str, solution: str, note: str = "") -> Dict:
    with _LOCK:
        col = _get_global_collection()
        text_for_embed = f"{question}\n{solution}\n{note}".strip()
        vec = llm_providers.embed(text_for_embed)
        item_id = uuid.uuid4().hex[:12]
        now = datetime.now().isoformat(timespec="seconds")
        col.add(
            ids=[item_id],
            embeddings=[vec],
            documents=[question],
            metadatas=[{"solution": solution, "note": note,
                        "created_at": now, "scope": "global"}],
        )
        logger.info("rag 全局添加: %s", item_id)
        return {"id": item_id, "question": question, "solution": solution,
                "note": note, "created_at": now, "scope": "global"}


def delete_global(item_id: str) -> bool:
    with _LOCK:
        col = _get_global_collection()
        try:
            col.delete(ids=[item_id])
            logger.info("rag 全局删除: %s", item_id)
            return True
        except Exception as e:
            logger.error("rag 全局删除失败: %s", e)
            return False


def list_all_global(limit: int = 200) -> List[Dict]:
    with _LOCK:
        col = _get_global_collection()
        data = col.get(limit=limit)
    return _format_list(data, scope="global")


def clear_global():
    global _collections
    with _LOCK:
        try:
            _get_client().delete_collection(name=_GLOBAL_COLL_NAME)
        except Exception as e:
            logger.warning("rag 全局 clear: %s", e)
        _collections.pop(_GLOBAL_COLL_NAME, None)
        logger.info("rag 全局已清空")


def count_global() -> int:
    try:
        with _LOCK:
            return _get_global_collection().count()
    except Exception:
        return 0


# ================================================================
#                    辅助：结果格式化
# ================================================================
def _format_list(data: Dict, scope: str) -> List[Dict]:
    out = []
    ids = data.get("ids", []) or []
    docs = data.get("documents", []) or []
    metas = data.get("metadatas", []) or []
    for i, _id in enumerate(ids):
        meta = metas[i] if i < len(metas) else {}
        out.append({
            "id": _id,
            "question": docs[i] if i < len(docs) else "",
            "solution": meta.get("solution", ""),
            "note": meta.get("note", ""),
            "created_at": meta.get("created_at", ""),
            "scope": meta.get("scope", scope),
        })
    out.sort(key=lambda x: x["created_at"], reverse=True)
    return out


# ================================================================
#                    检索（学生 + 全局 合并）
# ================================================================
def search(sid: str, query_text: str, top_k: int = None) -> List[Dict]:
    """在该学生的 collection 和全局 collection 中都查一次，合并后按距离排序。"""
    top_k = top_k or config.RAG_TOP_K
    if not query_text:
        return []

    # v5 加速：若两层都为空，直接返回，避免一次 embed 调用
    try:
        with _LOCK:
            student_has = False
            if sid:
                try:
                    student_has = _get_student_collection(sid).count() > 0
                except Exception:
                    student_has = False
            gcol = _get_global_collection()
            global_has = gcol.count() > 0
        if not student_has and not global_has:
            return []
    except Exception as e:
        logger.debug("rag 预检失败, 继续走正常流程: %s", e)

    try:
        vec = llm_providers.embed(query_text)
    except Exception as e:
        logger.error("rag search embed 失败: %s", e)
        return []

    hits = []

    # 学生 collection
    if sid:
        try:
            with _LOCK:
                col = _get_student_collection(sid)
                if col.count() > 0:
                    res = col.query(query_embeddings=[vec], n_results=top_k)
                    hits.extend(_parse_query_result(res, scope="student"))
        except Exception as e:
            logger.error("rag 学生检索失败: %s", e)

    # 全局 collection
    try:
        with _LOCK:
            gcol = _get_global_collection()
            if gcol.count() > 0:
                res = gcol.query(query_embeddings=[vec], n_results=top_k)
                hits.extend(_parse_query_result(res, scope="global"))
    except Exception as e:
        logger.error("rag 全局检索失败: %s", e)

    # 按 distance 升序（更近 = 更相似）并截断
    hits.sort(key=lambda h: (h.get("distance") is None, h.get("distance") or 0))
    return hits[:top_k]


def _parse_query_result(res: Dict, scope: str) -> List[Dict]:
    out = []
    ids = (res.get("ids") or [[]])[0]
    docs = (res.get("documents") or [[]])[0]
    metas = (res.get("metadatas") or [[]])[0]
    dists = (res.get("distances") or [[]])[0]
    for i, _id in enumerate(ids):
        meta = metas[i] if i < len(metas) else {}
        out.append({
            "id": _id,
            "question": docs[i] if i < len(docs) else "",
            "solution": meta.get("solution", ""),
            "note": meta.get("note", ""),
            "distance": float(dists[i]) if i < len(dists) else None,
            "scope": meta.get("scope", scope),
        })
    return out


def search_as_prompt_snippet(sid: str, query_text: str, top_k: int = None) -> str:
    hits = search(sid, query_text, top_k)
    if not hits:
        return ""
    lines = ["【长期题库检索结果（按相似度排序）】"]
    for i, h in enumerate(hits, 1):
        tag = "[全局]" if h.get("scope") == "global" else "[该学生]"
        lines.append(f"{i}. {tag} 题目：{h['question']}")
        if h["solution"]:
            lines.append(f"   参考解答：{h['solution']}")
        if h["note"]:
            lines.append(f"   备注：{h['note']}")
    return "\n".join(lines)
