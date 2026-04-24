"""
ollama_client.py —— 兼容层
自 v2 起，所有 LLM 调用改由 modules.llm_providers 统一路由。
本文件仅保留旧接口，转发到新模块，保证既有代码无痛迁移。
"""
from typing import List, Dict, Optional

from modules import llm_providers


def vision_chat(prompt: str, image_bytes: bytes,
                system: str = "", model: str = None,
                temperature: float = 0.2) -> str:
    return llm_providers.vision_chat(prompt, image_bytes,
                                     system=system, temperature=temperature,
                                     model=model)


def text_chat(prompt: str, system: str = "", model: str = None,
              temperature: float = 0.3,
              history: List[Dict] = None) -> str:
    return llm_providers.text_chat(prompt, system=system,
                                   temperature=temperature,
                                   history=history, model=model)


def embed(text: str, model: str = None) -> List[float]:
    return llm_providers.embed(text, model=model)


def parse_json(raw: str) -> Optional[Dict]:
    return llm_providers.parse_json(raw)
