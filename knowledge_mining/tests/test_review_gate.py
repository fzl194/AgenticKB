"""B6 单测：人审两道 Gate 的回写 + 暂停/恢复编排（纯逻辑 + 假 store，无需 DB）。

覆盖：
- _check_review_gate（N4 反转闸序）：gate2（pending mention）优先于 gate1（本体候选），都无→None（自动放行）；
- _rebuild_from_run_documents：resume 时从 run_documents 重建 snapshot_decisions + 计数；
- OntologyStore.promote_accepted_candidates：克隆旧版类型 + 追加候选 + 激活新版；review_candidate 标态/改名；
- GraphStore.resolve_mention：merge / new / reject 三路分流。

假 store 子类只覆盖 _execute/_fetchone/_fetchall（或个别高层方法），让被测方法的真实
SQL 拼装与编排逻辑照常执行。
"""
from __future__ import annotations

from types import SimpleNamespace

import knowledge_mining.mining.jobs.run as run_mod
from knowledge_mining.mining.jobs.run import _check_review_gate, _rebuild_from_run_documents
from knowledge_mining.mining.infra.ontology_store import OntologyStore, GraphStore


# ---- _check_review_gate：Gate 优先级 ----

def test_gate2_takes_priority_over_gate1(monkeypatch) -> None:
    # N4 反转：先把"暂无类型/有歧义"的实体确认干净，再归纳类型给人审。
    asset = SimpleNamespace(pool=object())
    monkeypatch.setattr(OntologyStore, "count_proposed_candidates", lambda self, d: 3)
    monkeypatch.setattr(GraphStore, "count_pending_mentions_for_run", lambda self, r: 7)
    assert _check_review_gate(asset, "run1", "dom") == "entity_review"


def test_gate1_when_no_pending_mentions(monkeypatch) -> None:
    asset = SimpleNamespace(pool=object())
    monkeypatch.setattr(OntologyStore, "count_proposed_candidates", lambda self, d: 4)
    monkeypatch.setattr(GraphStore, "count_pending_mentions_for_run", lambda self, r: 0)
    assert _check_review_gate(asset, "run1", "dom") == "ontology_review"


def test_no_gate_auto_release(monkeypatch) -> None:
    asset = SimpleNamespace(pool=object())
    monkeypatch.setattr(OntologyStore, "count_proposed_candidates", lambda self, d: 0)
    monkeypatch.setattr(GraphStore, "count_pending_mentions_for_run", lambda self, r: 0)
    assert _check_review_gate(asset, "run1", "dom") is None


# ---- _rebuild_from_run_documents：resume 重建 ----

def test_rebuild_counts_and_decisions() -> None:
    rt = SimpleNamespace(get_run_documents=lambda rid: [
        {"id": "rd-1", "status": "committed", "document_id": "d1",
         "document_snapshot_id": "s1", "document_key": "k1", "action": "NEW"},
        {"id": "rd-2", "status": "committed", "document_id": "d2",
         "document_snapshot_id": "s2", "document_key": "k2", "action": "UPDATE"},
        {"id": "rd-3", "status": "failed", "document_id": None, "document_snapshot_id": None,
         "document_key": "k3", "action": "NEW"},
        {"id": "rd-4", "status": "skipped", "document_id": None, "document_snapshot_id": None,
         "document_key": "k4", "action": "SKIP"},
    ])
    # 36号三分区：committed 历史行进 staged 候选（finalize 按文档级 readiness
    # 分区后才定 committed_count——草稿计数里 committed 恒 0）。
    index = _rebuild_from_run_documents(rt, "run1")
    assert [c["document_snapshot_id"] for c in index.candidates] == ["s1", "s2"]
    # A terminal SKIPPED row without a reusable document/snapshot identity is
    # a required-pipeline rejection, not an incremental carry-forward.
    assert index.counts == {"committed_count": 0, "new_count": 1, "updated_count": 1,
                            "failed_count": 2, "skipped_count": 0}


# ---- Gate1：OntologyStore 候选裁决 + 升版 ----

class _FakeOnto(OntologyStore):
    def __init__(self, *, accepted, nodes, rels) -> None:
        self.executed: list[tuple[str, tuple]] = []
        self._accepted = accepted
        self._nodes = nodes
        self._rels = rels

    def _execute(self, sql, params=()):  # type: ignore[override]
        self.executed.append((sql, params))

    def _fetchone(self, sql, params=()):  # type: ignore[override]
        if "MAX(version_no)" in sql or "COALESCE" in sql:
            return {"m": 1}
        return None

    def _fetchall(self, sql, params=()):  # type: ignore[override]
        if "ontology_candidates" in sql:
            return self._accepted
        if "ontology_node_types" in sql:
            return self._nodes
        if "ontology_relation_types" in sql:
            return self._rels
        return []


def _inserts(store, table):
    return [p for s, p in store.executed if s.strip().startswith("INSERT") and table in s]


def test_promote_clones_old_and_appends_accepted() -> None:
    store = _FakeOnto(
        accepted=[
            {"kind": "node_type", "proposed_name": "5GC", "payload_json": {}},
            {"kind": "relation_type", "proposed_name": "triggers",
             "payload_json": {"head_type": "alarm", "tail_type": "feature"}},
        ],
        nodes=[{"name": "network_element", "layer": "concept", "is_strong": True,
                "definition": None, "examples_json": []}],
        rels=[{"name": "connects_to", "layer": "concept", "is_directed": True,
               "inverse_name": None, "allowed_pairs_json": [{"head": "network_element", "tail": "interface"}],
               "definition": None}],
    )
    new_vid = store.promote_accepted_candidates("dom", created_by="tester")
    assert new_vid

    node_names = {p[2] for p in _inserts(store, "ontology_node_types")}
    rel_names = {p[2] for p in _inserts(store, "ontology_relation_types")}
    assert node_names == {"network_element", "5GC"}          # 克隆旧 + 追加新
    assert rel_names == {"connects_to", "triggers"}
    # 升版后激活新版本
    assert any("status = 'active'" in s for s, _ in store.executed)


def test_promote_none_when_no_accepted() -> None:
    store = _FakeOnto(accepted=[], nodes=[], rels=[])
    assert store.promote_accepted_candidates("dom") is None
    assert store.executed == []                              # 无候选不动版本


# ---- N5：回贴成员名单 + __untyped__ 改类型 ----

class _FakeOntoMembers(OntologyStore):
    def __init__(self, rows) -> None:
        self._rows = rows

    def _fetchall(self, sql, params=()):  # type: ignore[override]
        return self._rows


def test_accepted_members_flatten_entity_ids() -> None:
    store = _FakeOntoMembers([
        {"proposed_name": "network_slice", "payload_json": {"member_entity_ids": ["e1", "e2"]}},
        {"proposed_name": "func", "payload_json": {"member_entity_ids": ["e3"]}},
        {"proposed_name": "empty", "payload_json": {}},          # 无成员→跳过
    ])
    out = store.accepted_node_type_members("dom")
    assert out == [("e1", "network_slice"), ("e2", "network_slice"), ("e3", "func")]


class _FakeGraphRebind(GraphStore):
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple]] = []

    def _execute(self, sql, params=()):  # type: ignore[override]
        self.executed.append((sql, params))


def test_rebind_updates_only_untyped_rows() -> None:
    g = _FakeGraphRebind()
    n = g.rebind_untyped_entities("dom", [("e1", "network_slice"), ("e2", "func")])
    assert n == 2
    for sql, params in g.executed:
        assert "node_type = %s" in sql and "__untyped__" in params      # 幂等守卫


def test_rebind_skips_blank_and_untyped_targets() -> None:
    g = _FakeGraphRebind()
    n = g.rebind_untyped_entities("dom", [("", "func"), ("e1", ""), ("e2", "__untyped__")])
    assert n == 0 and g.executed == []                                  # 无一可回贴


# ---- N5：收尾计数重算（提及计数 + 文档去重）----

class _FakeGraphCounts(GraphStore):
    def __init__(self) -> None:
        self.counts: list[tuple] = []

    def set_entity_counts(self, eid, *, mention_count, document_count):  # type: ignore[override]
        self.counts.append((eid, mention_count, document_count))


def test_recount_aggregates_mentions_and_dedups_docs() -> None:
    g = _FakeGraphCounts()
    rows = [
        {"entity_id": "e1", "document_snapshot_id": "s1"},
        {"entity_id": "e1", "document_snapshot_id": "s1"},   # 同文档第二条提及
        {"entity_id": "e1", "document_snapshot_id": "s2"},
        {"entity_id": "e2", "document_snapshot_id": "s3"},
        {"entity_id": None, "document_snapshot_id": "s4"},   # 缺实体 → 跳过
    ]
    n = run_mod._recount_entities(g, rows)
    assert n == 2
    d = {c[0]: (c[1], c[2]) for c in g.counts}
    assert d["e1"] == (3, 2)            # 3 条提及，2 篇去重文档
    assert d["e2"] == (1, 1)


class _FakeOntoRec(OntologyStore):
    def __init__(self, status: str = "proposed") -> None:
        self.executed: list[tuple[str, tuple]] = []
        self._status = status

    def _execute(self, sql, params=()):  # type: ignore[override]
        self.executed.append((sql, params))

    def _fetchone(self, sql, params=()):  # type: ignore[override]
        # 幂等保护读当前状态：返回构造的状态行
        return {"status": self._status}


def test_review_candidate_accept_with_rename() -> None:
    store = _FakeOntoRec()
    store.review_candidate("c1", action="accept", new_name="alarm_triggers_feature")
    sql, params = store.executed[0]
    assert "status = %s" in sql and "proposed_name = %s" in sql
    assert "accepted" in params and "alarm_triggers_feature" in params


def test_review_candidate_reject() -> None:
    store = _FakeOntoRec()
    store.review_candidate("c1", action="reject")
    sql, params = store.executed[0]
    assert "rejected" in params


def test_review_candidate_already_decided_rejected() -> None:
    """幂等保护：已 accepted/rejected 的候选不允许重复裁决（防前端误点/并发重复提交）。"""
    import pytest
    store = _FakeOntoRec(status="accepted")
    with pytest.raises(ValueError, match="already accepted"):
        store.review_candidate("c1", action="reject")
    assert store.executed == []  # 没有发出 UPDATE


# ---- Gate2：GraphStore mention 裁决 ----

class _FakeGraph(GraphStore):
    def __init__(self, *, mention=None, entity=None) -> None:
        self.executed: list[tuple[str, tuple]] = []
        self.upserts: list[tuple] = []
        self._m = mention
        self._e = entity

    def _execute(self, sql, params=()):  # type: ignore[override]
        self.executed.append((sql, params))

    def get_mention(self, mid):  # type: ignore[override]
        return self._m

    def get_entity(self, eid):  # type: ignore[override]
        return self._e

    def upsert_entity(self, domain_id, **kw):  # type: ignore[override]
        self.upserts.append((domain_id, kw))
        return "new-ent"


def test_resolve_merge_points_to_existing() -> None:
    g = _FakeGraph(mention={"mention_text": "SMF会话", "node_type": "network_element"},
                   entity={"canonical_name": "SMF"})
    out = g.resolve_mention("m1", action="merge", entity_id="e-smf")
    assert out == "e-smf"
    sql, params = g.executed[0]
    assert "resolved_entity_id = %s" in sql and "resolve_status = 'human'" in sql
    assert "e-smf" in params and "SMF" in params


def test_resolve_new_creates_entity() -> None:
    g = _FakeGraph(mention={"mention_text": "5GC", "node_type": "concept"})
    out = g.resolve_mention("m1", action="new", domain_id="dom")
    assert out == "new-ent"
    domain_id, kw = g.upserts[0]
    assert domain_id == "dom" and kw["canonical_name"] == "5GC" and kw["node_type"] == "concept"
    assert any("resolved_entity_id = %s" in s for s, _ in g.executed)


def test_resolve_reject_marks_non_entity() -> None:
    g = _FakeGraph()
    out = g.resolve_mention("m1", action="reject")
    assert out is None
    sql, params = g.executed[0]
    assert "resolve_status = 'rejected'" in sql


def test_resolve_merge_bumps_mention_count() -> None:
    # 人审激活原 pending 提及 → 目标实体 mention_count 即时 +1。
    g = _FakeGraph(mention={"mention_text": "SMF会话", "node_type": "network_element"},
                   entity={"canonical_name": "SMF"})
    g.resolve_mention("m1", action="merge", entity_id="e-smf")
    bumps = [p for s, p in g.executed if "mention_count = mention_count + %s" in s]
    assert len(bumps) == 1 and 1 in bumps[0] and "e-smf" in bumps[0]


def test_resolve_new_bumps_mention_count() -> None:
    g = _FakeGraph(mention={"mention_text": "5GC", "node_type": "concept"})
    g.resolve_mention("m1", action="new", domain_id="dom")
    assert any("mention_count = mention_count + %s" in s for s, _ in g.executed)


def test_resolve_reject_no_count_bump() -> None:
    g = _FakeGraph()
    g.resolve_mention("m1", action="reject")
    assert not any("mention_count = mention_count + %s" in s for s, _ in g.executed)


# ---- 出处补写：人工确认实体时写 ontology_evidence_nodes（L1 §7 / L4 §14.5 修复）----

def _evidence_inserts(g: "_FakeGraph") -> list[tuple]:
    return [params for sql, params in g.executed if "INSERT INTO ontology_evidence_nodes" in sql]


def test_resolve_new_writes_entity_evidence() -> None:
    g = _FakeGraph(mention={
        "mention_text": "5GC", "node_type": "concept",
        "segment_id": "seg-1", "document_snapshot_id": "snap-1", "metadata_json": {},
    })
    eid = g.resolve_mention("m1", action="new", domain_id="dom")
    ins = _evidence_inserts(g)
    assert len(ins) == 1
    params = ins[0]
    assert "entity" in params and "seg-1" in params and "snap-1" in params and eid in params


def test_resolve_merge_writes_entity_evidence() -> None:
    g = _FakeGraph(
        mention={"mention_text": "SMF会话", "node_type": "network_element",
                 "segment_id": "seg-2", "document_snapshot_id": "snap-2"},
        entity={"canonical_name": "SMF", "domain_id": "dom"})
    g.resolve_mention("m1", action="merge", entity_id="e-smf", domain_id="dom")
    ins = _evidence_inserts(g)
    assert len(ins) == 1
    params = ins[0]
    assert "e-smf" in params and "seg-2" in params and "entity" in params


def test_resolve_reject_writes_no_evidence() -> None:
    g = _FakeGraph()
    g.resolve_mention("m1", action="reject")
    assert _evidence_inserts(g) == []


def test_resolve_without_segment_writes_no_evidence() -> None:
    # 提及缺 segment_id（兜底）→ 不写出处，不报错
    g = _FakeGraph(mention={"mention_text": "X", "node_type": "concept", "metadata_json": {}})
    g.resolve_mention("m1", action="new", domain_id="dom")
    assert _evidence_inserts(g) == []


# ---- §14.4：批量裁决 ----

class _FakeBatchGraph(_FakeGraph):
    """正常 id 返回 mention，id == 'bad' 时 get_mention 返回 None（触发逐条失败）。"""
    def get_mention(self, mid):  # type: ignore[override]
        return None if mid == "bad" else {"mention_text": "X", "node_type": "concept"}


def test_resolve_batch_reject_all() -> None:
    g = _FakeGraph()
    res = g.resolve_mentions_batch(["m1", "m2", "m3"], action="reject")
    assert len(res) == 3 and all(r["ok"] for r in res)
    assert all(r["resolved_entity_id"] is None for r in res)
    assert sum("resolve_status = 'rejected'" in s for s, _ in g.executed) == 3


def test_resolve_batch_merge_to_same_entity() -> None:
    g = _FakeGraph(mention={"mention_text": "SMF会话", "node_type": "network_element"},
                   entity={"canonical_name": "SMF"})
    res = g.resolve_mentions_batch(["m1", "m2"], action="merge", entity_id="e-smf")
    assert len(res) == 2 and all(r["ok"] for r in res)
    assert all(r["resolved_entity_id"] == "e-smf" for r in res)


def test_resolve_batch_continues_on_error() -> None:
    g = _FakeBatchGraph(entity={"canonical_name": "SMF"})
    res = g.resolve_mentions_batch(["m1", "bad", "m2"], action="merge", entity_id="e-smf")
    assert [r["ok"] for r in res] == [True, False, True]
    assert "error" in res[1]


# ---- B7：GraphStore 图谱浏览查询（实体检索 / 出处 / 批量取点）----

class _FakeGraphQuery(GraphStore):
    """记录 _fetchall/_fetchone 的 SQL+params，校验 B7 查询 SQL 拼装。"""

    def __init__(self) -> None:
        self.queries: list[tuple[str, tuple]] = []

    def _fetchall(self, sql, params=()):  # type: ignore[override]
        self.queries.append((sql, tuple(params)))
        return []

    def _fetchone(self, sql, params=()):  # type: ignore[override]
        self.queries.append((sql, tuple(params)))
        return {"n": 0}


def test_list_entities_filters_and_paginates() -> None:
    g = _FakeGraphQuery()
    g.list_entities("dom", node_type="network_element", q="SMF", limit=20, offset=40)
    sql, params = g.queries[0]
    assert "domain_id = %s" in sql and "node_type = %s" in sql and "ILIKE %s" in sql
    assert params == ("dom", "network_element", "%SMF%", 20, 40)


def test_list_entities_minimal_only_domain() -> None:
    g = _FakeGraphQuery()
    g.list_entities("dom", limit=50, offset=0)
    sql, params = g.queries[0]
    assert "node_type = %s" not in sql and "ILIKE" not in sql
    assert params == ("dom", 50, 0)


def test_count_entities_with_query() -> None:
    g = _FakeGraphQuery()
    n = g.count_entities("dom", q="5GC")
    assert n == 0
    sql, params = g.queries[0]
    assert "COUNT(*)" in sql and "ILIKE %s" in sql
    assert params == ("dom", "%5GC%")


def test_get_entities_by_ids_builds_in_clause() -> None:
    g = _FakeGraphQuery()
    g.get_entities_by_ids(["a", "b", "c"])
    sql, params = g.queries[0]
    assert "IN (%s,%s,%s)" in sql
    assert params == ("a", "b", "c")


def test_get_entities_by_ids_empty_short_circuits() -> None:
    g = _FakeGraphQuery()
    assert g.get_entities_by_ids([]) == []
    assert g.queries == []                                   # 空列表不查库


def test_suggest_entities_bidirectional_match() -> None:
    g = _FakeGraphQuery()
    g.suggest_entities_for_mention("dom", mention_text="SMF会话", node_type="network_element", limit=3)
    sql, params = g.queries[0]
    assert "domain_id = %s" in sql and "node_type = %s" in sql
    assert "canonical_name ILIKE %s" in sql and "%s ILIKE" in sql      # 双向包含
    assert "ORDER BY mention_count DESC" in sql
    assert params == ("dom", "network_element", "%SMF会话%", "SMF会话", 3)


def test_suggest_entities_empty_inputs_short_circuit() -> None:
    g = _FakeGraphQuery()
    assert g.suggest_entities_for_mention("dom", mention_text="", node_type="x") == []
    assert g.suggest_entities_for_mention("dom", mention_text="x", node_type="") == []
    assert g.queries == []                                   # 缺名字/类型不查库


def test_evidence_for_target_joins_segment() -> None:
    g = _FakeGraphQuery()
    g.evidence_for_target("ent1")
    sql, params = g.queries[0]
    assert "ontology_evidence_nodes" in sql and "asset_raw_segments" in sql
    assert "target_id = %s" in sql
    assert params == ("ent1",)
