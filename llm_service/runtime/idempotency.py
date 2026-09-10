from __future__ import annotations

import json

from llm_service.db import LlmRuntimeDB

#: Same-status candidate scan bound: a key hit with a mismatched request falls
#: through to older candidates; this caps the scan when a key accumulated many
#: shape changes (config flips are rare — 50 is far beyond operational reality).
_MAX_CANDIDATES_PER_STATUS = 50


def request_fingerprint(
    *,
    messages_json,
    input_json,
    params_json,
    expected_output_type,
) -> str:
    """Canonical request identity — the parts that define the response contract.

    An idempotency key alone is not a safe dedup identity: mining keys are
    content-derived while the resolved model/dimensions live server-side. Two
    submissions sharing a key but differing in any of these fields describe
    different requests and must not reuse each other's results.

    The ``*_json`` inputs arrive as strings on the submit side and as parsed
    objects on the read-back side (the request columns are JSONB — PG
    normalizes key order), so both sides are parsed and serialized with
    ``sort_keys`` + compact separators before comparison.
    """

    def _canon(value):
        if isinstance(value, str):
            try:
                return json.loads(value)
            except (ValueError, TypeError):
                return value
        return value

    return json.dumps(
        [
            _canon(messages_json),
            _canon(input_json),
            _canon(params_json),
            expected_output_type,
        ],
        sort_keys=True,
        separators=(",", ":"),
    )


async def find_existing_task(
    db: LlmRuntimeDB,
    idempotency_key: str,
    *,
    request_fingerprint: str | None = None,
) -> str | None:
    """Return task_id if an active/succeeded task exists for this key.

    Priority: latest succeeded > latest running > latest queued > None (allow new).
    Only failed/dead_letter/cancelled tasks are ignored.

    With ``request_fingerprint``: a key hit only counts when the stored request
    row matches the fingerprint (same response contract). Non-matching
    candidates are skipped in favour of older matches within the same status —
    config flips (model/dimensions change) create a new task instead of
    silently serving stale vectors.
    """
    for status in ("succeeded", "running", "queued"):
        row = await db.fetchone(
            "SELECT id FROM agent_llm_tasks WHERE idempotency_key = %s AND status = %s ORDER BY created_at DESC LIMIT 1",
            (idempotency_key, status),
        )
        if not row:
            continue
        if request_fingerprint is None:
            return row["id"]
        if await _request_matches(db, row["id"], request_fingerprint):
            return row["id"]
        # Latest candidate is a different request shape — scan older ones.
        candidates = await db.fetchall(
            "SELECT id FROM agent_llm_tasks WHERE idempotency_key = %s AND status = %s "
            "ORDER BY created_at DESC LIMIT %s",
            (idempotency_key, status, _MAX_CANDIDATES_PER_STATUS),
        )
        for candidate in candidates:
            if candidate["id"] == row["id"]:
                continue
            if await _request_matches(db, candidate["id"], request_fingerprint):
                return candidate["id"]

    return None


async def _request_matches(db: LlmRuntimeDB, task_id: str, fingerprint: str) -> bool:
    row = await db.fetchone(
        "SELECT messages_json, input_json, params_json, expected_output_type "
        "FROM agent_llm_requests WHERE task_id = %s",
        (task_id,),
    )
    if row is None:
        return False
    stored = request_fingerprint(
        messages_json=row["messages_json"],
        input_json=row["input_json"],
        params_json=row["params_json"],
        expected_output_type=row["expected_output_type"],
    )
    return stored == fingerprint
