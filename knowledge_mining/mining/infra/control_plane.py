"""Mining 配置统一从 main_control_service 获取（仿 llm_service）。

唯一的环境变量是引导用的 ``CONTROL_PLANE_BASE_URL``（默认 http://localhost:8910），
其余配置全部向主控制服务拉取：
- ``GET /api/v1/system/mining/raw``   —— 挖掘服务配置（llm_service_url / max_workers /
  mining_run_submission_engine / upload.* / port）。对应 main_control_service/config/system/mining.yaml。
- ``GET /api/v1/system/database/raw`` —— 数据库配置（default 段）。对应 .../system/database.yaml。

启动时（lifespan / __main__）拉取一次并缓存，后续 ``MiningDbConfig()`` / ``MiningConfig()``
/ ``UploadConfig()`` 无参构造直接读缓存，不再打 HTTP。测试通过 ``set_*_config`` 预填缓存，
不依赖控制面，也不读 .env 文件。
"""
from __future__ import annotations

import os
from typing import Any

import httpx
import yaml

# 唯一引导环境变量：主控制服务地址。与 llm_service 的 CONTROL_PLANE_BASE_URL 同名同默认。
CONTROL_PLANE_BASE_URL = os.getenv("CONTROL_PLANE_BASE_URL", "http://localhost:8910").rstrip("/")

_service_config_cache: dict[str, Any] | None = None
_database_config_cache: dict[str, Any] | None = None
_auth_config_cache: dict[str, Any] | None = None


def _get_raw(service_name: str, *, timeout: float = 5.0) -> dict[str, Any]:
    """GET /api/v1/system/{service_name}/raw，返回 yaml.safe_load 后的 dict。"""
    endpoint = f"{CONTROL_PLANE_BASE_URL}/api/v1/system/{service_name}/raw"
    # proxy=None / trust_env=False：显式绕过系统代理与环境代理变量，与 llm_service 一致。
    resp = httpx.get(endpoint, timeout=timeout, proxy=None, trust_env=False)
    resp.raise_for_status()
    return yaml.safe_load(resp.text) or {}


def fetch_mining_service_config(*, force: bool = False) -> dict[str, Any]:
    """拉取并缓存挖掘服务配置（mining.yaml）。"""
    global _service_config_cache
    if _service_config_cache is None or force:
        _service_config_cache = _get_raw("mining")
    return _service_config_cache


def fetch_database_config(*, force: bool = False) -> dict[str, Any]:
    """拉取并缓存数据库配置（database.yaml）。"""
    global _database_config_cache
    if _database_config_cache is None or force:
        _database_config_cache = _get_raw("database")
    return _database_config_cache


def get_mining_service_config() -> dict[str, Any]:
    """读缓存；缓存空则触发一次拉取。"""
    if _service_config_cache is None:
        return fetch_mining_service_config()
    return _service_config_cache


def get_database_config() -> dict[str, Any]:
    """读缓存；缓存空则触发一次拉取。"""
    if _database_config_cache is None:
        return fetch_database_config()
    return _database_config_cache


def set_mining_service_config(cfg: dict[str, Any]) -> None:
    """预填挖掘服务配置缓存（启动 / 测试用）。"""
    global _service_config_cache
    _service_config_cache = cfg


def set_database_config(cfg: dict[str, Any]) -> None:
    """预填数据库配置缓存（启动 / 测试用）。"""
    global _database_config_cache
    _database_config_cache = cfg


def override_upload_root(root: str) -> None:
    """测试用：覆盖已缓存服务配置里的 upload.root（不动其它字段）。

    生产配置来自主控制服务；测试需要把上传根指到 tmp 目录时用此覆盖缓存。
    """
    if _service_config_cache is None:
        set_mining_service_config({})
    _service_config_cache.setdefault("upload", {})["root"] = root


# ── auth.yaml（Phase 2：内部校验凭证 + bootstrap）──────────────────────────────
def fetch_auth_config(*, force: bool = False) -> dict[str, Any]:
    """拉取并缓存 auth.yaml。best-effort：控制面不可达抛异常由调用方兜底。"""
    global _auth_config_cache
    if _auth_config_cache is None or force:
        _auth_config_cache = _get_raw("auth")
    return _auth_config_cache


def get_auth_config() -> dict[str, Any]:
    if _auth_config_cache is None:
        return fetch_auth_config()
    return _auth_config_cache


def set_auth_config(cfg: dict[str, Any]) -> None:
    """预填 auth 配置缓存（启动 / 测试用）。"""
    global _auth_config_cache
    _auth_config_cache = cfg


def get_internal_verify_secret() -> str | None:
    """current_user 校验 X-Internal-Auth 用的内部凭证。

    缓存未就绪 / 空 / 仍是 auth.yaml 样板占位符 → None（current_user 一律 401）。
    拒占位符是防误部署：占位符在仓库公开，若接受则任何人能直连 8901 伪造 X-Internal-Auth。
    """
    if _auth_config_cache is None:
        return None
    val = _auth_config_cache.get("internal_verify_secret")
    if not isinstance(val, str) or not val or val.startswith("change-me"):
        return None
    return val
