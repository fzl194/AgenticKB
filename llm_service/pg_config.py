"""PostgreSQL connection config — single source: control plane database.yaml.

No defaults, no env fallbacks. Missing required fields = hard error.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx
import yaml

from llm_service.config import CONTROL_PLANE_BASE_URL
from main_control_service.internal_auth import control_plane_internal_headers

logger = logging.getLogger(__name__)


@dataclass
class LlmDbConfig:
    """PostgreSQL connection settings — every field is required."""

    host: str
    port: int
    dbname: str
    user: str
    password: str
    sslmode: str
    gssencmode: str
    pool_min: int
    pool_max: int

    @property
    def conninfo(self) -> str:
        return (
            f"host={self.host} "
            f"port={self.port} "
            f"dbname={self.dbname} "
            f"user={self.user} "
            f"password={self.password} "
            f"sslmode={self.sslmode} "
            f"gssencmode={self.gssencmode}"
        )

    @property
    def maintenance_conninfo(self) -> str:
        return (
            f"host={self.host} "
            f"port={self.port} "
            f"dbname=postgres "
            f"user={self.user} "
            f"password={self.password} "
            f"sslmode={self.sslmode} "
            f"gssencmode={self.gssencmode}"
        )


def load_db_config() -> LlmDbConfig:
    """Fetch database.yaml from control plane. Fatal on failure."""
    url = CONTROL_PLANE_BASE_URL.rstrip("/")
    endpoint = f"{url}/api/v1/system/database/raw"
    try:
        resp = httpx.get(
            endpoint,
            headers=control_plane_internal_headers(),
            timeout=5.0,
            proxy=None,
            trust_env=False,
        )
        resp.raise_for_status()
        data = yaml.safe_load(resp.text)
    except Exception as exc:
        raise RuntimeError(
            f"Cannot fetch DB config from control plane ({endpoint}): {exc}. "
            f"Ensure main_control_service is running and database.yaml exists."
        ) from exc

    if not isinstance(data, dict):
        raise ValueError("Control plane returned non-dict YAML for database")

    default = data.get("default")
    if not isinstance(default, dict):
        raise ValueError("database.yaml must have a 'default' section")

    required = ["host", "port", "dbname", "user", "password", "sslmode", "gssencmode", "pool_min", "pool_max"]
    missing = [k for k in required if k not in default]
    if missing:
        raise ValueError(f"Missing required DB fields: {', '.join(missing)} in database.yaml → default")

    logger.info("Loaded DB config from control plane (%s)", endpoint)
    return LlmDbConfig(
        host=str(default["host"]),
        port=int(default["port"]),
        dbname=str(default["dbname"]),
        user=str(default["user"]),
        password=str(default["password"]),
        sslmode=str(default["sslmode"]),
        gssencmode=str(default["gssencmode"]),
        pool_min=int(default["pool_min"]),
        pool_max=int(default["pool_max"]),
    )
