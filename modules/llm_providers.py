"""
LLM 供应商抽象层
===================

对外提供统一的三个能力：
- vision_chat(prompt, image_bytes, system, temperature)  -> str
- text_chat(prompt, system, temperature, history)        -> str
- embed(text)                                            -> List[float]

支持的供应商：
- ollama            —— 本地，使用 ollama Python 包
- custom/deepseek/doubao/qwen —— 远程，统一走 OpenAI 兼容协议（/chat/completions, /embeddings）
  · custom 是通用自定义端点：用户自己填 base_url / api_key / 模型名，
    任何 OpenAI 兼容接口（Gemini OpenAI 兼容代理、OpenRouter、自建 vLLM、
    Kimi、智谱、SiliconFlow 等）都可以走这里

调用时会根据当前 active_provider 自动路由；若当前供应商不支持 vision/embed
（例如 DeepSeek），而配置开启了 fallback_xxx_to_ollama，则自动回退到 Ollama。
"""
from __future__ import annotations

import base64
import json
import re
from typing import Any, Dict, List, Optional, Iterator, Callable

import requests

import config
from modules import llm_config
from modules.applogger import get_logger

logger = get_logger(__name__)


# =====================================================================
#                           JSON 容错解析
# =====================================================================
_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(.+?)\s*```", re.DOTALL)


def parse_json(raw: str) -> Optional[Any]:
    """
    从 LLM 的纯文本回复中提取 JSON。
    - 优先找 ```json ... ``` 代码块
    - 否则找第一个 { 或 [ 到最后一个 } 或 ] 的片段
    - 兼容尾部多余逗号
    """
    if not raw:
        return None
    m = _JSON_BLOCK_RE.search(raw)
    candidate = m.group(1) if m else raw
    positions = [i for i in (candidate.find("{"), candidate.find("[")) if i >= 0]
    if not positions:
        logger.warning("parse_json: 未发现 JSON 起始符")
        return None
    start = min(positions)
    end = max(candidate.rfind("}"), candidate.rfind("]"))
    if end < start:
        return None
    snippet = candidate[start:end + 1]
    try:
        return json.loads(snippet)
    except json.JSONDecodeError:
        fixed = re.sub(r",(\s*[\]}])", r"\1", snippet)
        try:
            return json.loads(fixed)
        except Exception as e:
            logger.warning("parse_json 失败: %s, snippet=%s", e, snippet[:200])
            return None


# =====================================================================
#                           基础 Provider
# =====================================================================
class ProviderError(Exception):
    pass


class NotSupported(ProviderError):
    """当前供应商不支持该能力（例如 DeepSeek 无视觉）"""


class BaseProvider:
    name: str = "base"

    def __init__(self, cfg: Dict[str, Any]):
        self.cfg = cfg or {}

    # 子类需要实现
    def vision_chat(self, prompt: str, image_bytes: bytes, system: str = "",
                    temperature: float = 0.2) -> str:
        raise NotSupported(f"{self.name} 不支持视觉")

    def text_chat(self, prompt: str, system: str = "",
                  temperature: float = 0.3, history: List[Dict] = None) -> str:
        raise NotSupported(f"{self.name} 不支持文本对话")

    def embed(self, text: str) -> List[float]:
        raise NotSupported(f"{self.name} 不支持向量嵌入")

    # ---- v5：流式接口（子类可选择实现；默认回退到非流式） ----
    def vision_chat_stream(self, prompt: str, image_bytes: bytes,
                           system: str = "",
                           temperature: float = 0.2) -> Iterator[str]:
        out = self.vision_chat(prompt, image_bytes,
                               system=system, temperature=temperature)
        if out:
            yield out

    def text_chat_stream(self, prompt: str, system: str = "",
                         temperature: float = 0.3,
                         history: List[Dict] = None) -> Iterator[str]:
        out = self.text_chat(prompt, system=system,
                             temperature=temperature, history=history)
        if out:
            yield out


# =====================================================================
#                          Ollama Provider
# =====================================================================
class OllamaProvider(BaseProvider):
    name = "ollama"

    def __init__(self, cfg: Dict[str, Any]):
        super().__init__(cfg)
        # 懒加载 ollama 客户端
        self._client = None

    def _get_client(self):
        if self._client is None:
            import ollama
            host = self.cfg.get("base_url") or config.OLLAMA_HOST
            self._client = ollama.Client(host=host)
        return self._client

    @staticmethod
    def _b64(image_bytes: bytes) -> str:
        return base64.b64encode(image_bytes).decode("utf-8")

    def vision_chat(self, prompt, image_bytes, system="", temperature=0.2):
        model = self.cfg.get("vision_model") or config.VISION_MODEL
        if not model:
            raise ProviderError("Ollama 未配置视觉模型")
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({
            "role": "user",
            "content": prompt,
            "images": [self._b64(image_bytes)],
        })
        logger.info("Ollama vision_chat model=%s prompt_len=%d", model, len(prompt))
        resp = self._get_client().chat(
            model=model, messages=messages,
            options={"temperature": temperature},
        )
        out = resp["message"]["content"]
        logger.info("Ollama 返回 %d 字", len(out))
        return out

    def text_chat(self, prompt, system="", temperature=0.3, history=None):
        model = self.cfg.get("text_model") or config.TEXT_MODEL
        if not model:
            raise ProviderError("Ollama 未配置文本模型")
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": prompt})
        logger.info("Ollama text_chat model=%s", model)
        resp = self._get_client().chat(
            model=model, messages=messages,
            options={"temperature": temperature},
        )
        return resp["message"]["content"]

    def embed(self, text):
        model = self.cfg.get("embed_model") or config.EMBED_MODEL
        if not model:
            raise ProviderError("Ollama 未配置嵌入模型")
        resp = self._get_client().embeddings(model=model, prompt=text)
        return resp["embedding"]

    # ---- v5：流式 ----
    def vision_chat_stream(self, prompt, image_bytes, system="", temperature=0.2):
        model = self.cfg.get("vision_model") or config.VISION_MODEL
        if not model:
            raise ProviderError("Ollama 未配置视觉模型")
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({
            "role": "user",
            "content": prompt,
            "images": [self._b64(image_bytes)],
        })
        logger.info("Ollama vision_stream model=%s prompt_len=%d", model, len(prompt))
        stream = self._get_client().chat(
            model=model, messages=messages,
            options={"temperature": temperature},
            stream=True,
        )
        total = 0
        for chunk in stream:
            msg = chunk.get("message") or {}
            piece = msg.get("content") or ""
            if piece:
                total += len(piece)
                yield piece
        logger.info("Ollama stream 结束, 共 %d 字", total)

    def text_chat_stream(self, prompt, system="", temperature=0.3, history=None):
        model = self.cfg.get("text_model") or config.TEXT_MODEL
        if not model:
            raise ProviderError("Ollama 未配置文本模型")
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": prompt})
        stream = self._get_client().chat(
            model=model, messages=messages,
            options={"temperature": temperature},
            stream=True,
        )
        for chunk in stream:
            msg = chunk.get("message") or {}
            piece = msg.get("content") or ""
            if piece:
                yield piece


# =====================================================================
#                       OpenAI 兼容 Provider
# =====================================================================
class OpenAICompatProvider(BaseProvider):
    """适用于 Custom/DeepSeek/豆包/千问 的 OpenAI 兼容协议实现"""

    # 子类可以覆盖以下以微调行为
    image_mime_default = "image/jpeg"

    def __init__(self, cfg: Dict[str, Any]):
        super().__init__(cfg)

    def _headers(self) -> Dict[str, str]:
        key = (self.cfg.get("api_key") or "").strip()
        if not key:
            raise ProviderError(f"{self.name} 未配置 API Key")
        return {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }

    def _chat_url(self) -> str:
        base = (self.cfg.get("base_url") or "").rstrip("/")
        return f"{base}/chat/completions"

    def _embed_url(self) -> str:
        base = (self.cfg.get("base_url") or "").rstrip("/")
        return f"{base}/embeddings"

    def _post(self, url: str, payload: Dict) -> Dict:
        try:
            r = requests.post(url, headers=self._headers(),
                              data=json.dumps(payload),
                              timeout=config.HTTP_TIMEOUT)
        except requests.RequestException as e:
            raise ProviderError(f"{self.name} 网络错误: {e}") from e
        if r.status_code >= 400:
            snippet = (r.text or "")[:400]
            raise ProviderError(
                f"{self.name} HTTP {r.status_code}: {snippet}"
            )
        try:
            return r.json()
        except Exception as e:
            raise ProviderError(f"{self.name} 返回非 JSON: {r.text[:200]}") from e

    # ---- vision ----
    def _build_vision_messages(self, prompt, image_bytes, system):
        b64 = base64.b64encode(image_bytes).decode("utf-8")
        data_url = f"data:{self.image_mime_default};base64,{b64}"
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": data_url}},
                {"type": "text", "text": prompt},
            ],
        })
        return messages

    def vision_chat(self, prompt, image_bytes, system="", temperature=0.2):
        model = self.cfg.get("vision_model")
        if not model:
            raise NotSupported(f"{self.name} 未配置视觉模型")
        payload = {
            "model": model,
            "temperature": temperature,
            "messages": self._build_vision_messages(prompt, image_bytes, system),
        }
        logger.info("%s vision_chat model=%s prompt_len=%d", self.name, model, len(prompt))
        data = self._post(self._chat_url(), payload)
        out = self._extract_text(data)
        logger.info("%s 返回 %d 字", self.name, len(out))
        return out

    # ---- text ----
    def text_chat(self, prompt, system="", temperature=0.3, history=None):
        model = self.cfg.get("text_model")
        if not model:
            raise NotSupported(f"{self.name} 未配置文本模型")
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": prompt})
        payload = {
            "model": model,
            "temperature": temperature,
            "messages": messages,
        }
        logger.info("%s text_chat model=%s", self.name, model)
        data = self._post(self._chat_url(), payload)
        return self._extract_text(data)

    @staticmethod
    def _extract_text(data: Dict) -> str:
        try:
            msg = data["choices"][0]["message"]
            content = msg.get("content", "")
            # 有些兼容 OpenAI 的接口（Gemini OpenAI 模式、部分网关）返回 list 形式 content
            if isinstance(content, list):
                texts = []
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        texts.append(part.get("text", ""))
                    elif isinstance(part, str):
                        texts.append(part)
                return "".join(texts)
            return content or ""
        except (KeyError, IndexError, TypeError) as e:
            raise ProviderError(f"无法解析响应: {e}, raw={str(data)[:300]}") from e

    # ---- embed ----
    def embed(self, text):
        model = self.cfg.get("embed_model")
        if not model:
            raise NotSupported(f"{self.name} 未配置嵌入模型")
        payload = {"model": model, "input": text}
        data = self._post(self._embed_url(), payload)
        try:
            return data["data"][0]["embedding"]
        except (KeyError, IndexError, TypeError) as e:
            raise ProviderError(f"嵌入响应解析失败: {e}") from e

    # ---- v5：流式（OpenAI SSE: data: {...}\n\n） ----
    def _post_stream(self, url: str, payload: Dict) -> Iterator[str]:
        payload = dict(payload)
        payload["stream"] = True
        # data=bytes 而非 data=str，避免 requests 自己用 latin-1 重编
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = self._headers()
        headers["Content-Type"] = "application/json; charset=utf-8"
        try:
            r = requests.post(url, headers=headers,
                              data=body, stream=True,
                              timeout=config.HTTP_TIMEOUT)
        except requests.RequestException as e:
            raise ProviderError(f"{self.name} 网络错误: {e}") from e
        # --------------------------------------------------------------
        # 关键修复：SSE 响应通常只带 Content-Type: text/event-stream（无 charset），
        # requests 会把所有 text/* 默认成 ISO-8859-1，导致
        # iter_lines(decode_unicode=True) 把 UTF-8 字节按 Latin-1 解码，
        # 中文变乱码。这里强制 UTF-8，彻底绕过 requests 的坑。
        # --------------------------------------------------------------
        r.encoding = "utf-8"
        if r.status_code >= 400:
            snippet = (r.text or "")[:400]
            raise ProviderError(f"{self.name} HTTP {r.status_code}: {snippet}")
        # 自己按字节切行再 UTF-8 解码，是最稳妥的做法（不依赖 r.encoding）。
        # iter_lines 在 stream 场景下偶尔会把未完成的多字节字符切断，
        # 走 iter_content + 手动缓冲更可控。
        decoder_buf = b""
        for chunk in r.iter_content(chunk_size=1024, decode_unicode=False):
            if not chunk:
                continue
            decoder_buf += chunk
            # SSE 按 \n 或 \r\n 分行
            while True:
                nl_idx = decoder_buf.find(b"\n")
                if nl_idx < 0:
                    break
                raw_line = decoder_buf[:nl_idx]
                decoder_buf = decoder_buf[nl_idx + 1:]
                # 去掉可能的 \r
                if raw_line.endswith(b"\r"):
                    raw_line = raw_line[:-1]
                if not raw_line:
                    continue
                try:
                    line = raw_line.decode("utf-8")
                except UnicodeDecodeError:
                    # 极少数边界切断，用 replace 兜底
                    line = raw_line.decode("utf-8", errors="replace")
                if not line.startswith("data:"):
                    continue
                data_str = line[5:].strip()
                if data_str == "[DONE]":
                    return
                try:
                    j = json.loads(data_str)
                except Exception:
                    continue
                try:
                    choice = j["choices"][0]
                    delta = choice.get("delta") or {}
                    piece = delta.get("content")
                    if isinstance(piece, list):
                        texts = []
                        for part in piece:
                            if isinstance(part, dict) and part.get("type") == "text":
                                texts.append(part.get("text", ""))
                        piece = "".join(texts)
                    if piece:
                        yield piece
                except (KeyError, IndexError, TypeError):
                    continue

    def vision_chat_stream(self, prompt, image_bytes, system="", temperature=0.2):
        model = self.cfg.get("vision_model")
        if not model:
            raise NotSupported(f"{self.name} 未配置视觉模型")
        payload = {
            "model": model,
            "temperature": temperature,
            "messages": self._build_vision_messages(prompt, image_bytes, system),
        }
        logger.info("%s vision_stream model=%s", self.name, model)
        yield from self._post_stream(self._chat_url(), payload)

    def text_chat_stream(self, prompt, system="", temperature=0.3, history=None):
        model = self.cfg.get("text_model")
        if not model:
            raise NotSupported(f"{self.name} 未配置文本模型")
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": prompt})
        payload = {"model": model, "temperature": temperature, "messages": messages}
        yield from self._post_stream(self._chat_url(), payload)


# -------- 具体供应商（大多只需要继承 OpenAICompatProvider） --------
class CustomProvider(OpenAICompatProvider):
    """
    自定义 OpenAI 兼容供应商：
    完全由用户配置决定（base_url / api_key / 模型名）。
    任何 OpenAI 兼容的 /v1/chat/completions + /v1/embeddings 接口都可以用。
    """
    name = "custom"


class DeepSeekProvider(OpenAICompatProvider):
    name = "deepseek"

    def vision_chat(self, prompt, image_bytes, system="", temperature=0.2):
        raise NotSupported("DeepSeek 官方 API 暂不支持视觉，请启用「视觉回退 Ollama」")

    def vision_chat_stream(self, prompt, image_bytes, system="", temperature=0.2):
        raise NotSupported("DeepSeek 官方 API 暂不支持视觉，请启用「视觉回退 Ollama」")

    def embed(self, text):
        raise NotSupported("DeepSeek 官方 API 暂不支持向量，请启用「嵌入回退 Ollama」")


class DoubaoProvider(OpenAICompatProvider):
    name = "doubao"


class QwenProvider(OpenAICompatProvider):
    name = "qwen"


# =====================================================================
#                             工厂
# =====================================================================
_PROVIDER_CLASSES = {
    "ollama": OllamaProvider,
    "custom": CustomProvider,
    "deepseek": DeepSeekProvider,
    "doubao": DoubaoProvider,
    "qwen": QwenProvider,
}


def _build_provider(name: str) -> BaseProvider:
    cls = _PROVIDER_CLASSES.get(name)
    if cls is None:
        raise ProviderError(f"未知供应商: {name}")
    cfg = llm_config.get_provider_config(name)
    return cls(cfg)


def get_active_provider() -> BaseProvider:
    return _build_provider(llm_config.active_provider_name())


def _get_ollama_fallback() -> OllamaProvider:
    return OllamaProvider(llm_config.get_provider_config("ollama"))


# =====================================================================
#                       对外暴露的统一入口
# =====================================================================
def vision_chat(prompt: str, image_bytes: bytes, system: str = "",
                temperature: float = 0.2, model: str = None) -> str:
    """调用视觉模型，支持自动回退"""
    cfg = llm_config.load()
    active = cfg.get("active_provider", "ollama")
    provider = _build_provider(active)
    try:
        return provider.vision_chat(prompt, image_bytes,
                                    system=system, temperature=temperature)
    except NotSupported as e:
        if active != "ollama" and cfg.get("fallback_vision_to_ollama", True):
            logger.warning("视觉回退到 Ollama: %s", e)
            return _get_ollama_fallback().vision_chat(
                prompt, image_bytes, system=system, temperature=temperature
            )
        raise


def text_chat(prompt: str, system: str = "",
              temperature: float = 0.3, history: List[Dict] = None,
              model: str = None) -> str:
    """调用文本模型"""
    return get_active_provider().text_chat(
        prompt, system=system, temperature=temperature, history=history
    )


def embed(text: str, model: str = None) -> List[float]:
    """嵌入文本，支持自动回退"""
    cfg = llm_config.load()
    active = cfg.get("active_provider", "ollama")
    provider = _build_provider(active)
    try:
        return provider.embed(text)
    except NotSupported as e:
        if active != "ollama" and cfg.get("fallback_embed_to_ollama", True):
            logger.warning("嵌入回退到 Ollama: %s", e)
            return _get_ollama_fallback().embed(text)
        raise


# ---- v5：流式对外入口 ----
def vision_chat_stream(prompt: str, image_bytes: bytes, system: str = "",
                       temperature: float = 0.2) -> Iterator[str]:
    """流式视觉，支持自动回退。产出一段段文本。"""
    cfg = llm_config.load()
    active = cfg.get("active_provider", "ollama")
    provider = _build_provider(active)
    try:
        gen = provider.vision_chat_stream(
            prompt, image_bytes, system=system, temperature=temperature
        )
        for piece in gen:
            yield piece
        return
    except NotSupported as e:
        if active != "ollama" and cfg.get("fallback_vision_to_ollama", True):
            logger.warning("视觉流式回退到 Ollama: %s", e)
            fb = _get_ollama_fallback()
            for piece in fb.vision_chat_stream(
                prompt, image_bytes, system=system, temperature=temperature
            ):
                yield piece
            return
        raise


def text_chat_stream(prompt: str, system: str = "",
                     temperature: float = 0.3,
                     history: List[Dict] = None) -> Iterator[str]:
    """流式文本。"""
    provider = get_active_provider()
    yield from provider.text_chat_stream(
        prompt, system=system, temperature=temperature, history=history
    )


# =====================================================================
#                       连通性测试（给 UI 用）
# =====================================================================
def test_provider(provider_name: str, capability: str = "text") -> Dict[str, Any]:
    """
    测试指定供应商的指定能力。
    capability: text / vision / embed
    返回 {ok, message, elapsed_ms}
    """
    import time
    start = time.time()
    try:
        provider = _build_provider(provider_name)
        if capability == "text":
            out = provider.text_chat("你好，请只回复「ok」", temperature=0.0)
            msg = f"文本模型响应: {out.strip()[:80]}"
        elif capability == "vision":
            # 构造一张 1x1 白色 JPEG
            import io
            from PIL import Image
            img = Image.new("RGB", (16, 16), (255, 255, 255))
            buf = io.BytesIO()
            img.save(buf, format="JPEG")
            out = provider.vision_chat("这张图是什么颜色？一个词。",
                                       buf.getvalue(), temperature=0.0)
            msg = f"视觉模型响应: {out.strip()[:80]}"
        elif capability == "embed":
            vec = provider.embed("测试")
            msg = f"嵌入维度: {len(vec)}"
        else:
            return {"ok": False, "message": f"未知能力: {capability}"}
        elapsed = int((time.time() - start) * 1000)
        return {"ok": True, "message": msg, "elapsed_ms": elapsed}
    except NotSupported as e:
        return {"ok": False, "message": f"不支持: {e}",
                "elapsed_ms": int((time.time() - start) * 1000)}
    except Exception as e:
        return {"ok": False, "message": str(e)[:500],
                "elapsed_ms": int((time.time() - start) * 1000)}
