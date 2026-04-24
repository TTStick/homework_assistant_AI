"""
LLM 供应商配置持久化模块
- 默认配置：首次启动时写入 data/llm_config.json
- 支持的供应商：ollama / custom / deepseek / doubao / qwen
  · custom 是通用 OpenAI 兼容端点（用户自己填 URL/Key/模型）
- 每个供应商的配置包括：api_key / base_url / vision_model / text_model / embed_model
"""
import json
import os
import threading
from typing import Dict, Any

import config
from modules.applogger import get_logger

logger = get_logger(__name__)

_LOCK = threading.Lock()

# 所有支持的供应商元信息（供前端展示使用）
PROVIDERS_META = {
    "ollama": {
        "name": "Ollama（本地）",
        "needs_api_key": False,
        "supports_vision": True,
        "supports_text": True,
        "supports_embed": True,
        "default_base_url": "http://localhost:11434",
        "docs": "https://ollama.com",
        "tips": "本地运行，零成本。需要先 ollama pull 对应模型。",
    },
    "custom": {
        "name": "自定义（OpenAI 兼容）",
        "needs_api_key": True,
        "supports_vision": True,
        "supports_text": True,
        "supports_embed": True,
        "default_base_url": "",
        "docs": "",
        "tips": "通用 OpenAI 兼容端点：自行填写 base_url（形如 https://api.xxx.com/v1）、"
                "API Key 以及模型名。可用于 OpenRouter、Gemini 的 OpenAI 兼容接口、"
                "自建 vLLM/llama.cpp-server、Kimi、智谱、SiliconFlow、Groq 等。"
                "如果端点不支持视觉/嵌入，请在下方对应输入框留空，并打开回退 Ollama。",
    },
    "deepseek": {
        "name": "DeepSeek",
        "needs_api_key": True,
        "supports_vision": False,
        "supports_text": True,
        "supports_embed": False,
        "default_base_url": "https://api.deepseek.com/v1",
        "docs": "https://platform.deepseek.com/",
        "tips": "官方 API 暂不支持视觉和向量。视觉/嵌入会自动回退到 Ollama。",
    },
    "doubao": {
        "name": "豆包（火山方舟）",
        "needs_api_key": True,
        "supports_vision": True,
        "supports_text": True,
        "supports_embed": True,
        "default_base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "docs": "https://www.volcengine.com/product/ark",
        "tips": "在火山方舟控制台创建「接入点(Endpoint)」，模型名填接入点 ID（形如 ep-xxx）或模型标识。",
    },
    "qwen": {
        "name": "通义千问（DashScope）",
        "needs_api_key": True,
        "supports_vision": True,
        "supports_text": True,
        "supports_embed": True,
        "default_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "docs": "https://help.aliyun.com/zh/dashscope/",
        "tips": "在阿里云百炼（DashScope）申请 API Key。",
    },
}


def _default_config() -> Dict[str, Any]:
    """首次运行时写入的默认配置"""
    return {
        "active_provider": "ollama",
        "providers": {
            "ollama": {
                "base_url": config.OLLAMA_HOST,
                "api_key": "",
                "vision_model": config.VISION_MODEL,
                "text_model": config.TEXT_MODEL,
                "embed_model": config.EMBED_MODEL,
            },
            "custom": {
                "base_url": "",
                "api_key": "",
                "vision_model": "",
                "text_model": "",
                "embed_model": "",
            },
            "deepseek": {
                "base_url": PROVIDERS_META["deepseek"]["default_base_url"],
                "api_key": "",
                "vision_model": "",
                "text_model": "deepseek-chat",
                "embed_model": "",
            },
            "doubao": {
                "base_url": PROVIDERS_META["doubao"]["default_base_url"],
                "api_key": "",
                "vision_model": "doubao-1.5-vision-pro-250328",
                "text_model": "doubao-1.5-pro-32k-250115",
                "embed_model": "doubao-embedding-text-240715",
            },
            "qwen": {
                "base_url": PROVIDERS_META["qwen"]["default_base_url"],
                "api_key": "",
                "vision_model": "qwen-vl-max",
                "text_model": "qwen-max",
                "embed_model": "text-embedding-v3",
            },
        },
        # 如果当前供应商不支持 vision/embed，是否回退到 Ollama
        "fallback_vision_to_ollama": True,
        "fallback_embed_to_ollama": True,
    }


def _migrate_legacy(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """
    一次性迁移：把老版本里的 "gemini" 供应商搬到 "custom"。
    迁移规则：
    - 若旧配置有 gemini 但没有 custom，则把 gemini 的所有字段搬给 custom
      （保留用户填过的 base_url / api_key / 模型名，不丢）；
    - 若 active_provider == "gemini"，切到 "custom"；
    - 迁移完删除 gemini 键，避免污染。
    对于从未配过 gemini 的用户，这个函数是纯 no-op。
    """
    providers = cfg.get("providers") or {}
    if "gemini" in providers:
        legacy = providers.get("gemini") or {}
        # 有内容才搬，避免空 gemini 把用户后来建的 custom 覆盖
        has_content = any(str(legacy.get(k, "")).strip()
                          for k in ("base_url", "api_key",
                                    "vision_model", "text_model", "embed_model"))
        if has_content and not providers.get("custom"):
            providers["custom"] = {
                "base_url": legacy.get("base_url", ""),
                "api_key": legacy.get("api_key", ""),
                "vision_model": legacy.get("vision_model", ""),
                "text_model": legacy.get("text_model", ""),
                "embed_model": legacy.get("embed_model", ""),
            }
            logger.info("已把旧 gemini 配置迁移到 custom 供应商")
        providers.pop("gemini", None)
        cfg["providers"] = providers
    if cfg.get("active_provider") == "gemini":
        cfg["active_provider"] = "custom"
        logger.info("active_provider 由 gemini 改为 custom")
    return cfg


def _load_raw() -> Dict[str, Any]:
    if not os.path.exists(config.LLM_CONFIG_FILE):
        cfg = _default_config()
        _save_raw(cfg)
        return cfg
    try:
        with open(config.LLM_CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        # 先迁移老字段，再补齐默认
        cfg = _migrate_legacy(cfg)
        # 补齐缺失的 provider
        defaults = _default_config()
        merged_providers = defaults["providers"].copy()
        for k, v in cfg.get("providers", {}).items():
            if k in merged_providers:
                merged_providers[k].update(v)
            else:
                merged_providers[k] = v
        cfg["providers"] = merged_providers
        cfg.setdefault("active_provider", defaults["active_provider"])
        cfg.setdefault("fallback_vision_to_ollama", True)
        cfg.setdefault("fallback_embed_to_ollama", True)
        return cfg
    except Exception as e:
        logger.error("读取 llm_config 失败: %s，使用默认", e)
        cfg = _default_config()
        _save_raw(cfg)
        return cfg


def _save_raw(cfg: Dict[str, Any]):
    tmp = config.LLM_CONFIG_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, config.LLM_CONFIG_FILE)


def load() -> Dict[str, Any]:
    """读取完整配置"""
    with _LOCK:
        return _load_raw()


def save(cfg: Dict[str, Any]):
    """保存完整配置"""
    with _LOCK:
        _save_raw(cfg)
        logger.info("LLM 配置已保存，active=%s", cfg.get("active_provider"))


def active_provider_name() -> str:
    return load().get("active_provider", "ollama")


def get_provider_config(provider: str) -> Dict[str, Any]:
    cfg = load()
    return cfg.get("providers", {}).get(provider, {})


def update_provider_config(provider: str, patch: Dict[str, Any]):
    """合并更新单个供应商的配置（api_key / model 等）"""
    with _LOCK:
        cfg = _load_raw()
        providers = cfg.setdefault("providers", {})
        current = providers.setdefault(provider, {})
        current.update(patch or {})
        _save_raw(cfg)
        logger.info("更新供应商 %s 配置: keys=%s", provider, list((patch or {}).keys()))


def set_active(provider: str):
    # 兼容：老前端若传来 gemini，静默映射到 custom
    if provider == "gemini":
        provider = "custom"
    if provider not in PROVIDERS_META:
        raise ValueError(f"未知供应商: {provider}")
    with _LOCK:
        cfg = _load_raw()
        cfg["active_provider"] = provider
        _save_raw(cfg)
        logger.info("切换到供应商: %s", provider)


def masked_config() -> Dict[str, Any]:
    """返回用于 UI 展示的配置：api_key 用掩码代替"""
    cfg = load()
    out = json.loads(json.dumps(cfg))  # 深拷贝
    for name, p in out.get("providers", {}).items():
        key = p.get("api_key", "")
        if key:
            if len(key) <= 8:
                p["api_key_masked"] = "*" * len(key)
            else:
                p["api_key_masked"] = key[:4] + "*" * (len(key) - 8) + key[-4:]
            p["api_key_set"] = True
        else:
            p["api_key_masked"] = ""
            p["api_key_set"] = False
        # 不把原始 key 回给前端
        p.pop("api_key", None)
    out["providers_meta"] = PROVIDERS_META
    return out
