"""kg 专用的最小 LLM 调用（v1.1-design 3.2 节）：DeepSeek（OpenAI 兼容接口）+ 磁盘缓存。

不复用 ai_service/llm/：总设计规定 kg 与 ai_service 不互相 import。
"""
import hashlib
import json
from pathlib import Path

import httpx

from kg.config import WORK, load_env

CACHE_DIR = WORK / "llm_cache"
RETRIES = 2


class LLMError(RuntimeError):
    """重试后仍失败；调用方把该条目标为 needs_manual。"""


def _settings() -> tuple[str, str, str]:
    env = load_env()
    key = env.get("DEEPSEEK_API_KEY", "")
    if not key:
        raise LLMError("未配置 DEEPSEEK_API_KEY（写在仓库根目录的 .env）")
    return env.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com", env.get("LLM_MODEL") or "deepseek-chat", key


def chat_json(prompt: str, *, client: httpx.Client | None = None, cache_dir: Path = CACHE_DIR) -> dict:
    """发一条用户消息，要求返回 JSON 对象。相同"模型 + 提示词"命中缓存时不发请求。

    结构校验由调用方负责（DeepSeek 只支持 json_object，不支持 JSON Schema 约束）。
    """
    base_url, model, key = _settings()
    digest = hashlib.sha256((model + "\n" + prompt).encode()).hexdigest()
    path = cache_dir / f"{digest}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))["result"]

    body = {"model": model, "temperature": 0, "response_format": {"type": "json_object"},
            "messages": [{"role": "user", "content": prompt}]}
    own = client is None
    client = client or httpx.Client(timeout=30)
    last_err = None
    try:
        for _ in range(RETRIES + 1):
            try:
                r = client.post(f"{base_url}/chat/completions", json=body, headers={"Authorization": f"Bearer {key}"})
                r.raise_for_status()
                data = r.json()
                result = json.loads(data["choices"][0]["message"]["content"])
                if not isinstance(result, dict):
                    raise ValueError("返回的不是 JSON 对象")
                break
            except (httpx.HTTPError, ValueError, KeyError, IndexError) as e:  # 含 JSON 解析失败
                last_err = e
        else:
            raise LLMError(f"重试 {RETRIES} 次后仍失败：{type(last_err).__name__}: {last_err}")
    finally:
        if own:
            client.close()

    cache_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"model": model, "prompt": prompt, "result": result, "usage": data.get("usage")},
                               ensure_ascii=False, indent=1), encoding="utf-8")
    return result
