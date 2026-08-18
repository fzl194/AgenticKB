"""M4.5：golden corpus 全量「转正」端到端验收（真实适配器 + 编排器）.

50 份语料逐份走 DocumentParseService（真实 parser/normalizer + Reconciler +
QualityGate + SnapshotCommitService），断言 M4 退出条件的语料级形态：

- 正例/复杂样本 → SUCCEEDED 且快照已转正（PASS/WARN）；
- 退化空样本 / 负例（解析崩溃）→ FAILED、**零快照**（低质量不形成 READY
  Snapshot）；
- 每个成功 Run 恰好有一条 attempt 审计事件；
- 快照计数 == 成功 Run 计数（无幽灵快照、无漏转正）。
"""
from __future__ import annotations

import hashlib
import sys

import pytest

pytestmark = pytest.mark.asyncio

if sys.platform == "win32":
    import asyncio

    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from knowledge_mining.mining.contracts.parse_plan import ParsePlan  # noqa: E402
from knowledge_mining.mining.contracts.storage.types import (  # noqa: E402
    ObjectLocation,
    PutOptions,
)
from knowledge_mining.mining.file_management.repositories_memory import (  # noqa: E402
    MemoryStorageObjectRepository,
)
from knowledge_mining.mining.frozen_input.contracts import FrozenInput  # noqa: E402
from knowledge_mining.mining.infra.object_store.fake import FakeObjectStore  # noqa: E402
from knowledge_mining.mining.parse_adapters.factory import resolve_pipeline  # noqa: E402
from knowledge_mining.mining.parse_operator.service import (  # noqa: E402
    DocumentParseService,
)
from knowledge_mining.mining.parse_quality.gate import QualityGate  # noqa: E402
from knowledge_mining.mining.parse_reconciler import StructuralReconciler  # noqa: E402
from knowledge_mining.mining.shadow_parse.repositories_memory import (  # noqa: E402
    MemoryParseAttemptRepository,
    MemoryParseRunRepository,
)
from knowledge_mining.mining.snapshot_store.repositories_memory import (  # noqa: E402
    MemorySnapshotRepository,
)
from knowledge_mining.mining.snapshot_store.service import (  # noqa: E402
    SnapshotCommitService,
)

from tests.golden_corpus.corpus import PARSER_ID, build_corpus  # noqa: E402


def _service(tmp_path):  # noqa: ANN001
    store = FakeObjectStore(str(tmp_path / "objects"))
    snapshots = MemorySnapshotRepository()

    async def _no_stale(frozen: FrozenInput) -> None:
        return None

    commit = SnapshotCommitService(
        snapshots=snapshots,
        stale_checker=_no_stale,
        storage_objects=None,
        object_store=None,
    )
    service = DocumentParseService(
        object_store=store,
        parse_runs=MemoryParseRunRepository(),
        attempts=MemoryParseAttemptRepository(),
        storage_objects=MemoryStorageObjectRepository(),
        parser_resolver=resolve_pipeline,
        commit_service=commit,
        quality_gate=QualityGate(),
        reconciler=StructuralReconciler(),
        bucket_prefix="corpus-m4-",
    )
    return service, snapshots, store


def _frozen(doc):  # noqa: ANN001
    sha = hashlib.sha256(doc.data).hexdigest()
    return FrozenInput(
        document_id=f"doc-{doc.name}",
        source_storage_object_id=f"so-{doc.name}",
        source_raw_hash=sha,
        source_content_revision=1,
        mime=doc.mime,
        size=len(doc.data),
        original_filename=f"{doc.name}",
        captured_at="2026-08-18T00:00:00+00:00",
        provider="fake",
        bucket="corpus-m4-source",
        object_key=f"v1/ab/{sha[:12]}",
        object_version_id=None,
    )


async def _seed(store, frozen, data):  # noqa: ANN001
    await store.put_bytes(
        ObjectLocation(bucket=frozen.bucket, object_key=frozen.object_key),
        data,
        PutOptions(artifact_class="source"),
    )


async def test_corpus_commit_end_to_end(tmp_path) -> None:
    service, snapshots, store = _service(tmp_path)
    corpus = build_corpus()
    expected_snapshots = 0
    outcomes: dict[str, str] = {}

    for doc in corpus:
        frozen = _frozen(doc)
        await _seed(store, frozen, doc.data)
        plan = ParsePlan(
            plan_id=f"plan-{doc.format_key}",
            primary_parser_id=PARSER_ID[doc.format_key],
        )
        run = await service.execute(
            frozen, plan, domain="default", source_text=doc.source_text,
        )
        outcomes[doc.name] = run.status

        if run.status == "SUCCEEDED":
            assert run.snapshot_id, f"{doc.name}: SUCCEEDED without snapshot"
            expected_snapshots += 1
            events = await service._attempts.list_by_run(run.id)
            assert len(events) == 1 and events[0].outcome == "SUCCEEDED", (
                f"{doc.name}: attempt audit missing"
            )
        else:
            assert run.status == "FAILED", (
                f"{doc.name}: unexpected terminal {run.status}"
            )
            assert run.snapshot_id is None, (
                f"{doc.name}: {run.status} must not produce a snapshot"
            )

    # 快照计数与成功 Run 一一对应（无幽灵、无漏转正）。
    assert snapshots.count() == expected_snapshots

    # 正例/复杂样本全部转正（低质量不形成 READY Snapshot 的正向面）。
    not_committed = {
        doc.name for doc in corpus
        if doc.category in ("positive", "complex")
        and outcomes[doc.name] != "SUCCEEDED"
    }
    assert not not_committed, sorted(not_committed)

    # 空样本/坏字节必须 FAILED 且零快照（负向面）。
    for name in (
        "md-empty", "txt-empty", "txt-blank-only", "docx-empty", "html-empty",
        "md-garbage",
    ):
        assert outcomes[name] == "FAILED", (name, outcomes[name])
