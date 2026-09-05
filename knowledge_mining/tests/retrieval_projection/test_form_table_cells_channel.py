"""36号根因 6：表格 cells 列号通道（表单/横幅表头文档入库）.

2026-09-04 现场回归（`05.学术学位申请审批书.doc`）：表头只取首个
is_header 行，横幅式表头仅 1 个 cell、网格 10+ 列；表头外列拿
`col{N}` 兜底名且真实列号丢失；结构投影按列名反查把所有表头外列
塌缩为 `column_index=-1`，同行 ≥2 个即撞
`asset_table_cells_staging_pkey`，整篇文档 asset_persist 崩溃。

契约（36号 §二.6 修复要求）：
- `row_cells` 携带真实列号三元组 `[name, value, column_index]`；
- 投影优先消费真实列号，未知列名不得塌缩为单一 -1；
- 同键重复 cell 防御去重（保留首个并计数），不炸整篇入库；
- legacy 二元组（29号格式落库行）读取端兼容。
"""
from __future__ import annotations

from knowledge_mining.mining.contracts.parse_ir.enums import (
    PARSE_IR_SCHEMA_VERSION,
)
from knowledge_mining.mining.contracts.parse_ir.types import (
    Confidence,
    Container,
    Element,
    EvidenceSpan,
    ParseIdentity,
    ParsedDocument,
    TableAsset,
    TableCell,
)
from knowledge_mining.mining.contracts.segment_compiler import (
    CompiledSegment,
    SegmentPolicy,
)


def _span(i: int) -> EvidenceSpan:
    return EvidenceSpan(span_id=f"s{i}", raw_text=f"span-{i}")


def _el(eid, etype, text, order=0):
    return Element(
        element_id=eid, element_type=etype, order_index=order, text=text,
        style={}, source_spans=tuple(_span(i) for i in range(1)),
    )


def _form_table_doc() -> ParsedDocument:
    """审批书式表单：横幅表头（1 cell 跨 12 列）+ 稀疏网格数据行.

    形态取自现场 e-00015/e-00016 表：header(1) vs 网格 12 列；
    行 1 的列 0 在表头覆盖内（拿到横幅名），列 2/4/7/8 全部在表头外。
    """
    cells = (
        # 横幅表头：单一 cell 跨全表（is_header 行只有 1 个格子）
        TableCell(row_index=0, column_index=0, text="攻读硕士学位研究生课程考试成绩",
                  is_header=True, column_span=12),
        # 数据行 1：稀疏列（表单格子布局）
        TableCell(row_index=1, column_index=0, text="傅志凌"),
        TableCell(row_index=1, column_index=2, text="男"),
        TableCell(row_index=1, column_index=4, text="1999.06.10"),
        TableCell(row_index=1, column_index=7, text="出  生  地"),
        TableCell(row_index=1, column_index=8, text="江西宜春"),
        # 数据行 2
        TableCell(row_index=2, column_index=0, text="华东理工大学"),
        TableCell(row_index=2, column_index=9, text="全国大学英语四级"),
    )
    asset = TableAsset(
        table_id="e15-table", page_span_ids=("c0",), rows=3, columns=12,
        cells=cells, header_regions=((0, 0),),
        confidence=Confidence(source="t"),
    )
    return ParsedDocument(
        schema_version=PARSE_IR_SCHEMA_VERSION,
        source_identity=ParseIdentity(
            source_raw_hash="raw-15", parser_fingerprint="t@1",
            parse_ir_schema_version=PARSE_IR_SCHEMA_VERSION,
        ),
        containers=(Container(container_id="c0", container_type="page",
                              order_index=0),),
        elements=tuple([
            _el("e15", "table",
                "攻读硕士学位研究生课程考试成绩\n傅志凌\t\t男\t\t1999.06.10"
                "\t\t\t出  生  地\t江西宜春", order=0),
        ]),
        relations=(),
        structured_assets={"e15-table": asset},
    )


def _compiled():
    from knowledge_mining.mining.segment_compiler.compiler import compile_segments

    return compile_segments(_form_table_doc(), SegmentPolicy(table_view="both"))


def _row_segments():
    return sorted(
        (s for s in _compiled() if s.block_type == "table_row"),
        key=lambda s: s.metadata["row_index"],
    )


def _duplicate_cell_segments() -> tuple[CompiledSegment, ...]:
    def seg(view, extra):
        meta = {
            "view": view, "table_ref": "t-dup",
            "table_header": ["A", "B"], "rows": 2, "columns": 2,
        }
        meta.update(extra)
        return CompiledSegment(
            segment_index=-1,
            block_type="table" if view == "whole" else "table_row",
            raw_text="x", metadata=meta,
        )

    return (
        seg("whole", {}),
        seg("row", {
            "row_index": 1,
            "row_cells": [["A", "first", 0], ["A", "second", 0], ["B", "b", 1]],
        }),
        seg("row", {
            "row_index": 1,
            "row_cells": [["A", "third", 0]],
        }),
    )


def test_row_cells_carry_true_column_index():
    """三元组契约：[name, value, column_index]——表头外列不再丢列号."""
    rows = _row_segments()
    row1 = next(s for s in rows if s.metadata["row_index"] == 1)
    triplets = row1.metadata["row_cells"]
    assert triplets == [
        ["攻读硕士学位研究生课程考试成绩", "傅志凌", 0],
        ["col2", "男", 2],
        ["col4", "1999.06.10", 4],
        ["col7", "出  生  地", 7],
        ["col8", "江西宜春", 8],
    ], triplets


def test_sparse_header_preserves_physical_column_positions():
    """表头位于 0/2 列时不得压缩成 [A,C] 后把数据列 1 错标为 C。"""
    from knowledge_mining.mining.segment_compiler.compiler import _header_of

    asset = TableAsset(
        table_id="sparse", page_span_ids=("c0",), rows=2, columns=4,
        cells=(
            TableCell(row_index=0, column_index=0, text="A", is_header=True),
            TableCell(row_index=0, column_index=2, text="C", is_header=True),
            TableCell(row_index=1, column_index=1, text="value"),
        ),
        header_regions=((0, 0),), confidence=Confidence(source="t"),
    )

    headers, rows = _header_of(asset)
    assert rows == {0}
    assert headers == ["A", "col1", "C", "col3"]


def test_projection_keeps_true_column_index_for_out_of_header_cells():
    """投影消费真实列号：col2/col4/col7/col8 各归其位，绝不塌缩为 -1."""
    from knowledge_mining.mining.retrieval_projection.structure_projection import (
        project_structure,
    )

    structure = project_structure(_compiled(), document_ref="form.doc")
    cells = [c for c in structure.table_cells if c["table_ref"] == "e15-table"]
    row1 = {c["column_index"]: c["value"] for c in cells if c["row"] == 1}
    assert row1 == {
        0: "傅志凌", 2: "男", 4: "1999.06.10", 7: "出  生  地", 8: "江西宜春",
    }, row1
    columns = structure.table_assets[0]["columns"]
    assert columns[2] == "col2"
    assert columns[8] == "col8"


def test_form_document_cells_have_unique_primary_keys():
    """入库契约：全表 (table_ref, row, column_index) 无重复——staging
    主键 (snapshot_id, table_ref, row_index, column_index) 不可能再撞."""
    from knowledge_mining.mining.retrieval_projection.structure_projection import (
        project_structure,
    )

    structure = project_structure(_compiled(), document_ref="form.doc")
    cells = [c for c in structure.table_cells if c["table_ref"] == "e15-table"]
    # 行 1 五个 cell + 行 2 两个，一个不少
    assert len(cells) == 7, cells
    keys = {(c["table_ref"], c["row"], c["column_index"]) for c in cells}
    assert len(keys) == len(cells), cells


def test_projection_accepts_legacy_binary_row_cells():
    """读取端兼容：29号二元组（已落库行）仍可消费——列名在表头内的
    正常入库；表头外的按旧语义落 -1（同一行最多保一个，去重）."""
    from knowledge_mining.mining.retrieval_projection.structure_projection import (
        project_structure,
    )

    seg = CompiledSegment(
        segment_index=-1, block_type="table",
        raw_text="表头\tv1\tv2",
        metadata={
            "view": "whole", "table_ref": "t-legacy",
            "table_header": ["表头"],
            "rows": 2, "columns": 3,
        },
    )
    row = CompiledSegment(
        segment_index=-1, block_type="table_row",
        raw_text="表头=v1；未知列A=v2；未知列B=v3",
        metadata={
            "view": "row", "table_ref": "t-legacy",
            "table_header": ["表头"], "row_index": 1,
            "row_cells": [["表头", "v1"], ["未知列A", "v2"], ["未知列B", "v3"]],
        },
    )
    structure = project_structure((seg, row), document_ref="legacy.doc")
    cells = [c for c in structure.table_cells if c["table_ref"] == "t-legacy"]
    # 已知列正常入；未知列只剩首个 -1（防御去重），其余丢弃且计数
    assert [(c["row"], c["column_index"], c["value"]) for c in cells] == [
        (1, 0, "v1"), (1, -1, "v2"),
    ], cells
    assert structure.dropped_duplicate_cells == 1


def test_projection_dedups_duplicate_keys_defensively():
    """恶意/异常形状：同 (row, column_index) 重复 cell → 保留首个并计数，
    绝不让一张表的形状问题炸掉整篇文档的 asset_persist."""
    from knowledge_mining.mining.retrieval_projection.structure_projection import (
        project_structure,
    )

    structure = project_structure(_duplicate_cell_segments(), document_ref="dup.doc")
    cells = [c for c in structure.table_cells if c["table_ref"] == "t-dup"]
    assert [(c["column_index"], c["value"]) for c in cells if c["row"] == 1] == [
        (0, "first"), (1, "b"),
    ], cells
    assert structure.dropped_duplicate_cells == 2


def test_asset_persist_surfaces_dropped_cells_in_diagnostics_and_warning():
    """去重不能静默：数量进入文档 diagnostics，并形成节点 warning。"""
    from types import SimpleNamespace

    from knowledge_mining.mining.retrieval_projection.persist import (
        AssetPersistService,
        MemoryAssetWriter,
    )
    from knowledge_mining.mining.workflow.bundle import MiningDocumentBundle
    from knowledge_mining.mining.workflow.core import DocumentState
    from knowledge_mining.mining.workflow.handlers.persist import (
        asset_persist_handler,
    )

    class _Store:
        def __init__(self, rows):
            self.rows = rows

        async def list_for_snapshot(self, _snapshot_id):
            return self.rows

    service = AssetPersistService(
        segment_store=_Store(_duplicate_cell_segments()),
        representation_store=_Store(()), embedding_store=_Store(()),
        writer=MemoryAssetWriter(),
    )
    state = DocumentState(
        run_document_id="rd-1", doc_key="dup.doc",
        context=MiningDocumentBundle(
            document_ref="dup.doc", run_document_id="rd-1",
            snapshot_ref="snap-1", document_id="doc-1",
        ),
    )
    staged = []
    runtime = SimpleNamespace(
        manifest={"runId": "run-1"},
        runtime_repository=SimpleNamespace(
            document_persist_marker=lambda _rd: None,
        ),
        services=SimpleNamespace(
            asset_persist_service=service,
            stage_document=lambda *args: staged.append(args),
            document_persist_lock=None,
        ),
    )

    result = asset_persist_handler(state, {}, runtime)

    assert result.status.value == "success"
    assert result.outputs.context.diagnostics["dropped_duplicate_cells"] == 2
    assert [warning.code for warning in result.warnings] == [
        "duplicate_table_cells_dropped",
    ]
