"""Backfill legacy asset_documents (kb_id IS NULL) into a per-domain "default" KB.

P6.2 迁移脚本。存量文档在 P1 之前入库，kb_id 为 NULL（未归类）。本脚本为每个域
建一个 system owner 的「默认 KB」（visibility=private），把存量文档归入。

用法：
    python backfill_default_kb.py            # 实际执行
    python backfill_default_kb.py --dry-run  # 只打印计划，不改库

连接走 .env 的 PG_*（MiningDbConfig）。库名必须以 _test 结尾的护栏这里不强制——
这是运维脚本，可能对生产库跑；--dry-run 先看清楚。
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import psycopg


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill legacy docs into per-domain default KB")
    parser.add_argument("--dry-run", action="store_true", help="print plan, do not write")
    args = parser.parse_args()

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from knowledge_mining.mining.infra.pg_config import MiningDbConfig

    cfg = MiningDbConfig()
    print(f"target: {cfg.pg_host}/{cfg.pg_dbname} (dry_run={args.dry_run})")

    conn = psycopg.connect(cfg.conninfo, autocommit=not args.dry_run)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT domain, COUNT(*) AS n FROM asset_documents "
                "WHERE kb_id IS NULL GROUP BY domain ORDER BY domain"
            )
            orphans = cur.fetchall()
        if not orphans:
            print("no legacy docs (kb_id IS NULL) — nothing to backfill.")
            return 0

        # ensure system user
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO kb_users (id, username, display_name, status, created_at)
                   VALUES (%s, 'system', 'System (backfill)', 'active', %s)
                   ON CONFLICT (username) DO UPDATE SET display_name = EXCLUDED.display_name
                   RETURNING id""",
                (uuid.uuid4().hex, _utcnow()),
            )
            system_user_id = cur.fetchone()[0]

        total = 0
        for domain, n in orphans:
            # ensure default KB for this domain
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO knowledge_bases
                         (id, domain, name, description, owner_id, visibility, status,
                          metadata_json, created_at, updated_at)
                       VALUES (%s, %s, 'default', 'auto-created by backfill_default_kb.py',
                               %s, 'private', 'active', %s::jsonb, %s, %s)
                       ON CONFLICT (domain, name) DO UPDATE SET updated_at = EXCLUDED.updated_at
                       RETURNING id""",
                    (uuid.uuid4().hex, domain, system_user_id, json.dumps({"backfill": True}),
                     _utcnow(), _utcnow()),
                )
                default_kb_id = cur.fetchone()[0]
            print(f"  domain={domain}: {n} docs -> default KB {default_kb_id}")
            if not args.dry_run:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE asset_documents SET kb_id = %s "
                        "WHERE kb_id IS NULL AND domain = %s",
                        (default_kb_id, domain),
                    )
                    total += cur.rowcount
        if args.dry_run:
            print(f"[dry-run] would have backfilled {sum(n for _, n in orphans)} docs.")
        else:
            print(f"backfilled {total} docs into per-domain default KBs.")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
