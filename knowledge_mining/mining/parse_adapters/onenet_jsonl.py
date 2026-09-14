# -*- coding: utf-8 -*-
"""知识一张网切片批次解析适配器（47 号 §二「切片批次即文件格式」）.

输入对象：一个还原「HTML 文件」的全字段切片 JSONL（每行 {nid, part_id,
path, title, content, ...}，行已按 part_id 升序——由 onenet.import_service
写入保证）。

parse 产出（BackendBlock 序列，喂 ``LegacyLineNormalizer``）：

- heading：切片 path 各层级，**仅在新进入层级时产出**（连续同 path 切片
  不重复），``level`` = 层级深度（1 起，normalizer 据此建 parent 链）；
- paragraph：content 清洗 ``[tbl_predict_*]`` 后的正文；
- table：content 内 ``[tbl_predict_start/end]`` 管道表格块，
  ``structure={"columns":[...], "rows":[{col:val}]}``（normalizer 建表资产）；
- 每个 block 带 ``native_ref={"nid":..., "part_id":...}``（回源溯源）。

专用 MIME ``application/x-onenet+jsonl``——registry 按首个 supports 命中
胜出，专用 MIME 保证不被 legacy_txt 等文本类 descriptor 抢路由。
"""
from __future__ import annotations

import json
import re
from typing import Any

from knowledge_mining.mining.contracts.parser_adapter import (
    BackendBlock,
    BackendParseArtifact,
    ParserDescriptor,
    ParserAdapterError,
    UnsupportedFormat,
)
from knowledge_mining.mining.onenet.restore import clean_content, split_path

ONENET_JSONL_PARSER_ID = "onenet_jsonl"
ONENET_JSONL_VERSION = "1.0.0"
#: 规则常量进指纹：β 还原规则或块产出规则变化 → 指纹变化 → 新快照。
ONENET_JSONL_FINGERPRINT = (
    f"{ONENET_JSONL_PARSER_ID}@{ONENET_JSONL_VERSION}"
    "#path-headings/pipe-tables/nid-native-ref"
)

ONENET_JSONL_MIME = "application/x-onenet+jsonl"
ONENET_JSONL_MIMES = frozenset({ONENET_JSONL_MIME})

_TBL_BLOCK_RE = re.compile(
    r"\[tbl_predict_start\]\s*(.*?)\s*\[tbl_predict_end\]", re.S)
_SEP_CELL_RE = re.compile(r"^:?\s*-{2,}\s*:?$")


def _parse_pipe_table(block_text: str) -> dict[str, Any] | None:
    """管道表格文本 → normalizer 表结构 {columns, rows}；非表格返回 None.

    表格逻辑自 kone_connector/src/ingest_kb.py `_extract_tables` 移植：
    首个非分隔行 = 表头，其余 = 数据行；分隔行（|---|---|）跳过。
    """
    lines = [ln.strip() for ln in block_text.splitlines() if ln.strip()]
    grid: list[list[str]] = []
    for ln in lines:
        if not ln.startswith("|"):
            return None
        cells = [c.strip() for c in ln.strip("|").split("|")]
        if all(_SEP_CELL_RE.match(c or "-") for c in cells):
            continue  # 分隔行
        grid.append(cells)
    if len(grid) < 2:  # 表头 + 至少一行数据
        return None
    columns = grid[0]
    rows = [
        {columns[i] if i < len(columns) else f"col{i}": (r[i] if i < len(r) else "")
         for i in range(len(columns))}
        for r in grid[1:]
    ]
    return {"columns": columns, "rows": rows}


class OnenetJsonlParser:
    """DocumentParser 实现：切片 JSONL → heading/paragraph/table blocks."""

    def __init__(self) -> None:
        self.descriptor = ParserDescriptor(
            parser_id=ONENET_JSONL_PARSER_ID,
            display_name="Onenet Slice-Batch (JSONL) Parser",
            version=ONENET_JSONL_VERSION,
            supported_mimes=ONENET_JSONL_MIMES,
            backend_kind="local",
            parser_fingerprint=ONENET_JSONL_FINGERPRINT,
            capabilities=frozenset({"headings", "tables", "paragraphs"}),
        )

    def supports(self, mime: str) -> bool:
        return self.descriptor.supports(mime)

    def parse(self, data: bytes, *, mime: str) -> BackendParseArtifact:
        if not self.supports(mime):
            raise UnsupportedFormat(
                f"{ONENET_JSONL_PARSER_ID} cannot parse mime {mime!r}")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as e:
            raise ParserAdapterError(f"{ONENET_JSONL_PARSER_ID}: utf-8 decode 失败") from e

        blocks: list[BackendBlock] = []
        warnings: list[str] = []
        bad_lines = 0
        last_heading_segs: list[str] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                bad_lines += 1
                continue
            if not isinstance(row, dict):
                bad_lines += 1
                continue
            nid = str(row.get("nid") or "")
            part_id = row.get("part_id")
            native_ref: dict[str, Any] = {}
            if nid:
                native_ref["nid"] = nid
            if part_id is not None:
                native_ref["part_id"] = int(part_id)

            # 1) path 层级 → heading（仅新进入层级时产出）
            segs = split_path(row.get("path"))
            if len(segs) >= 2:
                segs = segs[1:]  # 剔包名
            if segs and segs != last_heading_segs:
                common = 0
                for a, b in zip(last_heading_segs, segs):
                    if a == b:
                        common += 1
                    else:
                        break
                for depth in range(common, len(segs)):
                    blocks.append(BackendBlock(
                        block_type="heading",
                        text=segs[depth],
                        level=depth + 1,
                        native_ref=native_ref or None,
                    ))
                last_heading_segs = segs

            # 2) content → paragraph / table
            content = row.get("content") or ""
            pos = 0
            for m in _TBL_BLOCK_RE.finditer(content):
                before = content[pos:m.start()].strip()
                if before:
                    blocks.append(BackendBlock(
                        block_type="paragraph", text=before,
                        native_ref=native_ref or None))
                table = _parse_pipe_table(m.group(1))
                if table is not None:
                    blocks.append(BackendBlock(
                        block_type="table", text=m.group(1).strip(),
                        structure=table, native_ref=native_ref or None))
                else:
                    warnings.append(f"表格块未解析为管道表格: {nid}")
                pos = m.end()
            tail = content[pos:].strip()
            if tail:
                blocks.append(BackendBlock(
                    block_type="paragraph", text=tail,
                    native_ref=native_ref or None))

        if bad_lines:
            warnings.append(f"坏行跳过: {bad_lines}")
        return BackendParseArtifact(
            parser_id=ONENET_JSONL_PARSER_ID,
            parser_version=ONENET_JSONL_VERSION,
            mime=mime.lower(),
            blocks=tuple(blocks),
            raw_output=text,
            warnings=tuple(warnings),
        )


__all__ = [
    "ONENET_JSONL_FINGERPRINT", "ONENET_JSONL_MIME", "ONENET_JSONL_MIMES",
    "ONENET_JSONL_PARSER_ID", "ONENET_JSONL_VERSION", "OnenetJsonlParser",
]
