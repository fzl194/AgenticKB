import yaml
from copy import deepcopy

from llm_service import config as llm_config
from llm_service import pg_config
from llm_service.tests.conftest import TEST_CFG


class _Response:
    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        return None


def test_llm_config_request_sends_internal_auth(monkeypatch) -> None:
    captured = {}
    cfg = deepcopy(TEST_CFG)
    cfg["provider"]["active_model"] = "test-model"
    for section in ("embedding", "rerank"):
        cfg[section].update({"timeout": 30, "bypass_proxy": False, "headers": {}})

    def fake_get(url, **kwargs):
        captured.update(url=url, **kwargs)
        return _Response(yaml.safe_dump(cfg))

    monkeypatch.setattr(llm_config, "control_plane_internal_headers", lambda: {
        "X-Internal-Auth": "bootstrap-secret",
    })
    monkeypatch.setattr(llm_config.httpx, "get", fake_get)
    llm_config.fetch_config_from_control_plane("http://control")
    assert captured["headers"] == {"X-Internal-Auth": "bootstrap-secret"}


def test_database_config_request_sends_internal_auth(monkeypatch) -> None:
    captured = {}
    database = {"default": {
        "host": "db", "port": 5432, "dbname": "kb", "user": "u",
        "password": "p", "sslmode": "disable", "gssencmode": "disable",
        "pool_min": 1, "pool_max": 2,
    }}

    def fake_get(url, **kwargs):
        captured.update(url=url, **kwargs)
        return _Response(yaml.safe_dump(database))

    monkeypatch.setattr(pg_config, "control_plane_internal_headers", lambda: {
        "X-Internal-Auth": "bootstrap-secret",
    })
    monkeypatch.setattr(pg_config.httpx, "get", fake_get)
    pg_config.load_db_config()
    assert captured["headers"] == {"X-Internal-Auth": "bootstrap-secret"}
