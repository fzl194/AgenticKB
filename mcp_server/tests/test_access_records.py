from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastmcp.exceptions import ToolError

from mcp_server import access_records as access_records_module
from mcp_server import client, server
from mcp_server.access_records import (
    REQUEST_PAYLOAD_MAX_BYTES,
    RESPONSE_PAYLOAD_MAX_BYTES,
    _UPLOAD_TICKETS,
    build_access_payload,
    complete_upload_ticket,
    current_access_call,
    get_access_record_metrics,
    register_upload_tickets,
    reset_access_record_metrics,
)
from mcp_server.identity import Identity


IDENTITY = Identity(
    username="alice",
    user_id="u-1",
    key_id="key-1",
    key_domain="cloud_core_network",
    open_kbs=({"id": "kb-1", "name": "Manual", "domain": "cloud_core_network"},),
)


def context(name: str, arguments: dict):
    return SimpleNamespace(message=SimpleNamespace(name=name, arguments=arguments))


@pytest.mark.parametrize(
    ("failure", "expected_status", "expected_code"),
    [
        (ToolError("bad argument"), "invalid", "invalid_tool_call"),
        (server.LedgerToolError(
            "forbidden", ledger_status="denied", error_code="out_of_scope",
        ), "denied", "out_of_scope"),
        (TimeoutError("slow"), "timeout", "tool_timeout"),
        (RuntimeError("boom"), "failed", "tool_failed"),
    ],
)
def test_exception_status_uses_stable_classification(
    failure, expected_status, expected_code,
) -> None:
    payload = build_access_payload(
        call_id="call-status",
        tool_name="get_knowledge",
        arguments={},
        identity=IDENTITY,
        result=None,
        failure=failure,
        duration_ms=1,
    )

    assert payload["status"] == expected_status
    assert payload["error_code"] == expected_code


@pytest.mark.parametrize(
    ("http_error", "expected_status"),
    [("HTTP 403", "denied"), ("HTTP 422", "invalid"),
     ("HTTP 504", "timeout"), ("HTTP 500", "failed")],
)
def test_result_http_status_is_classified(http_error, expected_status) -> None:
    payload = build_access_payload(
        call_id="call-http",
        tool_name="search_knowledge",
        arguments={"query": "q"},
        identity=IDENTITY,
        result={"error": http_error},
        failure=None,
        duration_ms=1,
    )

    assert payload["status"] == expected_status


@pytest.mark.asyncio
async def test_access_record_http_writes_have_bounded_concurrency(monkeypatch) -> None:
    active = 0
    peak = 0

    class _Response:
        def raise_for_status(self):
            return None

    class _Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, *_args, **_kwargs):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1
            return _Response()

    monkeypatch.setattr(access_records_module, "_internal_auth_secret", lambda: "secret")
    monkeypatch.setattr(access_records_module.httpx, "AsyncClient", _Client)

    await asyncio.gather(*(
        access_records_module.post_access_record({"id": str(index)})
        for index in range(24)
    ))

    assert 1 < peak <= access_records_module.ACCESS_RECORD_WRITE_CONCURRENCY


@pytest.mark.asyncio
async def test_search_tool_writes_one_safe_record_and_suppresses_java_duplicate(
    monkeypatch,
) -> None:
    records: list[dict] = []
    monkeypatch.setattr(server, "require_identity", lambda _headers: IDENTITY)
    monkeypatch.setattr(server, "_post_access_record", records.append)

    async def call_next(_context):
        headers = client._identity_headers(IDENTITY)
        assert headers["X-KB-Call-Source"] == "mcp"
        assert headers["X-KB-Access-Id"]
        return {"query": "q", "evidence": [{"ref": "ev_1"}], "has_more": False}

    result = await server.PersonalizationMiddleware().on_call_tool(
        context("search_knowledge", {"query": "q", "kb_names": ["Manual"]}),
        call_next,
    )

    assert result["evidence"][0]["ref"] == "ev_1"
    assert len(records) == 2
    assert records[0]["status"] == "pending"
    record = records[1]
    assert record["id"]
    assert record["domain"] == "cloud_core_network"
    assert record["actor_user_id"] == "u-1"
    assert record["actor_username"] == "alice"
    assert record["source"] == "mcp"
    assert record["operation"] == "search"
    assert record["tool_name"] == "search_knowledge"
    assert record["mcp_key_id"] == "key-1"
    assert record["kb_ids"] == ["kb-1"]
    assert record["query_text"] == "q"
    assert record["status"] == "success"
    assert record["result_count"] == 1
    assert "evidence" not in record["details_json"]
    assert "authorization" not in str(record).lower()


@pytest.mark.asyncio
async def test_search_no_result_and_backend_error_are_distinct(monkeypatch) -> None:
    records: list[dict] = []
    monkeypatch.setattr(server, "require_identity", lambda _headers: IDENTITY)
    monkeypatch.setattr(server, "_post_access_record", records.append)

    async def no_result(_context):
        return {"query": "q", "evidence": [], "has_more": False}

    await server.PersonalizationMiddleware().on_call_tool(
        context("search_knowledge", {"query": "q"}), no_result)
    assert records[-2]["status"] == "pending"
    assert records[-1]["status"] == "no_result"

    async def failed(_context):
        return {"error": "no_paradigm_configured", "message": "details"}

    await server.PersonalizationMiddleware().on_call_tool(
        context("search_knowledge", {"query": "q"}), failed)
    assert records[-1]["status"] == "failed"
    assert records[-1]["error_code"] == "no_paradigm_configured"
    assert records[-1]["details_json"] == {"terminal_owner": "mcp"}
    assert "message" not in records[-1]
    assert "details" not in records[-1].values()


@pytest.mark.asyncio
async def test_get_and_upload_use_read_and_pending_without_sensitive_arguments(
    monkeypatch,
) -> None:
    records: list[dict] = []
    monkeypatch.setattr(server, "require_identity", lambda _headers: IDENTITY)
    monkeypatch.setattr(server, "_post_access_record", records.append)

    async def read_result(_context):
        return {"view": "document_content", "segments": [{"content": "secret body"}]}

    await server.PersonalizationMiddleware().on_call_tool(
        context("get_knowledge", {"ref": "doc_secret"}), read_result)
    assert records[-2]["status"] == "pending"
    assert records[-1]["operation"] == "read"
    assert records[-1]["status"] == "success"
    assert records[-1]["details_json"] == {
        "action": "document_content", "ref_type": "document",
        "terminal_owner": "mcp",
    }
    assert records[-1]["payload"]["request_json"]["ref"] == "doc_secret"
    assert "secret body" not in str(records[-1]["payload"])

    async def upload_result(_context):
        return {"uploads": [{"upload_url": "https://example/secret-ticket"}]}

    await server.PersonalizationMiddleware().on_call_tool(
        context("manage_files", {
            "action": "upload",
            "kb_name": "Manual", "filenames": ["customer-secret.pdf"],
        }),
        upload_result,
    )
    assert records[-2]["status"] == "pending"
    assert records[-1]["operation"] == "upload"
    assert records[-1]["status"] == "pending"
    assert records[-1]["kb_ids"] == ["kb-1"]
    assert records[-1]["details_json"] == {"file_count": 1, "action": "upload"}
    assert records[-1]["payload"]["request_json"]["filenames"] == [
        "customer-secret.pdf",
    ]
    assert "secret-ticket" not in str(records[-1]["payload"])

    # 58号§5：manage_files 的 replace 动作在账本里独立分类（operation=replace）
    async def replace_result(_context):
        return {"uploads": [{"upload_url": "https://example/secret-ticket"}]}

    await server.PersonalizationMiddleware().on_call_tool(
        context("manage_files", {
            "action": "replace",
            "kb_name": "Manual", "filenames": ["customer-secret.pdf"],
            "document_id": "doc-1", "expected_revision": 4,
        }),
        replace_result,
    )
    assert records[-1]["operation"] == "replace"
    assert records[-1]["status"] == "pending"


@pytest.mark.asyncio
async def test_invalid_mcp_key_is_not_written_to_business_ledger(
    monkeypatch,
) -> None:
    records: list[dict] = []
    monkeypatch.setattr(
        server, "require_identity",
        lambda _headers: (_ for _ in ()).throw(server.IdentityError("bad key")),
    )
    monkeypatch.setattr(server, "_post_access_record", records.append)

    with pytest.raises(ToolError):
        await server.PersonalizationMiddleware().on_call_tool(
            context("search_knowledge", {"query": "q", "domain": "d"}),
            lambda _context: None,
        )
    assert records == []


@pytest.mark.asyncio
async def test_reporting_failure_never_replaces_tool_result(
    monkeypatch,
) -> None:

    monkeypatch.setattr(server, "require_identity", lambda _headers: IDENTITY)
    monkeypatch.setattr(
        server, "_post_access_record",
        lambda _payload: (_ for _ in ()).throw(RuntimeError("ledger down")),
    )

    async def success(_context):
        return {"view": "kb_tree"}

    result = await server.PersonalizationMiddleware().on_call_tool(
        context("get_knowledge", {}), success)
    assert result == {"view": "kb_tree"}


@pytest.mark.asyncio
async def test_record_timeout_isolated_from_successful_tool_call(monkeypatch) -> None:
    monkeypatch.setattr(server, "require_identity", lambda _headers: IDENTITY)
    monkeypatch.setattr(server, "ACCESS_RECORD_WRITE_TIMEOUT_SECONDS", 0.01)

    async def blocked_writer(_payload):
        await asyncio.sleep(60)

    monkeypatch.setattr(server, "_post_access_record", blocked_writer)

    async def success(_context):
        return {"view": "kb_tree"}

    result = await asyncio.wait_for(
        server.PersonalizationMiddleware().on_call_tool(
            context("get_knowledge", {}), success,
        ),
        timeout=0.2,
    )

    assert result == {"view": "kb_tree"}


@pytest.mark.asyncio
async def test_future_tool_requires_identity_and_injects_it_without_business_record(
    monkeypatch,
) -> None:
    called = False
    records: list[dict] = []
    monkeypatch.setattr(
        server, "require_identity",
        lambda _headers: (_ for _ in ()).throw(server.IdentityError("bad key")),
    )
    monkeypatch.setattr(server, "_post_access_record", records.append)

    async def should_not_run(_context):
        nonlocal called
        called = True

    with pytest.raises(ToolError):
        await server.PersonalizationMiddleware().on_call_tool(
            context("future_tool", {}), should_not_run,
        )
    assert called is False
    assert records == []

    monkeypatch.setattr(server, "require_identity", lambda _headers: IDENTITY)

    async def sees_identity(_context):
        assert server.require_current_identity() == IDENTITY
        return {"ok": True}

    result = await server.PersonalizationMiddleware().on_call_tool(
        context("future_tool", {}), sees_identity,
    )
    assert result == {"ok": True}
    assert records == []


def test_terminal_mcp_payloads_are_owned_but_pending_is_unowned() -> None:
    pending = build_access_payload(
        call_id="owned-1", tool_name="search_knowledge",
        arguments={"query": "q"}, identity=IDENTITY, result=None,
        failure=None, duration_ms=0, forced_status="pending",
    )
    terminal = build_access_payload(
        call_id="owned-1", tool_name="search_knowledge",
        arguments={"query": "q"}, identity=IDENTITY,
        result={"query": "q", "evidence": []}, failure=None,
        duration_ms=1,
    )

    assert "terminal_owner" not in pending["details_json"]
    assert terminal["details_json"]["terminal_owner"] == "mcp"

def test_client_has_no_mcp_headers_outside_tool_call() -> None:
    token = current_access_call.set(None)
    try:
        assert client._identity_headers(IDENTITY) == {"X-KB-User": "alice"}
    finally:
        current_access_call.reset(token)


def test_single_upload_ticket_completes_the_same_record() -> None:
    pending = {
        "id": "upload-1",
        "domain": "cloud_core_network",
        "source": "mcp",
        "operation": "upload",
        "status": "pending",
        "details_json": {"file_count": 1},
    }
    register_upload_tickets(
        pending,
        {"uploads": [{"upload_url": "https://kb.example/upload/up_one"}]},
    )

    completed = complete_upload_ticket("up_one", success=True)

    assert completed is not None
    assert completed["id"] == "upload-1"
    assert completed["status"] == "success"
    assert completed["details_json"] == {
        "file_count": 1, "uploaded_count": 1, "failed_count": 0,
        "terminal_owner": "mcp",
    }
    assert completed["completed_at"]
    assert completed["details_json"]["terminal_owner"] == "mcp"


def test_expired_upload_ticket_is_swept(monkeypatch) -> None:
    _UPLOAD_TICKETS.clear()
    clock = iter((100.0, 100.0, 701.0))
    monkeypatch.setattr(access_records_module.time, "monotonic", lambda: next(clock))
    register_upload_tickets(
        {
            "id": "upload-expired",
            "domain": "cloud_core_network",
            "source": "mcp",
            "operation": "upload",
            "status": "pending",
            "details_json": {"file_count": 1},
        },
        {"uploads": [{
            "upload_url": "https://kb.example/upload/up_expired",
            "expires_in": 600,
        }]},
    )

    assert complete_upload_ticket("up_expired", success=True) is None
    assert _UPLOAD_TICKETS == {}


def test_http_429_is_failed_not_timeout() -> None:
    payload = build_access_payload(
        call_id="call-rate-limit",
        tool_name="search_knowledge",
        arguments={"query": "q"},
        identity=IDENTITY,
        result={"error": "HTTP 429"},
        failure=None,
        duration_ms=1,
    )

    assert payload["status"] == "failed"
    assert payload["error_code"] == "http_429"


def test_search_payload_captures_complete_safe_request_effective_context_and_response() -> None:
    arguments = {
        "query": "原始问题 token 仍是自然语言正文",
        "kb_names": ["Manual"],
        "within": {"document_refs": ["doc_1"], "section_refs": ["st_1"]},
        "filters": {"asset_types": ["table"]},
        "expansion": {"mode": "window"},
        "top_k": 12,
        "paradigm": "careful",
        "debug": True,
        "authorization": "Bearer never-store",
        "nested": {"password": "never-store", "allowed": "kept"},
    }
    result = {
        "query": arguments["query"],
        "evidence": [{
            "ref": "ev_1", "type": "prose", "content": "actual evidence",
            "source": {"document_ref": "doc_1", "title": "Manual"},
            "truncated": False,
        }],
        "has_more": False,
        "diagnostics": {"stages": ["lexical", "semantic"]},
    }
    token = current_access_call.set({
        "id": "call-snapshot", "paradigm_id": "p-1", "paradigm_version": 3,
    })
    try:
        payload = build_access_payload(
            call_id="call-snapshot", tool_name="search_knowledge",
            arguments=arguments, identity=IDENTITY, result=result,
            failure=None, duration_ms=9,
        )
    finally:
        current_access_call.reset(token)

    captured = payload["payload"]
    assert captured["payload_schema_version"] == 1
    assert captured["request_json"]["query"] == arguments["query"]
    assert captured["request_json"]["within"] == arguments["within"]
    assert captured["request_json"]["nested"] == {"allowed": "kept"}
    assert "authorization" not in captured["request_json"]
    assert captured["effective_context_json"] == {
        "domain": "cloud_core_network", "kb_ids": ["kb-1"],
        "paradigm_id": "p-1", "paradigm_version": 3,
    }
    assert captured["response_mode"] == "snapshot"
    assert captured["response_json"] == result
    assert captured["response_refs_json"] == []
    assert captured["request_bytes"] <= REQUEST_PAYLOAD_MAX_BYTES
    assert captured["response_bytes"] <= RESPONSE_PAYLOAD_MAX_BYTES
    assert captured["response_truncated"] is False
    assert captured["response_original_bytes"] == captured["response_bytes"]
    assert len(captured["response_sha256"]) == 64
    assert captured["redactions_json"] == ["authorization", "nested.password"]


@pytest.mark.parametrize("view", ["evidence_content", "document_content"])
def test_content_reads_store_reference_envelope_without_copying_body(view) -> None:
    payload = build_access_payload(
        call_id="call-reference", tool_name="get_knowledge",
        arguments={"ref": "ev_1" if view == "evidence_content" else "doc_1",
                   "mode": "window", "limit": 100},
        identity=IDENTITY,
        result={
            "view": view, "ref": "ev_1", "document_ref": "doc_1",
            "segments": [{"ref": "st_1", "content": "must not be copied",
                          "source": {"document_ref": "doc_1"}}],
            "content": "must not be copied either", "next_cursor": "next-1",
        },
        failure=None, duration_ms=2,
    )["payload"]

    assert payload["response_mode"] == "reference"
    assert payload["response_json"] is None
    assert payload["response_refs_json"][0]["ref"] == "ev_1"
    assert payload["response_refs_json"][0]["segments"][0]["ref"] == "st_1"
    assert "must not be copied" not in str(payload)


@pytest.mark.parametrize(
    "view", ["table_rows", "aggregate", "navigation", "capabilities", "documents", "kb_tree"],
)
def test_non_content_reads_store_safe_response_snapshot(view) -> None:
    result = {"view": view, "rows": [{"name": "kept"}], "token": "drop-me"}
    payload = build_access_payload(
        call_id=f"call-{view}", tool_name="get_knowledge",
        arguments={"ref": "st_1", "query": {"select": ["name"]}},
        identity=IDENTITY, result=result, failure=None, duration_ms=1,
    )["payload"]

    assert payload["response_mode"] == "snapshot"
    assert payload["response_json"] == {
        "view": view, "rows": [{"name": "kept"}],
    }
    assert payload["redactions_json"] == ["token"]


def test_payload_boundaries_are_valid_json_with_explicit_response_metadata() -> None:
    huge = "界" * (RESPONSE_PAYLOAD_MAX_BYTES // 2)
    payload = build_access_payload(
        call_id="call-large", tool_name="search_knowledge",
        arguments={"query": "问" * REQUEST_PAYLOAD_MAX_BYTES},
        identity=IDENTITY,
        result={"evidence": [{"ref": "ev_1", "content": huge}], "has_more": True},
        failure=None, duration_ms=1,
    )["payload"]

    assert payload["request_bytes"] <= REQUEST_PAYLOAD_MAX_BYTES
    assert payload["request_json"]["__truncated__"] is True
    assert payload["response_bytes"] <= RESPONSE_PAYLOAD_MAX_BYTES
    assert payload["response_truncated"] is True
    assert payload["response_original_bytes"] > payload["response_bytes"]
    assert payload["response_omitted_count"] >= 1
    assert len(payload["response_sha256"]) == 64



def test_failure_payload_keeps_only_public_error_code_and_message() -> None:
    payload = build_access_payload(
        call_id="call-public-error", tool_name="search_knowledge",
        arguments={"query": "q"}, identity=IDENTITY, result=None,
        failure=RuntimeError("password=db-secret stack=/internal/path"), duration_ms=1,
    )["payload"]

    assert payload["response_mode"] == "summary"
    assert payload["response_json"] == {
        "error": "tool_failed", "message": "检索执行失败，请稍后重试。",
    }
    assert "db-secret" not in str(payload)
    assert "internal/path" not in str(payload)


def test_sanitizer_stops_at_depth_and_node_budgets_without_recursion_error() -> None:
    deeply_nested: dict = {"leaf": "kept"}
    for _ in range(2_000):
        deeply_nested = {"child": deeply_nested}
    payload = build_access_payload(
        call_id="call-deep", tool_name="get_knowledge",
        arguments={"query": {"select": ["name"]}, "nested": deeply_nested,
                   "many": list(range(200_000))},
        identity=IDENTITY, result={"view": "capabilities"},
        failure=None, duration_ms=1,
    )["payload"]

    assert payload["request_bytes"] <= REQUEST_PAYLOAD_MAX_BYTES
    assert "__sanitizer_truncated__" in str(payload["request_json"])

@pytest.mark.asyncio
async def test_reporting_failure_increments_metric_and_emits_structured_warning(
    monkeypatch, caplog,
) -> None:
    reset_access_record_metrics()
    monkeypatch.setattr(server, "require_identity", lambda _headers: IDENTITY)
    monkeypatch.setattr(
        server, "_post_access_record",
        lambda _payload: (_ for _ in ()).throw(RuntimeError("ledger down")),
    )

    async def success(_context):
        return {"view": "kb_tree"}

    with caplog.at_level("WARNING", logger="mcp_server.server"):
        result = await server.PersonalizationMiddleware().on_call_tool(
            context("get_knowledge", {}), success,
        )

    assert result == {"view": "kb_tree"}
    assert get_access_record_metrics()["access_record_write_failures"] == 2
    failures = [record for record in caplog.records
                if record.getMessage() == "access_record_write_failed"]
    assert {record.phase for record in failures} == {"create", "complete"}
    assert all(record.record_id for record in failures)
    assert all(record.tool == "get_knowledge" for record in failures)
    assert all(record.error_class == "RuntimeError" for record in failures)


@pytest.mark.asyncio
async def test_mcp_health_exposes_access_record_write_failures() -> None:
    reset_access_record_metrics()
    access_records_module.increment_access_record_write_failures()

    response = await server._health(None)

    assert response.status_code == 200
    assert b'"access_record_write_failures":1' in response.body



def test_upload_completion_adds_safe_document_and_run_references() -> None:
    pending = build_access_payload(
        call_id="upload-refs", tool_name="manage_files",
        arguments={"action": "upload",
                   "kb_name": "Manual", "filenames": ["guide.pdf"]},
        identity=IDENTITY,
        result={"uploads": [{
            "filename": "guide.pdf",
            "upload_url": "https://kb.example/upload/secret-ticket",
        }]},
        failure=None, duration_ms=1,
    )
    register_upload_tickets(
        pending,
        {"uploads": [{"upload_url": "https://kb.example/upload/up_refs"}]},
    )

    completed = complete_upload_ticket(
        "up_refs", success=True,
        result={
            "document_id": "document-1", "run_id": "run-1", "auto_mined": True,
            "ticket": "never-store", "upload_url": "https://never-store",
        },
    )

    assert completed is not None
    refs = completed["payload"]["response_refs_json"]
    assert refs[0]["uploads"] == [{"filename": "guide.pdf"}]
    assert refs[1] == {
        "document_id": "document-1", "run_id": "run-1", "auto_mined": True,
    }
    assert "never-store" not in str(completed)


@pytest.mark.asyncio
async def test_each_initialize_expires_orphan_uploads_and_retries_after_failure(
    monkeypatch,
) -> None:
    calls = 0

    async def expire():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("mining unavailable")
        return 2

    monkeypatch.setattr(server, "_expire_pending_uploads", expire)

    await server._recover_pending_uploads_once()

    await server._recover_pending_uploads_once()
    await server._recover_pending_uploads_once()

    assert calls == 3
