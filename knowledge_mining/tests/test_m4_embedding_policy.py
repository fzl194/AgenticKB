"""M4 契约（批次8，24 号 §5.7）：per-representation embedding policy.

- 策略枚举 ``skip|isolated|structural|contextualized|late_chunking``；
- 版本化默认矩阵 + 参数按 representation_type 覆盖（显式 fallback 才降级）；
- provider capability 校验：不支持 contextualized/late_chunking → 显式失败；
- 输入构造按策略：isolated=content_text；structural=面包屑+content；
- 每条 embedding 冻结 provenance（model/version/dim/strategy/policy
  version/input hash/fallback）；单模型单空间，query 侧单次嵌入；
- handler 消费 bundle（representations 计数），产出 embeddings_count。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from knowledge_mining.mining.contracts.retrieval_projection import (
    RetrieRepresentation,
)


def _rep(
    rep_type: str,
    *,
    content_text: str = "正文",
    structural_context: str = "章一",
    ordinal: int = 0,
) -> RetrieRepresentation:
    return RetrieRepresentation(
        representation_id=f"d:s:{rep_type}:{ordinal}",
        representation_type=rep_type,
        content_type=rep_type,
        content_text=content_text,
        structural_context=structural_context,
        target_type=rep_type,
        target_ref=f"d#{rep_type}:{ordinal}",
        canonical_evidence_id=f"d#{rep_type}:{ordinal}",
    )


def _reps() -> tuple[RetrieRepresentation, ...]:
    return (
        _rep("prose", content_text="段落正文", ordinal=0),
        _rep("table_row", content_text="告警码为A-101", ordinal=1),
        _rep("code_block", content_text="systemctl restart", ordinal=2),
        _rep("section", content_text="章一\n直接内容", ordinal=3),
        _rep("document", content_text="manual.md", ordinal=4),
    )


# ---------------------------------------------------------------------------
# 策略矩阵与解析
# ---------------------------------------------------------------------------


def test_default_policy_matrix_matches_spec() -> None:
    from knowledge_mining.mining.retrieval_projection.embedding_policy import (
        default_policy,
    )

    policy = default_policy()
    assert policy.version  # 版本化
    assert policy.strategy_for(_rep("prose")) == "structural"
    assert policy.strategy_for(_rep("section")) == "structural"
    assert policy.strategy_for(_rep("table_row")) == "structural"
    assert policy.strategy_for(_rep("table")) == "structural"
    assert policy.strategy_for(_rep("list_group")) == "structural"
    assert policy.strategy_for(_rep("figure_caption")) == "structural"
    assert policy.strategy_for(_rep("document")) == "isolated"
    assert policy.strategy_for(_rep("code_block")) == "isolated"
    assert policy.strategy_for(_rep("formula")) == "isolated"
    assert policy.strategy_for(_rep("query_alias")) == "isolated"
    assert policy.strategy_for(_rep("summary_alias")) == "isolated"


def test_strategy_overrides_by_representation_type() -> None:
    from knowledge_mining.mining.retrieval_projection.embedding_policy import (
        default_policy,
    )

    policy = default_policy().with_overrides(
        {"code_block": "structural"},
    )
    assert policy.strategy_for(_rep("code_block")) == "structural"
    assert policy.version.endswith("+override")


def test_unsupported_strategy_fails_unless_explicit_fallback() -> None:
    from knowledge_mining.mining.retrieval_projection.embedding_policy import (
        default_policy,
    )

    policy = default_policy().with_overrides({"prose": "contextualized"})
    # provider 不支持 contextualized 且无显式 fallback → 显式失败
    with pytest.raises(ValueError, match="contextualized"):
        policy.strategy_for(_rep("prose"), capabilities={"isolated", "structural"})
    # 显式 fallback → 降级并标记
    policy_fb = default_policy().with_overrides(
        {"prose": "contextualized"},
        fallbacks={"prose": "structural"},
    )
    decision = policy_fb.decide(_rep("prose"), capabilities={"isolated", "structural"})
    assert decision.strategy == "structural"
    assert decision.fallback_from == "contextualized"


def test_input_construction_per_strategy() -> None:
    from knowledge_mining.mining.retrieval_projection.embedding_policy import (
        embedding_input,
    )

    rep = _rep("prose", content_text="段落", structural_context="章一 > 节")
    assert embedding_input(rep, "isolated") == "段落"
    assert embedding_input(rep, "structural") == "章一 > 节\n段落"
    assert embedding_input(rep, "skip") is None


# ---------------------------------------------------------------------------
# 门面：分组嵌入 + provenance 冻结 + 暂存
# ---------------------------------------------------------------------------


class _FakeEmbeddingGenerator:
    """embed_batch 替身：按输入哈希生成确定性向量."""

    name = "fake-embed"
    capabilities = frozenset({"skip", "isolated", "structural"})

    def embed_batch(self, texts):
        import hashlib

        return [
            [float(b) for b in hashlib.sha256(t.encode("utf-8")).digest()[:8]]
            for t in texts
        ]

    def describe(self):
        return {"provider": "fake", "model": "fake-embed", "version": "v1", "dimension": 8}


def test_embed_for_snapshot_groups_and_freezes_provenance(tmp_path):
    import asyncio

    from knowledge_mining.mining.retrieval_projection.embedding import EmbeddingFacade
    from knowledge_mining.mining.retrieval_projection.repositories_memory import (
        MemoryRepresentationStore,
    )

    reps = _reps()
    representation_store = MemoryRepresentationStore()

    async def seed():
        await representation_store.replace_for_snapshot(
            "s1", reps, "proj-v1", document_key="d",
        )

    asyncio.new_event_loop().run_until_complete(seed())

    embedding_store = SimpleNamespace(written=None)

    class _Store:
        async def replace_for_snapshot(self, snapshot_id, records, fingerprint, *, document_key, vectors=()):
            embedding_store.written = (snapshot_id, records, fingerprint)
            return len(records)

    facade = EmbeddingFacade(
        representation_store=representation_store,
        embedding_store=_Store(),
        generator=_FakeEmbeddingGenerator(),
    )
    outcome = facade.embed_for_snapshot(snapshot_id="s1", params={})

    records = outcome.records
    assert outcome.skipped == 0
    # 全部 eligible 表示都有记录；prose/table_row/section=structural，code/document=isolated
    by_rep = {r.representation_id: r for r in records}
    assert len(records) == len(reps)
    assert by_rep["d:s:prose:0"].strategy == "structural"
    assert by_rep["d:s:document:4"].strategy == "isolated"
    # provenance 冻结
    rec = by_rep["d:s:prose:0"]
    assert rec.provider == "fake" and rec.model == "fake-embed"
    assert rec.dimension == 8 and rec.policy_version
    assert rec.input_hash  # 真实输入哈希
    # 输入构造正确进入哈希前的 raw input 记录
    assert rec.strategy_input == "章一\n段落正文"
    # 暂存写入被调用
    assert embedding_store.written[0] == "s1"
    assert len(embedding_store.written[1]) == len(reps)


def test_embed_skips_none_and_respects_override(tmp_path):
    import asyncio

    from knowledge_mining.mining.retrieval_projection.embedding import EmbeddingFacade
    from knowledge_mining.mining.retrieval_projection.repositories_memory import (
        MemoryRepresentationStore,
    )

    reps = (_rep("prose"), _rep("code_block"))
    representation_store = MemoryRepresentationStore()

    async def seed():
        await representation_store.replace_for_snapshot(
            "s1", reps, "proj-v1", document_key="d",
        )

    asyncio.new_event_loop().run_until_complete(seed())

    class _Store:
        async def replace_for_snapshot(self, snapshot_id, records, fingerprint, *, document_key, vectors=()):
            return len(records)

    facade = EmbeddingFacade(
        representation_store=representation_store,
        embedding_store=_Store(),
        generator=_FakeEmbeddingGenerator(),
    )
    outcome = facade.embed_for_snapshot(
        snapshot_id="s1",
        params={"strategyOverrides": {"prose": "skip"}},
    )
    assert outcome.skipped == 1
    assert len(outcome.records) == 1
    assert outcome.records[0].representation_id == "d:s:code_block:0"


# ---------------------------------------------------------------------------
# handler：bundle 消费
# ---------------------------------------------------------------------------


def test_embedding_handler_consumes_bundle_and_updates_count():
    from knowledge_mining.mining.workflow.bundle import MiningDocumentBundle
    from knowledge_mining.mining.workflow.handlers.document import embedding_handler

    bundle = MiningDocumentBundle(
        document_ref="d", run_document_id="rd-1", snapshot_ref="s1",
        representations_count=5,
    )
    service = SimpleNamespace(
        embed_for_snapshot=lambda *, snapshot_id, params: SimpleNamespace(
            records=[SimpleNamespace()] * 3, skipped=0,
        ),
    )
    state = SimpleNamespace(
        run_document_id="rd-1", doc_key="d", context=bundle,
        capabilities=frozenset(), tags=(),
        with_context=lambda ctx, capabilities=frozenset(): SimpleNamespace(
            run_document_id="rd-1", doc_key="d", context=ctx,
            capabilities=capabilities, tags=(),
        ),
    )
    result = embedding_handler(
        state, {}, SimpleNamespace(services=SimpleNamespace(
            embedding_service=service,
        )),
    )
    assert result.status.value == "success"
    assert "embeddings" in result.capabilities
    out = result.outputs.context
    assert isinstance(out, MiningDocumentBundle)
    assert out.embeddings_count == 3
    assert "embeddings" in out.capability_facts


def test_embedding_handler_requires_bundle_with_representations():
    from knowledge_mining.mining.workflow.bundle import MiningDocumentBundle
    from knowledge_mining.mining.workflow.handlers.document import embedding_handler

    bundle = MiningDocumentBundle(document_ref="d", run_document_id="rd-1")
    state = SimpleNamespace(
        run_document_id="rd-1", doc_key="d", context=bundle,
        capabilities=frozenset(), tags=(),
        with_context=lambda ctx, capabilities=frozenset(): SimpleNamespace(
            run_document_id="rd-1", doc_key="d", context=ctx,
            capabilities=capabilities, tags=(),
        ),
    )
    result = embedding_handler(
        state, {}, SimpleNamespace(services=SimpleNamespace()),
    )
    assert result.status.value == "skipped"


def test_embed_rejects_short_or_empty_vectors(tmp_path) -> None:
    """27号审查修复：embed_batch 短返/空向量必须显式失败——zip 截断会让
    空占位向量以 NULL 落库并虚报 dense_ready。"""
    import asyncio

    from knowledge_mining.mining.retrieval_projection.embedding import (
        EmbeddingFacade,
    )
    from knowledge_mining.mining.retrieval_projection.repositories_memory import (
        MemoryRepresentationStore,
    )

    reps = (_rep("prose"), _rep("code_block"))
    store = MemoryRepresentationStore()

    async def seed():
        await store.replace_for_snapshot(
            "s1", reps, "proj-v1", document_key="d",
        )

    asyncio.new_event_loop().run_until_complete(seed())

    def _generator(batch):
        # describe 提供 dimension=8；embed_batch 按注入行为返回
        return SimpleNamespace(
            capabilities=("skip", "isolated", "structural"),
            describe=lambda: {
                "provider": "x", "model": "m", "version": "1",
                "dimension": 8,
            },
            embed_batch=lambda inputs: batch(len(inputs)),
        )

    short = _generator(lambda n: [[0.0] * 8 for _ in range(n - 1)])
    with pytest.raises(RuntimeError, match="vectors"):
        EmbeddingFacade(
            representation_store=store, embedding_store=SimpleNamespace(),
            generator=short,
        ).embed_for_snapshot(snapshot_id="s1", params={})

    with_empty = _generator(lambda n: [[0.0] * 8] + [[]] * (n - 1))
    with pytest.raises(RuntimeError, match="empty vector"):
        EmbeddingFacade(
            representation_store=store, embedding_store=SimpleNamespace(),
            generator=with_empty,
        ).embed_for_snapshot(snapshot_id="s1", params={})
