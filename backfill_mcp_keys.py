"""Backfill mcp_keys from legacy mcp_access (51号批次2 Task 9).

存量迁移：把旧 mcp_access（一人一钥）每行搬成一把 mcp_keys 单域钥匙 +
mcp_key_open_kbs 开放库勾选。规则：
  - 幂等：旧行 key_hash 已存在于 mcp_keys → skip（跑两遍不产生第二把）；
    INSERT 亦带显式 ON CONFLICT ... DO NOTHING 兜底（并发双跑安全）。
  - 域推导：a) 开放库（active）按 kb.domain 计数取最多（并列字典序最小）；
    b) 开放库为空 → user_domains 字典序第一个；c) --fallback-domain 只覆盖 b；
    d) 仍无且用户为 admin → get_default_domain()；e) 仍无 → WARN + skip。
  - open_tools 归一（_migrate_open_tools）：None 语义与读路径对齐——
    normalize 返回 None（无 legacy 名）→ 原样保留（子集不放大）；
    返回空列表（全退役名）→ NULL=全开。
  - active+revoked 旧行都迁（保状态）；跨域/失活开放库勾选丢弃并打印清单
    （给用户重建第二把钥匙的依据）。
  - 失败续跑：单用户写库失败 → rollback + [FAIL] 打印，继续下一用户。
  - 回滚安全：本脚本只写 mcp_keys/mcp_key_open_kbs，不删/不改旧
    mcp_access/mcp_open_kbs——回滚=删新表行即可。

退出码（部署清单引用此约定）：
    0 = 全部处理完（含正常 skip）
    1 = 有单用户失败（failed>0）或 DB 连接/致命错
    2 = 成功但有需人工复核的跳过（zero_binding / skipped_name_conflict /
        orphan_rows 任一 >0）

用法：
    python backfill_mcp_keys.py                        # 实际执行
    python backfill_mcp_keys.py --dry-run              # 只打印报告，不写库
    python backfill_mcp_keys.py --fallback-domain X    # 空开放库用户的兜底域

--dry-run 严格只读。运维脚本，可能对生产库跑；出错打印清晰信息而非栈崩。
"""
from __future__ import annotations

import argparse
import os
import sys
import uuid
from pathlib import Path

import psycopg
from psycopg.types.json import Json


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill mcp_keys/mcp_key_open_kbs from legacy mcp_access"
    )
    parser.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    parser.add_argument(
        "--fallback-domain",
        default=None,
        help="override fallback domain for users whose open-KB list is empty "
             "(does NOT override derivation from non-empty open KBs)",
    )
    return parser.parse_args()


def derive_open_kb_domain(domain_counts: dict[str, int]) -> str | None:
    """开放库域计数取最多；并列取域名字典序最小。"""
    if not domain_counts:
        return None
    return max(sorted(domain_counts), key=lambda d: domain_counts[d])


def _derive_domain(
    *,
    kbs: list[tuple],
    fallback_domain: str | None,
    first_binding: str | None,
    site_role: str | None,
    get_default_domain,
) -> tuple[str | None, str]:
    """域推导五级链（规格 2a-2d）。返回 (domain, 来源标签)。

    a) 开放库（active）按 kb.domain 计数取最多（并列字典序最小）；
    b) 开放库为空 → --fallback-domain 或 user_domains 字典序第一个；
    c) 仍无且 admin → get_default_domain()；d) 仍无 → (None, "")。
    """
    active_domains: dict[str, int] = {}
    for _, domain, _, kb_status in kbs:
        if kb_status == "active":
            active_domains[domain] = active_domains.get(domain, 0) + 1
    domain = derive_open_kb_domain(active_domains)
    if domain is not None:
        return domain, "open-kbs"
    domain = fallback_domain or first_binding
    if domain is not None:
        return domain, "fallback" if fallback_domain else "user_domains"
    if site_role == "admin":
        return get_default_domain(), "admin-default"
    return None, ""


def _migrate_open_tools(orig, normalize_legacy_open_tools):
    """open_tools 迁移值——None 语义与读路径（list_keys/验钥）对齐。

    normalize 返回 None = 无 legacy 名、无需迁移 → 原样保留（子集不放大）；
    返回空列表 = 全是退役名 → 落 NULL=全开（旧体系不可表达"全关"）。
    """
    if orig is None:
        return None
    normalized = normalize_legacy_open_tools(list(orig))
    if normalized is None:
        return list(orig)
    return normalized or None


def main() -> int:
    args = parse_args()

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from knowledge_mining.mining.infra.pg_config import MiningDbConfig
    from knowledge_mining.mining.kb.services.mcp_key_service import (
        normalize_legacy_open_tools,
    )
    from knowledge_mining.mining.infra.domain_pack import get_default_domain

    # PG_* 环境变量优先（测试库直连）；否则走控制面配置（同 backfill_user_domains）。
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
        conn = psycopg.connect(cfg.conninfo, autocommit=False)
    except Exception as exc:
        print(f"[ERROR] cannot connect to database ({cfg.pg_host}/{cfg.pg_dbname}): {exc}")
        return 1

    total = 0
    migrated = 0
    skipped_hash_exists = 0
    skipped_name_conflict = 0
    zero_binding = 0
    orphan_rows = 0
    failed = 0
    dropped_total = 0
    dropped_detail: list[str] = []
    detail_lines: list[str] = []

    try:
        with conn.cursor() as cur:
            # 旧表全行（active+revoked 都迁，保状态）。LEFT JOIN：孤儿行
            # （kb_users 无此用户）告警跳过，不进 total。
            cur.execute(
                """SELECT a.user_id, a.key_hash, a.key_prefix, a.status,
                          a.open_tools, a.instructions, a.tool_descriptions,
                          u.username, u.site_role
                     FROM mcp_access a
                     LEFT JOIN kb_users u ON u.id = a.user_id
                    ORDER BY u.username NULLS LAST, a.user_id"""
            )
            access_rows = cur.fetchall()

            # 幂等闸批量预查：已迁的 key_hash 集合
            cur.execute("SELECT key_hash FROM mcp_keys")
            existing_hashes = {row[0] for row in cur.fetchall()}

            # 开放库勾选 + 库元数据（域/状态/名）
            cur.execute(
                """SELECT o.user_id, o.kb_id, kb.domain, kb.name, kb.status
                     FROM mcp_open_kbs o
                     JOIN knowledge_bases kb ON kb.id = o.kb_id"""
            )
            open_kbs: dict[str, list[tuple]] = {}
            for user_id, kb_id, domain, name, status in cur.fetchall():
                open_kbs.setdefault(user_id, []).append((kb_id, domain, name, status))

            # user_domains 字典序第一个（兜底 b）
            cur.execute(
                """SELECT user_id, MIN(domain) FROM user_domains
                    GROUP BY user_id"""
            )
            first_binding = dict(cur.fetchall())
    except psycopg.Error as exc:
        conn.rollback()
        conn.close()
        print(f"[ERROR] database failure (pre-scan, nothing processed): {exc}")
        return 1

    def report() -> int:
        print()
        print("=" * 72)
        print(
            f"total={total} migrated={'0 (dry-run)' if args.dry_run else migrated} "
            f"failed={failed} "
            f"skipped_hash_exists={skipped_hash_exists} "
            f"skipped_name_conflict={skipped_name_conflict} "
            f"zero_binding={zero_binding} orphan_rows={orphan_rows} "
            f"dropped_cross_domain_total={dropped_total}"
        )
        print("-" * 72)
        for line in detail_lines:
            print(f"  {line}")
        if dropped_detail:
            print("-" * 72)
            print("被丢弃的开放库勾选（跨域或已失活——用户如需保留请重建第二把对应域钥匙）：")
            for line in dropped_detail:
                print(f"  {line}")
        print("=" * 72)
        if args.dry_run:
            print("[dry-run] 未写任何行。")
        if failed > 0:
            print("exit=1: 存在单用户失败，请复核 [FAIL] 行后重跑（幂等）。")
            return 1
        if zero_binding > 0 or skipped_name_conflict > 0 or orphan_rows > 0:
            print("exit=2: 成功但有需人工复核的跳过（zero_binding/"
                  "skipped_name_conflict/orphan_rows）。")
            return 2
        return 0

    for (user_id, key_hash, key_prefix, status, open_tools,
         instructions, tool_descriptions, username, site_role) in access_rows:
        # 孤儿行：kb_users 无此用户——告警跳过，不进 total
        if username is None:
            orphan_rows += 1
            print(f"[WARN] orphan mcp_access row: user_id={user_id} "
                  "已跳过（kb_users 无此用户）")
            continue
        total += 1
        # 1) 幂等闸
        if key_hash in existing_hashes:
            skipped_hash_exists += 1
            detail_lines.append(f"{username}: [skipped] key_hash 已存在于 mcp_keys")
            continue

        # 2) 域推导
        kbs = open_kbs.get(user_id, [])
        domain, domain_src = _derive_domain(
            kbs=kbs,
            fallback_domain=args.fallback_domain,
            first_binding=first_binding.get(user_id),
            site_role=site_role,
            get_default_domain=get_default_domain,
        )
        if domain is None:
            zero_binding += 1
            print(f"[WARN-zero-binding] {username}: 无开放库、无域绑定、非 admin，跳过")
            detail_lines.append(f"{username}: [warn] 无法推导域，未迁移")
            continue

        # 3) 开放库分流：同域 active=保留；其余=丢弃（跨域/非 active）
        kept_ids = [kb_id for kb_id, d, _, s in kbs
                    if d == domain and s == "active"]
        dropped = [(name, d, s) for _, d, name, s in kbs
                   if not (d == domain and s == "active")]

        migrated_open_tools = _migrate_open_tools(open_tools, normalize_legacy_open_tools)

        key_id = uuid.uuid4().hex
        tag = "would-migrate" if args.dry_run else "migrated"
        detail_lines.append(
            f"{username}: 域={domain}({domain_src}) "
            f"开放库=保留{len(kept_ids)}/丢弃{len(dropped)} [{tag}]"
        )
        if dropped:
            dropped_total += len(dropped)
            items = ", ".join(
                f"({n}@{d}{'' if s == 'active' else f', status={s}'})"
                for n, d, s in dropped
            )
            dropped_detail.append(f"{username} -> [{items}]")

        # 4) 写库（每用户一个事务：钥匙行 + 开放库一起成败；失败续跑）
        if not args.dry_run:
            try:
                with conn.cursor() as cur:
                    inserted = False
                    name = "default"
                    # 同名撞车（该用户已有 'default' 钥匙且 key_hash 不同）→ 加后缀重试
                    for attempt in range(10):
                        cur.execute(
                            """INSERT INTO mcp_keys
                               (id, user_id, name, domain, key_hash, key_prefix,
                                status, open_tools, instructions, tool_descriptions)
                               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                               ON CONFLICT (user_id, name) DO NOTHING
                               RETURNING id""",
                            (key_id, user_id, name, domain, key_hash, key_prefix,
                             status,
                             Json(migrated_open_tools) if migrated_open_tools is not None else None,
                             instructions,
                             Json(tool_descriptions) if tool_descriptions is not None else None),
                        )
                        if cur.fetchone() is not None:
                            inserted = True
                            break
                        name = f"default-{attempt + 2}"
                    if inserted:
                        for kb_id in kept_ids:
                            cur.execute(
                                """INSERT INTO mcp_key_open_kbs (key_id, kb_id)
                                   VALUES (%s, %s)
                                   ON CONFLICT (key_id, kb_id) DO NOTHING""",
                                (key_id, kb_id),
                            )
                        migrated += 1
                        existing_hashes.add(key_hash)
                    else:
                        # 10 次同名仍撞——极端场景，单独计数
                        skipped_name_conflict += 1
                conn.commit()
            except psycopg.Error as exc:
                conn.rollback()
                failed += 1
                print(f"[FAIL] {username}: {exc}")
                continue
            except Exception as exc:
                # 非 DB 异常（编码/适配层等）同样按单用户失败续跑
                conn.rollback()
                failed += 1
                print(f"[FAIL] {username}: {exc!r}")
                continue

    # 连接级致命错（如连接被服务端断开）：循环内已全部 catch-continue，
    # 到这里说明循环结束；关连接并出报告（failed>0 时退出码 1）。
    try:
        conn.close()
    except Exception:
        pass

    return report()


if __name__ == "__main__":
    raise SystemExit(main())
