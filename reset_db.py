"""
CoreMasterKB 数据库重置脚本
- 读取 .env 中的 PG 连接信息
- DROP 所有业务表、索引、触发器、函数
- 按 databases/ 下的 SQL 文件重新建表

用法：python reset_db.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# ── 加载 .env ──────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parent
ENV_FILE = REPO_ROOT / ".env"

if not ENV_FILE.exists():
    print(f"[ERROR] .env not found: {ENV_FILE}")
    sys.exit(1)

# 手动解析 .env（不依赖 pydantic-settings，减少依赖）
_env = {}
for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if not line or line.startswith("#"):
        continue
    if "=" in line:
        k, v = line.split("=", 1)
        _env[k.strip()] = v.strip()

PG_HOST = _env.get("PG_HOST", "")
PG_PORT = _env.get("PG_PORT", "5432")
PG_DBNAME = _env.get("PG_DBNAME", "")
PG_USER = _env.get("PG_USER", "")
PG_PASSWORD = _env.get("PG_PASSWORD", "")
PG_SSLMODE = _env.get("PG_SSLMODE", "disable")
PG_GSSENCMODE = _env.get("PG_GSSENCMODE", "disable")

if not all([PG_HOST, PG_DBNAME, PG_USER]):
    print("[ERROR] PG_HOST / PG_DBNAME / PG_USER must be set in .env")
    sys.exit(1)

CONNINFO = (
    f"host={PG_HOST} port={PG_PORT} dbname={PG_DBNAME} "
    f"user={PG_USER} password={PG_PASSWORD} "
    f"sslmode={PG_SSLMODE} gssencmode={PG_GSSENCMODE}"
)

# ── 所有要 DROP 的表（按依赖顺序，子表先删） ─────────────────
# 注：用 DROP TABLE ... CASCADE，顺序容错；但仍按依赖从子到父排，便于阅读。
ALL_TABLES = [
    # ontology 概念层（FK 指向 asset_* / mining_runs，必须先于它们删）
    "ontology_evidence_nodes",
    "ontology_candidates",
    "asset_segment_entity_mentions",   # 段↔实体桥接表，定义在 ontology DDL 文件里
    "ontology_entity_relations",
    "ontology_alias_dictionary",
    "ontology_entities",
    "ontology_relation_types",
    "ontology_node_types",
    "ontology_versions",
    # agent_llm_runtime（LLM 服务）
    "agent_llm_events",
    "agent_llm_results",
    "agent_llm_attempts",
    "agent_llm_requests",
    "agent_llm_tasks",
    "agent_llm_prompt_templates",
    "agent_llm_model_calls",
    # mining_runtime（挖掘服务）
    "mining_workflow_node_events",
    "mining_run_stage_events",
    "mining_run_documents",
    "mining_runs",
    # mining_control（全局 Workflow 定义）
    "mining_workflow_versions",
    "mining_workflows",
    # asset_core（资产核心）
    "asset_retrieval_embeddings",
    "asset_retrieval_units",
    "asset_raw_segment_relations",
    "asset_raw_segments",
    "asset_build_document_snapshots",
    "asset_publish_releases",
    "asset_builds",
    "asset_document_snapshot_links",
    "asset_document_snapshots",
    "asset_documents",
    "asset_source_batches",
    # kb management（asset_documents.kb_id → knowledge_bases；CASCADE 容错）
    "kb_folders",
    "kb_members",
    "knowledge_bases",
    "kb_users",
]

# 要 DROP 的触发器函数
ALL_FUNCTIONS = [
    "asset_retrieval_units_search_vector_update()",
    "populate_embedding_vector_vec()",
]

# 要 DROP 的触发器
ALL_TRIGGERS = [
    ("asset_retrieval_units", "trg_asset_retrieval_units_search_vector"),
    ("asset_retrieval_embeddings", "trg_populate_embedding_vector"),
]

# ── 要执行的建表 SQL 文件（按顺序） ─────────────────────────
# 顺序与 knowledge_mining 的 ensure_schema（mining/infra/pg_schema.py）保持一致：
#   asset_core 002 先建（它装 pg_trgm / vector 扩展），
#   mining_runtime 002/003/004 增量迁移紧随其后，
#   mining_runtime 005 增加 Workflow Run 绑定和节点事件，
#   asset_core 003（domain 隔离）在 002 之后，
#   ontology 在 Domain DDL 中必须最后——它的外键指向 asset_* 和 mining_runs，
#   mining_control 最后应用且只属于主库。
#
# 不含 serving_query_logs / serving_query_cache 与 operator_paradigm*：那些是
# agent_serving_java 自有表，由它自己在启动/建池时创建
# （ServingRuntimeSchemaInitializer / ParadigmSchemaInitializer）。它们写在按域路由的
# DataSource 上，这个脚本只连 .env 那一个库，本来也覆盖不全。见 db_tables.OPTIONAL_TABLES。
SCHEMA_FILES = [
    # LLM runtime（llm_service 自管，同库；重建后重启 llm_service 会从 pack 重新注册 prompt 模板）
    REPO_ROOT / "databases" / "agent_llm_runtime" / "schemas" / "002_agent_llm_runtime_postgresql.sql",
    # ── 顺序对齐 pg_schema.domain_schema_paths（含 KB 中心化挖掘的 005/006/007）──
    REPO_ROOT / "databases" / "asset_core" / "schemas" / "002_asset_core_postgresql.sql",   # 扩展 + asset 基表
    # KB 四表
    REPO_ROOT / "databases" / "kb" / "schemas" / "001_kb_users.sql",
    REPO_ROOT / "databases" / "kb" / "schemas" / "002_knowledge_bases.sql",
    REPO_ROOT / "databases" / "kb" / "schemas" / "003_kb_members.sql",
    REPO_ROOT / "databases" / "kb" / "schemas" / "004_kb_folders.sql",
    # asset_documents 的 KB 列 + 文件元信息（需 knowledge_bases + asset_documents）
    REPO_ROOT / "databases" / "asset_core" / "schemas" / "004_kb_isolation.sql",
    REPO_ROOT / "databases" / "asset_core" / "schemas" / "005_kb_file_meta.sql",
    # KB 挖掘范式绑定（knowledge_bases.mining_workflow_id）
    REPO_ROOT / "databases" / "kb" / "schemas" / "005_kb_mining_binding.sql",
    # mining_runtime（含 workflow 绑定 / 节点事件 / preflight）
    REPO_ROOT / "databases" / "mining_runtime" / "schemas" / "002_mining_runtime_postgresql.sql",
    REPO_ROOT / "databases" / "mining_runtime" / "schemas" / "003_mining_runtime_domain.sql",
    REPO_ROOT / "databases" / "mining_runtime" / "schemas" / "004_mining_runtime_run_stage.sql",
    REPO_ROOT / "databases" / "mining_runtime" / "schemas" / "005_mining_workflow_runtime.sql",
    REPO_ROOT / "databases" / "mining_runtime" / "schemas" / "006_mining_run_preflight.sql",
    # asset_core 域隔离 + snapshot workflow 绑定 + KB build 归属（需 mining_runs 已建）
    REPO_ROOT / "databases" / "asset_core" / "schemas" / "003_asset_core_domain_isolation.sql",
    REPO_ROOT / "databases" / "asset_core" / "schemas" / "004_asset_snapshot_workflow_binding.sql",
    REPO_ROOT / "databases" / "asset_core" / "schemas" / "006_asset_build_kb.sql",
    # mining_runs 的 kb_id
    REPO_ROOT / "databases" / "mining_runtime" / "schemas" / "007_mining_run_kb.sql",
    # ontology（Domain DDL 中必须最后——FK 指向 asset_* / mining_runs）
    REPO_ROOT / "databases" / "ontology" / "schemas" / "001_ontology_concept_postgresql.sql",
    # ── control（全局 Workflow 定义，主库最后）──
    REPO_ROOT / "databases" / "mining_control" / "schemas" / "001_mining_workflow_postgresql.sql",
]


def drop_all(cur):
    """删除所有触发器、表、函数"""
    print("\n=== Step 1: DROP existing objects ===")

    # 1. DROP triggers
    for table, trigger in ALL_TRIGGERS:
        cur.execute(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")
        print(f"  DROP TRIGGER {trigger} ON {table}")

    # 2. DROP tables（CASCADE 会连带删索引）
    for t in ALL_TABLES:
        cur.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
        print(f"  DROP TABLE {t}")

    # 3. DROP functions
    for fn in ALL_FUNCTIONS:
        cur.execute(f"DROP FUNCTION IF EXISTS {fn} CASCADE")
        print(f"  DROP FUNCTION {fn}")

    # 4. DROP extensions if needed (vector is usually kept)
    # Uncomment if you want a full clean:
    # cur.execute("DROP EXTENSION IF EXISTS vector CASCADE")
    # cur.execute("DROP EXTENSION IF EXISTS pg_trgm CASCADE")

    print("  Done.")


def create_all(cur):
    """按顺序执行 schema SQL 文件"""
    print("\n=== Step 2: CREATE tables from schema files ===")
    for sql_file in SCHEMA_FILES:
        if not sql_file.exists():
            print(f"  [WARN] File not found, skipping: {sql_file}")
            continue
        sql = sql_file.read_text(encoding="utf-8")
        cur.execute(sql)
        print(f"  Executed: {sql_file.name}")
    print("  Done.")


def main():
    try:
        import psycopg
    except ImportError:
        print("[ERROR] psycopg is required. Install with: pip install psycopg[binary]")
        sys.exit(1)

    print(f"Connecting to: {PG_HOST}:{PG_PORT}/{PG_DBNAME} (user={PG_USER})")
    print("WARNING: This will DELETE ALL DATA in the tables listed above!")
    confirm = input("Type 'YES' to proceed: ")
    if confirm != "YES":
        print("Aborted.")
        sys.exit(0)

    with psycopg.connect(CONNINFO, autocommit=True) as conn:
        with conn.cursor() as cur:
            drop_all(cur)
            create_all(cur)

    print("\n=== Database reset complete ===")
    print("All tables dropped and recreated from latest schema files.")


if __name__ == "__main__":
    main()
