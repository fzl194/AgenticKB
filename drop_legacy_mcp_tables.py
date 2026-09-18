"""Drop legacy mcp_access / mcp_open_kbs tables (51号批次3 Task 3).

数据面退役：批次2 已把旧 mcp_access（一人一钥）迁移到 mcp_keys/mcp_key_open_kbs，
批次3 代码面（008/010）已摘除旧表注册——本脚本在确认迁移落地后手动 DROP 旧表。

前置条件（操作员自查）：
  - 批次2 迁移三连完成（backfill_mcp_keys.py 报告已复核）
  - 回滚窗口已过（删表后无法回滚到批次2 前）

不可逆警告：DROP 后旧钥匙数据（mcp_access/mcp_open_kbs）永久消失。
spec 7.2 已显式接受此损失——迁移报告是唯一留档。

退出码：
    0 = 删除完成 / 旧表已退役（无事可做）
    1 = 防呆门禁拒绝、缺少 --yes、或 DB 错误

用法：
    python drop_legacy_mcp_tables.py --dry-run   # 只读报告，零写
    python drop_legacy_mcp_tables.py --yes       # 实删（必须显式携带）
    python drop_legacy_mcp_tables.py --yes --force  # 越过计数门禁（迁移报告已人工复核）

--dry-run 严格只读。运维脚本，可能对生产库跑；出错打印清晰信息而非栈崩。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import psycopg

LEGACY_TABLES = ("mcp_open_kbs", "mcp_access")  # 删除顺序：FK 依赖，先 open_kbs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Drop legacy mcp_access/mcp_open_kbs (batch 3 retirement)"
    )
    parser.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    parser.add_argument("--yes", action="store_true",
                        help="actually drop tables (required for real run)")
    parser.add_argument("--force", action="store_true",
                        help="bypass count gate (after manual review of backfill report)")
    return parser.parse_args()


def _gate_counts(keys_n: int, access_n: int, force: bool) -> tuple[bool, str]:
    """计数门禁 b：mcp_keys 行数 < mcp_access 行数 → 迁移不完整，拒绝。

    返回 (是否放行, 消息)。--force 时越过（返回放行 + 醒目警告消息）。
    """
    if keys_n >= access_n:
        return True, f"gate-ok: mcp_keys={keys_n} >= mcp_access={access_n}"
    msg = (f"迁移不完整（mcp_keys={keys_n} < mcp_access={access_n}），"
           f"先跑 backfill_mcp_keys.py；确认迁移报告后可 --force")
    if force:
        return True, f"[FORCE] !!! 越过计数门禁（{msg}）——操作员自担风险 !!!"
    return False, msg


def main() -> int:
    args = parse_args()

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from knowledge_mining.mining.infra.pg_config import MiningDbConfig

    # PG_* 环境变量优先（测试库直连）；否则走控制面配置（同 backfill_mcp_keys）。
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
    print(f"target: {cfg.pg_host}/{cfg.pg_dbname} "
          f"(dry_run={args.dry_run}, yes={args.yes}, force={args.force})")

    if not args.dry_run and not args.yes:
        print("[ERROR] 实删必须显式携带 --yes（防误操作）。先 --dry-run 看报告。")
        return 1

    try:
        conn = psycopg.connect(cfg.conninfo, autocommit=False)
    except Exception as exc:
        print(f"[ERROR] cannot connect to database ({cfg.pg_host}/{cfg.pg_dbname}): {exc}")
        return 1

    try:
        with conn.cursor() as cur:
            # 门禁 a：新表体系未建立 → 禁止删旧表
            cur.execute("SELECT to_regclass('mcp_keys')")
            if cur.fetchone()[0] is None:
                conn.close()
                print("[ERROR] mcp_keys 不存在——新表体系未建立，禁止删除旧表。")
                return 1

            # 报告三表行数（给操作员对照迁移已落地）
            counts: dict[str, int | None] = {}
            for tbl in ("mcp_access", "mcp_open_kbs", "mcp_keys"):
                cur.execute(f"SELECT to_regclass('{tbl}')")
                if cur.fetchone()[0] is None:
                    counts[tbl] = None
                    print(f"  {tbl}: (不存在)")
                else:
                    cur.execute(f"SELECT count(*) FROM {tbl}")
                    counts[tbl] = cur.fetchone()[0]
                    print(f"  {tbl}: {counts[tbl]} 行")

            # 两表都已不在 → 已退役，幂等退出
            if counts["mcp_access"] is None and counts["mcp_open_kbs"] is None:
                conn.close()
                print("[INFO] 旧表 mcp_access/mcp_open_kbs 已退役（均不存在），无事可做。")
                return 0
            if counts["mcp_access"] is None or counts["mcp_open_kbs"] is None:
                missing = ("mcp_access" if counts["mcp_access"] is None
                           else "mcp_open_kbs")
                print(f"[WARN] 异常态：{missing} 不存在而另一张仍在"
                      "（两表理论上同生共死）——继续删除存在的那张。")

            # 门禁 b：计数比对（mcp_keys 非 None 已由门禁 a 保证——显式判断防 -O 失效）
            if counts["mcp_keys"] is None:
                conn.close()
                print("[ERROR] mcp_keys 不存在（内部状态不一致），拒绝删除。")
                return 1
            access_n = counts["mcp_access"] or 0
            ok, msg = _gate_counts(counts["mcp_keys"], access_n, args.force)
            print(f"计数门禁: {msg}")
            if not ok:
                conn.close()
                print("exit=1: 门禁拒绝。")
                return 1

            to_drop = [t for t in LEGACY_TABLES if counts[t] is not None]
            if args.dry_run:
                conn.close()
                print(f"[dry-run] 将删除 {len(to_drop)} 张表（行数如上）: "
                      + ", ".join(to_drop))
                print("[dry-run] 未写任何 DDL。")
                return 0

            # 实删：先 mcp_open_kbs（FK 依赖）→ mcp_access；显式 RESTRICT（不用 CASCADE）。
            # 循环内不打印成功——第二张失败回滚时终端不能留下第一张假 [OK]；
            # commit() 之后统一打印，保证 [OK] 只在事务真正提交后出现。
            for tbl in to_drop:
                cur.execute(f"DROP TABLE IF EXISTS {tbl} RESTRICT")
            conn.commit()
            for tbl in to_drop:
                print(f"[OK] DROP TABLE {tbl} 成功（已提交）")

            # 复核
            for tbl in to_drop:
                cur.execute(f"SELECT to_regclass('{tbl}')")
                print(f"  verify: to_regclass('{tbl}') = {cur.fetchone()[0]}")
        conn.close()
        return 0
    except psycopg.Error as exc:
        try:
            conn.rollback()
            conn.close()
        except Exception:
            pass
        print(f"[ERROR] database failure (rolled back): {exc}")
        return 1
    except Exception as exc:
        # 非 psycopg 异常兜底：同样回滚关闭，单行报错而非裸栈崩
        try:
            conn.rollback()
            conn.close()
        except Exception:
            pass
        print(f"[ERROR] unexpected failure (rolled back): {exc!r}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
