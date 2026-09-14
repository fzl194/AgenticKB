# -*- coding: utf-8 -*-
"""OnenetConfig 配置加载（47 号 §四-1：凭据走服务端配置，不进前端不进 git）.

覆盖：yaml 构造 / 显式 kwargs / 缺凭据报错 / env 覆盖 / 默认值 / resolve_config 回落。
"""
from __future__ import annotations

import pytest

from knowledge_mining.mining.onenet.config import (
    DEFAULT_SEARCH_URL,
    DEFAULT_TOKEN_URL,
    OnenetConfig,
)


def _write_yaml(tmp_path, body: str):
    p = tmp_path / "onenet.yaml"
    p.write_text(body, encoding="utf-8")
    return p


class TestOnenetConfig:
    def test_from_file_reads_credentials_and_defaults(self, tmp_path):
        p = _write_yaml(tmp_path, "app_id: 'com.x.y'\nstatic_token: 'tok-1'\n")
        cfg = OnenetConfig.from_file(p)
        assert cfg.app_id == "com.x.y"
        assert cfg.static_token == "tok-1"
        assert cfg.token_url == DEFAULT_TOKEN_URL
        assert cfg.search_url == DEFAULT_SEARCH_URL
        assert cfg.source_type == 0
        assert cfg.chunk_width == 10000
        assert cfg.page_size == 1000
        assert cfg.throttle_seconds == 0.3
        assert cfg.timeout == 300

    def test_from_file_allows_url_override(self, tmp_path):
        p = _write_yaml(
            tmp_path,
            "app_id: 'a'\nstatic_token: 'b'\n"
            "token_url: 'http://oauth2.example/token'\n"
            "search_url: 'http://apigw.example/api/search/source_type?source_type=0'\n",
        )
        cfg = OnenetConfig.from_file(p)
        assert cfg.token_url == "http://oauth2.example/token"
        assert cfg.search_url == "http://apigw.example/api/search/source_type?source_type=0"

    def test_missing_static_token_raises(self, tmp_path):
        p = _write_yaml(tmp_path, "app_id: 'a'\n")
        with pytest.raises(ValueError, match="missing_credentials"):
            OnenetConfig.from_file(p)

    def test_missing_app_id_raises(self, tmp_path):
        p = _write_yaml(tmp_path, "static_token: 'b'\n")
        with pytest.raises(ValueError, match="missing_credentials"):
            OnenetConfig.from_file(p)

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            OnenetConfig.from_file(tmp_path / "nope.yaml")

    def test_explicit_kwargs_bypass_file(self):
        cfg = OnenetConfig(app_id="a", static_token="b", chunk_width=5000)
        assert cfg.chunk_width == 5000
        assert cfg.search_url == DEFAULT_SEARCH_URL

    def test_explicit_kwargs_require_credentials(self):
        with pytest.raises(ValueError, match="missing_credentials"):
            OnenetConfig(app_id="a")
        with pytest.raises(ValueError, match="missing_credentials"):
            OnenetConfig()

    def test_env_overrides_file(self, tmp_path, monkeypatch):
        p = _write_yaml(tmp_path, "app_id: 'file-app'\nstatic_token: 'file-tok'\n")
        monkeypatch.setenv("ONENET_APP_ID", "env-app")
        monkeypatch.setenv("ONENET_STATIC_TOKEN", "env-tok")
        cfg = OnenetConfig.from_file(p)
        assert cfg.app_id == "env-app"
        assert cfg.static_token == "env-tok"

    def test_from_env_factory(self, monkeypatch):
        monkeypatch.setenv("ONENET_APP_ID", "env-app")
        monkeypatch.setenv("ONENET_STATIC_TOKEN", "env-tok")
        cfg = OnenetConfig.from_env()
        assert cfg is not None
        assert cfg.app_id == "env-app"
        assert cfg.static_token == "env-tok"

    def test_from_env_missing_returns_none(self, monkeypatch):
        monkeypatch.delenv("ONENET_APP_ID", raising=False)
        monkeypatch.delenv("ONENET_STATIC_TOKEN", raising=False)
        assert OnenetConfig.from_env() is None

    def test_masked_repr_no_credentials(self):
        cfg = OnenetConfig(app_id="a", static_token="secret-token")
        assert "secret-token" not in repr(cfg)
        assert "secret-token" not in str(cfg)
