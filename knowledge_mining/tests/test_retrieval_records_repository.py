from __future__ import annotations

from datetime import datetime
import json

import pytest

from knowledge_mining.mining.infra.retrieval_records_db import (
    RetrievalRecordRepository,
)


class _Rows:
    def __init__(self, one=None, many=None):
        self._one = one
        self._many = many or []

    async def fetchone(self):
        return self._one

    async def fetchall(self):
        return self._many


class _Connection:
    def __init__(self):
        self.statements: list[tuple[str, dict]] = []
        self._responses = [
            _Rows(one={
                "total_calls": 3,
                "calls": 1,
                "no_result": 1,
                "failed": 1,
                "p95_duration_ms": 20,
                "avg_duration_ms": 10,
                "active_paradigms": 1,
            }),
            _Rows(many=[]),
            _Rows(many=[]),
            _Rows(many=[]),
            _Rows(many=[]),
            _Rows(many=[]),
            _Rows(many=[]),
        ]

    async def execute(self, statement, params=None):
        self.statements.append((str(statement), dict(params or {})))
        return self._responses.pop(0)


class _ConnectionContext:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, *_args):
        return False


class _Pool:
    def __init__(self, connection):
        self._connection = connection

    def connection(self):
        return _ConnectionContext(self._connection)


class _ListConnection:
    def __init__(self):
        self.statements: list[tuple[str, dict]] = []

    async def execute(self, statement, params=None):
        self.statements.append((str(statement), dict(params or {})))
        return _Rows(many=[])


@pytest.mark.asyncio
async def test_summary_counts_searches_separately_from_read_and_upload_calls() -> None:
    connection = _Connection()
    repository = RetrievalRecordRepository(_Pool(connection))

    result = await repository.summary(
        domain="d1",
        days=7,
        actor_user_id=None,
        kb_id=None,
        visible_kb_ids=["kb-1"],
    )

    summary_sql = connection.statements[0][0]
    assert "COUNT(*) AS total_calls" in summary_sql
    assert "operation = 'search'" in summary_sql
    assert result["summary"]["total_calls"] == 3
    assert result["summary"]["calls"] == 1
    assert result["summary"]["no_result_rate"] == 1.0
    assert result["summary"]["failure_rate"] == pytest.approx(1 / 3, abs=0.0001)


def test_kb_visibility_clause_uses_only_existing_visible_ids() -> None:
    clauses, params = RetrievalRecordRepository._where(
        domain="d1", visible_kb_ids=["kb-1", "kb-2"]
    )

    assert any("visible_kb_ids" in clause for clause in clauses)
    assert params["visible_kb_ids"] == ["kb-1", "kb-2"]


@pytest.mark.asyncio
async def test_list_records_applies_the_same_days_window_as_summary() -> None:
    connection = _ListConnection()
    repository = RetrievalRecordRepository(_Pool(connection))

    await repository.list_records(
        domain="d1",
        days=30,
        actor_user_id=None,
        source=None,
        tool_name=None,
        operation=None,
        kb_id=None,
        mcp_key_id=None,
        status=None,
        paradigm_id=None,
        visible_kb_ids=["kb-1"],
        cursor_at=None,
        cursor_id=None,
        page_size=25,
    )

    sql, params = connection.statements[0]
    assert "occurred_at >= %(since)s" in sql
    assert isinstance(params["since"], datetime)
    assert params["limit"] == 26


def test_upsert_only_transitions_pending_to_terminal_and_terminal_replay_is_idempotent() -> None:
    import inspect

    sql_source = inspect.getsource(RetrievalRecordRepository)
    assert "knowledge_access_records.status = 'pending'" in sql_source
    assert "EXCLUDED.status <> 'pending'" in sql_source
    assert "SELECT * FROM knowledge_access_records WHERE id = %(id)s" in sql_source
    assert "COALESCE(EXCLUDED.result_count" in sql_source
    assert "COALESCE(EXCLUDED.duration_ms" in sql_source


class _DetailConnection:
    def __init__(self):
        self.statements: list[tuple[str, dict]] = []
        self._responses = [
            _Rows(one={"id": "r-1", "kb_ids": [], "details_json": {}}),
            _Rows(one={
                "record_id": "r-1",
                "request_json": {"query": "q"},
                "effective_context_json": {"kb_ids": ["kb-1"]},
                "response_mode": "snapshot",
                "response_json": {"items": []},
                "response_refs_json": [],
                "redactions_json": [],
            }),
        ]

    async def execute(self, statement, params=None):
        self.statements.append((str(statement), dict(params or {})))
        return self._responses.pop(0)


@pytest.mark.asyncio
async def test_detail_loads_payload_only_after_visible_record_is_found() -> None:
    connection = _DetailConnection()
    repository = RetrievalRecordRepository(_Pool(connection))

    result = await repository.get_record(
        record_id="r-1",
        domain="d1",
        actor_user_id="u-1",
        visible_kb_ids=["kb-1"],
    )

    assert result is not None
    assert result["payload"]["response_mode"] == "snapshot"
    assert "JOIN knowledge_access_record_payloads" not in connection.statements[0][0]
    assert "knowledge_access_record_payloads" in connection.statements[1][0]


@pytest.mark.asyncio
async def test_detail_without_kb_scope_does_not_add_visibility_predicate() -> None:
    connection = _DetailConnection()
    repository = RetrievalRecordRepository(_Pool(connection))

    result = await repository.get_record(
        record_id="r-1",
        domain="d1",
        actor_user_id=None,
        visible_kb_ids=None,
    )

    assert result is not None
    sql, params = connection.statements[0]
    assert "visible_kb_ids" not in sql
    assert "visible_kb_ids" not in params


@pytest.mark.asyncio
async def test_detail_with_empty_kb_scope_only_allows_unscoped_records() -> None:
    connection = _DetailConnection()
    repository = RetrievalRecordRepository(_Pool(connection))

    result = await repository.get_record(
        record_id="r-1",
        domain="d1",
        actor_user_id=None,
        visible_kb_ids=[],
    )

    assert result is not None
    sql, params = connection.statements[0]
    assert "kb_ids = '[]'::jsonb OR kb_ids ?| %(visible_kb_ids)s" in sql
    assert params["visible_kb_ids"] == []


def test_list_query_never_joins_large_payload_table() -> None:
    import inspect

    sql_source = inspect.getsource(RetrievalRecordRepository.list_records)
    assert "knowledge_access_record_payloads" not in sql_source


def test_upsert_writes_payload_in_the_same_connection_scope() -> None:
    import inspect

    sql_source = inspect.getsource(RetrievalRecordRepository)
    assert "knowledge_access_record_payloads" in sql_source
    assert "ON CONFLICT (record_id) DO UPDATE" in sql_source



def test_terminal_replay_cannot_mutate_an_existing_payload_snapshot() -> None:
    import inspect

    sql_source = inspect.getsource(RetrievalRecordRepository.upsert)
    assert "main_changed = row is not None" in sql_source
    assert "payload_snapshot is not None and main_changed" in sql_source

def test_mcp_final_can_replace_serving_fallback_without_mutating_identity_or_request() -> None:
    """One MCP call shares an id with Java: final client-visible response wins."""
    import inspect

    upsert_source = inspect.getsource(RetrievalRecordRepository.upsert)
    payload_source = inspect.getsource(RetrievalRecordRepository._upsert_payload)

    assert "finalize_existing_mcp" in upsert_source
    conflict_update = upsert_source.split("ON CONFLICT (id) DO UPDATE SET", 1)[1].split("RETURNING", 1)[0]
    assert "actor_user_id =" not in conflict_update
    assert "request_json = COALESCE(knowledge_access_record_payloads.request_json" in payload_source
    assert "terminal_owner" in upsert_source
    assert "knowledge_access_records.details_json->>'terminal_owner' = 'serving'" in upsert_source


class _TerminalOwnerConnection:
    def __init__(self, *, source: str = "mcp", terminal_owner: str | None = "serving") -> None:
        self.source = source
        self.terminal_owner = terminal_owner
        self.status = "success"
        self.result_count = 1
        self.payload_writes = 0
        self.statements: list[tuple[str, dict]] = []

    def _row(self) -> dict:
        details = ({"terminal_owner": self.terminal_owner}
                   if self.terminal_owner else {})
        return {
            "id": "shared-1", "domain": "d1", "actor_user_id": "u-1",
            "actor_username": "alice", "source": self.source,
            "operation": "search", "status": self.status,
            "result_count": self.result_count, "kb_ids": ["kb-1"],
            "details_json": details,
        }

    async def execute(self, statement, params=None):
        sql = str(statement)
        values = dict(params or {})
        self.statements.append((sql, values))
        if "INSERT INTO knowledge_access_records" in sql:
            incoming_details = json.loads(values["details_json"])
            allowed = (
                values["finalize_existing_mcp"] is True
                and self.source == "mcp"
                and self.terminal_owner == "serving"
                and incoming_details.get("terminal_owner") == "mcp"
            )
            if not allowed:
                return _Rows(one=None)
            self.status = values["status"]
            self.result_count = values["result_count"]
            self.terminal_owner = "mcp"
            return _Rows(one=self._row())
        if sql.startswith("SELECT * FROM knowledge_access_records"):
            return _Rows(one=self._row())
        assert "INSERT INTO knowledge_access_record_payloads" in sql
        self.payload_writes += 1
        return _Rows()


def _terminal_payload(*, source: str = "mcp", status: str = "no_result", evidence: list | None = None) -> dict:
    evidence = [] if evidence is None else evidence
    return {
        "id": "shared-1", "occurred_at": None,
        "completed_at": datetime.now().astimezone(), "domain": "d1",
        "actor_user_id": "u-1", "actor_username": "alice",
        "source": source, "operation": "search",
        "tool_name": "search_knowledge", "mcp_key_id": "key-1",
        "kb_ids": ["kb-1"], "query_text": "q", "paradigm_id": "p-1",
        "paradigm_version": 1, "status": status,
        "result_count": len(evidence), "duration_ms": 20,
        "error_code": None, "details_json": {"terminal_owner": "mcp"},
        "payload": {
            "request_json": {"query": "q"},
            "effective_context_json": {"kb_ids": ["kb-1"]},
            "response_mode": "snapshot",
            "response_json": {"query": "q", "evidence": evidence},
            "response_refs_json": [],
        },
    }


@pytest.mark.asyncio
async def test_java_fallback_allows_exactly_one_mcp_final_payload_transition() -> None:
    connection = _TerminalOwnerConnection()
    repository = RetrievalRecordRepository(_Pool(connection))

    first = await repository.upsert(_terminal_payload())
    replay = await repository.upsert(_terminal_payload(
        status="failed", evidence=[{"ref": "must-not-replace"}],
    ))

    assert first["status"] == "no_result"
    assert first["details_json"]["terminal_owner"] == "mcp"
    assert replay["status"] == "no_result"
    assert replay["result_count"] == 0
    assert connection.payload_writes == 1
    payload_sql, payload_params = next(
        item for item in connection.statements
        if "INSERT INTO knowledge_access_record_payloads" in item[0]
    )
    assert payload_params["replace_response"] is True
    assert '"evidence": []' in payload_params["response_json"]
    assert "actor_user_id =" not in connection.statements[0][0].split(
        "ON CONFLICT (id) DO UPDATE SET", 1
    )[1]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source", "terminal_owner"),
    [("mcp", "mining"), ("web", None), ("api", None)],
)
async def test_mining_upload_and_ordinary_terminal_rows_are_immutable(
    source: str, terminal_owner: str | None,
) -> None:
    connection = _TerminalOwnerConnection(
        source=source, terminal_owner=terminal_owner,
    )
    repository = RetrievalRecordRepository(_Pool(connection))

    replay = await repository.upsert(_terminal_payload(source=source, status="failed"))

    assert replay["status"] == "success"
    assert replay["result_count"] == 1
    assert connection.payload_writes == 0

class _UploadCompletionConnection:
    def __init__(self, row):
        self.row = row
        self.statements: list[tuple[str, dict]] = []

    async def execute(self, statement, params=None):
        self.statements.append((str(statement), dict(params or {})))
        return _Rows(one=self.row)


@pytest.mark.asyncio
async def test_upload_completion_is_one_atomic_pending_only_transition() -> None:
    connection = _UploadCompletionConnection({
        "id": "upload-1",
        "status": "pending",
        "kb_ids": ["kb-1"],
        "details_json": {
            "file_count": 3,
            "completed_count": 1,
            "uploaded_count": 1,
            "failed_count": 0,
        },
    })
    repository = RetrievalRecordRepository(_Pool(connection))

    result = await repository.complete_upload_file(
        record_id="upload-1",
        success=True,
        error_code=None,
        response_refs=[{"document_id": "doc-1", "run_id": "run-1"}],
    )

    sql, params = connection.statements[0]
    assert "FOR UPDATE" in sql
    assert "status = 'pending'" in sql
    assert "completed_count" in sql
    assert "uploaded_count" in sql
    assert "failed_count" in sql
    assert "UPDATE knowledge_access_records" in sql
    assert "knowledge_access_record_payloads" in sql
    assert "terminal_owner" in sql
    assert "mining" in sql
    assert "document_id" in params["response_refs_json"]
    assert params["record_id"] == "upload-1"
    assert result["details_json"]["completed_count"] == 1


def test_upload_completion_sql_keeps_terminal_rows_immutable_and_serializes_concurrency() -> None:
    import inspect

    source = inspect.getsource(RetrievalRecordRepository.complete_upload_file)
    assert "FOR UPDATE" in source
    assert "locked.status = 'pending'" in source
    assert "NOT EXISTS (SELECT 1 FROM updated)" in source


@pytest.mark.asyncio
async def test_expire_pending_uploads_is_bounded_and_idempotent() -> None:
    connection = _UploadCompletionConnection({"expired_count": 2})
    repository = RetrievalRecordRepository(_Pool(connection))

    count = await repository.expire_pending_uploads(older_than_seconds=600)

    sql, params = connection.statements[0]
    assert "status = 'pending'" in sql
    assert "operation = 'upload'" in sql
    assert "error_code = 'upload_expired'" in sql
    assert "SKIP LOCKED" in sql
    assert params["older_than_seconds"] == 600
    assert count == 2
