import os
import importlib

import pytest


_CONFIG_KEYS = (
    "MEMORY_BACKEND",
    "ZEP_API_KEY",
    "ZEP_API_URL",
    "NEO4J_URI",
    "NEO4J_USER",
    "NEO4J_PASSWORD",
    "LLM_API_KEY",
)


def _reload_config(monkeypatch, **env):
    for key in _CONFIG_KEYS:
        monkeypatch.delenv(key, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    # ponytail: block root .env from overriding test env on reload
    monkeypatch.setattr("dotenv.load_dotenv", lambda *_a, **_k: False)
    import app.config as config_mod
    importlib.reload(config_mod)
    for key in _CONFIG_KEYS:
        setattr(config_mod.Config, key, env.get(key))
    return config_mod.Config


def test_graphiti_default_requires_neo4j_not_zep(monkeypatch):
    Config = _reload_config(
        monkeypatch,
        MEMORY_BACKEND="graphiti",
        LLM_API_KEY="llm",
        NEO4J_URI="bolt://localhost:7687",
        NEO4J_USER="neo4j",
        NEO4J_PASSWORD="secret",
    )
    errors = Config.validate()
    assert "ZEP_API_KEY 未配置" not in errors
    assert not any("NEO4J" in e for e in errors)


def test_zep_backend_requires_zep_key(monkeypatch):
    Config = _reload_config(
        monkeypatch,
        MEMORY_BACKEND="zep",
        LLM_API_KEY="llm",
    )
    errors = Config.validate()
    assert "ZEP_API_KEY 未配置" in errors


def test_graphiti_missing_neo4j_password(monkeypatch):
    Config = _reload_config(
        monkeypatch,
        MEMORY_BACKEND="graphiti",
        LLM_API_KEY="llm",
        NEO4J_URI="bolt://localhost:7687",
        NEO4J_USER="neo4j",
    )
    errors = Config.validate()
    assert any("NEO4J_PASSWORD" in e for e in errors)
