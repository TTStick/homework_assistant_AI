"""图片处理工具"""
import io
import hashlib
from PIL import Image

import config


def load_and_resize(img_bytes: bytes, max_size: int = None) -> bytes:
    """限制图片最大边，返回压缩后的 JPEG bytes"""
    max_size = max_size or config.MAX_IMAGE_SIZE
    img = Image.open(io.BytesIO(img_bytes))
    img = img.convert("RGB")
    w, h = img.size
    scale = min(max_size / max(w, h), 1.0)
    if scale < 1.0:
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def image_hash(img_bytes: bytes) -> str:
    """计算图片感知哈希 —— 用于实时模式判断画面是否变化"""
    img = Image.open(io.BytesIO(img_bytes)).convert("L").resize((16, 16))
    pixels = list(img.getdata())
    avg = sum(pixels) / len(pixels)
    bits = "".join("1" if p > avg else "0" for p in pixels)
    # 折叠成 hex
    return hex(int(bits, 2))[2:].zfill(64)


def hamming_distance(h1: str, h2: str) -> int:
    if len(h1) != len(h2):
        return 64
    # hex -> int -> xor -> popcount
    return bin(int(h1, 16) ^ int(h2, 16)).count("1")
