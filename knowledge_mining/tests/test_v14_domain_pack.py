"""Domain Pack v1.4 tests.

Tests cover:
- Domain pack loading via registry + scenario_packs
- Domain registry resolution
- Entity schema from profile
- Retrieval policy from profile
- Eval questions contract
- Backward compatibility
- Per-domain DB connection
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# Main Control owns the only runtime scenario-pack tree.
SCENARIO_PACKS_ROOT = (
    Path(__file__).resolve().parents[2]
    / "main_control_service"
    / "config"
    / "scenario_packs"
)


@pytest.fixture
def cloud_profile():
    """Load cloud_core_network from new scenario_packs path (no packs_root → registry)."""
    from knowledge_mining.mining.infra.domain_pack import load_domain_pack
    return load_domain_pack("cloud_core_network")


@pytest.fixture
def cloud_profile_explicit_root():
    """Load cloud_core_network with explicit scenario_packs root."""
    from knowledge_mining.mining.infra.domain_pack import load_domain_pack
    return load_domain_pack("cloud_core_network", packs_root=SCENARIO_PACKS_ROOT)


@pytest.fixture
def generic_profile():
    """Load generic from scenario_packs path."""
    from knowledge_mining.mining.infra.domain_pack import load_domain_pack
    return load_domain_pack("generic", packs_root=SCENARIO_PACKS_ROOT)


# ---------------------------------------------------------------------------
# Test: Domain Registry
# ---------------------------------------------------------------------------

class TestDomainRegistry:
    def test_load_registry(self):
        from knowledge_mining.mining.infra.domain_pack import load_domain_registry
        registry = load_domain_registry()
        assert "domains" in registry
        assert "cloud_core_network" in registry["domains"]

    def test_resolve_cloud_core_network(self):
        from knowledge_mining.mining.infra.domain_pack import resolve_domain
        entry = resolve_domain("cloud_core_network")
        assert entry["enabled"] is True
        assert entry["scenario_pack"] == "cloud_core_network"
        assert "database" in entry or "database_url_env" in entry

    def test_resolve_nonexistent_raises(self):
        from knowledge_mining.mining.infra.domain_pack import resolve_domain
        with pytest.raises(KeyError, match="not found in registry"):
            resolve_domain("nonexistent_domain")

    def test_resolve_generic(self):
        from knowledge_mining.mining.infra.domain_pack import resolve_domain
        entry = resolve_domain("generic")
        assert entry["enabled"] is True


# ---------------------------------------------------------------------------
# Test: Domain Pack Loader
# ---------------------------------------------------------------------------

class TestDomainPackLoader:
    def test_load_cloud_core_network_via_registry(self, cloud_profile):
        assert cloud_profile.domain_id == "cloud_core_network"
        assert "command" in cloud_profile.entity_types
        assert "SMF" in cloud_profile.display_name or "Cloud" in cloud_profile.display_name

    def test_load_cloud_core_network_explicit_root(self, cloud_profile_explicit_root):
        assert cloud_profile_explicit_root.domain_id == "cloud_core_network"
        assert "command" in cloud_profile_explicit_root.entity_types

    def test_load_generic(self, generic_profile):
        assert generic_profile.domain_id == "generic"
        assert "concept" in generic_profile.entity_types
        assert generic_profile.strong_entity_types == frozenset()

    def test_load_nonexistent_raises(self):
        from knowledge_mining.mining.infra.domain_pack import load_domain_pack
        with pytest.raises(FileNotFoundError):
            load_domain_pack("nonexistent_domain", packs_root=SCENARIO_PACKS_ROOT)

    def test_profile_is_frozen(self, cloud_profile):
        with pytest.raises(AttributeError):
            cloud_profile.domain_id = "changed"

    def test_extractor_rules_compiled(self, cloud_profile):
        for rule in cloud_profile.extractor_rules:
            assert rule.compiled is not None
            assert rule.compiled.pattern == rule.pattern

    def test_partitioned_yaml_ontology(self, cloud_profile):
        """New partitioned YAML: ontology fields are read correctly."""
        assert "command" in cloud_profile.entity_types
        assert "network_element" in cloud_profile.entity_types
        assert "parameter" in cloud_profile.strong_entity_types

    def test_partitioned_yaml_mining(self, cloud_profile):
        """New partitioned YAML: mining fields are read correctly."""
        assert cloud_profile.retrieval_policy.entity_card == "off"
        assert cloud_profile.retrieval_policy.max_questions_per_segment == 1
        assert len(cloud_profile.llm_templates) >= 4

    def test_partitioned_yaml_eval_questions(self, cloud_profile):
        """New partitioned YAML: eval questions from mining section."""
        assert len(cloud_profile.eval_questions) == 30


# ---------------------------------------------------------------------------
# Test: Entity Schema from Profile
# ---------------------------------------------------------------------------

class TestDomainEntitySchema:
    def test_segment_understanding_has_semantic_role_enum(self, cloud_profile):
        """实体抽取已拆出（L4 §15）：segment-understanding 只剩篇章字段，注入 semantic_role enum。"""
        from knowledge_mining.mining.infra.llm_templates import build_templates_from_profile
        templates = build_templates_from_profile(cloud_profile)
        seg_tpl = next(t for t in templates if t["template_key"] == "mining-segment-understanding")
        schema = json.loads(seg_tpl["output_schema_json"])
        # 实体不再在 segment-understanding 里
        assert "entities" not in schema["properties"]
        if cloud_profile.semantic_roles:
            assert "enum" in schema["properties"]["semantic_role"]

    def test_entity_extraction_template_dual_channel(self, cloud_profile):
        """新 mining-entity-extraction：双通道 schema（entities + out_of_schema）。"""
        from knowledge_mining.mining.infra.llm_templates import build_templates_from_profile
        templates = build_templates_from_profile(cloud_profile)
        ent_tpl = next(t for t in templates if t["template_key"] == "mining-entity-extraction")
        schema = json.loads(ent_tpl["output_schema_json"])
        props = schema["properties"]
        assert "entities" in props and "out_of_schema" in props
        oos_props = props["out_of_schema"]["items"]["properties"]
        assert "proposed_type" in oos_props

    def test_generic_schema_no_templates(self, generic_profile):
        """Generic pack has no LLM templates."""
        assert len(generic_profile.llm_templates) == 0

    def test_templates_require_an_explicit_domain(self):
        """Template loading uses the requested domain without a global default."""
        from knowledge_mining.mining.infra.llm_templates import load_templates

        templates = load_templates("civil_engineering")

        assert len(templates) >= 4
        assert all(t["knowledge_domain"] == "civil_engineering" for t in templates)
        keys = [t["template_key"] for t in templates]
        assert "mining-question-gen" in keys
        assert "mining-segment-understanding" in keys


# ---------------------------------------------------------------------------
# Test: Domain Retrieval Policy
# ---------------------------------------------------------------------------

class TestDomainRetrievalPolicy:
    def test_cloud_entity_card_only_strong(self, cloud_profile):
        from knowledge_mining.mining.stages.retrieval_units import build_retrieval_units
        from knowledge_mining.mining.contracts.models import RawSegmentData

        seg = RawSegmentData(
            document_key="doc:test",
            segment_index=0,
            raw_text="Configure SMF and UPF",
            entity_refs_json=[
                {"type": "network_element", "name": "SMF"},
                {"type": "concept", "name": "5GC architecture"},
            ],
        )
        units = build_retrieval_units([seg], profile=cloud_profile)
        entity_cards = [u for u in units if u.unit_type == "entity_card"]
        # entity_card is "off" in cloud_core_network retrieval_policy, so no cards expected
        assert len(entity_cards) == 0

    def test_cloud_table_row_off_overrides_manifest_param(self, cloud_profile):
        """域包 retrieval_policy.table_row:'off' 覆盖工作流 manifest 冻结的 tableRowUnit:true。

        已发布工作流的 compiled_manifest 编译期把算子默认 tableRowUnit=true 冻进节点 params，
        改代码默认值改不到已发布 manifest。域包 "off" 必须权威覆盖，否则域级策略形同虚设。
        （所有内置域包 table_row 均为 off，故 table_row 单元默认不应出现。）
        """
        from knowledge_mining.mining.stages.retrieval_units import build_retrieval_units
        from knowledge_mining.mining.contracts.models import RawSegmentData

        seg = RawSegmentData(
            document_key="doc:test",
            segment_index=0,
            block_type="table",
            raw_text="参数 值\nMCC 454\nMNC 12",
            structure_json={
                "columns": ["参数", "值"],
                "rows": [{"参数": "MCC", "值": "454"}, {"参数": "MNC", "值": "12"}],
            },
        )
        # cloud 域包 table_row=off：即便显式 table_row_unit=True（模拟 manifest 冻结值），也不生成
        units = build_retrieval_units([seg], profile=cloud_profile, table_row_unit=True)
        assert not any(u.unit_type == "table_row" for u in units)

    def test_generic_no_entity_cards(self, generic_profile):
        from knowledge_mining.mining.stages.retrieval_units import build_retrieval_units
        from knowledge_mining.mining.contracts.models import RawSegmentData

        seg = RawSegmentData(
            document_key="doc:test",
            segment_index=0,
            raw_text="This describes a concept",
            entity_refs_json=[{"type": "concept", "name": "test"}],
        )
        units = build_retrieval_units([seg], profile=generic_profile)
        entity_cards = [u for u in units if u.unit_type == "entity_card"]
        assert len(entity_cards) == 0  # generic has no strong types

    def test_max_questions_from_policy(self, cloud_profile):
        assert cloud_profile.retrieval_policy.max_questions_per_segment == 1


# ---------------------------------------------------------------------------
# Test: Eval Questions Contract
# ---------------------------------------------------------------------------

class TestEvalQuestionsContract:
    def test_cloud_has_eval_questions(self, cloud_profile):
        assert len(cloud_profile.eval_questions) == 30

    def test_eval_question_structure(self, cloud_profile):
        for q in cloud_profile.eval_questions:
            assert q.id
            assert q.question
            assert isinstance(q.expected_entities, tuple)

    def test_generic_has_few_eval(self, generic_profile):
        assert len(generic_profile.eval_questions) > 0
        assert len(generic_profile.eval_questions) <= 5

    def test_eval_coverage_distribution(self, cloud_profile):
        """Verify the 30 questions cover expected categories."""
        categories = {
            "command": 0, "concept": 0, "parameter": 0,
            "troubleshooting": 0, "cross": 0,
        }
        for q in cloud_profile.eval_questions:
            qid = q.id
            if qid <= "q008":
                categories["command"] += 1
            elif qid <= "q015":
                categories["concept"] += 1
            elif qid <= "q022":
                categories["parameter"] += 1
            elif qid <= "q027":
                categories["troubleshooting"] += 1
            else:
                categories["cross"] += 1

        assert categories["command"] == 8
        assert categories["concept"] == 7
        assert categories["parameter"] == 7
        assert categories["troubleshooting"] == 5
        assert categories["cross"] == 3


# ---------------------------------------------------------------------------
# Test: Backward compatibility
# ---------------------------------------------------------------------------

class TestBackwardCompat:
    def test_models_strong_entity_types_empty(self):
        """STRONG_ENTITY_TYPES is now empty (deprecated)."""
        from knowledge_mining.mining.contracts.models import STRONG_ENTITY_TYPES
        assert STRONG_ENTITY_TYPES == frozenset()

    def test_default_profile_loads(self):
        from knowledge_mining.mining.infra.domain_pack import get_default_profile
        profile = get_default_profile()
        assert profile.domain_id == "cloud_core_network"
        assert "command" in profile.strong_entity_types

    def test_default_domain_comes_from_registry(self):
        """默认域来自 domain_registry.yaml 顶层 default_domain，不在 MiningConfig。"""
        from knowledge_mining.mining.infra.domain_pack import get_default_domain
        assert get_default_domain() == "cloud_core_network"
        # MiningConfig 不再持有 domain 字段（domain 统一由 registry 决定）
        from knowledge_mining.mining.infra.mining_config import MiningConfig
        cfg = MiningConfig(_env_file=None)
        assert not hasattr(cfg, "domain")

    def test_mining_config_defaults_to_workflow_submission(self, monkeypatch):
        """Workflow submission is enabled when no environment override exists."""
        from knowledge_mining.mining.infra.mining_config import MiningConfig

        monkeypatch.delenv("MINING_RUN_SUBMISSION_ENGINE", raising=False)

        cfg = MiningConfig(_env_file=None)

        assert cfg.mining_run_submission_engine == "workflow"
