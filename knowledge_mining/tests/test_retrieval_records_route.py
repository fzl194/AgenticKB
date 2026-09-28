from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


USER = {"id": "u-1", "username": "alice", "site_role": "member"}


class _DomainPools:
    async def async_pool(self, domain: str) -> object:
        assert domain == "cloud_core_network"
        return object()


class FakeService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def summary(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("summary", kwargs))
        return {
            "available": True,
            "days": kwargs["days"],
            "summary": {
                "total_calls": 1,
                "calls": 1,
                "no_result": 0,
                "failed": 0,
                "no_result_rate": 0.0,
                "failure_rate": 0.0,
                "p95_duration_ms": 25.0,
                "avg_duration_ms": 25.0,
                "active_paradigms": 1,
            },
            "trend": [],
            "sources": {"web": 1},
            "tools": [],
            "paradigms": [],
            "no_result_queries": [],
            "top_queries": [],
        }

    async def list_records(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("list", kwargs))
        return {"items": [], "next_cursor": None, "has_more": False, "page_size": kwargs["page_size"]}

    async def get_record(self, **kwargs: Any) -> dict[str, Any] | None:
        self.calls.append(("detail", kwargs))
        if kwargs["record_id"] == "missing":
            return None
        return {"id": kwargs["record_id"], "domain": kwargs["domain"], "status": "success"}

    async def write_record(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("write", payload))
        return {**payload, "created": True}


def _client(monkeypatch: pytest.MonkeyPatch, service: FakeService) -> TestClient:
    from knowledge_mining.mining.api.routes import retrieval_records

    app = FastAPI()
    app.state.domain_pools = _DomainPools()
    app.state.pg_pool = object()
    app.include_router(retrieval_records.router)
    app.dependency_overrides[retrieval_records.current_user] = lambda: USER
    app.dependency_overrides[retrieval_records._require_internal] = lambda: None
    monkeypatch.setattr(retrieval_records, "build_service", lambda *_args, **_kwargs: service)
    return TestClient(app)


def test_summary_static_route_is_registered_before_dynamic_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    service = FakeService()
    response = _client(monkeypatch, service).get(
        "/api/retrieval-records/summary",
        params={"domain": "cloud_core_network", "days": 30, "kb_id": "kb-1"},
    )

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "available", "days", "summary", "trend", "sources", "tools",
        "paradigms", "no_result_queries", "top_queries",
    }
    assert set(body["summary"]) == {
        "total_calls", "calls", "no_result", "failed", "no_result_rate",
        "failure_rate", "p95_duration_ms", "avg_duration_ms",
        "active_paradigms",
    }
    assert body["summary"]["calls"] == 1
    assert service.calls == [(
        "summary",
        {
            "domain": "cloud_core_network", "days": 30, "kb_id": "kb-1",
            "source": None, "tool_name": None, "operation": None,
            "mcp_key_id": None, "status": None, "paradigm_id": None,
            "actor_user_id": None, "user": USER,
        },
    )]


def test_list_passes_snake_case_filters_and_stable_cursor(monkeypatch: pytest.MonkeyPatch) -> None:
    service = FakeService()
    response = _client(monkeypatch, service).get(
        "/api/retrieval-records",
        params={
            "domain": "cloud_core_network",
            "days": 30,
            "source": "mcp",
            "tool_name": "search_knowledge",
            "operation": "search",
            "kb_id": "kb-1",
            "mcp_key_id": "key-1",
            "status": "no_result",
            "paradigm_id": "p-1",
            "actor_user_id": "u-2",
            "cursor": "2026-09-28T10:00:00Z|r-2",
            "page_size": 25,
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "items": [], "next_cursor": None, "has_more": False, "page_size": 25,
    }
    name, kwargs = service.calls[0]
    assert name == "list"
    assert kwargs["kb_id"] == "kb-1"
    assert kwargs["days"] == 30
    assert kwargs["mcp_key_id"] == "key-1"
    assert kwargs["page_size"] == 25
    assert kwargs["cursor"] == "2026-09-28T10:00:00Z|r-2"


def test_summary_passes_the_same_mcp_and_actor_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    service = FakeService()
    response = _client(monkeypatch, service).get(
        "/api/retrieval-records/summary",
        params={
            "domain": "cloud_core_network",
            "source": "mcp",
            "tool_name": "search_knowledge",
            "operation": "search",
            "mcp_key_id": "key-1",
            "status": "success",
            "actor_user_id": "u-2",
        },
    )

    assert response.status_code == 200
    _, kwargs = service.calls[0]
    assert kwargs["source"] == "mcp"
    assert kwargs["tool_name"] == "search_knowledge"
    assert kwargs["mcp_key_id"] == "key-1"
    assert kwargs["actor_user_id"] == "u-2"


@pytest.mark.parametrize("days", [0, 91])
def test_list_rejects_days_outside_supported_window(
    monkeypatch: pytest.MonkeyPatch, days: int,
) -> None:
    response = _client(monkeypatch, FakeService()).get(
        "/api/retrieval-records",
        params={"domain": "cloud_core_network", "days": days},
    )

    assert response.status_code == 422


def test_detail_returns_404_for_record_outside_visible_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    response = _client(monkeypatch, FakeService()).get(
        "/api/retrieval-records/missing",
        params={"domain": "cloud_core_network"},
    )
    assert response.status_code == 404


def test_internal_write_requires_safe_payload_and_never_accepts_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    service = FakeService()
    client = _client(monkeypatch, service)
    payload = {
        "id": "call-1",
        "domain": "cloud_core_network",
        "actor_user_id": "u-1",
        "actor_username": "alice",
        "source": "mcp",
        "operation": "search",
        "tool_name": "search_knowledge",
        "mcp_key_id": "key-1",
        "kb_ids": ["kb-1"],
        "query_text": "SMF timeout",
        "status": "success",
        "result_count": 2,
        "duration_ms": 25,
        "details_json": {"mode": "evidence"},
    }
    ok = client.post("/api/internal/retrieval-records", json=payload)
    unsafe = client.post(
        "/api/internal/retrieval-records",
        json={**payload, "details_json": {"token": "secret"}},
    )

    assert ok.status_code == 200
    assert ok.json()["id"] == "call-1"
    assert unsafe.status_code == 422


@pytest.mark.parametrize(
    "sensitive_key",
    ["refreshToken", "client_secret", "session-cookie", "credentials"],
)
def test_internal_write_rejects_nested_sensitive_detail_key_patterns(
    monkeypatch: pytest.MonkeyPatch,
    sensitive_key: str,
) -> None:
    client = _client(monkeypatch, FakeService())
    response = client.post(
        "/api/internal/retrieval-records",
        json={
            "id": "call-sensitive",
            "domain": "cloud_core_network",
            "source": "api",
            "operation": "search",
            "status": "success",
            "details_json": {"mode": {"nested": {sensitive_key: "leak"}}},
        },
    )

    assert response.status_code == 422


def test_internal_write_rejects_unknown_detail_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _client(monkeypatch, FakeService()).post(
        "/api/internal/retrieval-records",
        json={
            "id": "call-unknown-detail",
            "domain": "cloud_core_network",
            "source": "api",
            "operation": "search",
            "status": "success",
            "details_json": {"arbitrary_payload": "not part of the contract"},
        },
    )

    assert response.status_code == 422


def test_internal_write_accepts_only_known_terminal_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(monkeypatch, FakeService())
    base = {
        "id": "call-owner", "domain": "cloud_core_network",
        "source": "mcp", "operation": "search", "status": "success",
    }

    accepted = client.post(
        "/api/internal/retrieval-records",
        json={**base, "details_json": {"terminal_owner": "mcp"}},
    )
    rejected = client.post(
        "/api/internal/retrieval-records",
        json={**base, "details_json": {"terminal_owner": "arbitrary"}},
    )

    assert accepted.status_code == 200
    assert rejected.status_code == 422

def test_internal_write_rejects_unknown_enum_values(monkeypatch: pytest.MonkeyPatch) -> None:
    response = _client(monkeypatch, FakeService()).post(
        "/api/internal/retrieval-records",
        json={
            "id": "call-1",
            "domain": "cloud_core_network",
            "source": "shell",
            "operation": "search",
            "status": "success",
        },
    )
    assert response.status_code == 422


def test_internal_write_rejects_naive_timestamps_and_oversized_details(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, FakeService())
    base = {
        "id": "call-1",
        "domain": "cloud_core_network",
        "source": "api",
        "operation": "search",
        "status": "success",
    }

    naive = client.post(
        "/api/internal/retrieval-records",
        json={**base, "occurred_at": "2026-09-28T10:00:00"},
    )
    oversized = client.post(
        "/api/internal/retrieval-records",
        json={**base, "details_json": {"summary": "x" * 40_000}},
    )

    assert naive.status_code == 422
    assert oversized.status_code == 422


def test_internal_write_accepts_complete_payload_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = FakeService()
    response = _client(monkeypatch, service).post(
        "/api/internal/retrieval-records",
        json={
            "id": "call-payload",
            "domain": "cloud_core_network",
            "source": "mcp",
            "operation": "search",
            "status": "success",
            "payload": {
                "request_json": {"query": "完整问题", "top_k": 8},
                "effective_context_json": {"kb_ids": ["kb-1"]},
                "response_mode": "snapshot",
                "response_json": {"items": [{"ref": "ev-1", "content": "证据"}]},
                "response_refs_json": [{"ref": "ev-1", "document_id": "doc-1"}],
                "request_bytes": 42,
                "response_bytes": 96,
                "response_truncated": False,
                "response_original_bytes": 96,
                "response_omitted_count": 0,
                "response_sha256": "a" * 64,
                "redactions_json": [],
                "payload_schema_version": 1,
            },
        },
    )

    assert response.status_code == 200
    _, written = service.calls[-1]
    assert written["payload"]["request_json"]["top_k"] == 8


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("request_json", {"authorization": "Bearer secret"}),
        ("effective_context_json", {"nested": {"password": "secret"}}),
        ("response_json", {"sessionToken": "secret"}),
        ("response_refs_json", [{"cookie": "secret"}]),
        ("redactions_json", [{"api_key": "secret"}]),
    ],
)
def test_internal_write_rejects_sensitive_keys_in_every_payload_json_tree(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: Any,
) -> None:
    response = _client(monkeypatch, FakeService()).post(
        "/api/internal/retrieval-records",
        json={
            "id": "call-sensitive-payload",
            "domain": "cloud_core_network",
            "source": "api",
            "operation": "search",
            "status": "success",
            "payload": {"response_mode": "snapshot", field: value},
        },
    )

    assert response.status_code == 422


def test_internal_write_enforces_request_and_response_payload_size_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(monkeypatch, FakeService())
    base = {
        "id": "call-large-payload",
        "domain": "cloud_core_network",
        "source": "api",
        "operation": "search",
        "status": "success",
    }
    request_too_large = client.post(
        "/api/internal/retrieval-records",
        json={**base, "payload": {"response_mode": "snapshot", "request_json": {"query": "x" * (256 * 1024)}}},
    )
    response_too_large = client.post(
        "/api/internal/retrieval-records",
        json={**base, "payload": {"response_mode": "snapshot", "response_json": {"content": "x" * (4 * 1024 * 1024)}}},
    )

    assert request_too_large.status_code == 422
    assert response_too_large.status_code == 422


def test_internal_expire_pending_uploads_calls_batch_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = FakeService()

    async def expire_pending_uploads(*, older_than_seconds: int) -> int:
        service.calls.append(("expire", {"older_than_seconds": older_than_seconds}))
        return 3

    service.expire_pending_uploads = expire_pending_uploads  # type: ignore[attr-defined]
    response = _client(monkeypatch, service).post(
        "/api/internal/retrieval-records/expire-pending-uploads"
    )

    assert response.status_code == 200
    assert response.json() == {"expired_count": 3}
    assert service.calls[-1] == (
        "expire", {"older_than_seconds": 600},
    )


def test_internal_expire_scans_every_enabled_domain_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from knowledge_mining.mining.api.routes import retrieval_records

    seen_pools: list[str] = []

    class Pools:
        async def async_pool(self, domain: str) -> str:
            return f"pool:{domain}"

    class Service:
        def __init__(self, pool: str) -> None:
            seen_pools.append(pool)

        async def expire_pending_uploads(self, *, older_than_seconds: int) -> int:
            assert older_than_seconds == 600
            return 1

    app = FastAPI()
    app.state.domain_pools = Pools()
    app.state.enabled_domains = ("domain-a", "domain-b")
    app.include_router(retrieval_records.router)
    app.dependency_overrides[retrieval_records._require_internal] = lambda: None
    monkeypatch.setattr(retrieval_records, "build_service", Service)

    response = TestClient(app).post(
        "/api/internal/retrieval-records/expire-pending-uploads"
    )

    assert response.status_code == 200
    assert response.json() == {"expired_count": 2}
    assert seen_pools == ["pool:domain-a", "pool:domain-b"]
