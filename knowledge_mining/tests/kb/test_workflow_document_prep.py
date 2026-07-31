"""回归测试：_prepare_document_states 必须按 storage_path 查文档身份（G1），
不能用 document_key= 调 get_document_lifecycle_state——那是 jobs/run.py:805 的签名 bug
（feat G1 把方法入参从 document_key 改成 storage_path，旧调用点漏改），真实挖掘时
崩在 workflow runtime 的 input 阶段。

本测试直接构造 _WorkflowJobServices（None LLM——分类阶段不需要 LLM，_init_llm(None)
优雅降级）+ 真实 DB + tmp 上传目录，调 _prepare_document_states：
旧代码会抛 TypeError（unexpected keyword argument 'document_key'），新代码返回 DocumentState。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import psycopg

from knowledge_mining.mining.contracts.models import BatchParams
from knowledge_mining.mining.infra.domain_pack import load_domain_pack
from knowledge_mining.mining.jobs.run import _WorkflowJobServices
from knowledge_mining.mining.runtime import RuntimeTracker

DOMAIN = "cloud_core_network"


def test_prepare_document_states_runs_without_signature_error(
    asset_db, runtime_db, db_config, tmp_path
):
    """_prepare_document_states 不应抛 'unexpected keyword argument document_key'。"""
    upload_dir = tmp_path / "kb-upload"
    upload_dir.mkdir()
    (upload_dir / "a.txt").write_text("AMF 是 5G 核心网的接入与移动性管理功能实体。", encoding="utf-8")

    run_id = "run-prep-test-1"
    manifest = {"runtimeBinding": {"uploadBatchId": "test-batch"}}
    # 插 mining_runs 行：_prepare_document_states 会 runtime_db.get_run(run_id) 读它。
    with psycopg.connect(db_config.conninfo, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO mining_runs
               (id, input_path, domain, channel, status, current_stage, started_at,
                execution_engine, workflow_manifest_json, metadata_json,
                total_documents, new_count, updated_count, skipped_count,
                failed_count, committed_count)
               VALUES (%s, %s, %s, 'prod', 'queued', 'queued', %s,
                       'workflow', %s::jsonb, '{}'::jsonb, 0,0,0,0,0,0)""",
            (run_id, str(upload_dir), DOMAIN,
             datetime.now(timezone.utc).isoformat(), json.dumps(manifest)),
        )

    tracker = RuntimeTracker(runtime_db)
    profile = load_domain_pack(DOMAIN)
    services = _WorkflowJobServices(
        action="execute",
        run_id=run_id,
        asset_db=asset_db,
        runtime_db=runtime_db,
        tracker=tracker,
        profile=profile,
        channel="prod",
        input_path=upload_dir,
        batch_params=BatchParams(),
        llm_base_url=None,  # 分类阶段不需 LLM；_init_llm(None) 优雅降级
        max_workers=1,
        execution_mode="publish",
        ontology_version_id=None,
        manifest=manifest,
    )

    # 旧代码（document_key=）会在这里抛 TypeError: get_document_lifecycle_state()
    # got an unexpected keyword argument 'document_key'。新代码（storage_path）不会。
    states = services._prepare_document_states()

    # 能返回非空 DocumentState 列表 = ingest_directory 扫到 a.txt + 分类阶段（含 805 的
    # get_document_lifecycle_state 调用）跑通、未抛签名错。这就是 document_key 回归 guard。
    assert len(states) >= 1


def test_prepare_document_states_filters_to_selected_document_ids(
    asset_db, runtime_db, db_config, tmp_path
):
    """P2b：mining_runs.metadata_json.document_ids 非空时，_prepare_document_states
    只返回所选文档子集（按 storage_path 过滤 ingest_directory 扫描结果）。

    上传目录里放 a.txt + b.txt，但只给 a.txt 建身份行并把 document_ids=['doc-a']
    写进 run metadata → states 只含 a.txt，b.txt 被过滤掉。未传 document_ids 时
    不过滤（整库增量），由上面那条回归测试覆盖。
    """
    upload_dir = tmp_path / "kb-upload"
    upload_dir.mkdir()
    (upload_dir / "a.txt").write_text("文档 A 内容", encoding="utf-8")
    (upload_dir / "b.txt").write_text("文档 B 内容", encoding="utf-8")

    a_storage = str(upload_dir / "a.txt")
    run_id = "run-sel-1"
    now_iso = datetime.now(timezone.utc).isoformat()
    manifest = {"runtimeBinding": {"uploadBatchId": "test-batch"}}
    meta = json.dumps({"document_ids": ["doc-a"]})
    with psycopg.connect(db_config.conninfo, autocommit=True) as conn, conn.cursor() as cur:
        # a.txt 身份行（id=doc-a，storage_path=落盘位置，kb_id=NULL 免 FK）；b.txt 故意不建
        cur.execute(
            """INSERT INTO asset_documents
               (id, domain, document_key, document_name, metadata_json, created_at,
                kb_id, storage_path, directory_path)
               VALUES (%s, %s, 'doc:/a.txt', 'a.txt', '{}'::jsonb, %s, NULL, %s, '')""",
            ("doc-a", DOMAIN, now_iso, a_storage),
        )
        cur.execute(
            """INSERT INTO mining_runs
               (id, input_path, domain, channel, status, current_stage, started_at,
                execution_engine, workflow_manifest_json, metadata_json,
                total_documents, new_count, updated_count, skipped_count,
                failed_count, committed_count)
               VALUES (%s, %s, %s, 'prod', 'queued', 'queued', %s,
                       'workflow', %s::jsonb, %s::jsonb, 0,0,0,0,0,0)""",
            (run_id, str(upload_dir), DOMAIN, now_iso, json.dumps(manifest), meta),
        )

    tracker = RuntimeTracker(runtime_db)
    profile = load_domain_pack(DOMAIN)
    services = _WorkflowJobServices(
        action="execute",
        run_id=run_id,
        asset_db=asset_db,
        runtime_db=runtime_db,
        tracker=tracker,
        profile=profile,
        channel="prod",
        input_path=upload_dir,
        batch_params=BatchParams(),
        llm_base_url=None,  # 分类/过滤阶段不需 LLM
        max_workers=1,
        execution_mode="publish",
        ontology_version_id=None,
        manifest=manifest,
    )

    states = services._prepare_document_states()

    # 只选了 a → states 只含 a.txt；b.txt 被过滤掉（整库增量时本会是 2 个）
    assert len(states) == 1
    assert states[0].doc_key == "doc:/a.txt"
