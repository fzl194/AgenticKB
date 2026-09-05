"""KB workflow input must be built from persisted object identities.

This is deliberately a pure unit test: the input stage is given repository
doubles and must never touch a local upload directory or a real database.
"""
from __future__ import annotations

from types import SimpleNamespace

from knowledge_mining.mining.contracts.models import BatchParams
from knowledge_mining.mining.jobs import run as run_job
from knowledge_mining.mining.pipeline import PipelineConfig


class _RuntimeDb:
    def get_run(self, run_id):
        return {
            "id": run_id,
            "source_batch_id": "batch-1",
            "metadata_json": {"kb_id": "kb-1", "document_ids": ["doc-1"]},
            "preflight_manifest_json": {},
        }

    def get_run_documents(self, run_id):
        return []

    def _execute(self, *args):
        raise AssertionError("the run already has a source batch")

    def commit(self):
        pass


class _AssetDb:
    def __init__(self):
        self.query = None
        self.params = None
        self.pool = object()

    def _fetchall(self, query, params):
        self.query = query
        self.params = params
        return [{
            "id": "doc-1",
            "domain": "plant-a",
            "document_key": "doc:/manual.md",
            "document_name": "manual.md",
            "document_type": "markdown",
            "storage_object_id": "object-1",
            "source_raw_hash": "a" * 64,
            "content_revision": 7,
            "metadata_json": {"title": "Manual"},
            "directory_path": "ops",
            "mime": "text/markdown",
            "size": 42,
            "object_key": "source/aa/manual.md",
            "bucket": "agentickb-source",
            "provider": "minio",
            "object_version_id": "v7",
        }]

    def get_document_lifecycle_state(self, **kwargs):
        raise AssertionError("KB object input must not reclassify from input_path")


class _Tracker:
    def __init__(self):
        self.registered = []

    def set_run_phase(self, *args):
        return True

    def register_document(self, row):
        self.registered.append(row)

    def start_document(self, run_document_id):
        pass

    def finish_ingest(self, *args):
        pass


def test_kb_workflow_prepares_document_states_from_object_storage_identities(monkeypatch):
    """A KB run reads its selected documents by ``kb_id``, never local paths."""
    services = object.__new__(run_job._WorkflowJobServices)
    services.action = "execute"
    services.run_id = "run-1"
    services.asset_db = _AssetDb()
    services.runtime_db = _RuntimeDb()
    services.tracker = _Tracker()
    services.profile = SimpleNamespace(domain_id="plant-a")
    services.channel = "prod"
    services.input_path = "C:/must-not-be-scanned"
    services.batch_params = BatchParams()
    services.manifest = {"runtimeBinding": {"uploadBatchId": "batch-1"}}
    services.pipeline_config = PipelineConfig(domain="plant-a")
    services.document_parse_service = None
    services.segment_compile_service = None
    services._object_input_services_ready = False
    from knowledge_mining.mining.jobs.kb_incremental import KbIncrementDecision
    services._classify_kb_increment = lambda docs: {
        "doc-1": KbIncrementDecision(
            document_id="doc-1", action="NEW", decision="new_document",
            reason="document has never entered a validated KB build",
        ),
    }
    v2_services = SimpleNamespace(
        document_parse_service=object(),
        segment_compile_service=object(),
        retrieval_project_service=object(),
        embedding_service=object(),
        asset_persist_service=object(),
        query_expansion_service=None,
        hierarchical_summary_service=None,
    )
    monkeypatch.setattr(
        run_job,
        "_build_workflow_object_input_services",
        lambda *, sync_pool, embedding_generator=None, llm_generator=None: v2_services,
    )
    monkeypatch.setattr(
        run_job,
        "ingest_directory",
        lambda *args: (_ for _ in ()).throw(AssertionError("local scan called")),
    )

    states = services._prepare_document_states()

    assert services.asset_db.params == ("kb-1", "plant-a", ["doc-1"])
    assert "asset_documents" in services.asset_db.query
    assert len(states) == 1
    ctx = states[0].context
    raw = ctx.raw_file
    assert states[0].doc_key == "doc:/manual.md"
    assert raw.document_id == "doc-1"
    assert raw.mime == "text/markdown"
    assert raw.document_key == "doc:/manual.md"
    assert raw.existing_doc["id"] == "doc-1"
    assert raw.file_path == "minio://agentickb-source/source/aa/manual.md"
    assert ctx.document_id == "doc-1"
    assert ctx.existing_doc == raw.existing_doc
    assert services.tracker.registered[0].document_id == "doc-1"
    assert services.tracker.registered[0].action == "NEW"
    assert services.document_parse_service is v2_services.document_parse_service
    assert services.segment_compile_service is v2_services.segment_compile_service


def test_kb_increment_classification_failure_does_not_fall_back_to_full_run(monkeypatch):
    """分类事实不可读时必须显式失败，不能把 19,789 篇静默改成全量 UPDATE。"""
    import pytest

    services = object.__new__(run_job._WorkflowJobServices)
    services.action = "execute"
    services.run_id = "run-1"
    services.asset_db = _AssetDb()
    services.runtime_db = _RuntimeDb()
    services.tracker = _Tracker()
    services.profile = SimpleNamespace(domain_id="plant-a")
    services.channel = "prod"
    services.input_path = "C:/must-not-be-scanned"
    services.batch_params = BatchParams()
    services.manifest = {"runtimeBinding": {"uploadBatchId": "batch-1"}}
    services.pipeline_config = PipelineConfig(domain="plant-a")
    services.document_parse_service = object()
    services.segment_compile_service = object()
    services._object_input_services_ready = True
    services._classify_kb_increment = lambda docs: (_ for _ in ()).throw(
        RuntimeError("increment facts unavailable")
    )

    with pytest.raises(RuntimeError, match="increment facts unavailable"):
        services._prepare_document_states()


def test_kb_workflow_services_use_control_plane_object_store_config(monkeypatch):
    """The production composition root receives the resolved MinIO config."""
    from knowledge_mining.mining.infra.object_store.config import ObjectStoreConfig
    from knowledge_mining.mining.infra.object_store import factory as store_factory
    from knowledge_mining.mining.workflow import new_chain_services

    config = ObjectStoreConfig(
        provider="minio",
        bucket_prefix="agentickb-test-",
        endpoint="minio.example:9000",
        access_key="test-access",
        secret_key="test-secret",
    )
    object_store = object()
    expected = SimpleNamespace(
        document_parse_service=object(), segment_compile_service=object(),
    )
    calls = {}
    monkeypatch.setattr(
        ObjectStoreConfig,
        "from_control_plane",
        classmethod(lambda cls: config),
    )
    monkeypatch.setattr(
        store_factory,
        "make_object_store",
        lambda actual: calls.setdefault("config", actual) and object_store,
    )
    monkeypatch.setattr(
        new_chain_services,
        "build_new_chain_services",
        lambda **kwargs: calls.setdefault("services", kwargs) and expected,
    )

    result = run_job._build_workflow_object_input_services(sync_pool="sync-pool")

    assert result is expected
    assert calls["config"] is config
    assert calls["services"] == {
        "bucket_prefix": "agentickb-test-",
        "object_store": object_store,
        "sync_pool": "sync-pool",
        # 批次8 联调（feea1b0）：组合根透传 embedding_generator（None=无向量线）
        "embedding_generator": None,
        # 29号 M3 接线：生成客户端透传（None=实验算子 FALLBACK degraded）
        "llm_generator": None,
    }


def test_kb_workflow_services_reject_fake_object_store(monkeypatch):
    """A production workflow job cannot silently use the test adapter."""
    import pytest

    from knowledge_mining.mining.infra.object_store.config import ObjectStoreConfig

    fake_config = ObjectStoreConfig(provider="fake", root_path=".objects")
    monkeypatch.setattr(
        ObjectStoreConfig,
        "from_control_plane",
        classmethod(lambda cls: fake_config),
    )

    with pytest.raises(RuntimeError, match="requires object_store.provider='minio'"):
        run_job._build_workflow_object_input_services(sync_pool="sync-pool")
