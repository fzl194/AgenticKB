"""36号：KB 增量判定状态机 + KB-scoped 父 Build（§五/§七）.

锁定契约：
- classify_kb_documents 纯函数：NEW/SKIP/RETRY(→UPDATE+原因)/UPDATE 集合判定，
  不做任何 N+1（输入全部为预取的批量事实）；
- classify_documents/assemble_build 在 kb_id 非空时以「该 KB 最新 validated
  Build」为 parent；不同 KB（同域）互不 carry-forward；
- get_latest_validated_kb_build 只查本 KB 的 validated/published Build，
  created_at DESC, id DESC 稳定排序。
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any

# ───────────────────────── A. 增量判定状态机（纯函数） ─────────────────────────


def _doc(document_id: str, revision: int = 1):
    from knowledge_mining.mining.jobs.kb_incremental import KbDocInput

    return KbDocInput(
        document_id=document_id,
        storage_object_id=f"obj-{document_id}",
        content_revision=revision,
    )


def _fact(document_id: str, snapshot_id: str, *, obj_revision: int = 1,
          workflow_version_id: str = "wfv-1", graph_hash: str = "gh-1",
          build_finished_at=None):
    from knowledge_mining.mining.jobs.kb_incremental import KbSnapshotFact

    return KbSnapshotFact(
        document_id=document_id,
        snapshot_id=snapshot_id,
        workflow_version_id=workflow_version_id,
        workflow_graph_hash=graph_hash,
        source_storage_object_id=f"obj-{document_id}",
        source_content_revision=obj_revision,
        build_finished_at=build_finished_at,
    )


def _attempt(status: str, started_at: str = "2026-09-04T10:00:00+00:00"):
    from knowledge_mining.mining.jobs.kb_incremental import KbLastAttempt

    return KbLastAttempt(status=status, started_at=started_at)


def _classify(docs, *, facts=None, readiness=None, attempts=None,
              workflow_version_id="wfv-1", graph_hash="gh-1",
              build_finished_at="2026-09-03T00:00:00+00:00",
              require_dense=True):
    from knowledge_mining.mining.jobs.kb_incremental import classify_kb_documents

    return classify_kb_documents(
        docs=docs,
        build_facts=facts or {},
        readiness_by_snapshot=readiness or {},
        last_attempts=attempts or {},
        workflow_version_id=workflow_version_id,
        workflow_graph_hash=graph_hash,
        build_finished_at=build_finished_at,
        require_dense=require_dense,
    )


def test_new_document_never_mined():
    d = _classify([_doc("d1")])
    assert d["d1"].action == "NEW"
    assert d["d1"].decision == "new_document"
    assert d["d1"].serving_snapshot_id is None


def test_skip_when_in_build_unchanged_and_ready():
    d = _classify(
        [_doc("d1")],
        facts={"d1": _fact("d1", "snap-1")},
        readiness={"snap-1": {
            "search_ready": True,
            "counts": {"dense_units": 2, "dense_covered": 2},
        }},
    )
    assert d["d1"].action == "SKIP"
    assert d["d1"].decision == "skip_unchanged"
    assert d["d1"].serving_snapshot_id == "snap-1"


def test_retry_when_last_attempt_failed_and_never_built():
    d = _classify(
        [_doc("d1")],
        attempts={"d1": _attempt("failed")},
    )
    assert d["d1"].action == "UPDATE"
    assert d["d1"].decision == "retry_after_failure"


def test_retry_when_readiness_incomplete():
    d = _classify(
        [_doc("d1")],
        facts={"d1": _fact("d1", "snap-1")},
        readiness={"snap-1": {
            "search_ready": True,
            "counts": {"dense_units": 5, "dense_covered": 3},
        }},
    )
    assert d["d1"].action == "UPDATE"
    assert d["d1"].decision == "retry_readiness_incomplete"


def test_retry_when_readiness_row_missing():
    d = _classify(
        [_doc("d1")],
        facts={"d1": _fact("d1", "snap-1")},
        readiness={},
    )
    assert d["d1"].action == "UPDATE"
    assert d["d1"].decision == "retry_readiness_incomplete"


def test_update_when_content_revision_changed():
    d = _classify(
        [_doc("d1", revision=2)],
        facts={"d1": _fact("d1", "snap-1", obj_revision=1)},
        readiness={"snap-1": {"search_ready": True, "counts": {
            "dense_units": 1, "dense_covered": 1}}},
    )
    assert d["d1"].action == "UPDATE"
    assert d["d1"].decision == "update_content_changed"


def test_update_when_workflow_signature_changed():
    d = _classify(
        [_doc("d1")],
        facts={"d1": _fact("d1", "snap-1")},
        readiness={"snap-1": {"search_ready": True, "counts": {
            "dense_units": 1, "dense_covered": 1}}},
        graph_hash="gh-NEW",
    )
    assert d["d1"].action == "UPDATE"
    assert d["d1"].decision == "update_workflow_changed"


def test_retry_when_failure_newer_than_build():
    d = _classify(
        [_doc("d1")],
        facts={"d1": _fact("d1", "snap-1")},
        readiness={"snap-1": {"search_ready": True, "counts": {
            "dense_units": 1, "dense_covered": 1}}},
        attempts={"d1": _attempt("failed", started_at="2026-09-04T12:00:00+00:00")},
    )
    assert d["d1"].action == "UPDATE"
    assert d["d1"].decision == "retry_after_failure"


def test_sparse_history_compares_failure_to_document_build_time():
    d = _classify(
        [_doc("d1")],
        facts={"d1": _fact(
            "d1", "snap-1",
            build_finished_at="2026-09-01T00:00:00+00:00",
        )},
        readiness={"snap-1": {"search_ready": True, "counts": {
            "dense_units": 1, "dense_covered": 1}}},
        attempts={"d1": _attempt(
            "failed", started_at="2026-09-02T00:00:00+00:00",
        )},
        # Another document produced a newer global Build; it must not hide d1's failure.
        build_finished_at="2026-09-04T00:00:00+00:00",
    )
    assert d["d1"].decision == "retry_after_failure"


def test_skip_when_failure_older_than_build():
    d = _classify(
        [_doc("d1")],
        facts={"d1": _fact("d1", "snap-1")},
        readiness={"snap-1": {"search_ready": True, "counts": {
            "dense_units": 1, "dense_covered": 1}}},
        attempts={"d1": _attempt("failed", started_at="2026-09-01T00:00:00+00:00")},
    )
    assert d["d1"].action == "SKIP"


def test_retry_when_attempt_interrupted_processing():
    d = _classify(
        [_doc("d1")],
        facts={"d1": _fact("d1", "snap-1")},
        readiness={"snap-1": {"search_ready": True, "counts": {
            "dense_units": 1, "dense_covered": 1}}},
        attempts={"d1": _attempt("processing", started_at="2026-09-04T12:00:00+00:00")},
    )
    assert d["d1"].action == "UPDATE"
    assert d["d1"].decision == "retry_after_failure"


def test_retry_rejected_marker_survives_crash_before_executor():
    from knowledge_mining.mining.jobs.run import _is_retry_rejected_row

    assert _is_retry_rejected_row({"status": "failed", "metadata_json": {}})
    assert _is_retry_rejected_row({
        "status": "processing",
        "metadata_json": {"retry_required": True},
    })
    assert not _is_retry_rejected_row({
        "status": "committed",
        "metadata_json": {"retry_required": True},
    })


def test_lexical_paradigm_skips_without_dense_requirement():
    d = _classify(
        [_doc("d1")],
        facts={"d1": _fact("d1", "snap-1")},
        readiness={"snap-1": {"search_ready": True, "counts": {
            "dense_units": 0, "dense_covered": 0}}},
        require_dense=False,
    )
    assert d["d1"].action == "SKIP"


def test_revision_zero_is_a_valid_unchanged_revision():
    """MED-1（36号审查）：revision 0 合法（列默认值）——不得因 falsy 兜底
    被永久判成内容变化（那会让 revision-0 文档永远无法 SKIP）。"""
    d = _classify(
        [_doc("d1", revision=0)],
        facts={"d1": _fact("d1", "snap-1", obj_revision=0)},
        readiness={"snap-1": {"search_ready": True, "counts": {
            "dense_units": 1, "dense_covered": 1}}},
    )
    assert d["d1"].action == "SKIP"
    assert d["d1"].decision == "skip_unchanged"


def test_signature_comes_from_committed_run_not_parse_chain():
    """E2E 追溯（36号）：快照行的 workflow_* 列是解析链标识
    （new-parse-chain@N），与挖掘工作流的 version_id（UUID）两套体系——
    分类器消费的事实必须来自「提交该文档的 Run」。此处锁定纯函数语义：
    fact 签名与 run 签名一致即 SKIP（不管值长什么样）。"""
    d = _classify(
        [_doc("d1")],
        facts={"d1": _fact("d1", "snap-1", workflow_version_id="fe670563",
                           graph_hash="49e2ef95")},
        readiness={"snap-1": {"search_ready": True, "counts": {
            "dense_units": 1, "dense_covered": 1}}},
        workflow_version_id="fe670563", graph_hash="49e2ef95",
    )
    assert d["d1"].action == "SKIP"


def test_committed_signature_is_bound_to_build_snapshot_pair():
    """v1.0.1 失败 Run 的伪 committed 新快照不得污染 serving 旧快照签名。"""
    from knowledge_mining.mining.jobs.kb_incremental import (
        fetch_kb_committed_signatures,
    )

    class _Runtime:
        def __init__(self):
            self.sql = ""
            self.params = ()

        def _fetchall(self, sql, params):
            self.sql = sql
            self.params = params
            return [{
                "document_id": "doc-1",
                "document_snapshot_id": "snap-serving",
                "workflow_version_id": "wfv-serving",
                "workflow_graph_hash": "gh-serving",
            }]

    runtime = _Runtime()
    rows = fetch_kb_committed_signatures(
        runtime,
        kb_id="kb-a",
        document_snapshots={"doc-1": "snap-serving"},
    )

    assert runtime.params == ("kb-a", ["doc-1"], ["snap-serving"])
    assert "rd.document_snapshot_id = ANY(%s)" in runtime.sql
    assert "JOIN asset_builds" in runtime.sql
    assert "JOIN asset_build_document_snapshots" in runtime.sql
    assert "bs.document_snapshot_id = rd.document_snapshot_id" in runtime.sql
    assert "b.status IN ('validated', 'published')" in runtime.sql
    assert rows[("doc-1", "snap-serving")]["workflow_version_id"] == "wfv-serving"


def test_increment_context_keeps_shared_snapshot_facts_per_document(monkeypatch):
    """同一内容快照被两篇文档复用时，source pointer/签名仍按文档隔离。"""
    from knowledge_mining.mining.jobs import kb_incremental as incremental

    class _Asset:
        def get_latest_validated_kb_build(self, kb_id):
            return {"id": "build-1", "created_at": "2026-09-01T00:00:00+00:00"}

        def get_build_snapshots(self, build_id):
            return [
                {"document_id": "doc-a", "document_snapshot_id": "snap-shared",
                 "selection_status": "active"},
                {"document_id": "doc-b", "document_snapshot_id": "snap-shared",
                 "selection_status": "active"},
            ]

        def fetch_kb_snapshot_facts(self, document_snapshots):
            assert document_snapshots == {
                "doc-a": "snap-shared", "doc-b": "snap-shared",
            }
            return {
                ("doc-a", "snap-shared"): {
                    "document_id": "doc-a", "snapshot_id": "snap-shared",
                    "source_storage_object_id": "obj-a", "source_content_revision": 1,
                },
                ("doc-b", "snap-shared"): {
                    "document_id": "doc-b", "snapshot_id": "snap-shared",
                    "source_storage_object_id": "obj-b", "source_content_revision": 7,
                },
            }

        def fetch_snapshot_readiness(self, snapshot_ids):
            return {"snap-shared": {"search_ready": True,
                                     "counts": {"dense_units": 1, "dense_covered": 1}}}

    monkeypatch.setattr(
        incremental,
        "fetch_kb_committed_signatures",
        lambda runtime_db, *, kb_id, document_snapshots: {
            ("doc-a", "snap-shared"): {
                "workflow_version_id": "wfv-a", "workflow_graph_hash": "gh-a"},
            ("doc-b", "snap-shared"): {
                "workflow_version_id": "wfv-b", "workflow_graph_hash": "gh-b"},
        },
    )
    monkeypatch.setattr(
        incremental, "fetch_kb_last_attempts", lambda *args, **kwargs: {},
    )

    facts, _, _, _ = incremental.fetch_kb_increment_context(
        _Asset(), object(), kb_id="kb-a",
        document_ids=["doc-a", "doc-b"], require_dense=True,
    )

    assert set(facts) == {"doc-a", "doc-b"}
    assert facts["doc-a"].source_storage_object_id == "obj-a"
    assert facts["doc-b"].source_storage_object_id == "obj-b"
    assert facts["doc-a"].workflow_version_id == "wfv-a"
    assert facts["doc-b"].workflow_version_id == "wfv-b"


def test_increment_context_uses_per_document_current_serving_history(monkeypatch):
    """最新 Build 稀疏时，也要看 serving 仍从旧 Build 提供的文档。"""
    from knowledge_mining.mining.jobs import kb_incremental as incremental

    class _Asset:
        def get_latest_validated_kb_build(self, kb_id):
            return {"id": "build-new", "finished_at": "2026-09-04T00:00:00+00:00"}

        def get_build_snapshots(self, build_id):
            return [{
                "document_id": "doc-new", "document_snapshot_id": "snap-new",
                "selection_status": "active",
            }]

        def get_current_kb_build_snapshots(self, kb_id):
            return [
                {"document_id": "doc-old", "document_snapshot_id": "snap-old",
                 "selection_status": "active", "build_finished_at": "2026-09-01T00:00:00+00:00"},
                {"document_id": "doc-new", "document_snapshot_id": "snap-new",
                 "selection_status": "active", "build_finished_at": "2026-09-04T00:00:00+00:00"},
            ]

        def fetch_kb_snapshot_facts(self, document_snapshots):
            return {
                (doc, snap): {
                    "document_id": doc, "snapshot_id": snap,
                    "source_storage_object_id": f"obj-{doc}",
                    "source_content_revision": 1,
                }
                for doc, snap in document_snapshots.items()
            }

        def fetch_snapshot_readiness(self, snapshot_ids):
            return {sid: {"search_ready": True, "counts": {
                "dense_units": 1, "dense_covered": 1,
            }} for sid in snapshot_ids}

    monkeypatch.setattr(
        incremental, "fetch_kb_committed_signatures",
        lambda runtime_db, *, kb_id, document_snapshots: {
            (doc, snap): {"workflow_version_id": "wfv", "workflow_graph_hash": "gh"}
            for doc, snap in document_snapshots.items()
        },
    )
    monkeypatch.setattr(
        incremental, "fetch_kb_last_attempts", lambda *args, **kwargs: {},
    )

    facts, _, _, _ = incremental.fetch_kb_increment_context(
        _Asset(), object(), kb_id="kb-a",
        document_ids=["doc-old", "doc-new"], require_dense=True,
    )

    assert set(facts) == {"doc-old", "doc-new"}


# ───────────────────────── B. KB-scoped 父 Build（publishing 层） ─────────────────────────


class _PublishingFakeDB:
    """publishing 层最小双打：域级 active build 与 KB build 分开配置."""

    def __init__(self, *, kb_builds: dict[str, dict | None],
                 snapshots_by_build: dict[str, list[dict]],
                 domain_build: dict | None = None) -> None:
        self.kb_builds = kb_builds
        self.snapshots_by_build = snapshots_by_build
        self.domain_build = domain_build
        self.builds: dict[str, dict] = {}
        self.inserted_builds: list[dict[str, Any]] = []
        self.status_updates: list[tuple[str, str]] = []
        self._next_snap: dict[str, list[dict]] = {}

    @contextmanager
    def transaction(self):
        yield

    def get_source_batch(self, *, domain, batch_id):
        return None

    def get_active_build(self, *, domain, channel):
        return self.domain_build

    def get_latest_validated_kb_build(self, kb_id):
        return self.kb_builds.get(kb_id)

    def get_build(self, build_id):
        build = self.builds.get(build_id)
        if build is not None:
            return build
        for candidate in list(self.kb_builds.values()) + [self.domain_build]:
            if candidate and candidate.get("id") == build_id:
                return candidate
        return None

    def get_build_snapshots(self, build_id):
        if build_id in self._next_snap:
            return self._next_snap[build_id]
        return self.snapshots_by_build.get(build_id, [])

    def insert_build(self, **kwargs):
        self.inserted_builds.append(kwargs)
        self.builds[kwargs["build_id"]] = {
            "id": kwargs["build_id"],
            "build_mode": kwargs.get("build_mode", "full"),
            "parent_build_id": kwargs.get("parent_build_id"),
            "status": kwargs.get("status", "building"),
            "summary_json": kwargs.get("summary_json") or {},
            "kb_id": kwargs.get("kb_id"),
        }
        return kwargs["build_id"]

    def upsert_build_document_snapshot(self, **kwargs):
        self._next_snap.setdefault(kwargs["build_id"], []).append(kwargs)

    def update_build_status(self, build_id, status):
        self.status_updates.append((build_id, status))
        if build_id in self.builds:
            self.builds[build_id]["status"] = status

    def count_segments_by_snapshot(self, snapshot_id):
        return 2

    def count_retrieval_units_by_snapshot(self, snapshot_id):
        return 1

    def count_embeddings_by_snapshot(self, snapshot_id):
        return 1


_KB_BUILD = {
    "id": "build-kb-a", "kb_id": "kb-a", "status": "validated",
    "build_mode": "full",
}
_DOMAIN_BUILD = {
    "id": "build-domain", "kb_id": None, "status": "validated",
    "build_mode": "full",
}


def test_classify_documents_uses_kb_scoped_parent():
    from knowledge_mining.mining.stages.publishing import classify_documents

    db = _PublishingFakeDB(
        kb_builds={"kb-a": _KB_BUILD},
        snapshots_by_build={
            # KB build 已含 doc-1（旧快照）
            "build-kb-a": [{
                "document_id": "doc-1",
                "document_snapshot_id": "snap-old",
                "selection_status": "active",
            }],
        },
        # 域级 active release 里有别的 KB 的 doc-other——不得参与比较
        domain_build=_DOMAIN_BUILD,
    )
    snapshots_by_build = {
        "build-domain": [{
            "document_id": "doc-other",
            "document_snapshot_id": "snap-other",
            "selection_status": "active",
        }],
    }
    db.snapshots_by_build.update(snapshots_by_build)

    decisions = classify_documents(
        db,
        [
            {"document_id": "doc-1", "document_snapshot_id": "snap-new"},
            {"document_id": "doc-other", "document_snapshot_id": "snap-x"},
        ],
        domain="odn", channel="prod", detect_remove=False, kb_id="kb-a",
    )
    by_doc = {d["document_id"]: d for d in decisions}
    # doc-1 对比 KB 父 Build：快照变化 → UPDATE
    assert by_doc["doc-1"]["action"] == "UPDATE"
    # doc-other 不在 KB 父 Build → NEW（不因域级 release 里存在而 SKIP）
    assert by_doc["doc-other"]["action"] == "NEW"


def test_assemble_build_uses_kb_parent_and_never_carries_cross_kb():
    from knowledge_mining.mining.stages.publishing import assemble_build

    db = _PublishingFakeDB(
        kb_builds={"kb-a": _KB_BUILD},
        snapshots_by_build={
            # KB 父 Build：doc-keep（本 run 未触碰 → carry-forward）
            "build-kb-a": [
                {"document_id": "doc-keep", "document_snapshot_id": "snap-keep",
                 "selection_status": "active", "source_batch_id": "b1",
                 "reason": "retain", "metadata_json": None},
            ],
            # 域级 active release：doc-other-kb 属于同域其它 KB——绝不 carry
            "build-domain": [
                {"document_id": "doc-other-kb", "document_snapshot_id": "snap-o",
                 "selection_status": "active", "source_batch_id": "b2",
                 "reason": "retain", "metadata_json": None},
            ],
        },
        domain_build=_DOMAIN_BUILD,
    )
    decisions = [{
        "document_id": "doc-1", "document_snapshot_id": "snap-new",
        "action": "UPDATE", "reason": "update", "selection_status": "active",
    }]
    build_id = assemble_build(
        db, domain="odn", channel="prod", run_id="run-1", batch_id=None,
        snapshot_decisions=decisions, kb_id="kb-a",
    )
    inserted = db.inserted_builds[0]
    # parent 是 KB Build，不是域级 release
    assert inserted["parent_build_id"] == "build-kb-a"
    assert inserted["build_mode"] == "incremental"
    assert inserted["kb_id"] == "kb-a"
    carried = {
        row["document_id"]: row
        for row in db.get_build_snapshots(build_id)
    }
    # 本 run 文档 + 本 KB carry-forward 在
    assert "doc-1" in carried and "doc-keep" in carried
    # 其它 KB 的文档绝不进入本 KB Build
    assert "doc-other-kb" not in carried


def test_assemble_build_without_kb_id_keeps_domain_parent():
    from knowledge_mining.mining.stages.publishing import assemble_build

    db = _PublishingFakeDB(
        kb_builds={"kb-a": _KB_BUILD},
        snapshots_by_build={"build-domain": [
            {"document_id": "doc-d", "document_snapshot_id": "snap-d",
             "selection_status": "active", "source_batch_id": "b2",
             "reason": "retain", "metadata_json": None},
        ]},
        domain_build=_DOMAIN_BUILD,
    )
    assemble_build(
        db, domain="odn", channel="prod", run_id="run-1", batch_id=None,
        snapshot_decisions=[{
            "document_id": "doc-1", "document_snapshot_id": "snap-new",
            "action": "NEW", "reason": "add", "selection_status": "active",
        }],
        kb_id=None,
    )
    assert db.inserted_builds[0]["parent_build_id"] == "build-domain"


def test_assemble_build_carries_per_document_history_from_sparse_builds():
    from knowledge_mining.mining.stages.publishing import assemble_build

    class _HistoricalDb(_PublishingFakeDB):
        def get_current_kb_build_snapshots(self, kb_id):
            return [
                {"document_id": "doc-old", "document_snapshot_id": "snap-old",
                 "selection_status": "active", "source_batch_id": "b-old",
                 "reason": "retain", "metadata_json": {}},
                {"document_id": "doc-new", "document_snapshot_id": "snap-parent",
                 "selection_status": "active", "source_batch_id": "b-new",
                 "reason": "retain", "metadata_json": {}},
            ]

    db = _HistoricalDb(
        kb_builds={"kb-a": _KB_BUILD},
        snapshots_by_build={"build-kb-a": [{
            "document_id": "doc-new", "document_snapshot_id": "snap-parent",
            "selection_status": "active", "source_batch_id": "b-new",
            "reason": "retain", "metadata_json": {},
        }]},
    )
    build_id = assemble_build(
        db, domain="odn", channel="prod", run_id="run-1", batch_id=None,
        snapshot_decisions=[{
            "document_id": "doc-new", "document_snapshot_id": "snap-next",
            "action": "UPDATE", "selection_status": "active", "reason": "update",
        }],
        kb_id="kb-a",
    )

    members = {row["document_id"] for row in db.get_build_snapshots(build_id)}
    assert members == {"doc-old", "doc-new"}


# ───────────────────────── C. KB Build 查询（SQL 形状） ─────────────────────────


class _RecordingPool:
    def __init__(self) -> None:
        self.log: list[tuple[str, tuple]] = []

    @contextmanager
    def connection(self):
        outer = self

        class _Cur:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, sql, params):
                outer.log.append((sql, tuple(params) if params else ()))

            def fetchone(self):
                return None

            def fetchall(self):
                return []

            rowcount = 0

        class _Conn:
            def cursor(self, row_factory=None):
                return _Cur()

        yield _Conn()


def test_get_latest_validated_kb_build_queries_kb_scoped_latest():
    from knowledge_mining.mining.infra.db import AssetCoreDB

    pool = _RecordingPool()
    db = AssetCoreDB(pool)
    db.get_latest_validated_kb_build("kb-a")

    sql, params = pool.log[-1]
    assert params == ("kb-a",)
    assert "FROM asset_builds" in sql
    assert "kb_id = %s" in sql
    assert "status IN ('validated', 'published')" in sql
    assert "ORDER BY created_at DESC, id DESC" in sql
    assert "LIMIT 1" in sql
