"""HTTP client for the knowledge base backend.

批次8 R8（25 号 §5.3/§7.1）：``search_knowledge`` 响应切<b>纯 EvidenceResponse</b>
（``query/evidence/has_more``），删除 ``_retrieval``、ContextPack 归一化与内部 id 回传
协议。路由仍为库为中心解析（显式范式 > 库级绑定 > 官方默认），无 legacy 回落——
resolve 无果直接报"该域未配置检索范式"。所有调用携带用户身份（X-KB-User），授权按
密钥用户实时判定；显式 within/filters/top_k/expansion 按 §7.1 语义透传（hard filter）。
"""

from __future__ import annotations

import logging
import os
import time

import httpx

from mcp_server.identity import Identity
from mcp_server.schemas import (
    HealthResult,
    SearchInput,
)

logger = logging.getLogger(__name__)

BACKEND_URL = os.environ.get("SERVING_URL", "http://127.0.0.1:8081").rstrip("/")
HEALTH_TIMEOUT = float(os.environ.get("HEALTH_TIMEOUT", "10.0"))
SEARCH_TIMEOUT = float(os.environ.get("SEARCH_TIMEOUT", "120.0"))

# The resolve lookup is a single indexed row in the control DB, over localhost in the deployed
# container. Kept short on purpose: it must never be the reason a search times out — if it is slow
# or unreachable we want to give up quickly and report the configuration gap.
RESOLVE_TIMEOUT = float(os.environ.get("RESOLVE_TIMEOUT", "5.0"))

#: How long a fetched paradigm catalog stays fresh.
#:
#: Cached, unlike :func:`_resolve_paradigm`, and the difference is deliberate: resolve decides
#: *which engine answers*, so a stale answer is a wrong answer; the catalog is a hint attached to
#: error responses, so a stale one costs an agent half a minute of not knowing a new paradigm
#: exists. What is never served stale is an explicit selection — :func:`_select_paradigm` forces
#: a refresh before it will call anything unknown, so "publish and it is usable" holds with no
#: TTL caveat.
CATALOG_TTL = float(os.environ.get("MCP_CATALOG_TTL", "30.0"))
CATALOG_TIMEOUT = float(os.environ.get("MCP_CATALOG_TIMEOUT", "5.0"))

#: Paradigm ids are minted as ``"pd-" + uuid4[:8]`` (ParadigmService.create), which is what lets
#: a caller-supplied string be recognised as an id without asking the backend — the one thing that
#: still works when the catalog is unreachable.
_PARADIGM_ID_PREFIX = "pd-"

_catalog_cache: dict = {"fetched_at": 0.0, "entries": None}

_client = httpx.Client(trust_env=False)


def health_check() -> HealthResult:
    """GET /health — returns structured result, never raises."""
    start = time.monotonic()
    try:
        resp = _client.get(f"{BACKEND_URL}/health", timeout=HEALTH_TIMEOUT)
        latency_ms = (time.monotonic() - start) * 1000
        if resp.status_code == 200:
            data = resp.json()
            return HealthResult(
                available=True,
                status=data.get("status", "ok"),
                version=data.get("version", ""),
                latency_ms=round(latency_ms, 1),
            )
        logger.warning("health_check returned HTTP %d", resp.status_code)
        return HealthResult(
            available=False,
            status="error",
            latency_ms=round(latency_ms, 1),
            error=f"HTTP {resp.status_code}",
        )
    except httpx.HTTPError as exc:
        latency_ms = (time.monotonic() - start) * 1000
        logger.warning("health_check failed: %s", exc)
        return HealthResult(
            available=False,
            status="unreachable",
            latency_ms=round(latency_ms, 1),
            error=str(exc),
        )


def search_knowledge(inp: SearchInput, identity: Identity, kb_ids: list[str]) -> dict:
    """Search the domain's knowledge — 库为中心路由（16 号方案 §2）。

    显式范式 > 库级绑定一致 > 官方默认；全无 → 明确报错（不回落 legacy）。
    成功响应是<b>纯 EvidenceResponse</b>（query/evidence/has_more，25 号 §5.3）——
    不附加任何内部检索元数据；``available_paradigms`` 提示只出现在错误响应上
    （Agent 有东西可以纠正方向）。
    """
    if inp.paradigm and inp.paradigm.strip():
        try:
            target = _select_paradigm(inp.paradigm.strip(), inp.domain)
        except _SelectionError as err:
            return err.envelope(inp.domain)
        return _search_via_paradigm(target, inp, identity, kb_ids)

    resolved = _resolve_paradigm(inp.domain, kb_ids)
    if resolved is None:
        out: dict = {
            "error": "no_paradigm_configured",
            "message": (
                f"知识域 {inp.domain!r} 没有可用的检索范式（库级/官方默认均未配置）。"
                "请联系管理员在范式管理中绑定默认范式。"
            ),
        }
        offered = _offered_or_omit(inp.domain)
        if offered is not None:
            out["available_paradigms"] = offered
        return out
    target, _source = resolved
    return _search_via_paradigm(target, inp, identity, kb_ids)


# ── paradigm catalog ─────────────────────────────────────────────────────


class _SelectionError(Exception):
    """An explicitly named paradigm could not be turned into something callable.

    Carries the envelope to return. Never falls back to another engine: the caller asserted a
    choice, and quietly answering from a different one would look identical to having honoured it.
    """

    def __init__(self, code: str, message: str, domain: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self._domain = domain

    def envelope(self, domain: str) -> dict:
        out: dict = {"error": self.code, "message": self.message}
        # The available-paradigms hint comes from the catalog. For `catalog_unavailable` that is
        # precisely what just failed, and asking again here would make the agent wait a second
        # timeout for an answer the first attempt already gave us.
        hint_domain = None if self.code == "catalog_unavailable" else (self._domain or domain)
        if hint_domain is not None:
            offered = _offered_or_omit(hint_domain)
            if offered is not None:
                out["available_paradigms"] = offered
        return out


def _fetch_catalog(*, force: bool = False) -> list[dict] | None:
    """Every paradigm this backend exposes, or None when the catalog is unreachable.

    None and ``[]`` mean different things and callers must not conflate them: ``[]`` is "this
    backend has no usable paradigm", None is "we could not find out". The first is an answer, the
    second is a degradation.

    Unfiltered on purpose — one cache serves every domain, and :func:`_select_paradigm` needs the
    full list to tell "no such paradigm" from "that one belongs to another domain".

    Never raises: a failure here must never be the reason a search fails.
    """
    now = time.monotonic()
    cached = _catalog_cache["entries"]
    if not force and cached is not None and (now - _catalog_cache["fetched_at"]) < CATALOG_TTL:
        return cached

    try:
        resp = _client.get(
            f"{BACKEND_URL}/api/v1/paradigm/mcp-catalog", timeout=CATALOG_TIMEOUT
        )
        if resp.status_code != 200:
            logger.warning(
                "paradigm catalog returned HTTP %d; continuing without it", resp.status_code
            )
            return None
        # Normalized once, here, so nothing downstream has to guard: an entry with no usable id
        # cannot be selected or executed, and letting one through would turn a malformed response
        # into a KeyError that fails the search — which is exactly what a hint must never do.
        entries = [
            e for e in (resp.json().get("paradigms") or [])
            if isinstance(e, dict) and isinstance(e.get("id"), str) and e["id"]
        ]
    except (httpx.HTTPError, ValueError, AttributeError, TypeError) as exc:
        logger.warning("paradigm catalog fetch failed (%s); continuing without it", exc)
        return None

    _catalog_cache["entries"] = entries
    _catalog_cache["fetched_at"] = now
    return entries


def _for_domain(entries: list[dict], domain: str | None) -> list[dict]:
    """批次6：域绑定退役——范式跨域通用，目录不再按域过滤（domain 参数兼容保留）。"""
    return list(entries)


def _select_paradigm(named: str, domain: str) -> dict:
    """Turn a caller-supplied name or id into a resolve-shaped target.

    Accepts either because the two have different origins: the *name* is what an agent just read
    out of an error hint's ``available_paradigms`` (and is unique — ``operator_paradigm.name``
    carries a UNIQUE constraint), while the *id* is what a script would hold. Matching is case-
    and whitespace-insensitive because both arrive by copy-paste.

    :raises _SelectionError: with a distinct code per cause; never falls back to another engine.
    """
    entries = _fetch_catalog()
    hit = _match(_for_domain(entries, domain), named) if entries is not None else None

    # A paradigm published seconds ago is not in a cache up to CATALOG_TTL old. Refreshing before
    # declaring it unknown is what makes "publish and it is immediately usable" true without
    # having to explain a staleness window to whoever just published it.
    if hit is None and entries is not None:
        entries = _fetch_catalog(force=True)
        hit = _match(_for_domain(entries, domain), named) if entries is not None else None

    if hit is not None:
        return {
            "paradigmId": hit["id"],
            "name": hit.get("name"),
            "version": hit.get("version"),
        }

    if entries is None:
        # Catalog unreachable. An id needs no lookup, so honour it; a name cannot be resolved
        # without the catalog, and guessing is not an option.
        if named.startswith(_PARADIGM_ID_PREFIX):
            logger.warning(
                "paradigm catalog unavailable; passing id %r through unverified", named
            )
            return {"paradigmId": named, "name": None, "version": None}
        raise _SelectionError(
            "catalog_unavailable",
            f"无法确认范式 {named!r}：范式清单当前不可用。可改用范式 id（pd- 开头），"
            "或稍后重试；不指定 paradigm 则使用该知识域的默认范式。",
            domain,
        )

    # Exists, but belongs to another domain. Worth its own error: executing it here would fail
    # deep inside scope_resolve as kb_not_found (kb ids are unique per domain), which points at
    # knowledge-base permissions and reads nothing like the actual mistake.
    elsewhere = _match(entries, named)
    if elsewhere is not None:
        raise _SelectionError(
            "paradigm_domain_mismatch",
            f"范式 {named!r} 属于知识域 {elsewhere.get('domain')!r}，"
            f"但本次检索的知识域是 {domain!r}。请改用该知识域下的范式，或把 domain 改成前者。",
            domain,
        )

    # Present but not offered (unpublished, not servable, or scoped to knowledge bases an
    # anonymous caller cannot read) is reported the same as absent. The catalog withholds that
    # distinction from anonymous callers on purpose, and inventing it here would leak it back.
    offered = _for_domain(entries, domain)
    available = ", ".join(e.get("name") or e["id"] for e in offered) or "（无）"
    raise _SelectionError(
        "unknown_paradigm",
        f"知识域 {domain!r} 下没有可用的范式 {named!r}。当前可用：{available}",
        domain,
    )


def _match(entries: list[dict], named: str) -> dict | None:
    key = named.strip().casefold()
    for e in entries:
        # `id` is guaranteed a non-empty str by _fetch_catalog; `name` is not guaranteed at all.
        if e["id"].casefold() == key or str(e.get("name") or "").strip().casefold() == key:
            return e
    return None


# ── paradigm routing ─────────────────────────────────────────────────────


def _identity_headers(identity: Identity) -> dict[str, str]:
    """X-KB-User 透传：serving 按密钥用户实时授权（private 库对 owner/member 可见）。"""
    return {"X-KB-User": identity.username}


def _resolve_paradigm(domain: str, kb_ids: list[str] | None) -> tuple[dict, str] | None:
    """库级 > 官方默认 解析：返回 (target, source) 或 None（全无）。

    kb_ids 非空时 serving 会尝试 library 层（目标库绑定一致才生效）。Deliberately
    uncached——resolve 决定"哪条管线回答"，陈旧的答案就是错误的答案。
    """
    params: dict = {"domain": domain}
    if kb_ids:
        params["kbIds"] = ",".join(kb_ids)
    try:
        resp = _client.get(
            f"{BACKEND_URL}/api/v1/paradigm/resolve",
            params=params,
            timeout=RESOLVE_TIMEOUT,
        )
        if resp.status_code != 200:
            logger.warning(
                "paradigm resolve for domain=%r returned HTTP %d", domain, resp.status_code
            )
            return None
        data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("paradigm resolve for domain=%r failed (%s)", domain, exc)
        return None

    if not data.get("bound"):
        return None
    if not data.get("paradigmId"):
        logger.warning("paradigm resolve for domain=%r said bound but named no paradigm", domain)
        return None
    source = str(data.get("source") or "domain")
    return data, source


def _search_via_paradigm(
    target: dict,
    inp: SearchInput,
    identity: Identity,
    kb_ids: list[str],
) -> dict:
    """POST /api/v1/paradigm/{id}/search（带用户身份、请求级库范围与显式 hard filters）。

    An execution failure here is NOT retried anywhere else. Falling back would hide a broken
    bound paradigm indefinitely and silently answer from an engine the operator did not choose.

    成功时原样返回 serving 的 ``evidenceResponse``（§5.3 纯协议：query/evidence/has_more，
    无内部 id/score/rank）；非该形状的响应按异常暴露，不冒充证据列表。
    """
    paradigm_id = target["paradigmId"]
    payload: dict = {"query": inp.query, "domain": inp.domain, "debug": inp.debug}
    if kb_ids:
        payload["kbIds"] = kb_ids
    # §7.1：显式传入 = hard filter / 显式覆盖；未传不发键（serving 侧宽检索）
    if inp.within:
        payload["within"] = inp.within
    if inp.filters:
        payload["filters"] = inp.filters
    if inp.expansion:
        payload["expansion"] = inp.expansion
    if inp.top_k is not None:
        payload["top_k"] = inp.top_k

    try:
        resp = _client.post(
            f"{BACKEND_URL}/api/v1/paradigm/{paradigm_id}/search",
            json=payload,
            headers=_identity_headers(identity),
            timeout=SEARCH_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        logger.warning("paradigm search failed (%s): %s", paradigm_id, exc)
        return {"error": str(exc), "message": "检索服务暂不可用，请稍后重试。"}

    if resp.status_code != 200:
        logger.warning(
            "paradigm search (%s) returned HTTP %d for query=%r",
            paradigm_id,
            resp.status_code,
            inp.query[:80],
        )
        return {"error": f"HTTP {resp.status_code}", "raw": resp.text[:500]}

    try:
        body = resp.json()
    except ValueError as exc:
        logger.warning("paradigm search (%s) returned non-JSON: %s", paradigm_id, exc)
        return {"error": "invalid_json_response"}

    evidence = body.get("evidenceResponse") if isinstance(body, dict) else None
    if not isinstance(evidence, dict):
        # 不是 EvidenceResponse = 范式终点不对（发布质量缺陷），原样暴露不冒充
        logger.warning(
            "paradigm %s returned no evidenceResponse (keys=%s)",
            paradigm_id,
            sorted(body.keys()) if isinstance(body, dict) else type(body).__name__,
        )
        return {"error": "no_evidence_response"}
    # 27号审查修复：debug=true 时附带 serving 的诊断信息（算子执行留痕），
    # 不再丢弃——工具描述承诺的 diagnostics 必须兑现。非 debug 保持纯协议。
    if inp.debug and isinstance(body, dict):
        diagnostics = {
            k: v for k, v in body.items()
            if k != "evidenceResponse" and isinstance(v, (dict, list, str, int, float, bool))
        }
        if diagnostics:
            return {**evidence, "diagnostics": diagnostics}
    return evidence


# ── error-side advisory list ─────────────────────────────────────────────


def _offered_or_omit(domain: str) -> list[dict] | None:
    """Name + description of the paradigms usable in this domain, or None if unknown."""
    entries = _fetch_catalog()
    if entries is None:
        return None
    return [
        {"name": e.get("name") or e["id"], "description": e.get("description") or ""}
        for e in _for_domain(entries, domain)
    ]
