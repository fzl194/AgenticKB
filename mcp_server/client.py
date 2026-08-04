"""HTTP client for the knowledge base backend.

Routes each search to the paradigm bound to its domain, falling back to the legacy pipeline when
the domain has no binding. See ``docs/mcp-paradigm-routing-design.md``.

Not a pure passthrough any more: the two backends return different envelopes, so responses are
normalized to one shape before they reach the agent (:func:`_normalize_paradigm_body`).
"""

from __future__ import annotations

import logging
import os
import time

import httpx

from mcp_server.schemas import (
    HealthResult,
    SearchInput,
)

logger = logging.getLogger(__name__)

BACKEND_URL = os.environ.get("SERVING_URL", "http://121.89.90.178:8081").rstrip("/")
HEALTH_TIMEOUT = float(os.environ.get("HEALTH_TIMEOUT", "10.0"))
SEARCH_TIMEOUT = float(os.environ.get("SEARCH_TIMEOUT", "120.0"))

# The resolve lookup is a single indexed row in the control DB, over localhost in the deployed
# container. Kept short on purpose: it must never be the reason a search times out — if it is slow
# or unreachable we want to give up quickly and serve the query on the legacy pipeline.
RESOLVE_TIMEOUT = float(os.environ.get("RESOLVE_TIMEOUT", "5.0"))

_FALSEY = {"0", "false", "no", "off", ""}

#: Kill switch back to plain passthrough, mirroring MINING_RUN_SUBMISSION_ENGINE=legacy on the
#: mining side. The other way to roll back needs no restart at all: clear the domain's
#: is_default binding and the next call resolves to nothing and falls back on its own.
PARADIGM_ROUTING = os.environ.get("MCP_PARADIGM_ROUTING", "1").strip().lower() not in _FALSEY

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


def search_knowledge(inp: SearchInput) -> dict:
    """Search the domain's knowledge, via its bound paradigm when it has one.

    Always returns the same envelope regardless of which backend served it; ``_retrieval`` says
    which one did.
    """
    ignored = _ignored_args(inp)
    target = _resolve_paradigm(inp.domain) if PARADIGM_ROUTING else None
    if target is not None:
        return _search_via_paradigm(target, inp, ignored)
    return _search_via_legacy(inp, ignored)


# ── paradigm routing ─────────────────────────────────────────────────────


def _resolve_paradigm(domain: str) -> dict | None:
    """Which paradigm serves this domain, or None to use the legacy pipeline.

    Deliberately uncached. The lookup is one indexed row over localhost, negligible next to a
    120s search budget, and paying it per call is what makes "publish, and MCP uses it on the very
    next query" true without having to explain a staleness window.

    Never raises: if the lookup fails the search still runs, just on the legacy pipeline. It does
    warn, though — a silent degrade is how ``LlmClient.submit_task`` returning None turned into a
    whole pipeline quietly doing nothing.
    """
    try:
        resp = _client.get(
            f"{BACKEND_URL}/api/v1/paradigm/resolve",
            params={"domain": domain},
            timeout=RESOLVE_TIMEOUT,
        )
        if resp.status_code != 200:
            logger.warning(
                "paradigm resolve for domain=%r returned HTTP %d; using the legacy pipeline",
                domain,
                resp.status_code,
            )
            return None
        data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning(
            "paradigm resolve for domain=%r failed (%s); using the legacy pipeline", domain, exc
        )
        return None

    if not data.get("bound"):
        return None
    if not data.get("paradigmId"):
        logger.warning("paradigm resolve for domain=%r said bound but named no paradigm", domain)
        return None
    return data


def _search_via_paradigm(target: dict, inp: SearchInput, ignored: list[str]) -> dict:
    """POST /api/v1/paradigm/{id}/search.

    An execution failure here is NOT retried on the legacy pipeline. Falling back would hide a
    broken bound paradigm indefinitely and silently answer from an engine the operator did not
    configure. Only a failure to *resolve* falls back — there, no choice has been made yet.
    """
    paradigm_id = target["paradigmId"]
    payload = {"query": inp.query, "domain": inp.domain, "debug": inp.debug}

    try:
        resp = _client.post(
            f"{BACKEND_URL}/api/v1/paradigm/{paradigm_id}/search",
            json=payload,
            timeout=SEARCH_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        logger.warning("paradigm search failed (%s): %s", paradigm_id, exc)
        return {"error": str(exc), "_retrieval": _meta(target, ignored)}

    if resp.status_code != 200:
        logger.warning(
            "paradigm search (%s) returned HTTP %d for query=%r",
            paradigm_id,
            resp.status_code,
            inp.query[:80],
        )
        return {
            "error": f"HTTP {resp.status_code}",
            "raw": resp.text[:500],
            "_retrieval": _meta(target, ignored),
        }

    try:
        body = resp.json()
    except ValueError as exc:
        logger.warning("paradigm search (%s) returned non-JSON: %s", paradigm_id, exc)
        return {"error": "invalid_json_response", "_retrieval": _meta(target, ignored)}

    return _normalize_paradigm_body(body, target, ignored)


def _search_via_legacy(inp: SearchInput, ignored: list[str]) -> dict:
    """POST /api/v1/search — the original passthrough, response shape untouched."""
    payload: dict = {"query": inp.query, "domain": inp.domain, "debug": inp.debug}
    if inp.scope:
        payload["scope"] = inp.scope
    if inp.entities:
        payload["entities"] = [e.model_dump() for e in inp.entities]

    try:
        resp = _client.post(f"{BACKEND_URL}/api/v1/search", json=payload, timeout=SEARCH_TIMEOUT)
        if resp.status_code != 200:
            logger.warning(
                "search returned HTTP %d for query=%r", resp.status_code, inp.query[:80]
            )
            return {"error": f"HTTP {resp.status_code}", "raw": resp.text[:500]}
        body = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("search failed: %s", exc)
        return {"error": str(exc)}

    if isinstance(body, dict):
        body["_retrieval"] = _meta(None, ignored)
    return body


# ── response normalization ───────────────────────────────────────────────

#: contextPack key → the key /api/v1/search uses. Only evidenceGroups actually differs: the legacy
#: controller hand-maps that one to snake_case while every other field, and everything nested
#: inside items/sources/relations, is serialized from the same records by the same Jackson
#: defaults. Listed in full anyway so the mapping is inspectable rather than inferred.
_PACK_KEYS = {
    "query": "query",
    "items": "items",
    "relations": "relations",
    "sources": "sources",
    "evidenceGroups": "evidence_groups",
    "issues": "issues",
    "suggestions": "suggestions",
}

_EMPTY_LIST_KEYS = ("items", "relations", "sources", "evidence_groups", "issues", "suggestions")


def _normalize_paradigm_body(body: dict, target: dict, ignored: list[str]) -> dict:
    """Flatten ``{"contextPack": {...}}`` into the shape /api/v1/search returns.

    Without this the agent would see two different envelopes depending on whether the domain
    happened to be bound — which is exactly the kind of difference nobody notices until a
    downstream consumer breaks on the domain that got configured last.
    """
    meta = _meta(target, ignored)
    pack = body.get("contextPack")

    if pack is None:
        # Binding rejects collect-terminated paradigms (paradigm_not_servable), so reaching here
        # means the binding predates that check or the row was edited directly. Pass the payload
        # through rather than inventing an empty pack, and make the anomaly visible.
        if "candidates" in body:
            logger.warning(
                "paradigm %s returned candidates, not a contextPack — it should not have been "
                "bindable; check its output operator",
                target.get("paradigmId"),
            )
            meta["output"] = "candidates"
            out = {"candidates": body["candidates"]}
        else:
            logger.warning("paradigm %s returned no contextPack", target.get("paradigmId"))
            out = {"error": "empty_paradigm_result"}
        out["_retrieval"] = meta
        return out

    out: dict = {}
    for src, dst in _PACK_KEYS.items():
        if src in pack:
            out[dst] = pack[src]
    for key in _EMPTY_LIST_KEYS:
        out.setdefault(key, [])

    if pack.get("debug"):
        out["debug"] = pack["debug"]
    # debug=true also puts the per-node trace at the top level of the paradigm response; keep it
    # under _retrieval so it cannot collide with the legacy pipeline's own "debug" payload.
    if body.get("trace"):
        meta["trace"] = body["trace"]

    out["_retrieval"] = meta
    return out


def _meta(target: dict | None, ignored: list[str]) -> dict:
    """The ``_retrieval`` block: which engine answered, and what the caller sent that we dropped."""
    if target is None:
        meta: dict = {"engine": "legacy"}
    else:
        meta = {
            "engine": "paradigm",
            "paradigm_id": target.get("paradigmId"),
            "name": target.get("name"),
            "version": target.get("version"),
        }
    if ignored:
        meta["ignored_args"] = ignored
    return meta


def _ignored_args(inp: SearchInput) -> list[str]:
    """Tool arguments the backend will not act on.

    ``scope`` and ``entities`` are accepted by /api/v1/search and then never read: SearchService
    consumes only query/domain/channel/debug/kbIds, and query understanding is handed the query
    string alone. (The ``query.scope()``/``query.entities()`` the retrievers use belong to
    QueryUnderstanding, which derives them itself.) They are reported rather than removed so
    existing callers keep working — but reported, not swallowed.
    """
    ignored = []
    if inp.scope:
        ignored.append("scope")
    if inp.entities:
        ignored.append("entities")
    if ignored:
        logger.warning(
            "ignoring tool argument(s) %s — not consumed by any retrieval path", ", ".join(ignored)
        )
    return ignored
