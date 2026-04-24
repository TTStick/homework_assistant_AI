"""全局配置文件 - 可根据实际情况调整

v3 新增: 多学生档案 / 错题本 / 知识图谱 / 能力分析 / 练习生成
"""
import os

# ===== 默认 LLM 配置（仅首次启动时使用） =====
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
VISION_MODEL = os.getenv("VISION_MODEL", "llava:7b")
TEXT_MODEL = os.getenv("TEXT_MODEL", "llava:7b")
EMBED_MODEL = os.getenv("EMBED_MODEL", "nomic-embed-text")

# ===== 服务配置 =====
HOST = "0.0.0.0"
PORT = 8000
DEBUG = True

# ===== 数据存储 =====
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
CHROMA_DIR = os.path.join(DATA_DIR, "chroma_db")
LLM_CONFIG_FILE = os.path.join(DATA_DIR, "llm_config.json")
LOG_FILE = os.path.join(DATA_DIR, "app.log")

# 学生档案目录
STUDENTS_DIR = os.path.join(DATA_DIR, "students")
STUDENTS_INDEX_FILE = os.path.join(DATA_DIR, "students_index.json")
AVATAR_DIR = os.path.join(DATA_DIR, "avatars")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(CHROMA_DIR, exist_ok=True)
os.makedirs(STUDENTS_DIR, exist_ok=True)
os.makedirs(AVATAR_DIR, exist_ok=True)

# ===== 业务参数 =====
MAX_IMAGE_SIZE = 1600
REALTIME_INTERVAL_MS = 3000
RAG_TOP_K = 3
SHORT_TERM_MAX = 30

# 能力雷达维度
ABILITY_DIMENSIONS = [
    "计算准确",     # 算术运算是否正确
    "概念理解",     # 对概念/定义是否理解到位
    "审题能力",     # 是否看清题意
    "逻辑推理",     # 推理步骤是否严密
    "知识运用",     # 能否调用相关知识
    "书写规范",     # 格式/步骤/书写是否规范
]

# 知识点掌握度阈值
MASTERY_GREAT = 85
MASTERY_GOOD = 70
MASTERY_WEAK = 50

# ===== 网络超时 =====
HTTP_TIMEOUT = 180
