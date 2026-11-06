import json

import httpx
import pytest

import kg.llm as llm


@pytest.fixture(autouse=True)
def fake_env(monkeypatch):
    monkeypatch.setattr(llm, "load_env", lambda: {"DEEPSEEK_API_KEY": "test-key", "LLM_MODEL": "m"})


def _client(responses, calls):
    def handler(request):
        calls.append(json.loads(request.content))
        return responses.pop(0)
    return httpx.Client(transport=httpx.MockTransport(handler))


def _ok(obj):
    return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(obj)}}], "usage": {"total_tokens": 9}})


def test_request_shape_and_cache(tmp_path):
    calls = []
    client = _client([_ok({"label": "INDICATED"})], calls)
    assert llm.chat_json("p", client=client, cache_dir=tmp_path) == {"label": "INDICATED"}
    assert calls[0]["temperature"] == 0 and calls[0]["response_format"] == {"type": "json_object"} and calls[0]["model"] == "m"
    # 第二次命中缓存，不再发请求（responses 已空，再请求会报错）
    assert llm.chat_json("p", client=client, cache_dir=tmp_path) == {"label": "INDICATED"}
    assert len(calls) == 1


def test_retries_then_succeeds(tmp_path):
    calls = []
    client = _client([httpx.Response(500), _ok({"x": 1})], calls)
    assert llm.chat_json("p", client=client, cache_dir=tmp_path) == {"x": 1}
    assert len(calls) == 2


def test_invalid_json_exhausts_retries(tmp_path):
    bad = httpx.Response(200, json={"choices": [{"message": {"content": "不是 JSON"}}]})
    calls = []
    client = _client([bad, bad, bad], calls)
    with pytest.raises(llm.LLMError):
        llm.chat_json("p", client=client, cache_dir=tmp_path)
    assert len(calls) == 3 and not list(tmp_path.iterdir())  # 失败结果不进缓存


def test_missing_key(monkeypatch, tmp_path):
    monkeypatch.setattr(llm, "load_env", lambda: {})
    with pytest.raises(llm.LLMError):
        llm.chat_json("p", cache_dir=tmp_path)


def test_kimi_thinking_disabled(monkeypatch, tmp_path):
    monkeypatch.setattr(llm, "load_env", lambda: {"KIMI_API_KEY": "k", "REVIEW_MODEL": "km", "KIMI_RPM": "0",
                                                  "KIMI_THINKING": "disabled"})
    calls = []
    assert llm.chat_json("p", provider="kimi", client=_client([_ok({"x": 1})], calls), cache_dir=tmp_path) == {"x": 1}
    assert calls[0]["thinking"] == {"type": "disabled"} and calls[0]["temperature"] == 0.6
    # 缓存键与思考开启时不同：不带 KIMI_THINKING 时不命中这条缓存
    monkeypatch.setattr(llm, "load_env", lambda: {"KIMI_API_KEY": "k", "REVIEW_MODEL": "km", "KIMI_RPM": "0"})
    assert llm.chat_json("p", provider="kimi", client=_client([_ok({"x": 2})], calls), cache_dir=tmp_path) == {"x": 2}
    assert calls[1]["temperature"] == 1 and "thinking" not in calls[1]
