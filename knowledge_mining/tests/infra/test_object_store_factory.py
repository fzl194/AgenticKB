"""Tests for the Object Store factory + config (M1.1, WP1A).

References:
- SRS §C00, §8.1
- ADR-0003 D-002 (dual adapter), D-006 (guarded MinIO), D-020 (location)
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest
import yaml

from knowledge_mining.mining.contracts.storage.port import ObjectStorePort
from knowledge_mining.mining.infra.object_store import FakeObjectStore, make_object_store
from knowledge_mining.mining.infra.object_store.config import ObjectStoreConfig


# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------


def test_config_defaults_to_fake() -> None:
    cfg = ObjectStoreConfig()
    assert cfg.provider == "fake"
    assert cfg.bucket_prefix == "agentickb-dev-"


def test_config_rejects_unknown_provider() -> None:
    with pytest.raises(ValueError):
        ObjectStoreConfig(provider="redis")


def test_config_repr_redacts_secrets() -> None:
    cfg = ObjectStoreConfig(
        provider="minio",
        access_key="AKIAEXAMPLE",
        secret_key="supersecretvalue",
    )
    rep = repr(cfg)
    assert "supersecretvalue" not in rep
    assert "AKIAEXAMPLE" not in rep
    assert "***set***" in rep


def test_config_from_dict_round_trip() -> None:
    d = {
        "provider": "minio",
        "bucket_prefix": "mykb-",
        "endpoint": "minio.local:9000",
        "access_key": "ak",
        "secret_key": "sk",
        "secure": True,
        "extra_field": "passed-through",
    }
    cfg = ObjectStoreConfig.from_dict(d)
    assert cfg.provider == "minio"
    assert cfg.endpoint == "minio.local:9000"
    assert cfg.secure is True
    assert cfg.extra == {"extra_field": "passed-through"}


def test_config_from_yaml(tmp_path: Path) -> None:
    yaml_path = tmp_path / "storage.yaml"
    yaml_path.write_text(
        yaml.dump(
            {
                "object_store": {
                    "provider": "fake",
                    "bucket_prefix": "yaml-",
                    "root_path": str(tmp_path / "store"),
                }
            }
        ),
        encoding="utf-8",
    )
    cfg = ObjectStoreConfig.from_yaml(yaml_path)
    assert cfg.provider == "fake"
    assert cfg.bucket_prefix == "yaml-"


def test_config_from_yaml_flat_shape(tmp_path: Path) -> None:
    yaml_path = tmp_path / "storage.yaml"
    yaml_path.write_text(
        "provider: fake\nbucket_prefix: flat-\nroot_path: " + str(tmp_path / "flat") + "\n",
        encoding="utf-8",
    )
    cfg = ObjectStoreConfig.from_yaml(yaml_path)
    assert cfg.provider == "fake"
    assert cfg.bucket_prefix == "flat-"


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def test_factory_returns_fake_for_fake_config(tmp_path: Path) -> None:
    cfg = ObjectStoreConfig(provider="fake", root_path=str(tmp_path / "f"))
    store = make_object_store(cfg)
    assert isinstance(store, FakeObjectStore)
    assert isinstance(store, ObjectStorePort)
    assert store.provider == "fake"


def test_factory_rejects_unknown_provider() -> None:
    cfg = ObjectStoreConfig(provider="fake")
    # Bypass __post_init__ validation by constructing then mutating the
    # provider slot via object.__setattr__ (frozen dataclass) to simulate a
    # stray value reaching the factory.
    object.__setattr__(cfg, "provider", "redis")
    with pytest.raises(ValueError):
        make_object_store(cfg)


def test_minio_adapter_constructible_without_real_sdk(monkeypatch) -> None:
    """Constructing MinioObjectStore works even when minio is not installed.

    We inject a stub ``minio`` module into ``sys.modules`` so the lazy import
    inside ``__init__`` succeeds without a real SDK (the spec mandates: do not
    import minio at module top-level; the factory's minio branch must not crash
    import time when minio is absent, and constructing must only fail with a
    clear ImportError when truly absent).
    """
    fake_minio_module = types.ModuleType("minio")

    class _FakeMinio:
        def __init__(self, *args, **kwargs) -> None:
            self.args = args
            self.kwargs = kwargs

    fake_minio_module.Minio = _FakeMinio  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "minio", fake_minio_module)

    cfg = ObjectStoreConfig(
        provider="minio",
        endpoint="minio.local:9000",
        access_key="ak",
        secret_key="sk",
    )
    store = make_object_store(cfg)
    assert store.provider == "minio"
    assert isinstance(store, ObjectStorePort)


def test_minio_adapter_raises_clear_error_when_sdk_truly_absent(monkeypatch) -> None:
    """If minio is genuinely not importable, construction raises ImportError."""
    import builtins

    real_import = builtins.__import__

    def _block_minio(name: str, *args, **kwargs):
        if name == "minio" or name.startswith("minio."):
            raise ImportError(f"No module named '{name.split('.')[0]}'")
        return real_import(name, *args, **kwargs)

    # Ensure no cached minio module is visible.
    monkeypatch.setitem(sys.modules, "minio", None)
    monkeypatch.setattr(builtins, "__import__", _block_minio)

    cfg = ObjectStoreConfig(provider="minio")
    with pytest.raises(ImportError):
        make_object_store(cfg)
