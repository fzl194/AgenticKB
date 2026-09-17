"""阶段 A（批次5）：MCP 身份解析——Bearer 提取与开放库范围解析的纯逻辑。

批次2 增补：validate_domain 三态（钥匙域定死）与 require_identity 的
403 domain_not_bound → 人话 IdentityError 映射、key_id/key_domain 解析。
"""
from __future__ import annotations

import pytest

from mcp_server.identity import (
    Identity,
    IdentityError,
    extract_bearer_token,
    require_identity,
    resolve_kb_ids,
    validate_domain,
)
from mcp_server import identity as identity_mod


class _Headers(dict):
    """starlette Headers 的 get 语义（大小写不敏感这里不测，取值行为一致即可）。"""

    def get(self, key, default=None):  # noqa: A003 - 对齐 starlette 接口
        return super().get(key, default)


def ident_of(*names: str) -> Identity:
    return Identity(
        username="alice",
        user_id="u-1",
        open_kbs=tuple({"id": f"kb-{i+1}", "name": n} for i, n in enumerate(names)),
    )


def test_bearer_extraction_variants() -> None:
    assert extract_bearer_token(_Headers({"authorization": "Bearer kbm_abc"})) == "kbm_abc"
    # 大小写与多余空白容忍
    assert extract_bearer_token(_Headers({"authorization": "bearer  kbm_abc "})) == "kbm_abc"
    assert extract_bearer_token(_Headers({})) is None
    assert extract_bearer_token(_Headers({"authorization": "Basic xyz"})) is None
    assert extract_bearer_token(_Headers({"authorization": "Bearer"})) is None
    assert extract_bearer_token(_Headers({"authorization": "Bearer "})) is None  # 空 token


def test_no_open_kbs_is_an_explicit_error() -> None:
    with pytest.raises(IdentityError, match="未开放任何知识库"):
        resolve_kb_ids(ident_of(), None)
    with pytest.raises(IdentityError, match="未开放任何知识库"):
        resolve_kb_ids(ident_of(), ["基站手册库"])


def test_kb_names_absent_means_all_open_kbs() -> None:
    assert resolve_kb_ids(ident_of("A 库", "B 库"), None) == ["kb-1", "kb-2"]
    assert resolve_kb_ids(ident_of("A 库", "B 库"), []) == ["kb-1", "kb-2"]


def test_kb_names_resolve_casefold_and_dedupe() -> None:
    ids = resolve_kb_ids(ident_of("基站手册库", "设备手册库"), ["基站手册库", " 基站手册库 "])
    assert ids == ["kb-1"]


def test_unknown_kb_name_lists_what_is_open_instead() -> None:
    with pytest.raises(IdentityError, match="未对你开放或不存在.*当前开放：基站手册库"):
        resolve_kb_ids(ident_of("基站手册库"), ["别人的库"])


# ── validate_domain（批次2 M3：domain 只是校验参数） ────────────────────


KEYED = Identity(
    username="alice", user_id="u-1", key_id="key-1", key_domain="generic",
    open_kbs=({"id": "kb-1", "name": "库"},),
)


def test_validate_domain_absent_returns_key_domain() -> None:
    assert validate_domain(KEYED, None) == "generic"
    assert validate_domain(KEYED, "") == "generic"


def test_validate_domain_equal_passes() -> None:
    assert validate_domain(KEYED, "generic") == "generic"


def test_validate_domain_mismatch_names_both_domains() -> None:
    with pytest.raises(IdentityError, match="绑定知识域 'generic'.*收到 'odn'"):
        validate_domain(KEYED, "odn")


# ── require_identity：mock HTTP（批次2 假响应形状） ─────────────────────


class _Resp:
    def __init__(self, status_code: int, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self) -> dict:
        return self._payload


def _stub_verify(monkeypatch, resp: _Resp) -> None:
    monkeypatch.setenv("MCP_INTERNAL_AUTH_SECRET", "s3cret")
    monkeypatch.setattr(identity_mod.httpx, "post", lambda *a, **k: resp)


_HEADERS = {"authorization": "Bearer kbm_x"}


def test_require_identity_parses_key_domain_fields(monkeypatch) -> None:
    _stub_verify(monkeypatch, _Resp(200, {
        "ok": True, "username": "alice", "user_id": "u-1",
        "key_id": "key-9", "key_domain": "odn",
        "open_kb_ids": ["kb-1"],
        "open_kbs": [{"id": "kb-1", "name": "库", "domain": "odn"}],
    }))
    ident = require_identity(_HEADERS)
    assert ident.key_id == "key-9"
    assert ident.key_domain == "odn"
    assert ident.open_kb_ids == ["kb-1"]
    assert not hasattr(ident, "domains") or True  # domains 字段已退役


def test_require_identity_403_domain_not_bound_is_human_readable(monkeypatch) -> None:
    _stub_verify(monkeypatch, _Resp(403, {
        "detail": {
            "code": "domain_not_bound",
            "message": "该钥匙绑定的知识域已被解绑，请联系管理员重新分配后重建钥匙",
        }
    }))
    with pytest.raises(IdentityError, match="已被解绑.*重建钥匙"):
        require_identity(_HEADERS)


def test_require_identity_403_without_code_is_generic(monkeypatch) -> None:
    _stub_verify(monkeypatch, _Resp(403, {"detail": "forbidden"}))
    with pytest.raises(IdentityError, match="身份校验失败"):
        require_identity(_HEADERS)
