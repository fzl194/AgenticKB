"""Configuration management routes."""
from __future__ import annotations

from fastapi import APIRouter, Request

from knowledge_mining.mining.infra.mining_config import MiningConfig
from knowledge_mining.mining.infra.pg_config import MiningDbConfig
from knowledge_mining.mining.infra.domain_pack import get_default_domain

router = APIRouter(prefix="/api/config", tags=["config"])


@router.get("")
async def get_config(request: Request) -> dict:
    """Current mining configuration (non-sensitive)."""
    cfg: MiningDbConfig = request.app.state.db_config
    mining_cfg = MiningConfig()
    return {
        "default_domain": get_default_domain(),
        "max_workers": mining_cfg.max_workers,
        "llm_service_url": mining_cfg.llm_service_url,
        "database": {
            "host": cfg.pg_host,
            "port": cfg.pg_port,
            "dbname": cfg.pg_dbname,
            "pool_min": cfg.pg_pool_min,
            "pool_max": cfg.pg_pool_max,
        },
    }


@router.get("/domain-packs")
async def list_domain_packs(request: Request) -> dict:
    """List available domain packs."""
    from knowledge_mining.mining.infra.domain_pack import _SCENARIO_PACKS_ROOT

    packs_dir = _SCENARIO_PACKS_ROOT
    packs = []
    if packs_dir.exists():
        for f in sorted(packs_dir.iterdir()):
            if f.is_dir() and (f / "profile.yaml").exists():
                packs.append({"name": f.name, "has_profile": True})
            elif f.is_dir():
                packs.append({"name": f.name, "has_profile": False})
    return {"packs": packs}


@router.get("/domain-packs/{name}")
async def get_domain_pack(name: str, request: Request) -> dict:
    """Get domain pack details."""
    try:
        from knowledge_mining.mining.infra.domain_pack import load_domain_pack
        profile = load_domain_pack(name)
        return {
            "name": name,
            "entity_types": [et.name for et in profile.entity_types],
            "role_rules": [{"block_type": r.block_type, "semantic_role": r.semantic_role}
                           for r in profile.role_rules],
            "retrieval_policy": {
                "min_segment_tokens": profile.retrieval_policy.min_segment_tokens,
                "max_segment_tokens": profile.retrieval_policy.max_segment_tokens,
                "context_window_segments": profile.retrieval_policy.context_window_segments,
            },
        }
    except Exception as e:
        return {"error": str(e)}


@router.get("/stages")
async def list_stages(request: Request) -> dict:
    """List registered pipeline stages."""
    stages = {
        "parse": {"1": "ParserStage"},
        "segment": {"1": "DefaultSegmenter"},
        "enrich": {"2": "LlmEnricher"},
        "discourse_relations": {"1": "DiscourseRelationBuilder"},
        "retrieval_units": {"1": "RetrievalUnitBuilder"},
    }
    return {"stages": stages}
