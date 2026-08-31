"""29号复审 R03（Wave 2）：staging/晋升架构的单元契约.

- PgAssetWriter 只写 *_staging；范式无 embedding 节点时清 staging 向量
  （防上轮残影晋升）；
- AssetCoreDB.promote_snapshot_assets 逐张表"清 final → 拷 staging → 清
  staging"，列清单显式（生成列 search_vector 不参与）。
"""
from __future__ import annotations

from contextlib import contextmanager


class _SyncRecordingConn:
    def __init__(self, log: list) -> None:
        self._log = log
        self.autocommit = False

    def cursor(self, row_factory=None):
        outer = self

        class _Cur:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, sql, params):
                outer._log.append((sql, params))

            def fetchone(self):
                return None

            def fetchall(self):
                return []

            rowcount = 0

        return _Cur()

    def commit(self):
        pass


class _SyncRecordingPool:
    def __init__(self) -> None:
        self.log: list = []

    @contextmanager
    def connection(self):
        yield _SyncRecordingConn(self.log)


def _faces_without_embeddings() -> dict:
    return {
        "schema_version": "asset-v2-1",
        "persist_version": "1",
        "document_ref": "manual.md",
        "tokenizer_version": "jieba-default-1",
        "raw_segments": (),
        "structure_nodes": (),
        "structure_edges": (),
        "table_assets": (),
        "table_cells": (),
        "representations": (
            {
                "representation_id": "s1:prose:0", "representation_type": "prose",
                "content_type": "paragraph", "content_text": "正文",
                "structural_context": "", "target_type": "segment",
                "target_ref": "d#seg:0", "canonical_evidence_id": "s1:prose:0",
                "container_ref": None, "ordinal": 0,
            },
        ),
        "lexical_rows": (
            {"representation_id": "s1:prose:0", "lexical_text": "正文",
             "tokenizer_version": "jieba-default-1"},
        ),
        "embeddings": (),  # lexical 范式：无 embedding 节点
        "raw_segment_count": 0,
        "representation_count": 1,
        "embedding_count": 0,
        "structure_node_count": 0,
        "readiness": {"search_ready": True},
    }


def test_writer_targets_staging_and_clears_stale_embeddings():
    from knowledge_mining.mining.retrieval_projection.repositories_pg import (
        PgAssetWriter,
    )
    from tests.retrieval_projection.test_repositories_pg import recording_pool

    pool = recording_pool()
    writer = PgAssetWriter(pool)
    assert writer.replace_for_snapshot("snap-1", _faces_without_embeddings()) == 1

    body = " ".join(e[0] for e in pool.log)
    # 全部派生资产写入 staging——final 不被触碰
    assert "INSERT INTO asset_retrieval_units_v2_staging" in body
    assert "INSERT INTO asset_retrieval_units_v2 (" not in body.replace(
        "asset_retrieval_units_v2_staging (", "",
    )
    # 无 embedding 面：清 staging 向量（防 hybrid→lexical 切换残影）
    deletes = [
        e for e in pool.log
        if e[0].startswith(
            "DELETE FROM asset_retrieval_embeddings_v2_staging",
        )
    ]
    assert deletes and deletes[0][1] == ["snap-1"]
    # readiness 落 staging
    assert "INSERT INTO asset_snapshot_readiness_staging" in body


def test_promote_swaps_all_seven_tables_with_explicit_columns():
    from knowledge_mining.mining.infra.db import AssetCoreDB

    pool = _SyncRecordingPool()
    db = AssetCoreDB(pool)
    with db.transaction():
        count = db.promote_snapshot_assets(["snap-1", "snap-2"])

    assert count == 2
    sqls = [e[0] for e in pool.log if not e[0].startswith("<")]
    tables = {
        "asset_retrieval_units_v2", "asset_retrieval_embeddings_v2",
        "asset_structure_nodes", "asset_structure_edges",
        "asset_structured_assets", "asset_table_cells",
        "asset_snapshot_readiness",
    }
    for table in tables:
        final_delete = [
            s for s in sqls
            if s.startswith(f"DELETE FROM {table} WHERE")
        ]
        copy_in = [
            s for s in sqls
            if s.startswith(f"INSERT INTO {table} (")
            and f"FROM {table}_staging" in s
        ]
        staging_delete = [
            s for s in sqls
            if s.startswith(f"DELETE FROM {table}_staging WHERE")
        ]
        assert final_delete and copy_in and staging_delete, table
        assert "search_vector" not in copy_in[0]  # 生成列不参与晋升
    # 逐表顺序：final 清旧必须先于拷贝
    first_copy = next(
        i for i, s in enumerate(sqls) if s.startswith("INSERT INTO ")
    )
    assert any(
        s.startswith("DELETE FROM ") and "staging" not in s
        for s in sqls[:first_copy]
    )


def test_asset_activation_requires_readiness_and_failure_policy() -> None:
    from knowledge_mining.mining.jobs.run import (
        _asset_activation_allowed,
        _asset_activation_block_reason,
    )

    assert _asset_activation_allowed(
        readiness_ok=True, has_failures=False,
        publish_on_partial_failure=False,
    ) is True
    assert _asset_activation_allowed(
        readiness_ok=False, has_failures=False,
        publish_on_partial_failure=False,
    ) is False
    assert _asset_activation_allowed(
        readiness_ok=True, has_failures=True,
        publish_on_partial_failure=False,
    ) is False
    assert _asset_activation_allowed(
        readiness_ok=True, has_failures=True,
        publish_on_partial_failure=True,
    ) is True
    assert "readiness" in _asset_activation_block_reason(
        readiness_ok=False, has_failures=False,
        publish_on_partial_failure=False,
    )
    assert "document failures" in _asset_activation_block_reason(
        readiness_ok=True, has_failures=True,
        publish_on_partial_failure=False,
    )
    assert _asset_activation_block_reason(
        readiness_ok=True, has_failures=False,
        publish_on_partial_failure=False,
    ) is None
