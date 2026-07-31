"""Mining pipeline configuration — 来源：main_control_service。

配置来自 ``GET /api/v1/system/mining/raw``（main_control_service/config/system/mining.yaml）。
**不再读 .env。** 无参 ``MiningConfig()`` 读控制面缓存（启动时预填）；显式 kwargs（测试）直接构造。

Mining calls llm_service for both chat (template-based) and embedding.
Only the embedding model name and dimensions need to be configured there;
the actual API key, base URL for embedding are handled by llm_service。

domain 不在 mining 配置里：统一来自 domain_registry.yaml（domain_pack.get_default_domain）。
"""
from __future__ import annotations

from typing import Any

from .control_plane import get_mining_service_config


class MiningConfig:
    """Mining pipeline configuration.

    Fields:
        llm_service_url:             llm_service address
        max_workers:                 max concurrent workers for streaming pipeline
        mining_run_submission_engine: 'legacy' | 'workflow'
        port:                        mining API listen port

    domain 由 domain_registry.yaml 决定，不在此处。
    """

    def __init__(self, **fields: Any) -> None:
        if not fields:
            data = get_mining_service_config()
            fields = {
                "llm_service_url": data.get("llm_service_url", "http://localhost:8900"),
                "max_workers": int(data.get("max_workers", 4)),
                "mining_run_submission_engine": data.get("mining_run_submission_engine", "workflow"),
                "port": int(data.get("port", 8901)),
            }
        else:
            # 显式构造（测试）：_env_file 等 pydantic 残留键被忽略
            fields = {
                "llm_service_url": fields.get("llm_service_url", "http://localhost:8900"),
                "max_workers": int(fields.get("max_workers", 4)),
                "mining_run_submission_engine": fields.get("mining_run_submission_engine", "workflow"),
                "port": int(fields.get("port", 8901)),
            }
        self.__dict__.update(fields)

    def __repr__(self) -> str:
        return (
            f"MiningConfig(llm_service_url={self.llm_service_url!r}, "
            f"max_workers={self.max_workers}, port={self.port})"
        )
