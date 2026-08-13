"""File Management package — Upload Session orchestration (M1.2, WP1B).

Hexagonal layering (ADR-0003 D-022):
- ``contracts/file_management.py`` — Repository Protocols + frozen records.
- ``service.py`` — ``UploadSessionService`` orchestration (depends only on the
  Protocols + ``ObjectStorePort``).
- ``repositories_memory.py`` — in-memory fake repos (tests + local dev).
- ``repositories_pg.py`` — PostgreSQL repos (psycopg pool, 008 DDL aligned).

References:
- SRS §4.1A (upload transaction), §4.3 / §4.3A (document current content),
  §C01 (error codes), §9.0A / §9.5 (upload session state machine + recovery).
"""
from __future__ import annotations
