"""M4 DocumentParseService（WP7+WP9 编排）验收（RED 先行）.

SRS 验收场景映射：
- happy：primary PASS → SUCCEEDED + 快照转正 + attempt 可审计；
- A05：primary 失败 → 自动 fallback → 成功转正；两次 attempt 均留档；
- A06：全部后端失败 → FAILED、无快照、旧内容不受影响；
- 质量触发 fallback：字符覆盖硬失败（预算内）→ 换后端重试成功；
- SUPERSEDED：提交前发现文档已更新 → Run 标记 SUPERSEDED、无快照；
- FAIL 不入库：空解析结果 → FAILED、无快照（M4 退出条件）；
- A07：parser 升级（不同指纹）→ 新快照，旧快照原样保留；
- A09：backend raw artifact 重放 → 新快照，parser 不被重复调用；
- 幂等：同输入同计划复跑 → 复用 SUCCEEDED Run，不重复解析。
"""
from __future__ import annotations

import json
import sys

import pytest

pytestmark = pytest.mark.asyncio

if sys.platform == "win32":
    import asyncio

    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from knowledge_mining.mining.contracts.parse_ir.enums import (  # noqa: E402
    PARSE_IR_SCHEMA_VERSION,
)
from knowledge_mining.mining.contracts.parse_ir.types import (  # noqa: E402
    Container,
    Element,
    ParseIdentity,
    ParsedDocument,
    Relation,
)
from knowledge_mining.mining.contracts.parse_plan import (  # noqa: E402
    AttemptBudget,
    ParsePlan,
)
from knowledge_mining.mining.contracts.parser_adapter import (  # noqa: E402
    BackendBlock,
    BackendParseArtifact,
    ParserDescriptor,
)
from knowledge_mining.mining.contracts.storage.types import (  # noqa: E402
    ObjectLocation,
    PutOptions,
)
from knowledge_mining.mining.file_management.repositories_memory import (  # noqa: E402
    MemoryStorageObjectRepository,
)
from knowledge_mining.mining.frozen_input.contracts import (  # noqa: E402
    FrozenInput,
    FrozenInputStale,
)
from knowledge_mining.mining.infra.object_store.fake import FakeObjectStore  # noqa: E402
from knowledge_mining.mining.parse_quality.gate import QualityGate  # noqa: E402
from knowledge_mining.mining.parse_reconciler import StructuralReconciler  # noqa: E402
from knowledge_mining.mining.shadow_parse.repositories_memory import (  # noqa: E402
    MemoryParseAttemptRepository,
    MemoryParseRunRepository,
)
from knowledge_mining.mining.snapshot_store.repositories_memory import (  # noqa: E402
    MemorySnapshotRepository,
)

import hashlib


# ---------------------------------------------------------------------------
# 测试替身：可配置文本/失败的 stub parser + 全量块 normalizer
# ---------------------------------------------------------------------------


def _descriptor(parser_id: str) -> ParserDescriptor:
    return ParserDescriptor(
        parser_id=parser_id,
        display_name=parser_id,
        version="1.0.0",
        supported_mimes=frozenset({"text/plain"}),
        parser_fingerprint=f"{parser_id}@1.0.0",
    )


class StubParser:
    """按配置输出固定文本；``fail=True`` 时抛错（模拟后端崩溃）."""

    def __init__(self, parser_id: str, *, text: str, fail: bool = False) -> None:
        self.descriptor = _descriptor(parser_id)
        self._text = text
        self._fail = fail
        self.parse_calls = 0

    def supports(self, mime: str) -> bool:
        return mime.lower() in self.descriptor.supported_mimes

    def parse(self, data: bytes, *, mime: str) -> BackendParseArtifact:
        self.parse_calls += 1
        if self._fail:
            raise RuntimeError(f"{self.descriptor.parser_id} boom")
        lines = [ln for ln in self._text.splitlines() if ln]
        blocks = tuple(
            BackendBlock(block_type="paragraph", text=ln, line_start=i, line_end=i + 1)
            for i, ln in enumerate(lines)
        )
        return BackendParseArtifact(
            parser_id=self.descriptor.parser_id,
            parser_version=self.descriptor.version,
            mime=mime,
            blocks=blocks,
            raw_output=self._text,
        )


class FullNormalizer:
    """artifact 全部块 -> 元素（保真，供覆盖率场景使用）."""

    def __init__(self, version: str = "full-norm@1") -> None:
        self.version = version

    def normalize(self, artifact, *, source_raw_hash, parse_run_id=None):  # noqa: ANN001
        from knowledge_mining.mining.contracts.parse_ir.types import (
            EvidenceSpan,
        )

        elements = tuple(
            Element(
                element_id=f"e{i}", element_type="paragraph",
                order_index=i, text=b.text,
                source_spans=(EvidenceSpan(
                    span_id=f"s{i}",
                    text_range=(0, len(b.text)),
                    raw_text=b.text,
                ),),
            )
            for i, b in enumerate(artifact.blocks)
        )
        relations = tuple(
            Relation(source_element_id=f"e{i}", target_element_id=f"e{i+1}",
                     relation_type="next_in_reading_order", method="stub")
            for i in range(max(0, len(elements) - 1))
        )
        return ParsedDocument(
            schema_version=PARSE_IR_SCHEMA_VERSION,
            source_identity=ParseIdentity(
                source_raw_hash=source_raw_hash,
                parser_fingerprint=(
                    f"{artifact.parser_id}@{artifact.parser_version}"
                ),
                parse_ir_schema_version=PARSE_IR_SCHEMA_VERSION,
                normalizer_version=self.version,
            ),
            containers=(Container(container_id="c0", container_type="section", order_index=0),),
            elements=elements,
            relations=relations,
        )


# ---------------------------------------------------------------------------
# 组装
# ---------------------------------------------------------------------------


class Harness:
    def __init__(self, tmp_path):  # noqa: ANN001
        self.store = FakeObjectStore(str(tmp_path / "objects"))
        self.parse_runs = MemoryParseRunRepository()
        self.attempts = MemoryParseAttemptRepository()
        self.storage_objects = MemoryStorageObjectRepository()
        self.snapshots = MemorySnapshotRepository()
        self.parsers: dict[str, StubParser] = {}
        self._stale = None  # 可选 stale stub

    def register(self, parser: StubParser) -> StubParser:
        self.parsers[parser.descriptor.parser_id] = parser
        return parser

    def make_service(self):  # noqa: ANN001
        from knowledge_mining.mining.parse_operator.service import (
            DocumentParseService,
        )
        from knowledge_mining.mining.snapshot_store.service import (
            SnapshotCommitService,
        )

        def resolver(parser_id: str):  # noqa: ANN001
            parser = self.parsers[parser_id]
            return parser, FullNormalizer()

        async def stale_checker(frozen: FrozenInput) -> None:
            if self._stale is not None:
                raise self._stale

        commit = SnapshotCommitService(
            snapshots=self.snapshots,
            stale_checker=stale_checker,
            storage_objects=self.storage_objects,
            object_store=self.store,
        )
        return DocumentParseService(
            object_store=self.store,
            parse_runs=self.parse_runs,
            attempts=self.attempts,
            storage_objects=self.storage_objects,
            parser_resolver=resolver,
            commit_service=commit,
            quality_gate=QualityGate(),
            reconciler=StructuralReconciler(),
            bucket_prefix="testop-",
        )

    async def seed_source(self, frozen: FrozenInput, data: bytes) -> None:
        await self.store.put_bytes(
            ObjectLocation(bucket=frozen.bucket, object_key=frozen.object_key),
            data,
            PutOptions(artifact_class="source"),
        )


def _frozen(data: bytes = "line one\nline two\n".encode()) -> FrozenInput:
    sha = hashlib.sha256(data).hexdigest()
    return FrozenInput(
        document_id="doc1",
        source_storage_object_id="so_src",
        source_raw_hash=sha,
        source_content_revision=1,
        mime="text/plain",
        size=len(data),
        original_filename="doc.txt",
        captured_at="2026-08-18T00:00:00+00:00",
        provider="fake",
        bucket="testop-source",
        object_key=f"v1/ab/{sha[:8]}",
        object_version_id=None,
    )


def _plan(primary: str, *fallbacks: str, budget: AttemptBudget | None = None) -> ParsePlan:
    return ParsePlan(
        plan_id="plan_x",
        primary_parser_id=primary,
        fallback_parser_ids=fallbacks,
        budget=budget or AttemptBudget(max_backend_attempts=3),
    )


@pytest.fixture
def harness(tmp_path):  # noqa: ANN001
    h = Harness(tmp_path)
    h.register(StubParser("good", text="line one\nline two\n"))
    return h


# ---------------------------------------------------------------------------
# happy + 幂等
# ---------------------------------------------------------------------------


async def test_happy_path_commits_snapshot_and_audits_attempt(harness) -> None:
    service = harness.make_service()
    frozen = _frozen()
    await harness.seed_source(frozen, "line one\nline two\n".encode())

    run = await service.execute(frozen, _plan("good"), domain="default",
                                source_text="line one\nline two\n")
    assert run.status == "SUCCEEDED"
    assert run.snapshot_id is not None
    events = await harness.attempts.list_by_run(run.id)
    assert len(events) == 1
    assert events[0].attempt_kind == "primary"
    assert events[0].outcome == "SUCCEEDED"
    snap = await harness.snapshots.get(run.snapshot_id)
    assert snap is not None and snap.quality_status == "PASS"


async def test_idempotent_rerun_reuses_run(harness) -> None:
    service = harness.make_service()
    frozen = _frozen()
    await harness.seed_source(frozen, "line one\nline two\n".encode())
    first = await service.execute(frozen, _plan("good"), domain="default")
    second = await service.execute(frozen, _plan("good"), domain="default")
    assert second.id == first.id
    assert harness.parsers["good"].parse_calls == 1
    assert harness.snapshots.count() == 1


# ---------------------------------------------------------------------------
# A05 / A06 / 质量触发 fallback
# ---------------------------------------------------------------------------


async def test_a05_primary_failure_falls_back_and_audits(harness) -> None:
    harness.register(StubParser("bad", text="x", fail=True))
    service = harness.make_service()
    frozen = _frozen()
    await harness.seed_source(frozen, "line one\nline two\n".encode())

    run = await service.execute(frozen, _plan("bad", "good"), domain="default")
    assert run.status == "SUCCEEDED"
    events = await harness.attempts.list_by_run(run.id)
    assert [(e.attempt_kind, e.outcome) for e in events] == [
        ("primary", "FAILED"), ("fallback", "SUCCEEDED"),
    ]
    assert harness.parsers["good"].parse_calls == 1
    assert run.snapshot_id is not None


async def test_a06_all_backends_fail_no_snapshot(harness) -> None:
    harness.register(StubParser("bad1", text="x", fail=True))
    harness.register(StubParser("bad2", text="x", fail=True))
    service = harness.make_service()
    frozen = _frozen()
    await harness.seed_source(frozen, "line one\nline two\n".encode())

    run = await service.execute(frozen, _plan("bad1", "bad2"), domain="default")
    assert run.status == "FAILED"
    assert run.snapshot_id is None
    assert harness.snapshots.count() == 0
    assert "boom" in (run.error_message or "")
    events = await harness.attempts.list_by_run(run.id)
    assert len(events) == 2


async def test_quality_fallback_on_low_coverage(harness) -> None:
    # partial 只吐一半文本 → 覆盖率 ~0.5（< 0.85）→ 预算内 FALLBACK。
    harness.register(StubParser("partial", text="line one\n"))
    service = harness.make_service()
    frozen = _frozen()
    await harness.seed_source(frozen, "line one\nline two\n".encode())

    run = await service.execute(
        frozen, _plan("partial", "good"), domain="default",
        source_text="line one\nline two\n",
    )
    assert run.status == "SUCCEEDED"
    events = await harness.attempts.list_by_run(run.id)
    kinds = [(e.attempt_kind, e.outcome) for e in events]
    assert kinds == [("primary", "FAILED"), ("fallback", "SUCCEEDED")]


async def test_frozen_text_baseline_and_quality_metrics_are_persisted(harness) -> None:
    """S1：编排不传 source_text 时也要以冻结源文本度量并留存每次尝试。"""
    harness.register(StubParser("partial", text="line one\n"))
    service = harness.make_service()
    frozen = _frozen()
    await harness.seed_source(frozen, b"line one\nline two\n")

    # 生产调用不会手动传 source_text：ShadowParseService 必须复用已读取的
    # 冻结字节构造基准。S1 先灰度观察，不因旧链尚无 fallback 而阻断结果。
    run = await service.execute(
        frozen, _plan("partial", "good"), domain="default",
    )

    assert run.status == "SUCCEEDED"
    run_meta = json.loads(run.metadata_json)
    quality_attempts = run_meta["quality_attempts"]
    assert [item["decision"] for item in quality_attempts] == ["PASS"]
    assert quality_attempts[0]["metrics"]["char_coverage"] < 0.85

    # 失败 attempt 没有 snapshot，故 Run 投影是完整观测源；成功快照保留
    # 同一份质量数值，方便结果读取端展示。
    snapshot = await harness.snapshots.get(run.snapshot_id)
    assert snapshot is not None
    snapshot_meta = json.loads(snapshot.metadata_json)
    assert snapshot_meta["quality_metrics"]["char_coverage"] < 0.85


async def test_quality_fail_when_budget_exhausted(harness) -> None:
    harness.register(StubParser("partial", text="line one\n"))
    service = harness.make_service()
    frozen = _frozen()
    await harness.seed_source(frozen, "line one\nline two\n".encode())

    run = await service.execute(
        frozen, _plan("partial"), domain="default",
        source_text="line one\nline two\n",
        # max_backend_attempts=2 但链上只有 primary——覆盖失败且无备选。
    )
    assert run.status == "FAILED"
    assert harness.snapshots.count() == 0


# ---------------------------------------------------------------------------
# SUPERSEDED / FAIL 不入库
# ---------------------------------------------------------------------------


async def test_stale_input_marks_run_superseded_no_snapshot(harness) -> None:
    service = harness.make_service()
    frozen = _frozen()
    await harness.seed_source(frozen, "line one\nline two\n".encode())
    harness._stale = FrozenInputStale("doc1", 1, 2)

    run = await service.execute(frozen, _plan("good"), domain="default")
    assert run.status == "SUPERSEDED"
    assert run.snapshot_id is None
    assert harness.snapshots.count() == 0
    # attempt 本身成功留档（解析没错，只是输入过期）。
    events = await harness.attempts.list_by_run(run.id)
    assert events[0].outcome == "SUCCEEDED"


async def test_empty_parse_fails_without_snapshot(harness) -> None:
    harness.register(StubParser("empty", text=""))
    service = harness.make_service()
    frozen = _frozen()
    await harness.seed_source(frozen, "line one\nline two\n".encode())

    run = await service.execute(frozen, _plan("empty"), domain="default")
    assert run.status == "FAILED"
    assert harness.snapshots.count() == 0


# ---------------------------------------------------------------------------
# A07 / A09
# ---------------------------------------------------------------------------


async def test_a07_parser_upgrade_creates_new_snapshot_old_retained(harness) -> None:
    harness.register(StubParser("v1", text="line one\nline two\n"))
    harness.register(StubParser("v2", text="line one\nline two\n"))
    service = harness.make_service()
    frozen = _frozen()
    await harness.seed_source(frozen, "line one\nline two\n".encode())

    run1 = await service.execute(frozen, _plan("v1"), domain="default")
    run2 = await service.execute(frozen, _plan("v2"), domain="default")
    assert run1.snapshot_id != run2.snapshot_id
    assert harness.snapshots.count() == 2
    old = await harness.snapshots.get(run1.snapshot_id)
    assert old is not None and old.lifecycle_status == "READY"


async def test_a09_replay_from_raw_artifact_without_parser(harness) -> None:
    service = harness.make_service()
    frozen = _frozen()
    data = "line one\nline two\n".encode()
    await harness.seed_source(frozen, data)
    run = await service.execute(frozen, _plan("good"), domain="default")
    assert run.status == "SUCCEEDED"

    # 取回持久化的 backend_raw 对象，重放产新快照，parser 不再被调用。
    raw_records = [
        r for r in harness.storage_objects._by_id.values()
        if r.artifact_class == "backend_raw"
    ]
    assert raw_records
    before_calls = harness.parsers["good"].parse_calls
    replay_run = await service.replay(
        frozen, backend_raw_storage_object_id=raw_records[0].id,
        parser_id="good", domain="default",
        normalizer=FullNormalizer(version="full-norm@2"),
    )
    assert replay_run.status == "SUCCEEDED"
    assert replay_run.snapshot_id != run.snapshot_id  # 新指纹 → 新快照
    assert harness.parsers["good"].parse_calls == before_calls
    events = await harness.attempts.list_by_run(replay_run.id)
    assert events[0].attempt_kind == "replay"


async def test_commit_infrastructure_failure_fails_run_not_stuck(harness) -> None:
    """HIGH-1（对抗评审）：提交期基础设施异常（非 stale）必须落终态
    FAILED，不得永久卡 EVALUATING。"""
    service = harness.make_service()
    frozen = _frozen()
    await harness.seed_source(frozen, "line one\nline two\n".encode())
    # 破坏提交依赖：IR 制品注册行不存在 → commit 抛 StorageObjectMissing。
    harness._stale = None
    from knowledge_mining.mining.snapshot_store.service import (
        SnapshotCommitService as _SCS,
    )
    orig_verify = _SCS._verify_ir_object if hasattr(_SCS, "_verify_ir_object") else None

    class _BrokenCommit:
        async def commit(self, **kwargs):  # noqa: ANN003
            raise RuntimeError("db connection lost")

        def mark_lifecycle(self, *args, **kwargs):  # noqa: ANN002, ANN003
            raise NotImplementedError

    svc = harness.make_service()
    svc._commit = _BrokenCommit()
    run = await svc.execute(frozen, _plan("good"), domain="default")
    assert run.status == "FAILED", run.status
    assert "db connection lost" in (run.error_message or "")
    assert run.snapshot_id is None
