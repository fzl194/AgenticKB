"""批次4 验收门禁：validate_build 按范式能力校验。

背景（P04）：build 只检查『每快照 ≥1 切片』，不检查检索卡片/向量——
全量基线挖掘曾整批静默丢卡片仍 validated。本组测试锁定：
- assemble_build 把范式能力集与 embedding_fallback 冻进 build.summary_json
- validate_build：含 retrieval_unit_build 能力 → 每快照 ≥1 单元
- 含 embedding 能力 → 每快照 ≥1 向量，或 summary 已留痕 embedding_fallback
- legacy build（summary 无能力字段）→ 降级旧检查，行为不变
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any

import pytest

from knowledge_mining.mining.stages.publishing import assemble_build, validate_build


class FakeAssetCoreDB:
    """publishing 层所需的最小 AssetCoreDB 双打。"""

    def __init__(
        self,
        *,
        segments_by_snapshot: dict[str, int] | None = None,
        units_by_snapshot: dict[str, int] | None = None,
        embeddings_by_snapshot: dict[str, int] | None = None,
    ) -> None:
        self.segments_by_snapshot = segments_by_snapshot or {}
        self.units_by_snapshot = units_by_snapshot or {}
        self.embeddings_by_snapshot = embeddings_by_snapshot or {}
        self.builds: dict[str, dict[str, Any]] = {}
        self.build_snapshots: dict[str, list[dict[str, Any]]] = {}
        self.inserted_builds: list[dict[str, Any]] = []
        self.status_updates: list[tuple[str, str]] = []

    @contextmanager
    def transaction(self):
        yield

    def acquire_domain_publish_lock(self, domain):
        return None

    def get_source_batch(self, *, domain, batch_id):
        return None

    def get_active_build(self, *, domain, channel):
        return None

    def get_build(self, build_id):
        return self.builds.get(build_id)

    def get_build_snapshots(self, build_id):
        return self.build_snapshots.get(build_id, [])

    def insert_build(self, **kwargs):
        self.inserted_builds.append(kwargs)
        self.builds[kwargs["build_id"]] = {
            "id": kwargs["build_id"],
            "build_mode": kwargs.get("build_mode", "full"),
            "parent_build_id": kwargs.get("parent_build_id"),
            "status": kwargs.get("status", "building"),
            "summary_json": kwargs.get("summary_json") or {},
        }
        return kwargs["build_id"]

    def upsert_build_document_snapshot(self, **kwargs):
        self.build_snapshots.setdefault(kwargs["build_id"], []).append(kwargs)

    def update_build_status(self, build_id, status):
        self.status_updates.append((build_id, status))
        if build_id in self.builds:
            self.builds[build_id]["status"] = status

    def count_segments_by_snapshot(self, snapshot_id):
        return self.segments_by_snapshot.get(snapshot_id, 0)

    def count_retrieval_units_by_snapshot(self, snapshot_id):
        return self.units_by_snapshot.get(snapshot_id, 0)

    def count_embeddings_by_snapshot(self, snapshot_id):
        return self.embeddings_by_snapshot.get(snapshot_id, 0)


def _decisions(snapshot_id: str = "snap-1") -> list[dict[str, Any]]:
    return [{
        "document_id": "doc-1",
        "document_snapshot_id": snapshot_id,
        "action": "NEW",
        "reason": "add",
        "selection_status": "active",
    }]


FULL_BASELINE = [
    "asset_persist", "embedding", "enrich", "retrieval_unit_build",
    "segment_compile",
]


def test_assemble_build_freezes_capability_signature_into_summary() -> None:
    db = FakeAssetCoreDB(segments_by_snapshot={"snap-1": 2},
                         units_by_snapshot={"snap-1": 3},
                         embeddings_by_snapshot={"snap-1": 3})

    assemble_build(
        db, domain="odn", channel="prod", run_id="run-1", batch_id=None,
        snapshot_decisions=_decisions(), kb_id=None,
        capabilities=FULL_BASELINE, embedding_fallback=False,
    )

    summary = db.inserted_builds[0]["summary_json"]
    assert summary["paradigm_capabilities"] == sorted(FULL_BASELINE)
    assert summary["embedding_fallback"] is False


def test_validate_blocks_zero_units_when_paradigm_requires_them() -> None:
    db = FakeAssetCoreDB(segments_by_snapshot={"snap-1": 2},
                         units_by_snapshot={"snap-1": 0},
                         embeddings_by_snapshot={"snap-1": 0})

    with pytest.raises(ValueError, match="retrieval unit"):
        assemble_build(
            db, domain="odn", channel="prod", run_id="run-1", batch_id=None,
            snapshot_decisions=_decisions(), kb_id=None,
            capabilities=FULL_BASELINE, embedding_fallback=True,
        )
    # 校验失败 → build 不得标 validated
    assert ("build" not in db.status_updates
            or "validated" not in {s for _, s in db.status_updates})


def test_validate_allows_legacy_builds_without_capability_field() -> None:
    """legacy build（无 paradigm_capabilities 字段）→ 降级旧检查：有切片即过。"""
    db = FakeAssetCoreDB(segments_by_snapshot={"snap-1": 2},
                         units_by_snapshot={"snap-1": 0},
                         embeddings_by_snapshot={"snap-1": 0})

    build_id = assemble_build(
        db, domain="odn", channel="prod", run_id="run-1", batch_id=None,
        snapshot_decisions=_decisions(), kb_id=None,
    )

    summary = db.inserted_builds[0]["summary_json"]
    assert "paradigm_capabilities" not in summary
    assert ("validated",) not in ()
    assert ("validated" in {s for _, s in db.status_updates}) is True
    validate_build(db, build_id)  # 不抛异常即通过


def test_validate_blocks_zero_embeddings_without_fallback_trace() -> None:
    db = FakeAssetCoreDB(segments_by_snapshot={"snap-1": 2},
                         units_by_snapshot={"snap-1": 3},
                         embeddings_by_snapshot={"snap-1": 0})

    with pytest.raises(ValueError, match="embedding"):
        assemble_build(
            db, domain="odn", channel="prod", run_id="run-1", batch_id=None,
            snapshot_decisions=_decisions(), kb_id=None,
            capabilities=FULL_BASELINE, embedding_fallback=False,
        )


def test_validate_allows_zero_embeddings_with_fallback_trace() -> None:
    """嵌入服务不可用已留痕（embedding_fallback=True）→ 放行但把痕迹冻进 build。"""
    db = FakeAssetCoreDB(segments_by_snapshot={"snap-1": 2},
                         units_by_snapshot={"snap-1": 3},
                         embeddings_by_snapshot={"snap-1": 0})

    assemble_build(
        db, domain="odn", channel="prod", run_id="run-1", batch_id=None,
        snapshot_decisions=_decisions(), kb_id=None,
        capabilities=FULL_BASELINE, embedding_fallback=True,
    )

    assert "validated" in {s for _, s in db.status_updates}
    assert db.inserted_builds[0]["summary_json"]["embedding_fallback"] is True


def test_validate_passes_when_units_and_embeddings_present() -> None:
    db = FakeAssetCoreDB(segments_by_snapshot={"snap-1": 2},
                         units_by_snapshot={"snap-1": 3},
                         embeddings_by_snapshot={"snap-1": 3})

    assemble_build(
        db, domain="odn", channel="prod", run_id="run-1", batch_id=None,
        snapshot_decisions=_decisions(), kb_id=None,
        capabilities=FULL_BASELINE, embedding_fallback=False,
    )

    assert "validated" in {s for _, s in db.status_updates}


def test_carry_forward_snapshots_are_not_revalidated_by_new_capabilities() -> None:
    """父 build carry-forward 的旧快照（无向量）不得按新范式能力拦死整个 build。

    E2E 实锤：KB 首建增量 merge 域级父 build，父快照是嵌入服务欠费时代的
    产物（embeddings=0），按新能力校验会把全新 run 的建库误判失败。"""
    db = FakeAssetCoreDB(
        segments_by_snapshot={"snap-new": 2, "snap-old": 1},
        units_by_snapshot={"snap-new": 3, "snap-old": 1},
        embeddings_by_snapshot={"snap-new": 3, "snap-old": 0},
    )
    decisions = [
        *_decisions("snap-new"),            # 本 run NEW：必须过能力校验
        {                                    # 父 carry-forward SKIP：不重检
            "document_id": "doc-old",
            "document_snapshot_id": "snap-old",
            "action": "SKIP",
            "reason": "retain",
            "selection_status": "active",
        },
    ]

    assemble_build(
        db, domain="odn", channel="prod", run_id="run-1", batch_id=None,
        snapshot_decisions=decisions, kb_id=None,
        capabilities=FULL_BASELINE, embedding_fallback=False,
    )

    assert "validated" in {s for _, s in db.status_updates}
