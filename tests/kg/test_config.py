from kg.config import load_env


def test_load_env_skips_comments_and_strips_inline(tmp_path, monkeypatch):
    f = tmp_path / ".env"
    f.write_text("# 注释\n\nNEO4J_USER=neo4j\nLLM_PROVIDER=deepseek   # deepseek | qwen\nEMPTY=\nURL=http://a:1/v1\n", encoding="utf-8")
    monkeypatch.delenv("NEO4J_USER", raising=False)
    env = load_env(f)
    assert env == {"NEO4J_USER": "neo4j", "LLM_PROVIDER": "deepseek", "EMPTY": "", "URL": "http://a:1/v1"}


def test_environment_variable_overrides_file(tmp_path, monkeypatch):
    f = tmp_path / ".env"
    f.write_text("NEO4J_USER=neo4j\n", encoding="utf-8")
    monkeypatch.setenv("NEO4J_USER", "other")
    assert load_env(f)["NEO4J_USER"] == "other"


def test_missing_file_returns_empty(tmp_path):
    assert load_env(tmp_path / "nope") == {}
