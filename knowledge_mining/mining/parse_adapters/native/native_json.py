# -*- coding: utf-8 -*-
"""JSON 原生解析适配器（S2 文件格式支持补全，68号报告 §六.S2）.

结构化 JSON → 结构化文档块（heading/paragraph/table），IR 交给
``LegacyLineNormalizer``（heading level 建 parent 链、columns/rows 建表
资产，均已被 markdown/onenet 链验证）。

映射规则（确定性，无启发式）：
- dict：每个顶层/嵌套键 → heading（level = 深度），值递归；
- 标量叶子（str/num/bool/None）→ paragraph ``"key: value"``（值用
  ``json.dumps`` 规范化：true/false/null/浮点稳定）；
- dict 数组 → table block（columns = 首行键序 ∪ 后续新键，缺失列补空）；
- 标量数组 → paragraph（逗号连接，截断上限）；
- 根标量 → 单 paragraph。

几何上限：元素数（防巨型 JSON 轰炸，截断 + 诊断）；空对象/空数组 →
无块 + warning（挖掘侧 empty_document 质量门如实判 FAIL）。

fingerprint：``native_json@1.0.0#json-walk``。
"""
from __future__ import annotations

import json
from typing import Any

from knowledge_mining.mining.contracts.parser_adapter import (
    BackendBlock,
    BackendParseArtifact,
    ParserAdapterError,
    ParserDescriptor,
    UnsupportedFormat,
)

NATIVE_JSON_PARSER_ID = "native_json"
NATIVE_JSON_VERSION = "1.0.0"
NATIVE_JSON_FINGERPRINT = f"{NATIVE_JSON_PARSER_ID}@{NATIVE_JSON_VERSION}#json-walk"

NATIVE_JSON_MIMES = frozenset({"application/json"})

#: 产块上限（防巨型 JSON 轰炸；超过截断并记 warning）。
_MAX_ELEMENTS = 1_000
#: 标量数组连接条数上限。
_MAX_LIST_JOIN = 100


def _scalar_text(value: Any) -> str:
    """标量 → 稳定文本（json.dumps 规范化：true/false/null/浮点）。"""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


class NativeJsonParser:
    """DocumentParser 实现：JSON → heading/paragraph/table blocks。"""

    def __init__(self) -> None:
        self.descriptor = ParserDescriptor(
            parser_id=NATIVE_JSON_PARSER_ID,
            display_name="Native JSON Parser (json stdlib)",
            version=NATIVE_JSON_VERSION,
            supported_mimes=NATIVE_JSON_MIMES,
            backend_kind="local",
            parser_fingerprint=NATIVE_JSON_FINGERPRINT,
            capabilities=frozenset({"headings", "paragraphs", "tables"}),
        )

    def supports(self, mime: str) -> bool:
        return self.descriptor.supports(mime)

    def parse(self, data: bytes, *, mime: str) -> BackendParseArtifact:
        if not self.supports(mime):
            raise UnsupportedFormat(
                f"{NATIVE_JSON_PARSER_ID} cannot parse mime {mime!r}"
            )
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ParserAdapterError(
                f"{NATIVE_JSON_PARSER_ID}: utf-8 decode failed"
            ) from exc
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ParserAdapterError(
                f"{NATIVE_JSON_PARSER_ID}: invalid JSON: {exc}"
            ) from exc

        blocks: list[BackendBlock] = []
        warnings: list[str] = []
        budget = _Budget()

        if isinstance(payload, dict):
            for key, value in payload.items():
                _walk_value(str(key), value, 1, blocks, warnings, budget)
        elif isinstance(payload, list):
            _walk_value(None, payload, 0, blocks, warnings, budget)
        else:  # 根标量 / null
            blocks.append(BackendBlock(
                block_type="paragraph", text=_scalar_text(payload),
                native_ref={"kind": "json_root"},
            ))

        if budget.truncated:
            warnings.append(
                f"json elements truncated to {_MAX_ELEMENTS}"
            )
        if not blocks:
            warnings.append("empty json: no content blocks")
        return BackendParseArtifact(
            parser_id=NATIVE_JSON_PARSER_ID,
            parser_version=NATIVE_JSON_VERSION,
            mime=mime.lower(),
            blocks=tuple(blocks),
            warnings=tuple(warnings),
        )


class _Budget:
    """产块预算（元素数上限）。"""

    __slots__ = ("count", "truncated")

    def __init__(self) -> None:
        self.count = 0
        self.truncated = False

    def take(self) -> bool:
        """领取一个名额；超限返回 False（调用方停止产块并标记截断）。"""
        if self.count >= _MAX_ELEMENTS:
            self.truncated = True
            return False
        self.count += 1
        return True


def _walk_value(
    key: str | None,
    value: Any,
    depth: int,
    blocks: list[BackendBlock],
    warnings: list[str],
    budget: _Budget,
) -> None:
    """递归产块：dict→heading+子、dict数组→table、标量→paragraph。"""
    if isinstance(value, dict):
        if not value:
            return
        if key is not None:
            if not budget.take():
                return
            blocks.append(BackendBlock(
                block_type="heading", text=key, level=depth,
                native_ref={"kind": "json_key", "key": key},
            ))
        for k, v in value.items():
            _walk_value(str(k), v, depth + 1, blocks, warnings, budget)
        return

    if isinstance(value, list):
        if not value:
            return
        if value and all(isinstance(item, dict) for item in value):
            # dict 数组 → table（键 = 首行键序 ∪ 后续新键；缺失补空）
            columns: list[str] = []
            for item in value:
                for k in item:
                    if k not in columns:
                        columns.append(str(k))
            rows = [
                {col: _scalar_text(item.get(col, "")) for col in columns}
                for item in value
            ]
            if not budget.take():
                return
            if key is not None:
                blocks.append(BackendBlock(
                    block_type="heading", text=key, level=depth,
                    native_ref={"kind": "json_key", "key": key},
                ))
            blocks.append(BackendBlock(
                block_type="table", text="",
                structure={"columns": columns, "rows": rows},
                native_ref={"kind": "json_array", "key": key},
            ))
            return
        # 标量数组 → 段落（截断连接）
        joined = "、".join(
            _scalar_text(item) for item in value[:_MAX_LIST_JOIN]
        )
        if len(value) > _MAX_LIST_JOIN:
            warnings.append(
                f"json list of {key or 'root'} truncated to "
                f"{_MAX_LIST_JOIN} items in join"
            )
        text = f"{key}: {joined}" if key is not None else joined
        if not budget.take():
            return
        blocks.append(BackendBlock(
            block_type="paragraph", text=text,
            native_ref={"kind": "json_list", "key": key},
        ))
        return

    # 标量叶子
    text = (
        f"{key}: {_scalar_text(value)}" if key is not None
        else _scalar_text(value)
    )
    if not budget.take():
        return
    blocks.append(BackendBlock(
        block_type="paragraph", text=text,
        native_ref={"kind": "json_leaf", "key": key},
    ))


__all__ = [
    "NATIVE_JSON_FINGERPRINT", "NATIVE_JSON_MIMES", "NATIVE_JSON_PARSER_ID",
    "NATIVE_JSON_VERSION", "NativeJsonParser",
]
