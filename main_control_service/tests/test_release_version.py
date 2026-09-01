from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def _write_manifest(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _manifest() -> dict:
    return {
        "current": "1.0.0",
        "releases": [
            {
                "version": "0.9.0",
                "released_at": "2026-08-01",
                "title": "预览版",
                "changes": ["内部验证"],
            },
            {
                "version": "1.0.0",
                "released_at": "2026-09-01",
                "title": "首个可部署版本",
                "changes": ["知识库管理", "挖掘与检索闭环"],
            },
        ],
    }


def test_loader_returns_the_release_selected_by_current(tmp_path: Path) -> None:
    from main_control_service.release_info import load_current_release

    release = load_current_release(_write_manifest(tmp_path / "releases.json", _manifest()))

    assert release == {
        "version": "1.0.0",
        "released_at": "2026-09-01",
        "title": "首个可部署版本",
        "changes": ["知识库管理", "挖掘与检索闭环"],
    }


def test_loader_rejects_a_current_version_without_a_release_record(tmp_path: Path) -> None:
    from main_control_service.release_info import load_current_release

    payload = _manifest()
    payload["current"] = "2.0.0"

    with pytest.raises(ValueError, match="current release 2.0.0"):
        load_current_release(_write_manifest(tmp_path / "releases.json", payload))


def test_loader_rejects_empty_release_changes(tmp_path: Path) -> None:
    from main_control_service.release_info import load_current_release

    payload = _manifest()
    payload["releases"][1]["changes"] = []

    with pytest.raises(ValueError, match="changes"):
        load_current_release(_write_manifest(tmp_path / "releases.json", payload))


def test_version_endpoint_returns_the_deployed_release(tmp_path: Path) -> None:
    from main_control_service.main import create_app

    manifest_path = _write_manifest(tmp_path / "releases.json", _manifest())
    config_dir = tmp_path / "config"
    config_dir.mkdir()

    with TestClient(
        create_app(config_dir=config_dir, release_manifest_path=manifest_path)
    ) as client:
        response = client.get("/api/v1/version")

    assert response.status_code == 200
    assert response.json()["version"] == "1.0.0"
    assert response.json()["changes"] == ["知识库管理", "挖掘与检索闭环"]


def test_version_endpoint_keeps_the_existing_login_boundary(tmp_path: Path) -> None:
    from main_control_service.jwt_util import encode
    from main_control_service.main import create_app

    manifest_path = _write_manifest(tmp_path / "releases.json", _manifest())
    config_dir = tmp_path / "config"
    system_dir = config_dir / "system"
    system_dir.mkdir(parents=True)
    (system_dir / "auth.yaml").write_text(
        "enabled: true\n"
        "jwt_secret: release-secret\n"
        "internal_verify_secret: internal-secret\n",
        encoding="utf-8",
    )
    token = encode(
        {"sub": "alice", "role": "member", "name": "Alice"},
        "release-secret",
        ttl=3600,
    )

    with TestClient(
        create_app(config_dir=config_dir, release_manifest_path=manifest_path)
    ) as client:
        assert client.get("/api/v1/version").status_code == 401
        response = client.get(
            "/api/v1/version",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    assert response.json()["version"] == "1.0.0"


def test_repository_manifest_preserves_the_first_deployable_version() -> None:
    from main_control_service.release_info import (
        default_release_manifest_path,
        load_current_release,
    )

    manifest_path = default_release_manifest_path()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    first_release = next(
        release for release in manifest["releases"]
        if release["version"] == "1.0.0"
    )

    assert first_release["changes"]
    assert load_current_release(manifest_path)["version"] == manifest["current"]
