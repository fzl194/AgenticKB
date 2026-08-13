"""Tests for the File Management layer (M1.2).

- ``test_upload_session_service.py`` — full service flow over in-memory fakes
  + ``FakeObjectStore`` (no PostgreSQL).
- ``test_repositories_memory.py`` — the in-memory fake repos in isolation.
- ``test_repositories_pg.py`` — PG-gated smoke tests (skip without PG).

All service / memory tests are hermetic (no PG, no network, no MinIO).
"""
