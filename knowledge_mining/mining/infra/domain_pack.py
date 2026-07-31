"""Domain Pack: pluggable domain knowledge for the Mining pipeline.

A DomainProfile bundles all domain-specific knowledge:
- Entity types and strong entity types
- Regex extractors, role rules, heading role keywords
- LLM prompt templates
- Retrieval policy
- Eval questions

Loading a different domain pack swaps ALL domain knowledge without
changing any core mining code.

The domain registry (domain_registry.yaml) is the single source of truth:
- It maps domain_id → scenario_pack name + database URL env var
- domain.yaml files live under scenario_packs/<pack_name>/domain.yaml
- domain.yaml uses a partitioned structure: ontology / mining / serving
- Mining reads ontology + mining partitions only
"""
from __future__ import annotations

import logging
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# Repository root (knowledge_mining/mining/infra/ -> CoreMasterKB/)
_REPO_ROOT = Path(__file__).resolve().parents[3]

# Single source of truth paths. main_control_service owns the active config.
_MAIN_CONTROL_CONFIG_ROOT = _REPO_ROOT / "main_control_service" / "config"
_REGISTRY_PATH = _MAIN_CONTROL_CONFIG_ROOT / "domain_registry.yaml"
_SCENARIO_PACKS_ROOT = _MAIN_CONTROL_CONFIG_ROOT / "scenario_packs"



# ---------------------------------------------------------------------------
# Supporting dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ExtractorRule:
    """A single regex-based extraction rule."""
    name: str
    pattern: str
    entity_type: str
    groups: tuple[dict[str, Any], ...] = ()

    # Compiled pattern (not serialized, built at load time)
    _compiled_pattern: Any = field(default=None, repr=False, compare=False)

    @property
    def compiled(self) -> Any:
        import re
        if self._compiled_pattern is None:
            return re.compile(self.pattern)
        return self._compiled_pattern


@dataclass(frozen=True)
class RetrievalPolicy:
    """Retrieval unit generation policy.

    Also carries discourse/relation thresholds and segmentation config
    so they are configurable per-domain.
    """
    raw_text: str = "primary"
    generated_question: str = "auxiliary"
    entity_card: str = "strong_entities_only"
    table_row: str = "structured_tables"
    max_questions_per_segment: int = 2
    max_entity_cards_per_segment: int = 3
    contextual_retrieval: str = "on"

    # Discourse / relation thresholds
    min_confidence: float = 0.5
    max_distance: int = 5
    discourse_window_size: int = 15

    # Question-worthiness thresholds
    min_questionworthy_tokens: int = 50
    not_questionworthy_roles: frozenset[str] = frozenset({"navigation", "toc", "metadata"})

    # Segmentation thresholds (v2.0)
    min_chunk_tokens: int = 128          # minimum chunk size after merge
    max_chunk_tokens: int = 512          # maximum chunk size before split
    cross_section_merge: bool = True     # enable cross-section orphan absorption
    min_enrich_tokens: int = 30          # skip LLM enrich for segments below this
    structural_context_mode: str = "breadcrumb"  # "breadcrumb" | "off"


@dataclass(frozen=True)
class EvalQuestion:
    """A single eval question for retrieval quality assessment."""
    id: str
    question: str
    expected_entities: tuple[str, ...] = ()
    expected_evidence_contains: tuple[str, ...] = ()
    expected_semantic_role: str | None = None
    notes: str = ""


@dataclass(frozen=True)
class DomainProfile:
    """Complete domain knowledge pack for the mining pipeline.

    Immutable: all collection fields are frozen/tuples.
    """
    domain_id: str
    display_name: str

    # Entity configuration
    entity_types: frozenset[str]
    strong_entity_types: frozenset[str]

    # Semantic role mapping: domain keywords -> core semantic_role
    role_keyword_rules: tuple[tuple[list[str], str], ...]

    # Heading role classification: domain keywords -> heading role
    heading_role_keywords: tuple[tuple[list[str], str], ...]

    # Rule-based extractors
    extractor_rules: tuple[ExtractorRule, ...]

    # LLM templates (list of dicts, replaces hardcoded llm_templates.py)
    llm_templates: tuple[dict[str, Any], ...]

    # Domain-specific semantic roles (replaces hardcoded VALID_SEMANTIC_ROLES)
    semantic_roles: frozenset[str]

    # Domain-specific document types (replaces hardcoded VALID_DOCUMENT_TYPES)
    document_types: frozenset[str]

    # Retrieval policy
    retrieval_policy: RetrievalPolicy

    # Eval questions
    eval_questions: tuple[EvalQuestion, ...]


# ---------------------------------------------------------------------------
# Registry loader
# ---------------------------------------------------------------------------

def load_domain_registry() -> dict[str, Any]:
    """Load domain_registry.yaml and return the full dict."""
    if not _REGISTRY_PATH.exists():
        raise FileNotFoundError(
            f"Domain registry not found: {_REGISTRY_PATH}"
        )
    with open(_REGISTRY_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data


def resolve_domain(domain_id: str) -> dict[str, Any]:
    """Resolve a domain_id from the registry.

    Returns:
        Dict with keys: enabled, database_url_env, scenario_pack, default_channel.

    Raises:
        KeyError: If domain_id is not in the registry.
        ValueError: If domain is disabled.
    """
    registry = load_domain_registry()
    domains = registry.get("domains", {})
    if domain_id not in domains:
        raise KeyError(
            f"Domain '{domain_id}' not found in registry. "
            f"Available: {list(domains.keys())}"
        )
    entry = domains[domain_id]
    if not entry.get("enabled", False):
        raise ValueError(f"Domain '{domain_id}' is disabled in registry.")
    return entry


def get_default_domain() -> str:
    """默认域：统一来自 domain_registry.yaml。

    优先取顶层 ``default_domain``（且该域 enabled）；未配置或失效则回落到
    registry 里第一个 enabled 域。mining 自身不在配置里存默认域——domain 一律
    由 registry 决定。
    """
    registry = load_domain_registry()
    domains = registry.get("domains", {})
    default = registry.get("default_domain")
    if (
        default
        and isinstance(default, str)
        and default in domains
        and domains[default].get("enabled", False)
    ):
        return default
    for domain_id, entry in domains.items():
        if entry.get("enabled", False):
            return domain_id
    raise ValueError("No enabled domain found in domain registry")



# ---------------------------------------------------------------------------
# YAML -> DomainProfile converter
# ---------------------------------------------------------------------------

def _parse_extractor_rules(raw: list[dict[str, Any]]) -> tuple[ExtractorRule, ...]:
    import re as _re
    rules = []
    for r in raw:
        groups = tuple(r.get("groups", []))
        # Pre-compile pattern
        compiled = _re.compile(r["pattern"])
        rules.append(ExtractorRule(
            name=r["name"],
            pattern=r["pattern"],
            entity_type=r["entity_type"],
            groups=groups,
            _compiled_pattern=compiled,
        ))
    return tuple(rules)


def _parse_role_keyword_rules(
    raw: list[dict[str, Any]],
) -> tuple[tuple[list[str], str], ...]:
    return tuple(
        (item["keywords"], item["role"])
        for item in raw
    )


def _parse_retrieval_policy(raw: dict[str, Any]) -> RetrievalPolicy:
    nqr_raw = raw.get("not_questionworthy_roles", ["navigation", "toc", "metadata"])
    return RetrievalPolicy(
        raw_text=raw.get("raw_text", "primary"),
        generated_question=raw.get("generated_question", "auxiliary"),
        entity_card=raw.get("entity_card", "strong_entities_only"),
        table_row=raw.get("table_row", "structured_tables"),
        max_questions_per_segment=raw.get("max_questions_per_segment", 2),
        max_entity_cards_per_segment=raw.get("max_entity_cards_per_segment", 3),
        contextual_retrieval=raw.get("contextual_retrieval", "on"),
        min_confidence=raw.get("min_confidence", 0.5),
        max_distance=raw.get("max_distance", 5),
        discourse_window_size=raw.get("discourse_window_size", 15),
        min_questionworthy_tokens=raw.get("min_questionworthy_tokens", 50),
        not_questionworthy_roles=frozenset(nqr_raw),
        min_chunk_tokens=raw.get("min_chunk_tokens", 128),
        max_chunk_tokens=raw.get("max_chunk_tokens", 512),
        cross_section_merge=raw.get("cross_section_merge", True),
        min_enrich_tokens=raw.get("min_enrich_tokens", 30),
        structural_context_mode=raw.get("structural_context_mode", "breadcrumb"),
    )


def _parse_eval_questions(raw: list[dict[str, Any]]) -> tuple[EvalQuestion, ...]:
    return tuple(
        EvalQuestion(
            id=q["id"],
            question=q["question"],
            expected_entities=tuple(q.get("expected_entities", [])),
            expected_evidence_contains=tuple(q.get("expected_evidence_contains", [])),
            expected_semantic_role=q.get("expected_semantic_role"),
            notes=q.get("notes", ""),
        )
        for q in raw
    )


def _parse_domain_yaml(data: dict[str, Any], domain_id: str) -> DomainProfile:
    """Parse a domain.yaml dict into a DomainProfile.

    Supports both the new partitioned structure (ontology/mining/serving)
    and the legacy flat structure for backward compatibility.
    """
    # Detect partitioned vs flat structure
    ontology = data.get("ontology", {})
    mining = data.get("mining", {})

    if ontology or mining:
        # New partitioned structure
        entity_types = ontology.get("entity_types", data.get("entity_types", []))
        strong_entity_types = ontology.get("strong_entity_types", data.get("strong_entity_types", []))
        display_name = data.get("display_name", domain_id)

        role_keyword_rules = mining.get("role_keyword_rules", data.get("role_keyword_rules", []))
        heading_role_keywords = mining.get("heading_role_keywords", data.get("heading_role_keywords", []))
        extractor_rules = mining.get("extractor_rules", data.get("extractor_rules", []))
        llm_templates = mining.get("llm_templates", data.get("llm_templates", []))
        retrieval_policy = mining.get("retrieval_policy", data.get("retrieval_policy", {}))
        eval_questions = mining.get("eval_questions", data.get("eval_questions", []))
        semantic_roles = mining.get("semantic_roles", [])
        document_types = mining.get("document_types", [])
    else:
        # Legacy flat structure
        entity_types = data.get("entity_types", [])
        strong_entity_types = data.get("strong_entity_types", [])
        display_name = data.get("display_name", domain_id)

        role_keyword_rules = data.get("role_keyword_rules", [])
        heading_role_keywords = data.get("heading_role_keywords", [])
        extractor_rules = data.get("extractor_rules", [])
        llm_templates = data.get("llm_templates", [])
        retrieval_policy = data.get("retrieval_policy", {})
        eval_questions = data.get("eval_questions", [])
        semantic_roles = data.get("semantic_roles", [])
        document_types = data.get("document_types", [])

    return DomainProfile(
        domain_id=domain_id,
        display_name=display_name,
        entity_types=frozenset(entity_types),
        strong_entity_types=frozenset(strong_entity_types),
        role_keyword_rules=_parse_role_keyword_rules(role_keyword_rules),
        heading_role_keywords=_parse_role_keyword_rules(heading_role_keywords),
        extractor_rules=_parse_extractor_rules(extractor_rules),
        llm_templates=tuple(llm_templates),
        semantic_roles=frozenset(semantic_roles),
        document_types=frozenset(document_types),
        retrieval_policy=_parse_retrieval_policy(retrieval_policy),
        eval_questions=_parse_eval_questions(eval_questions),
    )


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def _resolve_scenario_pack(domain_id: str) -> str:
    """Resolve domain_id to scenario_pack name via registry.

    Falls back to domain_id itself if registry is unavailable.
    """
    try:
        entry = resolve_domain(domain_id)
        return entry["scenario_pack"]
    except (FileNotFoundError, KeyError, ValueError):
        # Fallback: assume scenario_pack == domain_id
        return domain_id


def load_domain_pack(domain_id: str, *, packs_root: Path | None = None) -> DomainProfile:
    """Load a DomainProfile from the unified scenario_packs directory.

    Resolution:
    1. If packs_root is provided (for testing), use it directly
    2. Otherwise, resolve via domain_registry.yaml → scenario_packs/<pack>/domain.yaml

    Args:
        domain_id: Domain identifier (e.g. "cloud_core_network").
        packs_root: Override root directory (for testing).

    Returns:
        Parsed DomainProfile.

    Raises:
        FileNotFoundError: If domain pack not found.
    """
    if packs_root is not None:
        yaml_path = packs_root / domain_id / "domain.yaml"
    else:
        scenario_pack = _resolve_scenario_pack(domain_id)
        yaml_path = _SCENARIO_PACKS_ROOT / scenario_pack / "domain.yaml"

    if not yaml_path.exists():
        raise FileNotFoundError(
            f"Domain pack not found: {yaml_path} "
            f"(domain_id={domain_id!r})"
        )

    with open(yaml_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    # domain_id is passed in, not read from file
    profile = _parse_domain_yaml(data, domain_id)
    logger.info("Loaded domain pack: %s (%s) from %s", profile.display_name, profile.domain_id, yaml_path)
    return profile


# ---------------------------------------------------------------------------
# Backward compatibility
# ---------------------------------------------------------------------------

def get_default_profile() -> DomainProfile:
    """Load the default domain's profile (from registry's default_domain).

    Provides backward compatibility for code that hasn't been migrated
    to explicitly pass a profile.
    """
    return load_domain_pack(get_default_domain())


# Deprecated: used by models.STRONG_ENTITY_TYPES alias
def _deprecated_strong_entity_types() -> frozenset[str]:
    warnings.warn(
        "models.STRONG_ENTITY_TYPES is deprecated; use DomainProfile.strong_entity_types",
        DeprecationWarning,
        stacklevel=3,
    )
    return get_default_profile().strong_entity_types
