"""Backfill user_domains bindings for existing kb_users (51号 spec §4.2).

存量用户迁移：为每个 active 用户写入 user_domains 绑定行。规则：
  - admin（site_role='admin'）不写绑定行（admin 全通，无需绑定），打印 [skip-admin]；
  - 有库用户（active 库的 owner 或 kb_members 成员）：绑定其库所在全部域（DISTINCT 排序）；
  - 无库用户：绑 --fallback-domain 指定域；未指定时取 active KB 数最多的域
    （GROUP BY domain ORDER BY n DESC, domain ASC，并列字典序）；
  - 幂等：INSERT ... ON CONFLICT DO NOTHING，可重复执行。

用法：
    python backfill_user_domains.py                        # 实际执行
    python backfill_user_domains.py --dry-run              # 只打印计划，不改库
    python backfill_user_domains.py --fallback-domain X    # 指定无库用户兜底域

--dry-run 严格只读（autocommit=False 且不执行任何写语句）。运维脚本，
可能对生产库跑；出错打印清晰信息而非栈崩。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import psycopg


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill user_domains bindings for existing users"
    )
    parser.add_argument("--dry-run", action="store_true", help="print plan, do not write")
    parser.add_argument(
        "--fallback-domain",
        default=None,
        help="domain for users with no active KBs (default: domain with most active KBs)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from knowledge_mining.mining.infra.pg_config import MiningDbConfig

    # PG_* 环境变量优先（测试库直连）；否则走控制面配置（MiningDbConfig 无参，
    # 需 main_control_service 在 CONTROL_PLANE_BASE_URL 可达）。
    if os.getenv("PG_HOST") and os.getenv("PG_DBNAME"):
        cfg = MiningDbConfig(
            pg_host=os.environ["PG_HOST"],
            pg_port=int(os.getenv("PG_PORT", "5432")),
            pg_dbname=os.environ["PG_DBNAME"],
            pg_user=os.getenv("PG_USER", "kb_user"),
            pg_password=os.getenv("PG_PASSWORD", ""),
        )
    else:
        cfg = MiningDbConfig()
    print(f"target: {cfg.pg_host}/{cfg.pg_dbname} (dry_run={args.dry_run})")

    try:
        # dry-run 时 autocommit=False 且全程无写语句——严格只读。
        conn = psycopg.connect(cfg.conninfo, autocommit=not args.dry_run)
    except Exception as exc:  # 连接/配置失败：打印清楚而不是栈崩
        print(f"[ERROR] cannot connect to database ({cfg.pg_host}/{cfg.pg_dbname}): {exc}")
        return 1

    try:
        with conn.cursor() as cur:
            # 1) 兜底域：显式参数优先，否则从数据推导（active KB 最多；并列字典序）
            fallback_domain = args.fallback_domain
            if fallback_domain is None:
                cur.execute(
                    "SELECT domain FROM knowledge_bases WHERE status = 'active' "
                    "GROUP BY domain ORDER BY COUNT(*) DESC, domain ASC LIMIT 1"
                )
                row = cur.fetchone()
                fallback_domain = row[0] if row else None
            print(f"fallback_domain: {fallback_domain}")

            # 2) 全量 active 用户（admin 判定只看 active 用户）
            cur.execute(
                "SELECT id, username, site_role FROM kb_users "
                "WHERE status = 'active' ORDER BY username"
            )
            users = cur.fetchall()

            # 3) 有库用户的域集合（owner 或 kb_members 成员，库 status='active'）
            cur.execute(
                """
                SELECT DISTINCT u.id, kb.domain
                  FROM kb_users u
                  JOIN knowledge_bases kb
                    ON (kb.owner_id = u.id
                        OR kb.id IN (SELECT m.kb_id FROM kb_members m WHERE m.user_id = u.id))
                 WHERE u.status = 'active' AND kb.status = 'active'
                """
            )
            user_domains: dict[str, list[str]] = {}
            for user_id, domain in cur.fetchall():
                user_domains.setdefault(user_id, []).append(domain)
            for domains in user_domains.values():
                domains.sort()

        total_inserted = 0
        total_skipped = 0
        admin_skipped = 0
        zero_binding = 0
        for user_id, username, site_role in users:
            if site_role == "admin":
                print(f"  [skip-admin] {username}")
                admin_skipped += 1
                continue
            domains = user_domains.get(user_id)
            if domains:
                print(f"  [bind] {username} -> {domains}")
            elif fallback_domain is not None:
                domains = [fallback_domain]
                print(f"  [bind] {username} -> {domains} (fallback)")
            else:
                print(f"  [WARN-zero-binding] {username}: no active KBs and no fallback domain")
                zero_binding += 1
                continue
            if not args.dry_run:
                with conn.cursor() as cur:
                    for d in domains:
                        cur.execute(
                            "INSERT INTO user_domains (user_id, domain) VALUES (%s, %s) "
                            "ON CONFLICT (user_id, domain) DO NOTHING",
                            (user_id, d),
                        )
                        if cur.rowcount > 0:
                            total_inserted += 1
                        else:
                            total_skipped += 1
        if args.dry_run:
            print(
                f"[dry-run] would have bound users; no rows written. "
                f"admin_skipped={admin_skipped}, zero_binding={zero_binding}"
            )
        else:
            print(
                f"done: inserted {total_inserted} binding rows, "
                f"{total_skipped} skipped (already present), "
                f"admin_skipped={admin_skipped}, zero_binding={zero_binding}"
            )
    except psycopg.Error as exc:
        print(f"[ERROR] database failure: {exc}")
        return 1
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
