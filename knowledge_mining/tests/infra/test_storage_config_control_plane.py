"""storage.yaml → control plane → ObjectStoreConfig 链路测试（M1 配置接通）。

验证 mining 从主控拉取对象存储配置的机制，与 database.yaml 同一模式。
不依赖真实 MinIO / minio SDK——只测配置解析与缓存。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from knowledge_mining.mining.infra import control_plane
from knowledge_mining.mining.infra.object_store.config import ObjectStoreConfig


@pytest.fixture(autouse=True)
def _clear_storage_cache() -> None:
    control_plane.set_storage_config(None)
    yield
    control_plane.set_storage_config(None)


def test_from_control_plane_minio() -> None:
    control_plane.set_storage_config({"object_store": {
        "provider": "minio",
        "bucket_prefix": "agentickb-",
        "endpoint": "121.89.90.178:19000",
        "access_key": "agentickb-app-e832ae69",
        "secret_key": "supersecret",
        "secure": False,
        "region": None,
    }})
    cfg = ObjectStoreConfig.from_control_plane()
    assert cfg.provider == "minio"
    assert cfg.endpoint == "121.89.90.178:19000"
    assert cfg.access_key == "agentickb-app-e832ae69"
    assert cfg.secret_key == "supersecret"
    assert cfg.secure is False


def test_from_control_plane_fake() -> None:
    control_plane.set_storage_config({"object_store": {
        "provider": "fake", "bucket_prefix": "dev-", "root_path": "/tmp/o",
    }})
    cfg = ObjectStoreConfig.from_control_plane()
    assert cfg.provider == "fake"
    assert cfg.root_path == "/tmp/o"


def test_repr_never_leaks_secret() -> None:
    control_plane.set_storage_config({"object_store": {
        "provider": "minio", "bucket_prefix": "agentickb-",
        "endpoint": "x:9000", "access_key": "ak", "secret_key": "TOPSECRET",
    }})
    cfg = ObjectStoreConfig.from_control_plane()
    assert "TOPSECRET" not in repr(cfg)
    assert "***set***" in repr(cfg)


def test_from_control_plane_missing_section() -> None:
    control_plane.set_storage_config({})
    with pytest.raises(ValueError, match="object_store"):
        ObjectStoreConfig.from_control_plane()


def test_fetch_storage_config_calls_storage_endpoint(monkeypatch) -> None:
    seen: dict[str, str] = {}

    def fake_get_raw(name: str, *, timeout: float = 5.0) -> dict:
        seen["name"] = name
        return {"object_store": {"provider": "fake", "bucket_prefix": "z-", "root_path": "/r"}}

    monkeypatch.setattr(control_plane, "_get_raw", fake_get_raw)
    control_plane.set_storage_config(None)
    cfg_data = control_plane.fetch_storage_config(force=True)
    assert seen["name"] == "storage"
    assert cfg_data["object_store"]["provider"] == "fake"


def test_real_storage_yaml_parses() -> None:
    """端到端：仓库里的 system/storage.yaml 能被 from_yaml 解析、凭据已填。"""
    # rootdir = repo root (pyproject.toml 所在)。
    yaml_path = Path("main_control_service/config/system/storage.yaml")
    if not yaml_path.exists():
        pytest.skip("storage.yaml not at expected repo-relative path")
    cfg = ObjectStoreConfig.from_yaml(yaml_path)
    assert cfg.provider == "minio"
    assert cfg.endpoint == "121.89.90.178:19000"
    assert cfg.access_key, "access_key 未填"
    assert cfg.secret_key, "secret_key 未填"
    assert cfg.bucket_prefix == "agentickb-dev-"
