"""本体 / 知识图谱路由（B7）— 对应实现设计 L2 §9。

把 B6 已交付的后端能力（OntologyStore / GraphStore 编排）暴露成 REST：
- 本体版本治理 + 引种（上传文件冷启动）；
- Gate1 本体候选评审 + 升版；
- Gate2 pending mention 裁决；
- 知识图谱浏览（对象检索 / 邻域多跳 / 出处回链）。

这两个 Store 是同步适配器（与挖掘流水线复用同一套逻辑，不重写成异步裸 SQL）。
路由用 asyncio.to_thread 把阻塞调用丢到线程池，跑在 app.state.sync_pool 上。
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from knowledge_mining.mining.api.domain_scope import require_domain
from knowledge_mining.mining.infra.ontology_store import (
    OntologyStore, GraphStore, find_duplicate_type,
)
from knowledge_mining.mining.stages.graph_write import scoped_recompute
from knowledge_mining.mining.infra.domain_pack import get_default_domain

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["ontology"])

_DEFAULT_DOMAIN = get_default_domain()  # 来自 domain_registry.yaml，不再硬编码


def _stores(request: Request, domain: str) -> tuple[OntologyStore, GraphStore]:
    pool = request.app.state.domain_pools.sync_pool(require_domain(domain))
    return OntologyStore(pool), GraphStore(pool)


async def _run(fn, *args, **kwargs):
    """把同步 Store 调用放到线程池执行（避免阻塞事件循环）。"""
    return await asyncio.to_thread(fn, *args, **kwargs)


def _existing_type_for_dup(node_type_row: dict) -> dict:
    """把 active_node_types 行规整成 find_duplicate_type 需要的 {name, examples}。

    examples_json 在库里以 JSON 文本存储，需 loads；非列表一律当空。
    """
    ex = node_type_row.get("examples_json")
    if isinstance(ex, str):
        try:
            ex = json.loads(ex)
        except Exception:
            ex = []
    if not isinstance(ex, list):
        ex = []
    return {"name": node_type_row.get("name") or "", "examples": ex}


# ── 请求模型 ──

class BootstrapRequest(BaseModel):
    domain: str | None = None
    yaml_text: str
    source_name: str = "uploaded.yaml"


class ReviewCandidateRequest(BaseModel):
    action: str                 # accept | reject
    new_name: str | None = None
    note: str | None = None


class PromoteRequest(BaseModel):
    domain: str | None = None
    created_by: str | None = None
    note: str | None = None


class DraftNodeTypeModel(BaseModel):
    name: str
    layer: str = "concept"
    is_strong: bool = False
    definition: str | None = None
    examples_json: list = []


class DraftRelationTypeModel(BaseModel):
    name: str
    layer: str = "concept"
    is_directed: bool = True
    inverse_name: str | None = None
    allowed_pairs_json: list = []
    definition: str | None = None


class SaveDraftRequest(BaseModel):
    domain: str | None = None
    node_types: list[DraftNodeTypeModel] = []
    relation_types: list[DraftRelationTypeModel] = []
    created_by: str | None = None


class PublishDraftRequest(BaseModel):
    domain: str | None = None


class ResolveMentionRequest(BaseModel):
    action: str                 # merge | new | reject
    entity_id: str | None = None
    domain: str | None = None
    canonical_name: str | None = None
    node_type: str | None = None


class ResolveBatchRequest(BaseModel):
    mention_ids: list[str]
    action: str                 # merge | new | reject
    entity_id: str | None = None  # merge 时所有提及并到同一对象
    domain: str | None = None
    node_type: str | None = None


class MergeEntitiesRequest(BaseModel):
    primary_id: str
    drop_ids: list[str]
    domain: str | None = None


class RetypeEntityRequest(BaseModel):
    new_type: str
    domain: str | None = None


# ── 本体版本 + 引种 ──

@router.post("/ontology/bootstrap")
async def bootstrap_ontology_route(body: BootstrapRequest, request: Request) -> dict:
    """上传一份本体 YAML 文件内容引种 v1（幂等：已有 active 版本则跳过）。"""
    from knowledge_mining.mining.infra.ontology_bootstrap import bootstrap_ontology_from_text

    domain = body.domain or _DEFAULT_DOMAIN
    onto, _ = _stores(request, domain)
    try:
        result = await _run(
            bootstrap_ontology_from_text,
            onto,
            domain_id=domain,
            yaml_text=body.yaml_text,
            source_name=body.source_name,
        )
    except Exception as e:
        logger.error("bootstrap ontology failed: %s", e, exc_info=True)
        raise HTTPException(400, f"引种失败：{e}")
    return result


@router.get("/ontology/versions")
async def list_ontology_versions(request: Request, domain: str = _DEFAULT_DOMAIN) -> dict:
    """某领域全部本体版本（版本治理页）。"""
    onto, _ = _stores(request, domain)
    versions = await _run(onto.list_versions, domain)
    return {"domain": domain, "items": [dict(v) for v in versions]}


@router.get("/ontology/active")
async def get_active_ontology(request: Request, domain: str = _DEFAULT_DOMAIN) -> dict:
    """当前 active 版本 + 它的点类型 / 边类型（抽取约束的真相源）。"""
    onto, _ = _stores(request, domain)
    version = await _run(onto.active_version, domain)
    if not version:
        return {"domain": domain, "version": None, "node_types": [], "relation_types": []}
    node_types = await _run(onto.active_node_types, domain)
    rel_types = await _run(onto.active_relation_types, domain)
    return {
        "domain": domain,
        "version": dict(version),
        "node_types": [dict(n) for n in node_types],
        "relation_types": [dict(r) for r in rel_types],
    }


# ── Gate1 本体候选评审 ──

@router.get("/ontology/candidates")
async def list_ontology_candidates(
    request: Request, domain: str = _DEFAULT_DOMAIN, status: str = "proposed",
) -> dict:
    """本体确认评审列表：某状态的本体候选（默认待审 proposed）。

    每条 node_type 候选附 duplicate_of：与当前 active 本体的现有类型判重，命中则为
    现有类型名（前端显示红色"疑似重复"徽标），无则 None。relation_type 暂不判重。
    """
    onto, graph = _stores(request, domain)
    rows = await _run(onto.list_candidates, domain, status=status)
    node_types = await _run(onto.active_node_types, domain)
    existing = [_existing_type_for_dup(n) for n in node_types]
    items: list[dict] = []
    for r in rows:
        d = dict(r)
        if d.get("kind") == "node_type":
            d["duplicate_of"] = find_duplicate_type(d.get("proposed_name") or "", existing)
        else:
            d["duplicate_of"] = None
        items.append(d)

    # 读时补"各自的"成员原文片段：按实体提及位置开窗，避免多个成员实体共用同一段摘录。
    # 同时覆盖历史候选里已落库的旧摘录（无需重跑 LLM 归纳）。mention 字段供前端在片段中加粗。
    member_ids: list[str] = []
    for d in items:
        if d.get("kind") != "node_type":
            continue
        for m in (d.get("payload_json") or {}).get("members") or []:
            if m.get("entity_id"):
                member_ids.append(m["entity_id"])
    if member_ids:
        quotes = await _run(graph.member_quotes, member_ids)
        for d in items:
            if d.get("kind") != "node_type":
                continue
            for m in (d.get("payload_json") or {}).get("members") or []:
                q = quotes.get(m.get("entity_id"))
                if q:
                    m["quote"] = q["quote"]
                    m["mention"] = q["mention"]

    return {"domain": domain, "status": status, "items": items}


@router.post("/ontology/candidates/{candidate_id}/review")
async def review_ontology_candidate(
    candidate_id: str, body: ReviewCandidateRequest, request: Request,
    domain: str = _DEFAULT_DOMAIN,
) -> dict:
    """单条裁决（accept 可改名 / reject）。只标状态，不升版——升版走 /ontology/promote。"""
    onto, _ = _stores(request, domain)
    try:
        await _run(
            onto.review_candidate, candidate_id,
            action=body.action, new_name=body.new_name, note=body.note,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"candidate_id": candidate_id, "action": body.action, "ok": True}


@router.post("/ontology/promote")
async def promote_candidates(body: PromoteRequest, request: Request) -> dict:
    """把全部 accepted 候选并入一个新 active 本体版本（无 accepted → 不升版）。"""
    domain = body.domain or _DEFAULT_DOMAIN
    onto, _ = _stores(request, domain)
    new_vid = await _run(
        onto.promote_accepted_candidates, domain,
        created_by=body.created_by, note=body.note,
    )
    return {"domain": domain, "new_version_id": new_vid, "promoted": new_vid is not None}


# ── 本体图谱编辑器：草稿 / 发布 ──

@router.get("/ontology/draft")
async def get_ontology_draft(request: Request, domain: str = _DEFAULT_DOMAIN) -> dict:
    """取该领域 draft 版本及其点/边类型。无 draft → version=null、空列表（前端据此从 active 克隆）。"""
    onto, _ = _stores(request, domain)
    draft = await _run(onto.get_draft_version, domain)
    if not draft:
        return {"domain": domain, "version": None, "node_types": [], "relation_types": []}
    node_types = await _run(onto.node_types_for_version, draft["id"])
    rel_types = await _run(onto.relation_types_for_version, draft["id"])
    return {
        "domain": domain,
        "version": dict(draft),
        "node_types": [dict(n) for n in node_types],
        "relation_types": [dict(r) for r in rel_types],
    }


@router.put("/ontology/draft")
async def save_ontology_draft(body: SaveDraftRequest, request: Request) -> dict:
    """整份覆盖式保存草稿：后端 get-or-create draft 版本 → 清空旧类型 → 按提交内容重写。"""
    domain = body.domain or _DEFAULT_DOMAIN
    onto, _ = _stores(request, domain)
    node_types = [
        {"name": n.name, "layer": n.layer, "is_strong": n.is_strong,
         "definition": n.definition, "examples": n.examples_json}
        for n in body.node_types
    ]
    relation_types = [
        {"name": r.name, "layer": r.layer, "is_directed": r.is_directed,
         "inverse_name": r.inverse_name, "allowed_pairs": r.allowed_pairs_json,
         "definition": r.definition}
        for r in body.relation_types
    ]
    try:
        draft_vid = await _run(
            onto.replace_draft, domain, node_types, relation_types,
            created_by=body.created_by,
        )
    except Exception as e:
        logger.error("save ontology draft failed: %s", e, exc_info=True)
        raise HTTPException(400, f"保存草稿失败：{e}")
    return {"domain": domain, "draft_version_id": draft_vid}


@router.post("/ontology/draft/publish")
async def publish_ontology_draft(body: PublishDraftRequest, request: Request) -> dict:
    """发布：把 draft 激活为新 active（旧 active → superseded）。无 draft → 400。"""
    domain = body.domain or _DEFAULT_DOMAIN
    onto, _ = _stores(request, domain)
    new_vid = await _run(onto.publish_draft, domain)
    if new_vid is None:
        raise HTTPException(400, "没有可发布的草稿，请先保存草稿")
    return {"domain": domain, "new_version_id": new_vid}


# ── Gate2 mention 裁决 ──

@router.get("/mentions/pending")
async def list_pending_mentions(
    request: Request, run_id: str | None = None, domain: str = _DEFAULT_DOMAIN,
) -> dict:
    """Gate2 评审列表：待人审 mention（给 run_id 则只列该 run 处理的快照）。"""
    _, graph = _stores(request, domain)
    if run_id:
        rows = await _run(graph.pending_mentions_for_run, run_id)
    else:
        rows = await _run(graph.pending_mentions)
    return {"run_id": run_id, "items": [dict(r) for r in rows]}


@router.post("/mentions/{mention_id}/resolve")
async def resolve_mention_route(
    mention_id: str, body: ResolveMentionRequest, request: Request,
) -> dict:
    """单条裁决：merge（指向已有对象）/ new（新建对象）/ reject（丢弃）。"""
    domain = body.domain or _DEFAULT_DOMAIN
    _, graph = _stores(request, domain)
    try:
        entity_id = await _run(
            graph.resolve_mention, mention_id,
            action=body.action, entity_id=body.entity_id,
            domain_id=domain,
            canonical_name=body.canonical_name, node_type=body.node_type,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"mention_id": mention_id, "action": body.action, "resolved_entity_id": entity_id}


@router.get("/mentions/suggest")
async def suggest_entities_route(
    request: Request,
    mention_text: str,
    node_type: str,
    domain: str = _DEFAULT_DOMAIN,
    limit: int = Query(5, ge=1, le=20),
) -> dict:
    """§14.3：Gate2 实时推荐——同类型下与提及名双向包含的已有对象 top-N（点击即合并）。"""
    _, graph = _stores(request, domain)
    rows = await _run(
        graph.suggest_entities_for_mention, domain,
        mention_text=mention_text, node_type=node_type, limit=limit,
    )
    return {"items": [dict(r) for r in rows]}


@router.post("/mentions/resolve-batch")
async def resolve_mentions_batch_route(
    body: ResolveBatchRequest, request: Request,
) -> dict:
    """批量裁决：对一批提及套用同一 action。merge → 全部并到同一已有对象（entity_id）。"""
    if not body.mention_ids:
        raise HTTPException(400, "mention_ids 不能为空")
    if body.action not in ("merge", "new", "reject"):
        raise HTTPException(400, f"unknown action: {body.action!r}")
    if body.action == "merge" and not body.entity_id:
        raise HTTPException(400, "批量合并需要 entity_id")
    domain = body.domain or _DEFAULT_DOMAIN
    _, graph = _stores(request, domain)
    results = await _run(
        graph.resolve_mentions_batch, body.mention_ids,
        action=body.action, entity_id=body.entity_id,
        domain_id=domain, node_type=body.node_type,
    )
    ok = sum(1 for r in results if r.get("ok"))
    return {"action": body.action, "total": len(results), "ok": ok,
            "failed": len(results) - ok, "results": results}


# ── 知识图谱浏览 ──

@router.get("/graph/entities")
async def list_graph_entities(
    request: Request,
    domain: str = _DEFAULT_DOMAIN,
    type: str | None = None,
    q: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict:
    """对象检索：按领域过滤，可选 node_type 与名称模糊匹配。"""
    _, graph = _stores(request, domain)
    total = await _run(graph.count_entities, domain, node_type=type, q=q)
    rows = await _run(
        graph.list_entities, domain, node_type=type, q=q, limit=limit, offset=offset,
    )
    return {
        "domain": domain, "total": total, "limit": limit, "offset": offset,
        "items": [dict(r) for r in rows],
    }


async def _neighbors_payload(graph: GraphStore, entity_id: str, hops: int = 1) -> dict:
    """构造邻域载荷（center_id/hops/nodes/edges），供端点复用。实体不存在时抛 404。"""
    center = await _run(graph.get_entity, entity_id)
    if not center:
        raise HTTPException(404, f"实体 {entity_id} 不存在")
    edges = await _run(graph.neighbors, entity_id, hops=hops)
    node_ids: set[str] = {entity_id}
    for e in edges:
        node_ids.add(e["head_entity_id"])
        node_ids.add(e["tail_entity_id"])
    nodes = await _run(graph.get_entities_by_ids, list(node_ids))
    return {
        "center_id": entity_id,
        "hops": hops,
        "nodes": [
            {"id": n["id"], "canonical_name": n["canonical_name"],
             "node_type": n["node_type"], "mention_count": n.get("mention_count", 0)}
            for n in nodes
        ],
        "edges": [
            {"id": e["id"], "head_entity_id": e["head_entity_id"],
             "tail_entity_id": e["tail_entity_id"], "relation_type": e["relation_type"],
             "confidence": e.get("confidence")}
            for e in edges
        ],
    }


@router.get("/graph/entities/{entity_id}/neighbors")
async def get_entity_neighbors(
    entity_id: str, request: Request, hops: int = Query(1, ge=1, le=3),
    domain: str = _DEFAULT_DOMAIN,
) -> dict:
    """邻域多跳：返回边 + 涉及的对象（节点），供前端力导向图直接渲染。"""
    _, graph = _stores(request, domain)
    return await _neighbors_payload(graph, entity_id, hops)


@router.post("/graph/entities/merge")
async def merge_entities_route(body: MergeEntitiesRequest, request: Request) -> dict:
    """合并实体：被并实体提及改指主实体 → scoped 重算受影响边。返回主实体新邻域。"""
    domain = body.domain or _DEFAULT_DOMAIN
    _, graph = _stores(request, domain)
    affected = await _run(graph.merge_entities, domain, body.primary_id, body.drop_ids)
    edges = await _run(
        scoped_recompute, request.app.state.domain_pools.sync_pool(domain), domain, affected
    )
    neighbors = await _neighbors_payload(graph, body.primary_id, hops=1)
    return {"primary_id": body.primary_id, "recomputed_edges": edges,
            "affected": affected, "neighbors": neighbors}


@router.post("/graph/entities/{entity_id}/retype")
async def retype_entity_route(entity_id: str, body: RetypeEntityRequest, request: Request) -> dict:
    """改类型：撞名自动并入 → scoped 重算。返回受影响实体新邻域。"""
    domain = body.domain or _DEFAULT_DOMAIN
    _, graph = _stores(request, domain)
    affected = await _run(graph.retype_entity, domain, entity_id, body.new_type)
    edges = await _run(
        scoped_recompute, request.app.state.domain_pools.sync_pool(domain), domain, affected
    )
    primary = affected[0] if affected else entity_id
    neighbors = await _neighbors_payload(graph, primary, hops=1)
    return {"entity_id": entity_id, "primary_id": primary,
            "recomputed_edges": edges, "neighbors": neighbors}


@router.delete("/graph/entities/{entity_id}")
async def delete_entity_route(entity_id: str, request: Request,
                              domain: str = _DEFAULT_DOMAIN) -> dict:
    """删除实体：删实体+提及+相连边（纯减法，无需重算）。"""
    _, graph = _stores(request, domain)
    await _run(graph.delete_entity, domain, entity_id)
    return {"entity_id": entity_id, "deleted": True}


@router.get("/graph/evidence/{target_id}")
async def get_target_evidence(
    target_id: str, request: Request, domain: str = _DEFAULT_DOMAIN
) -> dict:
    """出处回链：某对象/边/mention 的全部证据（带原文片段引用）。"""
    _, graph = _stores(request, domain)
    rows = await _run(graph.evidence_for_target, target_id)
    return {"target_id": target_id, "items": [dict(r) for r in rows]}
