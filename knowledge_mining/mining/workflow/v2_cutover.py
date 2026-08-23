"""Explicit, auditable removal of legacy mining-runtime output.

This module deliberately has no application startup hook.  Operators first
generate a read-only plan, then must provide that exact plan's confirmation
token to execute the deletion.  The delete allow-list is restricted to mining
runtime output tables; KB metadata, asset documents, and object-storage state
are never queried or modified.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import uuid
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal


_Dialect = Literal["postgresql", "sqlite"]
_CUTOVER_VERSION = "v2-mining-output-cutover-1"
_TERMINAL_STATUSES = ("completed", "failed", "cancelled")
_RUN_TABLE = "mining_runs"
_OUTPUT_TABLES = (
    "mining_workflow_node_events",
    "mining_run_stage_events",
    "mining_run_documents",
)


@dataclass(frozen=True)
class CutoverPlan:
    """A read-only inventory and the confirmation bound to that inventory."""

    database_identifier: str
    counts: dict[str, int]
    legacy_run_count: int
    active_legacy_run_count: int
    confirmation_token: str

    @property
    def total_rows(self) -> int:
        return sum(self.counts.values())

    @property
    def is_executable(self) -> bool:
        return self.active_legacy_run_count == 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "cutover_version": _CUTOVER_VERSION,
            "database_identifier": self.database_identifier,
            "counts": dict(self.counts),
            "legacy_run_count": self.legacy_run_count,
            "active_legacy_run_count": self.active_legacy_run_count,
            "total_rows": self.total_rows,
            "is_executable": self.is_executable,
            "confirmation_token": self.confirmation_token,
        }


@dataclass(frozen=True)
class CutoverResult:
    """Durably audited result of one confirmed cutover."""

    audit_id: str
    deleted_counts: dict[str, int]

    @property
    def deleted_row_count(self) -> int:
        return sum(self.deleted_counts.values())

    def as_dict(self) -> dict[str, Any]:
        return {
            "audit_id": self.audit_id,
            "deleted_counts": dict(self.deleted_counts),
            "deleted_row_count": self.deleted_row_count,
        }


class LegacyMiningOutputCutoverService:
    """Clear all completed historical mining runtime rows on one connection.

    The caller owns the connection.  No config discovery or implicit database
    connection is allowed, which prevents this maintenance action from using a
    surprising default database.
    """

    def __init__(self, connection: Any, *, dialect: _Dialect = "postgresql") -> None:
        if dialect not in ("postgresql", "sqlite"):
            raise ValueError("dialect must be 'postgresql' or 'sqlite'")
        self._connection = connection
        self._dialect = dialect

    def plan(self) -> CutoverPlan:
        """Return a read-only cutover plan; this method never creates tables."""
        self._require_table(_RUN_TABLE)
        predicate = self._legacy_run_predicate()
        counts: dict[str, int] = {}
        for table in _OUTPUT_TABLES:
            if self._table_exists(table):
                counts[table] = self._count(
                    f"SELECT COUNT(*) FROM {table} output "
                    f"JOIN {_RUN_TABLE} run ON run.id = output.run_id "
                    f"WHERE {predicate}"
                )
        counts[_RUN_TABLE] = self._count(
            f"SELECT COUNT(*) FROM {_RUN_TABLE} run WHERE {predicate}"
        )
        legacy_run_count = counts[_RUN_TABLE]
        terminal = ", ".join(self._quote(value) for value in _TERMINAL_STATUSES)
        active_legacy_run_count = self._count(
            f"SELECT COUNT(*) FROM {_RUN_TABLE} run "
            f"WHERE run.status NOT IN ({terminal})"
        )
        token = self._confirmation_token(
            database_identifier=self._database_identifier(),
            counts=counts,
            active_legacy_run_count=active_legacy_run_count,
        )
        return CutoverPlan(
            database_identifier=self._database_identifier(),
            counts=counts,
            legacy_run_count=legacy_run_count,
            active_legacy_run_count=active_legacy_run_count,
            confirmation_token=token,
        )

    def execute(self, confirmation_token: str) -> CutoverResult:
        """Delete the planned allow-list only after exact, fresh confirmation."""
        if not isinstance(confirmation_token, str) or not confirmation_token.strip():
            raise ValueError("explicit confirmation token is required")

        # Validate before opening a transaction.  Besides failing fast, this
        # avoids rolling back any caller-owned transaction merely because a
        # human pasted an incomplete confirmation token.
        preflight_plan = self.plan()
        if not preflight_plan.is_executable:
            raise RuntimeError(
                "cannot cut over while active legacy mining runs exist; stop workers "
                "and resolve queued/running/review/interrupted runs first"
            )
        if confirmation_token != preflight_plan.confirmation_token:
            raise ValueError(
                "explicit confirmation token does not match the current read-only plan"
            )

        with self._transaction():
            self._lock_output_tables()
            fresh_plan = self.plan()
            if not fresh_plan.is_executable:
                raise RuntimeError(
                    "cannot cut over while active legacy mining runs exist; stop workers "
                    "and resolve queued/running/review/interrupted runs first"
                )
            if confirmation_token != fresh_plan.confirmation_token:
                raise ValueError(
                    "explicit confirmation token does not match the current read-only plan"
                )

            self._create_audit_table()
            predicate = self._legacy_run_predicate(alias="")
            deleted: dict[str, int] = {}
            for table in _OUTPUT_TABLES:
                if self._table_exists(table):
                    deleted[table] = self._delete_outputs(table, predicate)
            deleted[_RUN_TABLE] = self._rowcount(
                self._execute(f"DELETE FROM {_RUN_TABLE} WHERE {predicate}")
            )
            if deleted != fresh_plan.counts:
                raise RuntimeError("cutover counts changed during the protected transaction")

            audit_id = uuid.uuid4().hex
            self._insert_audit(audit_id, fresh_plan, deleted)
        return CutoverResult(audit_id=audit_id, deleted_counts=deleted)

    def _delete_outputs(self, table: str, predicate: str) -> int:
        cursor = self._execute(
            f"DELETE FROM {table} WHERE run_id IN "
            f"(SELECT id FROM {_RUN_TABLE} run WHERE {predicate})"
        )
        return self._rowcount(cursor)

    def _legacy_run_predicate(self, *, alias: str = "run.") -> str:
        # Cutover happens before v2 is opened to traffic.  The approved product
        # policy is to clear every completed historical mining result, whether
        # it was produced by the legacy or the earlier workflow engine.
        terminal = ", ".join(self._quote(value) for value in _TERMINAL_STATUSES)
        return f"{alias}status IN ({terminal})"

    def _confirmation_token(
        self,
        *,
        database_identifier: str,
        counts: dict[str, int],
        active_legacy_run_count: int,
    ) -> str:
        payload = json.dumps(
            {
                "cutover_version": _CUTOVER_VERSION,
                "database_identifier": database_identifier,
                "counts": counts,
                "active_legacy_run_count": active_legacy_run_count,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        return f"DELETE_LEGACY_MINING_OUTPUTS:{digest}"

    def _create_audit_table(self) -> None:
        self._execute(
            "CREATE TABLE IF NOT EXISTS mining_v2_cutover_audits ("
            "id TEXT PRIMARY KEY, executed_at TEXT NOT NULL, "
            "cutover_version TEXT NOT NULL, confirmation_digest TEXT NOT NULL, "
            "legacy_run_count INTEGER NOT NULL, deleted_row_count INTEGER NOT NULL, "
            "deleted_counts_json TEXT NOT NULL)"
        )

    def _insert_audit(
        self,
        audit_id: str,
        plan: CutoverPlan,
        deleted_counts: dict[str, int],
    ) -> None:
        values = (
            audit_id,
            datetime.now(timezone.utc).isoformat(),
            _CUTOVER_VERSION,
            plan.confirmation_token.rsplit(":", 1)[-1],
            plan.legacy_run_count,
            sum(deleted_counts.values()),
            json.dumps(deleted_counts, sort_keys=True, separators=(",", ":")),
        )
        placeholder = "?" if self._dialect == "sqlite" else "%s"
        self._execute(
            "INSERT INTO mining_v2_cutover_audits "
            "(id, executed_at, cutover_version, confirmation_digest, legacy_run_count, "
            "deleted_row_count, deleted_counts_json) VALUES "
            f"({', '.join([placeholder] * len(values))})",
            values,
        )

    def _lock_output_tables(self) -> None:
        if self._dialect != "postgresql":
            return
        tables = [_RUN_TABLE, *(
            table for table in _OUTPUT_TABLES if self._table_exists(table)
        )]
        self._execute(
            "LOCK TABLE " + ", ".join(tables) + " IN SHARE ROW EXCLUSIVE MODE"
        )

    def _transaction(self):
        transaction = getattr(self._connection, "transaction", None)
        if callable(transaction):
            return transaction()
        if hasattr(self._connection, "__enter__"):
            return self._connection
        return nullcontext()

    def _table_exists(self, table: str) -> bool:
        if self._dialect == "sqlite":
            row = self._execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
            ).fetchone()
        else:
            row = self._execute(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = current_schema() AND table_name = %s",
                (table,),
            ).fetchone()
        return row is not None

    def _column_exists(self, table: str, column: str) -> bool:
        if self._dialect == "sqlite":
            rows = self._execute(f"PRAGMA table_info({table})").fetchall()
            return any(row[1] == column for row in rows)
        row = self._execute(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = %s AND column_name = %s",
            (table, column),
        ).fetchone()
        return row is not None

    def _require_table(self, table: str) -> None:
        if not self._table_exists(table):
            raise RuntimeError(f"required mining runtime table is missing: {table}")

    def _database_identifier(self) -> str:
        if self._dialect == "sqlite":
            rows = self._execute("PRAGMA database_list").fetchall()
            return str(rows[0][2] or ":memory:")
        return str(self._first_value(self._execute("SELECT current_database()").fetchone()))

    def _count(self, statement: str) -> int:
        return int(self._first_value(self._execute(statement).fetchone()))

    def _execute(self, statement: str, parameters: tuple[Any, ...] | None = None):
        if parameters is None:
            return self._connection.execute(statement)
        return self._connection.execute(statement, parameters)

    @staticmethod
    def _rowcount(cursor: Any) -> int:
        if cursor.rowcount is None or cursor.rowcount < 0:
            raise RuntimeError("database did not report a reliable deleted-row count")
        return int(cursor.rowcount)

    @staticmethod
    def _quote(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    @staticmethod
    def _first_value(row: Any) -> Any:
        if isinstance(row, dict):
            return next(iter(row.values()))
        return row[0]


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.  Planning is the default and never writes to the DB."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True, help="explicit PostgreSQL URL")
    parser.add_argument("--execute", action="store_true", help="perform the confirmed cutover")
    parser.add_argument("--confirm", help="exact token printed by the read-only plan")
    args = parser.parse_args(argv)
    if args.execute and not args.confirm:
        parser.error("--execute requires --confirm with the exact plan token")
    if args.confirm and not args.execute:
        parser.error("--confirm is only valid with --execute")
    if not args.database_url.startswith(("postgresql://", "postgres://")):
        parser.error("--database-url must be a PostgreSQL URL")

    import psycopg

    try:
        with psycopg.connect(args.database_url) as connection:
            service = LegacyMiningOutputCutoverService(connection, dialect="postgresql")
            output = (
                service.execute(args.confirm)
                if args.execute
                else service.plan()
            )
    except Exception as exc:  # never echo a credential-bearing URL/driver context
        if isinstance(exc, psycopg.Error):
            print("v2 cutover failed: database operation failed", file=sys.stderr)
        else:
            print(f"v2 cutover failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(output.as_dict(), ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["CutoverPlan", "CutoverResult", "LegacyMiningOutputCutoverService", "main"]
