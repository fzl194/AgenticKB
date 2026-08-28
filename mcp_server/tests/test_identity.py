"""阶段 A（批次5）：MCP 身份解析——Bearer 提取与开放库范围解析的纯逻辑。"""
from __future__ import annotations

import pytest

from mcp_server.identity import Identity, IdentityError, extract_bearer_token, resolve_kb_ids


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
