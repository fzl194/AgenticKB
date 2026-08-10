"""Shared test fixtures — PostgreSQL backend.

Only tests requesting a DB fixture initialize PostgreSQL. Database setup and
cleanup are refused unless the configured database name ends with ``_test``.
"""
from __future__ import annotations

import os

import pytest
import pytest_asyncio

from knowledge_mining.mining.infra.pg_config import MiningDbConfig
from knowledge_mining.mining.infra.pg_schema import (
    ensure_domain_schema,
    ensure_primary_schema,
    ensure_schema,
)


# ── 预填 mining 控制面配置缓存 ──────────────────────────────────────────────
# 生产环境 mining 启动时从 main_control_service 拉取配置（mining.yaml / database.yaml）。
# 测试环境没有 main_control，这里在 conftest 导入时（早于任何配置类构造）从测试命令行
# 导出的 PG_* / UPLOAD_* 环境变量预填缓存，使 MiningDbConfig()/MiningConfig()/UploadConfig()
# 在测试里无需 main_control 也能工作。**生产配置类自身不读 .env / 环境变量。**
def _prefill_control_plane_caches_from_env() -> None:
    from knowledge_mining.mining.infra.control_plane import (
        set_database_config,
        set_mining_service_config,
    )

    set_database_config({
        "default": {
            "host": os.environ.get("PG_HOST", "localhost"),
            "port": int(os.environ.get("PG_PORT", "5432")),
            "dbname": os.environ.get("PG_DBNAME", "kb_db_test"),
            "user": os.environ.get("PG_USER", ""),
            "password": os.environ.get("PG_PASSWORD", ""),
            "sslmode": os.environ.get("PG_SSLMODE", "disable"),
            "gssencmode": os.environ.get("PG_GSSENCMODE", "disable"),
            "pool_min": int(os.environ.get("PG_POOL_MIN", "1")),
            "pool_max": int(os.environ.get("PG_POOL_MAX", "2")),
        }
    })
    set_mining_service_config({
        "llm_service_url": os.environ.get("LLM_SERVICE_URL", "http://localhost:8900"),
        "max_workers": int(os.environ.get("MAX_WORKERS", "4")),
        "mining_run_submission_engine": "workflow",
        "port": 8901,
        "upload": {
            "root": os.environ.get("UPLOAD_ROOT", "./uploads"),
            "max_file_size": int(os.environ.get("UPLOAD_MAX_FILE_SIZE", str(100 * 1024 * 1024))),
            "max_archive_size": int(os.environ.get("UPLOAD_MAX_ARCHIVE_SIZE", str(500 * 1024 * 1024))),
            "max_files_per_request": int(os.environ.get("UPLOAD_MAX_FILES_PER_REQUEST", "100")),
            "disk_reserve_bytes": int(os.environ.get("UPLOAD_DISK_RESERVE_BYTES", str(1024 * 1024 * 1024))),
            "archive_extensions": os.environ.get("UPLOAD_ARCHIVE_EXTENSIONS", ".zip"),
        },
    })

    # Phase 2：预填 auth 缓存的内部校验凭证，使 current_user 在测试里接受 kb_headers()。
    from knowledge_mining.mining.infra.control_plane import set_auth_config
    set_auth_config({
        "internal_verify_secret": os.environ.get("KB_TEST_INTERNAL_AUTH", "test-ivs"),
    })


_prefill_control_plane_caches_from_env()


def kb_headers(username: str = "tester") -> dict[str, str]:
    """测试用 KB 请求头：X-KB-User + X-Internal-Auth（secret 与 prefill 一致）。

    current_user 现在双校验，既有路由测试的 headers 都改用本 helper。
    """
    return {
        "X-KB-User": username,
        "X-Internal-Auth": os.environ.get("KB_TEST_INTERNAL_AUTH", "test-ivs"),
    }


@pytest.fixture(autouse=True)
def _reset_auth_config():
    """每个测试前恢复 auth 缓存的 session 默认值，防 test_current_user /
    test_control_plane_auth 等改写后污染其它模块（current_user 读不到 secret 就 401）。"""
    from knowledge_mining.mining.infra.control_plane import set_auth_config
    set_auth_config({"internal_verify_secret": os.environ.get("KB_TEST_INTERNAL_AUTH", "test-ivs")})
    yield


def _assert_disposable_database(db_config) -> None:
    """Refuse schema setup and cleanup unless the configured DB is disposable."""
    dbname = db_config.pg_dbname.strip().lower()
    if not dbname.endswith("_test"):
        raise RuntimeError(
            f"Refusing to use non-disposable PostgreSQL database {dbname!r}; "
            "test database names must end with '_test'."
        )


@pytest.fixture(scope="session")
def db_config():
    """Load PG config once per test session."""
    return MiningDbConfig()


@pytest.fixture(scope="session")
def control_db_config(db_config):
    """Explicit alias for the global Workflow Control database."""

    _assert_disposable_database(db_config)
    return db_config


@pytest.fixture(scope="session")
def domain_db_config(control_db_config):
    """Build a distinct disposable Domain database config for isolation tests."""

    domain_name = os.environ.get("MINING_TEST_DOMAIN_PG_DBNAME", "").strip()
    if not domain_name:
        pytest.skip("MINING_TEST_DOMAIN_PG_DBNAME is required for PostgreSQL acceptance")
    config = MiningDbConfig(
        pg_host=os.environ.get(
            "MINING_TEST_DOMAIN_PG_HOST", control_db_config.pg_host
        ),
        pg_port=int(os.environ.get(
            "MINING_TEST_DOMAIN_PG_PORT", control_db_config.pg_port
        )),
        pg_dbname=domain_name,
        pg_user=os.environ.get(
            "MINING_TEST_DOMAIN_PG_USER", control_db_config.pg_user
        ),
        pg_password=os.environ.get(
            "MINING_TEST_DOMAIN_PG_PASSWORD", control_db_config.pg_password
        ),
        pg_sslmode=os.environ.get(
            "MINING_TEST_DOMAIN_PG_SSLMODE", control_db_config.pg_sslmode
        ),
        pg_gssencmode=os.environ.get(
            "MINING_TEST_DOMAIN_PG_GSSENCMODE", control_db_config.pg_gssencmode
        ),
        pg_pool_min=1,
        pg_pool_max=2,
    )
    _assert_disposable_database(config)
    if config.pg_dbname.lower() == control_db_config.pg_dbname.lower():
        raise RuntimeError("Control and Domain PostgreSQL databases must differ")
    return config


@pytest.fixture(scope="session", autouse=True)
def _guard_test_database(db_config):
    """Reject a non-test database for every test session without connecting."""
    _assert_disposable_database(db_config)


@pytest.fixture(scope="session")
def _ensure_schema(db_config):
    """Ensure database + schema exist before any test runs."""
    _assert_disposable_database(db_config)
    ensure_schema(db_config)


def _truncate_all(conn):
    """Truncate all mining tables (asset + runtime + ontology) for clean test isolation.

    安全护栏（默认彻底关闭自动清表）：除非显式设置环境变量
    ``KB_ALLOW_TEST_TRUNCATE=1``，否则本函数直接返回、不删任何表——
    防止误删 ``.env`` 指向的真实库（尤其生产库 coremasterkb）。
    需要自动清测试库时，自行 ``export KB_ALLOW_TEST_TRUNCATE=1`` 再跑；
    平时一律手动清库。
    """
    import os

    if os.environ.get("KB_ALLOW_TEST_TRUNCATE") != "1":
        return

    conn.execute("TRUNCATE TABLE mining_workflow_node_events CASCADE")
    conn.execute("TRUNCATE TABLE mining_run_stage_events CASCADE")
    conn.execute("TRUNCATE TABLE mining_run_documents CASCADE")
    conn.execute("TRUNCATE TABLE mining_runs CASCADE")
    conn.execute("TRUNCATE TABLE asset_raw_segment_relations CASCADE")
    conn.execute("TRUNCATE TABLE asset_raw_segments CASCADE")
    conn.execute("TRUNCATE TABLE asset_retrieval_embeddings CASCADE")
    conn.execute("TRUNCATE TABLE asset_retrieval_units CASCADE")
    conn.execute("TRUNCATE TABLE asset_build_document_snapshots CASCADE")
    conn.execute("TRUNCATE TABLE asset_publish_releases CASCADE")
    conn.execute("TRUNCATE TABLE asset_builds CASCADE")
    conn.execute("TRUNCATE TABLE asset_document_snapshot_links CASCADE")
    conn.execute("TRUNCATE TABLE asset_document_snapshots CASCADE")
    conn.execute("TRUNCATE TABLE asset_documents CASCADE")
    conn.execute("TRUNCATE TABLE asset_source_batches CASCADE")

    # KB 管理表（asset_documents 已清，knowledge_bases 无 FK 引用残留，安全）。
    # 顺序：子表先（kb_members → knowledge_bases → kb_users）。
    conn.execute("TRUNCATE TABLE kb_members CASCADE")
    conn.execute("TRUNCATE TABLE knowledge_bases CASCADE")
    conn.execute("TRUNCATE TABLE kb_users CASCADE")

    # Control tables exist only in the primary database. Domain-only fixtures
    # deliberately omit them, so cleanup must remain tolerant there.
    for tbl in ("mining_workflow_versions", "mining_workflows"):
        try:
            conn.execute(f"TRUNCATE TABLE {tbl} CASCADE")
        except Exception:
            pass

    # 本体/图谱表是 domain 维度（不随 run 销毁），不清会让上批测试残留的待审
    # 候选/实体在下批测试里触发"本体确认"闸口暂停，污染端到端流水线断言。
    # asset_segment_entity_mentions 存待审 mention（按 run 关联）。
    # 这些表在更老的测试库里可能不存在，逐个 try 以保持向后兼容。
    for tbl in (
        "asset_segment_entity_mentions",
        "ontology_candidates",
        "ontology_evidence_nodes",
        "ontology_entity_relations",
        "ontology_entities",
        "ontology_alias_dictionary",
        "ontology_relation_types",
        "ontology_node_types",
        "ontology_versions",
    ):
        try:
            conn.execute(f"TRUNCATE TABLE {tbl} CASCADE")
        except Exception:
            pass


def _require_postgres_acceptance() -> None:
    if os.environ.get("KB_RUN_POSTGRES_ACCEPTANCE") != "1":
        pytest.skip("set KB_RUN_POSTGRES_ACCEPTANCE=1 to run real PostgreSQL tests")


def _cleanup_config(config: MiningDbConfig) -> None:
    if os.environ.get("KB_ALLOW_TEST_TRUNCATE") != "1":
        return
    import psycopg

    _assert_disposable_database(config)
    conn = psycopg.connect(config.conninfo, autocommit=True)
    try:
        _truncate_all(conn)
    finally:
        conn.close()


@pytest_asyncio.fixture
async def control_pool(control_db_config):
    """Async pool for global definitions; only opens under the explicit DB gate."""

    from psycopg.rows import dict_row
    from psycopg_pool import AsyncConnectionPool

    _require_postgres_acceptance()
    ensure_primary_schema(control_db_config)
    pool = AsyncConnectionPool(
        control_db_config.conninfo,
        min_size=1,
        max_size=2,
        open=False,
        kwargs={"row_factory": dict_row},
    )
    await pool.open()
    await pool.wait()
    try:
        yield pool
    finally:
        await pool.close()
        _cleanup_config(control_db_config)


@pytest_asyncio.fixture
async def domain_pool(domain_db_config):
    """Async pool for one Domain runtime/asset store, never Control tables."""

    from psycopg.rows import dict_row
    from psycopg_pool import AsyncConnectionPool

    _require_postgres_acceptance()
    ensure_domain_schema(domain_db_config)
    pool = AsyncConnectionPool(
        domain_db_config.conninfo,
        min_size=1,
        max_size=2,
        open=False,
        kwargs={"row_factory": dict_row},
    )
    await pool.open()
    await pool.wait()
    try:
        yield pool
    finally:
        await pool.close()
        _cleanup_config(domain_db_config)

    # 本体/图谱表是 domain 维度（不随 run 销毁），不清会让上批测试残留的待审
    # 候选/实体在下批测试里触发"本体确认"闸口暂停，污染端到端流水线断言。
    # asset_segment_entity_mentions 存待审 mention（按 run 关联）。
    # 这些表在更老的测试库里可能不存在，逐个 try 以保持向后兼容。
@pytest.fixture
def asset_db(db_config, _ensure_schema):
    """Provide an AssetCoreDB connected to PG, with cleanup after test."""
    from knowledge_mining.mining.infra.db import AssetCoreDB
    import psycopg
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool

    pool = ConnectionPool(
        db_config.conninfo,
        min_size=1,
        max_size=2,
        open=True,
        kwargs={"row_factory": dict_row},
    )
    db = AssetCoreDB(pool)
    yield db

    import psycopg as _psycopg
    conn = _psycopg.connect(db_config.conninfo, autocommit=True)
    try:
        _truncate_all(conn)
    finally:
        conn.close()

    pool.close()


@pytest.fixture(autouse=True)
def _cleanup_db(request):
    """Auto-cleanup all tables BEFORE each test for full isolation."""
    if "_ensure_schema" not in request.fixturenames:
        yield
        return

    import psycopg
    db_config = request.getfixturevalue("db_config")
    _assert_disposable_database(db_config)
    conn = psycopg.connect(db_config.conninfo, autocommit=True)
    try:
        _truncate_all(conn)
    finally:
        conn.close()
    yield

@pytest.fixture
def runtime_db(db_config, _ensure_schema):
    """Provide a MiningRuntimeDB connected to PG, with cleanup after test."""
    from knowledge_mining.mining.infra.db import MiningRuntimeDB
    import psycopg
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool

    pool = ConnectionPool(
        db_config.conninfo,
        min_size=1,
        max_size=2,
        open=True,
        kwargs={"row_factory": dict_row},
    )
    db = MiningRuntimeDB(pool)
    yield db

    import psycopg as _psycopg
    conn = _psycopg.connect(db_config.conninfo, autocommit=True)
    try:
        _truncate_all(conn)
    finally:
        conn.close()

    pool.close()
