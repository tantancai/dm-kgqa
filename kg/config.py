"""路径与配置。Neo4j / LLM 的连接信息从仓库根目录的 .env 读取，环境变量优先。"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KG = ROOT / "kg"
RAW = KG / "raw"          # 不入库：DiaKG 原始 JSON、指南 PDF
WORK = KG / "work"        # 不入库：中间文件、LLM 缓存、待审 Excel
DATA = KG / "data"        # 入库：审核过的派生数据

DIAKG_DIR = RAW / "diakg"
GUIDES_DIR = RAW / "guides"


def load_env(path: Path = ROOT / ".env") -> dict[str, str]:
    """读 KEY=VALUE 格式的 .env（忽略注释和行尾 # 注释），已存在的环境变量不覆盖。"""
    env: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env[key.strip()] = value.split(" #", 1)[0].strip()
    env.update({k: v for k, v in os.environ.items() if k in env})
    return env
