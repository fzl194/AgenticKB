"""受保护域资产 reset（批次8 M6/R8，24 号 §10.2/§10.3）.

clean break 重建流程的清库步骤：
- DROP v2 资产表族（让 schema.py 按新形态重建；存量 asset_raw_segments
  是 legacy 形态必须 DROP 而非复用）；
- TRUNCATE 派生资产/挖掘运行/范式/研究线图谱/旧检索缓存；
- **保留** control-plane 与存储层（用户/密钥/开放库/域注册/文档记录/
  存储对象/审计/LLM 观测）。

保护（任一不满足即退出）：
1. 必须 --confirm-domain-assets-reset 显式确认；
2. 目标 host/dbname 必须来自 domain_registry.yaml 的注册域；
3. FK 完备性：引用清理清单的表必须全部在清单内（防 TRUNCATE CASCADE
   波及保留表）；
4. 默认 dry-run：--execute 才真正执行。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import psycopg
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = REPO_ROOT / "main_control_service" / "config" / "domain_registry.yaml"

# v2 表族：DROP 后由 mining ensure_asset_schema_v2 重建（新形态）
V2_DROP_TABLES = (
    "asset_raw_segments",
    "asset_structure_nodes",
    "asset_structure_edges",
    "asset_structured_assets",
    "asset_table_cells",
    "asset_retrieval_units_v2",
    "asset_retrieval_embeddings_v2",
)

# 派生资产/运行/范式/研究线/旧缓存：TRUNCATE（RESTART IDENTITY CASCADE）
TRUNCATE_TABLES = (
    # 旧派生资产
    "asset_retrieval_units",
    "asset_retrieval_embeddings",
    "asset_raw_segment_relations",
    "asset_segment_element_links",
    "asset_segment_entity_mentions",
    "asset_document_snapshots",
    "asset_document_snapshot_links",
    "asset_builds",
    "asset_build_document_snapshots",
    "asset_publish_releases",
    "asset_parse_runs",
    "asset_parse_run_attempts",
    # 挖掘运行历史
    "mining_runs",
    "mining_run_documents",
    "mining_run_stage_events",
    "mining_workflow_node_events",
    # 范式（挖掘+检索；启动 seeder 重建 4+2 套）
    "mining_workflows",
    "mining_workflow_versions",
    "operator_paradigm",
    "operator_paradigm_version",
    # 研究线图谱数据（算子保留、数据下线）
    "ontology_alias_dictionary",
    "ontology_candidates",
    "ontology_entities",
    "ontology_entity_relations",
    "ontology_evidence_nodes",
    "ontology_node_types",
    "ontology_relation_types",
    "ontology_versions",
    # 旧检索链缓存（契约随固定链删除）
    "serving_query_cache",
)

# 保留白名单（绝不触碰）：control-plane + 存储层 + 审计/观测
PRESERVED_TABLES = frozenset({
    "kb_users", "kb_folders", "kb_members", "knowledge_bases",
    "mcp_access", "mcp_open_kbs",
    "asset_documents", "asset_file_audit_events",
    "asset_storage_objects", "asset_storage_object_refs",
    "asset_storage_operations", "asset_storage_quotas", "asset_upload_sessions",
    "agent_llm_attempts", "agent_llm_events", "agent_llm_model_calls",
    "agent_llm_prompt_templates", "agent_llm_requests", "agent_llm_results",
    "agent_llm_tasks", "serving_query_logs",
})


def load_registered_databases() -> dict[str, dict]:
    registry = yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8"))
    domains = registry.get("domains") or {}
    return {
        name: {
            "host": cfg["database"]["host"],
            "port": int(cfg["database"]["port"]),
            "dbname": cfg["database"]["dbname"],
        }
        for name, cfg in domains.items()
        if cfg.get("enabled", False)
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dbname", required=True, help="目标域库名（必须来自注册表）")
    parser.add_argument("--host", required=True, help="目标 host（必须与注册表一致）")
    parser.add_argument("--user", default="kb_user")
    parser.add_argument("--password", default=None)
    parser.add_argument(
        "--confirm-domain-assets-reset", action="store_true",
        help="显式确认（否则即使 --execute 也拒绝）",
    )
    parser.add_argument("--execute", action="store_true", help="真正执行（默认 dry-run）")
    args = parser.parse_args()

    if not args.confirm_domain_assets_reset:
        print("拒绝：缺少 --confirm-domain-assets-reset 显式确认。")
        return 2

    registered = load_registered_databases()
    matches = [
        (name, cfg) for name, cfg in registered.items()
        if cfg["dbname"] == args.dbname and cfg["host"] == args.host
    ]
    if not matches:
        print(f"拒绝：{args.host}/{args.dbname} 不在 domain_registry.yaml 启用域中。")
        return 2
    print(f"目标库匹配注册域：{[name for name, _ in matches]}")

    conninfo = {
        "host": args.host, "port": matches[0][1]["port"],
        "dbname": args.dbname, "user": args.user,
    }
    if args.password:
        conninfo["password"] = args.password

    with psycopg.connect(**conninfo, autocommit=True) as conn:
        existing = {
            row[0]
            for row in conn.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='public'"
            ).fetchall()
        }

        drop_list = [t for t in V2_DROP_TABLES if t in existing]
        truncate_list = [t for t in TRUNCATE_TABLES if t in existing]

        # FK 完备性：引用清理清单的表必须全在清单内
        cleanup_set = set(drop_list) | set(truncate_list)
        referencing = conn.execute(
            """
            SELECT DISTINCT tc.table_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.constraint_column_usage ccu
              ON ccu.constraint_name = tc.constraint_name
            JOIN information_schema.referential_constraints rc
              ON rc.constraint_name = tc.constraint_name
            WHERE tc.constraint_type = 'FOREIGN KEY'
              AND ccu.table_name = ANY(%s)
              AND tc.table_schema = 'public'
            """,
            (list(cleanup_set),),
        ).fetchall()
        violators = {r[0] for r in referencing} - cleanup_set
        if violators:
            print(
                "拒绝：清理清单不完备，以下清单外表引用了清理对象"
                f"（TRUNCATE CASCADE 会波及）：{sorted(violators)}"
            )
            return 3

        preserved_present = PRESERVED_TABLES & existing
        overlap = preserved_present & cleanup_set
        if overlap:
            print(f"拒绝：保留白名单与清理清单重叠：{sorted(overlap)}")
            return 3

        print("\n=== DROP（v2 表族，schema.py 重建新形态） ===")
        for table in drop_list:
            print(f"  DROP TABLE {table}")
        print("\n=== TRUNCATE（派生资产/运行/范式/研究线/缓存） ===")
        for table in truncate_list:
            print(f"  TRUNCATE {table}")
        print(f"\n=== 保留（不触碰，共 {len(preserved_present)} 张在库） ===")
        print("  " + ", ".join(sorted(preserved_present)))

        if not args.execute:
            print("\n[dry-run] 未执行。加 --execute 落地。")
            return 0

        for table in drop_list:
            conn.execute(f'DROP TABLE IF EXISTS "{table}" CASCADE')
            print(f"DROPED {table}")
        if truncate_list:
            joined = ", ".join(f'"{t}"' for t in truncate_list)
            conn.execute(f"TRUNCATE {joined} RESTART IDENTITY CASCADE")
            print(f"TRUNCATED {len(truncate_list)} tables")
        print("\nreset 完成：启动服务后 seeder 重建 4 套挖掘预置 + 2 套检索预置；"
              "全部测试知识库需重新挖掘。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
