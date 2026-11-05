"""kg 专用的最小 LLM 调用（v1.1-design 3.2 节）：OpenAI 兼容接口 + 磁盘缓存。

provider="deepseek" 用于预标；provider="kimi" 用于交叉复审（ADR-0009，必须与预标不同厂商）。

不复用 ai_service/llm/：总设计规定 kg 与 ai_service 不互相 import。
"""
import hashlib
import json
import time
from pathlib import Path

import httpx

from kg.config import WORK, load_env

CACHE_DIR = WORK / "llm_cache"
RETRIES = 2


class LLMError(RuntimeError):
    """重试后仍失败；调用方把该条目标为 needs_manual。"""


# provider → (key 变量, base_url 变量, 默认 base_url, 模型变量, 默认模型, 超时秒, 每分钟请求上限变量, 默认上限, 温度)
PROVIDERS = {
    "deepseek": ("DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "https://api.deepseek.com", "LLM_MODEL", "deepseek-chat", 30, "DEEPSEEK_RPM", 0, 0),
    # 模型名由 .env 的 REVIEW_MODEL 指定；kimi-k2.6 只允许 temperature=1（2026-11-05 实测），结果靠缓存复现
    "kimi": ("KIMI_API_KEY", "KIMI_BASE_URL", "https://api.moonshot.cn/v1", "REVIEW_MODEL", "", 300, "KIMI_RPM", 3, 1),
}
_last_call: dict[str, float] = {}


def _settings(provider: str) -> tuple[str, str, str, int, float, float]:
    key_var, url_var, url, model_var, model, timeout, rpm_var, rpm, temperature = PROVIDERS[provider]
    env = load_env()
    key = env.get(key_var, "")
    if not key:
        raise LLMError(f"未配置 {key_var}（写在仓库根目录的 .env）")
    model = env.get(model_var) or model
    if not model:
        raise LLMError(f"未配置 {model_var}（写在仓库根目录的 .env）")
    rpm = int(env.get(rpm_var) or rpm)
    return env.get(url_var) or url, model, key, timeout, (60 / rpm + 1 if rpm else 0), temperature


def chat_json(prompt: str, *, provider: str = "deepseek", client: httpx.Client | None = None,
              cache_dir: Path = CACHE_DIR) -> dict:
    """发一条用户消息，要求返回 JSON 对象。相同"模型 + 提示词"命中缓存时不发请求。

    结构校验由调用方负责（DeepSeek 只支持 json_object，不支持 JSON Schema 约束）。
    """
    base_url, model, key, timeout, interval, temperature = _settings(provider)
    digest = hashlib.sha256((model + "\n" + prompt).encode()).hexdigest()
    path = cache_dir / f"{digest}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))["result"]

    body = {"model": model, "temperature": temperature, "response_format": {"type": "json_object"},
            "messages": [{"role": "user", "content": prompt}]}
    own = client is None
    client = client or httpx.Client(timeout=timeout)
    last_err = None
    try:
        for _ in range(RETRIES + 1):
            wait = _last_call.get(provider, 0) + interval - time.monotonic()
            if wait > 0:  # 按每分钟请求上限控速
                time.sleep(wait)
            _last_call[provider] = time.monotonic()
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
