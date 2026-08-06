from __future__ import annotations

from knowledge_mining.mining.infra import control_plane


def test_auth_config_cache_set_get():
    control_plane.set_auth_config({
        "jwt_secret": "s",
        "internal_verify_secret": "ivs-test",
        "token_ttl_seconds": 3600,
        "bootstrap": {"admin_password": "x"},
    })
    cfg = control_plane.get_auth_config()
    assert cfg["internal_verify_secret"] == "ivs-test"
    assert control_plane.get_internal_verify_secret() == "ivs-test"


def test_internal_verify_secret_none_when_missing():
    control_plane.set_auth_config({"jwt_secret": "s"})  # 无 internal_verify_secret
    assert control_plane.get_internal_verify_secret() is None


def test_internal_verify_secret_none_when_empty_string():
    control_plane.set_auth_config({"internal_verify_secret": ""})
    assert control_plane.get_internal_verify_secret() is None
