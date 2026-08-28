"""HTTP client for the knowledge base backend.

阶段 A（批次5）最终形态路由：库为中心的四层解析（显式范式 > 库级绑定 > 领域默认 >
官方默认），无 legacy 回落——resolve 无果直接报"该域未配置检索范式"。所有调用携带
用户身份（X-KB-User），授权按密钥用户实时判定（16 号方案）。

Not a pure passthrough: the paradigm backend's envelope is normalized to the search shape
before it reaches the agent (:func:`_normalize_paradigm_body`).
"""

from __future__ import annotations

import logging
import os
import time
from urllib.parse import quote

import httpx

from mcp_server.identity import Identity
from mcp_server.schemas import (
    FullTextInput,
    HealthResult,
    SearchInput,
)

logger = logging.getLogger(__name__)

BACKEND_URL = os.environ.get("SERVING_URL", "http://121.89.90.178:8081").rstrip("/")
HEALTH_TIMEOUT = float(os.environ.get("HEALTH_TIMEOUT", "10.0"))
SEARCH_TIMEOUT = float(os.environ.get("SEARCH_TIMEOUT", "120.0"))

# The resolve lookup is a single indexed row in the control DB, over localhost in the deployed
# container. Kept short on purpose: it must never be the reason a search times out — if it is slow
# or unreachable we want to give up quickly and report the configuration gap.
RESOLVE_TIMEOUT = float(os.environ.get("RESOLVE_TIMEOUT", "5.0"))

FULLTEXT_TIMEOUT = float(os.environ.get("FULLTEXT_TIMEOUT", "30.0"))

#: How long a fetched paradigm catalog stays fresh.
#:
#: Cached, unlike :func:`_resolve_paradigm`, and the difference is deliberate: resolve decides
#: *which engine answers*, so a stale answer is a wrong answer; the catalog is a hint attached to
#: the response, so a stale one costs an agent half a minute of not knowing a new paradigm exists.
#: What is never served stale is an explicit selection — :func:`_select_paradigm` forces a refresh
#: before it will call anything unknown, so "publish and it is usable" holds with no TTL caveat.
CATALOG_TTL = float(os.environ.get("MCP_CATALOG_TTL", "30.0"))
CATALOG_TIMEOUT = float(os.environ.get("MCP_CATALOG_TIMEOUT", "5.0"))

#: Paradigm ids are minted as ``"pd-" + uuid4[:8]`` (ParadigmService.create), which is what lets a
#: caller-supplied string be recognised as an id without asking the backend — the one thing that
#: still works when the catalog is unreachable.
_PARADIGM_ID_PREFIX = "pd-"

_catalog_cache: dict = {"fetched_at": 0.0, "entries": None}

#: Serving's base URL *as the reader of the answer can reach it*, used to build download links for
#: the original files. Left empty by default on purpose: ``BACKEND_URL`` is localhost inside the
#: deployed container, so deriving links from it would hand out URLs that resolve to nothing on the
#: caller's machine — worse than no link, because it looks like it should work.
RAW_FILE_BASE_URL = os.environ.get("MCP_RAW_FILE_BASE_URL", "").strip().rstrip("/")

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
    """Search the domain's knowledge — 库为中心四层路由（16 号方案 §2）。

    显式范式 > 库级绑定一致 > 领域默认 > 官方默认；全无 → 明确报错（不回落 legacy）。
    Always returns the same envelope; ``_retrieval`` says which paradigm served it,
    how it was picked (selected_by), and whether it degraded from a higher rung.
    """
    ignored = _ignored_args(inp)

    if inp.paradigm and inp.paradigm.strip():
        try:
            target = _select_paradigm(inp.paradigm.strip(), inp.domain)
        except _SelectionError as err:
            return err.envelope(inp.domain)
        return _search_via_paradigm(target, inp, identity, kb_ids, ignored, "explicit")

    resolved = _resolve_paradigm(inp.domain, kb_ids)
    if resolved is None:
        return {
            "error": "no_paradigm_configured",
            "message": (
                f"知识域 {inp.domain!r} 没有可用的检索范式（库级/领域/官方默认均未配置）。"
                "请联系管理员在范式管理中绑定默认范式。"
            ),
            "_retrieval": _meta(None, ignored, inp.domain, selected_by="unresolved"),
        }
    target, source = resolved
    return _search_via_paradigm(target, inp, identity, kb_ids, ignored, source)


def get_segment_fulltext(inp: FullTextInput, identity: Identity) -> dict:
    """POST /api/v1/segments/fulltext — the uncompressed text behind search-result ids.

    Routed through the *same* paradigm the search used, by resolving the domain's binding again.
    That is the point: a paradigm's ``scope_resolve`` can name knowledge bases, and looking the ids
    up against a different corpus than the one that produced them would report them missing.

    An explicit ``paradigm_id`` is honoured as given and skips resolution entirely. It has to be:
    once a search can be pointed at a paradigm by name, re-resolving the domain here would look up
    the *default* one instead — a different set of knowledge bases — and every id would come back
    ``found: false``. That reason string means "re-mined, or the library is no longer visible", so
    an agent would explain the miss with it and never suspect the scope was simply wrong.

    A resolve failure degrades differently from :func:`search_knowledge`. There, falling back
    means answering from the legacy pipeline; here it means looking in the domain's active release
    instead of the paradigm's knowledge bases. KB content never reaches a release (KB mining runs
    ``publish=false``), so the failure mode is "found nothing", never "found something it should
    not have" — the safe direction, but noisy enough to warrant the warning below.
    """
    if not inp.refs:
        return {"error": "refs_required", "items": []}

    payload: dict = {
        "domain": inp.domain,
        "refs": [{"type": r.type, "id": r.id} for r in inp.refs],
        "granularity": inp.granularity,
        "windowRadius": inp.window_radius,
    }

    if inp.paradigm_id and inp.paradigm_id.strip():
        # Not validated against the catalog: it came from a search this same process just served,
        # and serving authorizes it again anyway. A round trip here would only add a way to fail.
        target = {"paradigmId": inp.paradigm_id.strip()}
        payload["paradigm_id"] = target["paradigmId"]
    else:
        resolved = _resolve_paradigm(inp.domain, None)
        if resolved is not None:
            target, _source = resolved
            payload["paradigm_id"] = target["paradigmId"]
        else:
            target = None
            logger.info(
                "fulltext for domain=%r resolved no paradigm; looking in the active release. "
                "If the search that produced these ids ran on a bound paradigm, they will not be "
                "found",
                inp.domain,
            )

    try:
        resp = _client.post(
            f"{BACKEND_URL}/api/v1/segments/fulltext",
            json=payload,
            headers=_identity_headers(identity),
            timeout=FULLTEXT_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        logger.warning("fulltext lookup failed: %s", exc)
        return {"error": str(exc), "_retrieval": _meta(target, [])}

    if resp.status_code != 200:
        logger.warning("fulltext lookup returned HTTP %d", resp.status_code)
        return {
            "error": f"HTTP {resp.status_code}",
            "raw": resp.text[:500],
            "_retrieval": _meta(target, []),
        }

    try:
        body = resp.json()
    except ValueError as exc:
        logger.warning("fulltext lookup returned non-JSON: %s", exc)
        return {"error": "invalid_json_response", "_retrieval": _meta(target, [])}

    if isinstance(body, dict):
        _attach_raw_file_urls(body, inp.domain)
        body["_retrieval"] = _meta(target, [])
    return body


def _attach_raw_file_urls(body: dict, domain: str) -> None:
    """Add a download link to segments whose document still has its original file.

    Only when :data:`RAW_FILE_BASE_URL` is configured — see the note there. The file itself is not
    exposed as a tool: handing an agent a 200-page PDF costs a great deal of context and answers
    nothing the segment text did not, so the link is for a person or a UI to follow.
    """
    if not RAW_FILE_BASE_URL:
        return
    # domain and the document id both arrive from the tool caller, so they are quoted rather than
    # interpolated raw — an unescaped value would silently produce a link to a different request.
    domain_q = quote(domain, safe="")
    for item in body.get("items", []):
        for segment in item.get("segments", []):
            if segment.get("hasRawFile") and segment.get("documentId"):
                doc_q = quote(str(segment["documentId"]), safe="")
                segment["rawFileUrl"] = (
                    f"{RAW_FILE_BASE_URL}/api/v1/documents/{doc_q}/raw?domain={domain_q}"
                )


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
        hint_domain = None if self.code == "catalog_unavailable" else domain
        out["_retrieval"] = _meta(None, [], hint_domain, selected_by="rejected")
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
    """Entries usable in this domain: its own, plus the domain-agnostic ones.

    Filtering here rather than server-side keeps one cache serving every domain. Offering an agent
    a paradigm bound to a different domain would only earn it a ``paradigm_domain_mismatch`` — a
    choice that cannot work is worse than no choice at all.
    """
    if domain is None:
        return list(entries)
    return [e for e in entries if not e.get("domain") or e.get("domain") == domain]


def _select_paradigm(named: str, domain: str) -> dict:
    """Turn a caller-supplied name or id into a resolve-shaped target.

    Accepts either because the two have different origins: the *name* is what an agent just read
    out of ``available_paradigms`` (and is unique — ``operator_paradigm.name`` carries a UNIQUE
    constraint), while the *id* is what a script would hold. Matching is case- and
    whitespace-insensitive because both arrive by copy-paste.

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
        )

    # Present but not offered (unpublished, not servable, or scoped to knowledge bases an
    # anonymous caller cannot read) is reported the same as absent. The catalog withholds that
    # distinction from anonymous callers on purpose, and inventing it here would leak it back.
    offered = _for_domain(entries, domain)
    available = ", ".join(e.get("name") or e["id"] for e in offered) or "（无）"
    raise _SelectionError(
        "unknown_paradigm",
        f"知识域 {domain!r} 下没有可用的范式 {named!r}。当前可用：{available}",
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
    """四层解析（库级 > 领域 > 官方默认）：返回 (target, source) 或 None（全无）。

    kb_ids 非空时 serving 会尝试 library 层（目标库绑定一致才生效）；返回的 source 供
    `_retrieval.selected_by` 与 degraded 留痕。Deliberately uncached——resolve 决定"哪条
    管线回答"，陈旧的答案就是错误的答案。
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
    if data.get("degraded"):
        source = f"{source}(degraded_from_{data.get('degradedFrom')})"
    return data, source


def _search_via_paradigm(
    target: dict,
    inp: SearchInput,
    identity: Identity,
    kb_ids: list[str],
    ignored: list[str],
    selected_by: str,
) -> dict:
    """POST /api/v1/paradigm/{id}/search（带用户身份与请求级库范围）。

    An execution failure here is NOT retried anywhere else. Falling back would hide a broken
    bound paradigm indefinitely and silently answer from an engine the operator did not choose.
    """
    paradigm_id = target["paradigmId"]
    payload: dict = {"query": inp.query, "domain": inp.domain, "debug": inp.debug}
    if kb_ids:
        payload["kbIds"] = kb_ids

    try:
        resp = _client.post(
            f"{BACKEND_URL}/api/v1/paradigm/{paradigm_id}/search",
            json=payload,
            headers=_identity_headers(identity),
            timeout=SEARCH_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        logger.warning("paradigm search failed (%s): %s", paradigm_id, exc)
        return {"error": str(exc),
                "_retrieval": _meta(target, ignored, inp.domain, selected_by=selected_by)}

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
            "_retrieval": _meta(target, ignored, inp.domain, selected_by=selected_by),
        }

    try:
        body = resp.json()
    except ValueError as exc:
        logger.warning("paradigm search (%s) returned non-JSON: %s", paradigm_id, exc)
        return {"error": "invalid_json_response",
                "_retrieval": _meta(target, ignored, inp.domain, selected_by=selected_by)}

    return _normalize_paradigm_body(body, target, ignored, inp.domain, selected_by)


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


def _normalize_paradigm_body(
    body: dict, target: dict, ignored: list[str], domain: str, selected_by: str
) -> dict:
    """Flatten ``{"contextPack": {...}}`` into the shape /api/v1/search returns.

    Without this the agent would see two different envelopes depending on whether the domain
    happened to be bound — which is exactly the kind of difference nobody notices until a
    downstream consumer breaks on the domain that got configured last.
    """
    meta = _meta(target, ignored, domain, selected_by=selected_by)
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


def _meta(
    target: dict | None,
    ignored: list[str],
    domain: str | None = None,
    *,
    selected_by: str | None = None,
) -> dict:
    """The ``_retrieval`` block: which engine answered, how it was chosen, what else was available,
    and what the caller sent that we dropped.

    ``available_paradigms`` is what makes discovery a by-product instead of a tool: an agent that
    just calls ``search_knowledge`` learns from the answer that other paradigms exist, and can name
    one on its next call. It rides on *every* path including errors — a domain with no active
    release fails the very first unnamed call, and without the list attached there the agent would
    have nothing to correct towards.
    """
    if target is None:
        meta: dict = {"engine": "none"}
    else:
        meta = {
            "engine": "paradigm",
            "paradigm_id": target.get("paradigmId"),
            "name": target.get("name"),
            "version": target.get("version"),
        }
    if selected_by:
        # Not just for debugging: the distribution of this field is what tells us later whether the
        # choice belongs to the agent at all, or should move server-side.
        meta["selected_by"] = selected_by
    if domain:
        offered = _offered_summary(domain)
        if offered is not None:
            meta["available_paradigms"] = offered
    if ignored:
        meta["ignored_args"] = ignored
    return meta


def _offered_summary(domain: str) -> list[dict] | None:
    """Name + description of the paradigms usable in this domain, or None if unknown.

    Omitted rather than empty on failure: a hint that could not be fetched must be
    indistinguishable from one that was never offered, or an agent would read "no paradigms" into
    what was really a timeout. Deliberately just name and description — an id an agent cannot read
    is noise, and the name is what it passes back.
    """
    entries = _fetch_catalog()
    if entries is None:
        return None
    return [
        {"name": e.get("name") or e["id"], "description": e.get("description") or ""}
        for e in _for_domain(entries, domain)
    ]


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
