"""Persistent per-domain FIFO dispatcher backed by ``mining_runs``."""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any


logger = logging.getLogger(__name__)
Candidate = dict[str, Any]
Outcome = dict[str, Any] | None


class DomainRunQueueDispatcher:
    """Drain one domain serially; database leases remain the ownership fence."""

    def __init__(
        self,
        *,
        next_candidate: Callable[[str], Candidate | None],
        execute_run: Callable[[Candidate], Outcome],
        resume_run: Callable[[Candidate], Outcome],
        record_failure: Callable[[Candidate, Exception], None],
    ) -> None:
        self._next_candidate = next_candidate
        self._execute_run = execute_run
        self._resume_run = resume_run
        self._record_failure = record_failure
        self._guard = threading.Lock()
        self._threads: dict[str, threading.Thread] = {}
        self._wakeups: dict[str, threading.Event] = {}
        self._closed = False

    def drain(self, domain: str) -> int:
        """Synchronously drain available work; used by worker threads and tests."""
        processed = 0
        while not self._closed:
            candidate = self._next_candidate(domain)
            if candidate is None:
                return processed
            try:
                if candidate.get("status") == "interrupted":
                    outcome = self._resume_run(candidate)
                else:
                    outcome = self._execute_run(candidate)
            except Exception as exc:  # noqa: BLE001 - one Run must not stop the queue
                logger.exception("Queued mining run %s failed", candidate.get("id"))
                self._record_failure(candidate, exc)
                processed += 1
                continue
            if isinstance(outcome, dict) and outcome.get("status") == "claimed_elsewhere":
                return processed
            processed += 1
        return processed

    def kick(self, domain: str) -> bool:
        """Idempotently start or wake the one local dispatcher for ``domain``."""
        with self._guard:
            if self._closed:
                return False
            existing = self._threads.get(domain)
            if existing is not None and existing.is_alive():
                self._wakeups[domain].set()
                return False
            wakeup = threading.Event()
            wakeup.set()
            thread = threading.Thread(
                target=self._worker,
                args=(domain, wakeup),
                daemon=True,
                name=f"mining-queue-{domain}",
            )
            self._threads[domain] = thread
            self._wakeups[domain] = wakeup
            thread.start()
            return True

    def close(self) -> None:
        with self._guard:
            self._closed = True
            for wakeup in self._wakeups.values():
                wakeup.set()

    def _worker(self, domain: str, wakeup: threading.Event) -> None:
        while not self._closed:
            wakeup.clear()
            self.drain(domain)
            with self._guard:
                if wakeup.is_set() and not self._closed:
                    continue
                if self._threads.get(domain) is threading.current_thread():
                    self._threads.pop(domain, None)
                    self._wakeups.pop(domain, None)
                return


def build_domain_run_dispatcher(domain_pools: Any, db_config: Any) -> DomainRunQueueDispatcher:
    """Compose the dispatcher without introducing a second queue store."""

    def next_candidate(domain: str) -> Candidate | None:
        pool = domain_pools.sync_pool(domain)
        with pool.connection() as conn:
            cursor = conn.execute(
                """SELECT id, domain, status, input_path, kb_id
                   FROM mining_runs
                   WHERE domain = %s
                     AND (
                         status = 'queued'
                         OR (status = 'interrupted' AND execution_engine = 'workflow')
                     )
                     AND (
                         worker_id IS NULL OR lease_until IS NULL
                         OR lease_until < NOW()
                     )
                   ORDER BY
                     CASE status WHEN 'interrupted' THEN 0 ELSE 1 END,
                     started_at, id
                   LIMIT 1""",
                (domain,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def execute(candidate: Candidate) -> Outcome:
        from knowledge_mining.mining.jobs.run import run

        return run(
            str(candidate.get("input_path") or ""),
            db_config=db_config,
            domain=str(candidate["domain"]),
            run_id=str(candidate["id"]),
        )

    def resume(candidate: Candidate) -> Outcome:
        from knowledge_mining.mining.jobs.run import resume

        return resume(
            str(candidate["id"]),
            db_config=db_config,
            domain=str(candidate["domain"]),
        )

    def record_failure(candidate: Candidate, error: Exception) -> None:
        pool = domain_pools.sync_pool(str(candidate["domain"]))
        with pool.connection() as conn:
            conn.execute(
                """UPDATE mining_runs
                   SET status = 'failed', finished_at = NOW(),
                       error_summary = %s, worker_id = NULL, lease_until = NULL
                   WHERE id = %s
                     AND status IN ('queued', 'running', 'interrupted')""",
                (str(error)[:500], candidate["id"]),
            )

    return DomainRunQueueDispatcher(
        next_candidate=next_candidate,
        execute_run=execute,
        resume_run=resume,
        record_failure=record_failure,
    )
