"""人工验收脚本：一份文档走完新链路全流程（真 MinIO + 真 PG），**不清理**.

用途：为 kb-ui「结构化数据」tab 准备可查看的数据。跑完后在知识库文件
列表找到该文档 → 打开预览 → 「结构化数据」页签：出生证明 / 大纲 /
表格网格 / 切片列表（带章节路径与表头）。

用法（仓库根目录）：
  python tools/acceptance_demo.py [文件路径]
  # 不带参数时用内置 Markdown 样例（含表格/列表）

行为：M1 式建档 + 对象上传 → document_parse（质量门控+快照转正）→
segment_compile（rows 档，切片+元素映射落库）。幂等：同名文档重跑会
因 document 名冲突在库层报错——重复验收请先在知识库删除该文档。
"""
from __future__ import annotations

import asyncio
import hashlib
import sys
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

SAMPLE_MD = (
    "# 设备巡检手册\n\n"
    "## 日常巡检\n\n"
    "每日执行风扇、电源与指示灯巡检，异常时记录告警码。\n\n"
    "## 告警对照\n\n"
    "| 告警码 | 原因 | 处理建议 |\n| - | - | - |\n"
    "| A-101 | 风扇停转 | 检查风扇并更换 |\n"
    "| A-102 | 电源异常 | 检查供电模块 |\n\n"
    "## 注意事项\n\n"
    "- 巡检记录保留三个月\n"
    "- 升级固件前先备份配置\n"
).encode("utf-8")


async def _chunked(payload: bytes):
    for i in range(0, len(payload), 65536):
        yield payload[i : i + 65536]


async def main() -> None:
    import yaml

    target = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    if target is not None:
        if not target.is_file():
            raise SystemExit(f"file not found: {target}")
        data = target.read_bytes()
        filename = target.name
        mime = _guess_mime(target)
    else:
        data = SAMPLE_MD
        filename = "验收样例-设备巡检手册.md"
        mime = "text/markdown"

    from psycopg.rows import dict_row
    from psycopg_pool import AsyncConnectionPool

    from knowledge_mining.mining.contracts.file_management import (
        StorageObjectRecord,
    )
    from knowledge_mining.mining.contracts.storage.types import (
        ObjectLocation,
        PutOptions,
    )
    from knowledge_mining.mining.file_management.repositories_pg import (
        PgDocumentCurrentContentRepository,
        PgStorageObjectRepository,
    )
    from knowledge_mining.mining.infra.object_store.config import ObjectStoreConfig
    from knowledge_mining.mining.infra.object_store.factory import make_object_store
    from knowledge_mining.mining.infra.object_store.keys import build_object_key
    from knowledge_mining.mining.infra.pg_config import MiningDbConfig
    from knowledge_mining.mining.infra.pg_schema import ensure_primary_schema
    from knowledge_mining.mining.workflow.handlers.document import (
        document_parse_handler,
        segment_compile_handler,
    )
    from knowledge_mining.mining.workflow.new_chain_services import (
        build_new_chain_services,
    )

    db = yaml.safe_load(
        (REPO / "main_control_service" / "config" / "system" / "database.yaml")
        .read_text(encoding="utf-8")
    )["default"]
    ensure_primary_schema(MiningDbConfig(
        pg_host=str(db["host"]), pg_port=int(db.get("port", 5432)),
        pg_dbname=str(db["dbname"]), pg_user=str(db["user"]),
        pg_password=str(db["password"]),
    ))
    store_cfg = ObjectStoreConfig.from_yaml(
        REPO / "main_control_service" / "config" / "system" / "storage.yaml"
    )
    store = make_object_store(store_cfg)
    await store.ensure_buckets()
    pool = AsyncConnectionPool(
        f"host={db['host']} port={db.get('port', 5432)} "
        f"dbname={db['dbname']} user={db['user']} password={db['password']}",
        min_size=1, max_size=3, open=False, kwargs={"row_factory": dict_row},
    )
    await pool.open()

    src_bucket = f"{store_cfg.bucket_prefix}source"
    storage_objects = PgStorageObjectRepository(pool)
    documents = PgDocumentCurrentContentRepository(pool)
    services = build_new_chain_services(
        bucket_prefix=store_cfg.bucket_prefix, object_store=store,
        storage_objects=storage_objects, documents=documents, pool=pool,
    )
    runtime = SimpleNamespace(services=SimpleNamespace(
        document_parse_service=services.document_parse_service,
        segment_compile_service=services.segment_compile_service,
        domain="default",
    ))

    async with pool.connection() as conn:
        cur = await conn.execute("SELECT id FROM knowledge_bases LIMIT 1")
        kb_id = (await cur.fetchone())["id"]

    sha = hashlib.sha256(data).hexdigest()
    key = build_object_key("source", sha)
    await store.put_stream(
        ObjectLocation(bucket=src_bucket, object_key=key), _chunked(data),
        PutOptions(artifact_class="source", expected_sha256=sha),
    )
    so_id = f"so_accept_{sha[:12]}"
    if await storage_objects.find_by_location(src_bucket, key, None) is None:
        await storage_objects.register(StorageObjectRecord(
            id=so_id, provider=store_cfg.provider, bucket=src_bucket,
            object_key=key, object_version_id=None, sha256=sha, size=len(data),
            mime=mime, artifact_class="source", state="AVAILABLE",
            created_at="2026-08-21T00:00:00+00:00",
        ))
    doc_id = f"doc-accept-{sha[:12]}"
    await documents.create_document(
        kb_id=kb_id, document_id=doc_id, folder_id=None, owner_id=None,
        document_name=filename, document_type="other",
        storage_object_id=so_id, source_raw_hash=sha,
    )

    raw = SimpleNamespace(
        document_id=doc_id, document_key=filename,
        file_type=_file_type(filename), mime=mime,
    )
    state = SimpleNamespace(
        run_document_id="acceptance", doc_key=filename,
        context=SimpleNamespace(raw_file=raw), capabilities=frozenset(),
        tags=(),
        with_context=lambda ctx, capabilities=frozenset(): SimpleNamespace(
            run_document_id="acceptance", doc_key=filename, context=ctx,
            capabilities=capabilities, tags=(),
        ),
    )
    parsed = document_parse_handler(state, {}, runtime)
    if parsed.status.value != "success":
        raise SystemExit(f"document_parse failed: {parsed.error_message}")
    pctx = parsed.outputs.context
    compiled = segment_compile_handler(
        SimpleNamespace(
            run_document_id="acceptance", doc_key=filename, context=pctx,
            capabilities=frozenset(), tags=(),
            with_context=lambda ctx, capabilities=frozenset(): (
                SimpleNamespace(
                    run_document_id="acceptance", doc_key=filename,
                    context=ctx, capabilities=capabilities, tags=(),
                )
            ),
        ),
        {"tableView": "rows"}, runtime,
    )
    if compiled.status.value != "success":
        raise SystemExit(f"segment_compile failed: {compiled.error_message}")

    segs = compiled.outputs.context.segments
    table_rows = [s for s in segs if s.structure_json.get("table_header")]
    print("=" * 62)
    print("验收数据已就绪（未清理，可在知识库前端查看）")
    print(f"  文档名: {filename}")
    print(f"  文档 ID: {doc_id}")
    print(f"  知识库 ID: {kb_id}")
    print(f"  解析 Run: {pctx.run_id}")
    print(f"  知识快照: {pctx.snapshot_id}  质量结论见「结构化数据」页")
    print(f"  切片数: {len(segs)}（其中表格行 {len(table_rows)}，自带表头）")
    print()
    print("前端查看路径：知识库 → 文件列表 → 打开该文档 → 「结构化数据」页签")
    print("  - 知识快照卡：质量结论 / 内容版本 / 解析与切片管线指纹")
    print("  - 文档大纲树 / 表格网格（带表头列名）/ 切片列表（章节路径）")
    print()
    print("接口直查（可选）：")
    print(f"  GET /api/knowledge/documents/{doc_id}/parse-result?domain=default")
    await pool.close()


def _guess_mime(path: Path) -> str:
    suffix = path.suffix.lower()
    table = {
        ".md": "text/markdown", ".txt": "text/plain", ".html": "text/html",
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument."
                 "wordprocessingml.document",
        ".xlsx": "application/vnd.openxmlformats-officedocument."
                 "spreadsheetml.sheet",
        ".pptx": "application/vnd.openxmlformats-officedocument."
                 "presentationml.presentation",
    }
    return table.get(suffix, "application/octet-stream")


def _file_type(name: str) -> str:
    suffix = Path(name).suffix.lower().lstrip(".")
    return {"markdown": "md"}.get(suffix, suffix or "txt")


if __name__ == "__main__":
    asyncio.run(main())
