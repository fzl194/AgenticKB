from __future__ import annotations

from types import SimpleNamespace

import pytest

from knowledge_mining.mining.kb.routes import mcp_tools


class _Pools:
    async def async_pool(self, domain: str):
        assert domain == "cloud_core_network"
        return "pool"


@pytest.mark.asyncio
async def test_single_upload_completes_original_record(monkeypatch) -> None:
    writes: list[dict] = []

    class _Service:
        def __init__(self, pool):
            assert pool == "pool"

        async def write_record(self, payload):
            writes.append(payload)
            return payload

    monkeypatch.setattr(mcp_tools, "RetrievalRecordService", _Service)
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(domain_pools=_Pools())))
    entry = {
        "access_record_id": "call-1",
        "access_record_total": 1,
        "domain": "cloud_core_network",
        "user_id": "u-1",
        "username": "alice",
        "key_id": "key-1",
        "kb_id": "kb-1",
    }

    await mcp_tools._complete_upload_access_record(
        request, entry, success=True,
        response_refs=[{"document_id": "doc-1", "run_id": "run-1"}],)

    assert writes[0]["id"] == "call-1"
    assert writes[0]["status"] == "success"
    assert writes[0]["operation"] == "upload"
    assert writes[0]["kb_ids"] == ["kb-1"]
    assert writes[0]["details_json"]["uploaded_count"] == 1
    assert writes[0]["completed_at"] is not None
    assert writes[0]["payload"]["response_refs_json"] == [
        {"document_id": "doc-1", "run_id": "run-1"},
    ]


@pytest.mark.asyncio
async def test_upload_record_failure_does_not_replace_upload_result(monkeypatch) -> None:
    class _Service:
        def __init__(self, _pool):
            pass

        async def write_record(self, _payload):
            raise RuntimeError("ledger down")

    monkeypatch.setattr(mcp_tools, "RetrievalRecordService", _Service)
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(domain_pools=_Pools())))

    await mcp_tools._complete_upload_access_record(
        request,
        {
            "access_record_id": "call-1",
            "domain": "cloud_core_network",
        },
        success=False,
        error_code="upload_failed",
    )


@pytest.mark.asyncio
async def test_mining_completion_survives_mcp_restart_and_uses_database_counter(
    monkeypatch,
) -> None:
    events: list[dict] = []

    class _Service:
        def __init__(self, pool):
            assert pool == "pool"

        async def complete_upload_file(self, **kwargs):
            events.append(kwargs)
            return {"id": kwargs["record_id"], "status": "pending"}

        async def write_record(self, _payload):
            pytest.fail("multi-file completion must not use generic upsert")

    monkeypatch.setattr(mcp_tools, "RetrievalRecordService", _Service)
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(domain_pools=_Pools())))
    entry = {
        "access_record_id": "call-multi",
        "access_record_total": 2,
        "domain": "cloud_core_network",
    }

    await mcp_tools._complete_upload_access_record(
        request, entry, success=True
    )

    assert events == [{
        "record_id": "call-multi",
        "success": True,
        "error_code": None,
        "response_refs": None,
    }]


@pytest.mark.asyncio
async def test_expired_ticket_drain_marks_each_file_failed(monkeypatch) -> None:
    entries = [{
        "access_record_id": "call-expired",
        "domain": "cloud_core_network",
    }]
    completed: list[tuple[dict, bool, str | None]] = []
    monkeypatch.setattr(mcp_tools._TICKETS, "drain_expired", lambda: entries)

    async def _complete(_request, entry, *, success, error_code=None):
        completed.append((entry, success, error_code))

    monkeypatch.setattr(mcp_tools, "_complete_upload_access_record", _complete)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))

    await mcp_tools._expire_upload_tickets(request)

    assert completed == [(entries[0], False, "upload_expired")]


@pytest.mark.asyncio
async def test_mining_write_failure_increments_metric_and_is_structured(
    monkeypatch, caplog,
) -> None:
    from knowledge_mining.mining.services import retrieval_records as records_service

    class _Repository:
        async def upsert(self, _payload):
            raise RuntimeError("ledger down")

    service = records_service.RetrievalRecordService(object())
    service._repository = _Repository()
    records_service.reset_access_record_metrics()

    with caplog.at_level(
        "WARNING",
        logger="knowledge_mining.mining.services.retrieval_records",
    ):
        with pytest.raises(RuntimeError, match="ledger down"):
            await service.write_record({
                "id": "call-failed",
                "tool_name": "upload_document",
            })

    assert records_service.get_access_record_metrics() == {
        "access_record_write_failures": 1,
    }
    warning = next(
        record for record in caplog.records
        if record.getMessage() == "access_record_write_failed"
    )
    assert warning.record_id == "call-failed"
    assert warning.tool == "upload_document"
    assert warning.error_class == "RuntimeError"


@pytest.mark.asyncio
async def test_mining_health_exposes_access_record_write_failures() -> None:
    from knowledge_mining.mining.api.routes import health as health_route
    from knowledge_mining.mining.services import retrieval_records as records_service

    class _Connection:
        async def execute(self, _sql):
            return None

    class _Context:
        async def __aenter__(self):
            return _Connection()

        async def __aexit__(self, *_args):
            return False

    class _Pool:
        def connection(self):
            return _Context()

    records_service.reset_access_record_metrics()
    records_service._record_write_failure(
        record_id="call-health",
        tool="upload_document",
        phase="test",
        error=RuntimeError("down"),
    )
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(pg_pool=_Pool()))
    )

    result = await health_route.health(request)

    assert result["status"] == "ok"
    assert result["access_record_write_failures"] == 1
