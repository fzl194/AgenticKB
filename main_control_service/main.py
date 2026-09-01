from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

from main_control_service.auth import AuthMiddleware
from main_control_service.config import MainControlSettings
from main_control_service.ip_whitelist import IpWhitelistMiddleware
from main_control_service.proxy import (
    create_proxy_client,
    get_proxy_client,
    set_proxy_client,
    shutdown_proxy_client,
    proxy_request,
)
from main_control_service.service import YamlConfigService
from main_control_service.release_info import (
    default_release_manifest_path,
    load_current_release,
)


def _find_auth_mw(request: Request) -> AuthMiddleware | None:
    """在中间件栈中找 AuthMiddleware 实例（模块级，供 login/me/reload-auth 与测试复用）。"""
    layer = getattr(request.app, "middleware_stack", None) or request.app
    while hasattr(layer, "app"):
        if isinstance(layer, AuthMiddleware):
            return layer
        layer = layer.app
    return None


def _cors_origins() -> list[str]:
    return ["http://localhost:8080", "http://127.0.0.1:8080"]


async def verify_user_via_mining(
    verify_url: str, internal_secret: str, username: str, password: str,
) -> dict | None:
    """POST mining /api/kb/auth/verify（带 X-Internal-Auth）。成功返 {ok,user}，失败/异常返 None。"""
    if not internal_secret:
        return None
    try:
        resp = await get_proxy_client().post(
            f"{verify_url}/api/kb/auth/verify",
            json={"username": username, "password": password},
            headers={"X-Internal-Auth": internal_secret},
            timeout=10.0,
        )
    except Exception:  # noqa: BLE001 — best-effort
        return None
    if resp.status_code == 200:
        return resp.json()
    return None


async def identify_via_mining(
    verify_url: str, internal_secret: str, username: str,
) -> dict | None:
    """POST mining /api/kb/auth/identify（带 X-Internal-Auth）。返回 {mode,...} 或 None。"""
    if not internal_secret:
        return None
    try:
        resp = await get_proxy_client().post(
            f"{verify_url}/api/kb/auth/identify",
            json={"username": username},
            headers={"X-Internal-Auth": internal_secret},
            timeout=10.0,
        )
    except Exception:  # noqa: BLE001 — best-effort
        return None
    if resp.status_code == 200:
        return resp.json()
    return None


def create_app(
    *,
    config_dir: Path | None = None,
    settings: MainControlSettings | None = None,
    release_manifest_path: Path | None = None,
) -> FastAPI:
    cfg = settings or MainControlSettings()
    effective_config_dir = config_dir or cfg.config_dir
    service = YamlConfigService(config_dir=effective_config_dir)
    release_info = load_current_release(
        release_manifest_path or default_release_manifest_path()
    )
    ip_whitelist_path = effective_config_dir / "system" / "ip_whitelist.yaml"

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.main_control = service
        # Proxy client — shared across all reverse-proxy requests
        client = create_proxy_client()
        set_proxy_client(client)
        try:
            yield
        finally:
            await shutdown_proxy_client()

    app = FastAPI(
        title="Main Control Service",
        version=release_info["version"],
        description="YAML config center for CoreMasterKB services — full CRUD.",
        lifespan=lifespan,
    )

    auth_yaml_path = effective_config_dir / "system" / "auth.yaml"

    # 注册顺序 → Starlette 反序执行 → 执行序：IpWhitelist(最外) → CORS → Auth(最内)。
    # CORS 必须在 Auth 之外，否则浏览器 preflight OPTIONS 会被 Auth 当无 token → 401。
    app.add_middleware(AuthMiddleware, config_path=auth_yaml_path)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins(),
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(IpWhitelistMiddleware, config_path=ip_whitelist_path)

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    @app.get("/health")
    def health() -> dict[str, str]:
        return {
            "status": "ok",
            "mode": "yaml_crud",
            "version": release_info["version"],
        }

    @app.get("/api/v1/version")
    def version() -> dict[str, object]:
        """Return the immutable release record baked into this deployment."""
        return release_info

    # ------------------------------------------------------------------
    # Auth — login / me（SKIP_PATHS 免 token）/ reload-auth（admin-only）
    # ------------------------------------------------------------------

    @app.post("/api/v1/auth/login")
    async def login(request: Request) -> Response:
        body = await request.json()
        username = str(body.get("username") or "").strip()
        password = body.get("password")  # 可空：工号 member 无密码
        if not username:
            return JSONResponse(status_code=400, content={"detail": "username required"})
        # auth 是全局的 —— 取任一启用域的 mining_url 即可（验密码与域无关）。
        mining_url: str | None = None
        for entry in service.list_domains():
            if not entry.get("enabled", True):
                continue
            did = entry.get("domain_id")
            if not did:
                continue
            try:
                svcs = service.get_domain_services(did)
            except Exception:  # noqa: BLE001
                continue
            if svcs.get("mining_url"):
                mining_url = str(svcs["mining_url"]).rstrip("/")
                break
        if not mining_url:
            return JSONResponse(status_code=503, content={"detail": "mining backend unavailable"})
        result = await verify_user_via_mining(
            mining_url, request.app.state.internal_verify_secret, username, password,
        )
        if not result or not result.get("ok"):
            return JSONResponse(status_code=401, content={"detail": "invalid credentials"})
        u = result["user"]
        auth_mw = _find_auth_mw(request)
        secret = auth_mw.jwt_secret if auth_mw else ""
        ttl = auth_mw.token_ttl_seconds if auth_mw else 43200
        from main_control_service.jwt_util import encode as jwt_encode
        token = jwt_encode(
            {"sub": u["username"], "role": u["site_role"], "name": u.get("display_name") or u["username"]},
            secret, ttl=int(ttl),
        )
        return JSONResponse(content={"token": token, "user": u})

    @app.post("/api/v1/auth/identify")
    async def identify(request: Request) -> Response:
        """登录第一步：按用户名判定模式（password/member/not_found）。透传 mining。"""
        body = await request.json()
        username = str(body.get("username") or "").strip()
        if not username:
            return JSONResponse(status_code=400, content={"detail": "username required"})
        mining_url: str | None = None
        for entry in service.list_domains():
            if not entry.get("enabled", True):
                continue
            did = entry.get("domain_id")
            if not did:
                continue
            try:
                svcs = service.get_domain_services(did)
            except Exception:  # noqa: BLE001
                continue
            if svcs.get("mining_url"):
                mining_url = str(svcs["mining_url"]).rstrip("/")
                break
        if not mining_url:
            return JSONResponse(status_code=503, content={"detail": "mining backend unavailable"})
        result = await identify_via_mining(
            mining_url, request.app.state.internal_verify_secret, username,
        )
        if result is None:
            return JSONResponse(status_code=502, content={"detail": "mining identify unavailable"})
        return JSONResponse(content=result)

    @app.get("/api/v1/auth/me")
    def me(request: Request) -> Response:
        u = getattr(request.state, "user", None)
        if not u:
            return JSONResponse(status_code=401, content={"detail": "unauthenticated"})
        return JSONResponse(content={
            "username": u.get("username"),
            "site_role": u.get("role"),
            "display_name": u.get("name"),
        })

    # ------------------------------------------------------------------
    # System config — YAML text passthrough
    # ------------------------------------------------------------------

    @app.get("/api/v1/system")
    def list_system_configs() -> dict:
        return {"items": service.list_system_configs()}

    @app.get("/api/v1/system/{service_name}")
    def get_system_config(service_name: str) -> dict:
        return service.get_system_config(service_name)

    @app.get("/api/v1/system/{service_name}/raw")
    def get_system_config_raw(service_name: str) -> Response:
        return Response(content=service.get_system_config_yaml(service_name), media_type="text/yaml")

    @app.put("/api/v1/system/{service_name}/raw")
    async def update_system_config_raw(service_name: str, request: Request) -> dict:
        body = await request.body()
        text = body.decode("utf-8")
        service.update_system_config_yaml(service_name, text)
        return {"ok": True}

    # ------------------------------------------------------------------
    # Domains — JSON list + YAML text CRUD
    # ------------------------------------------------------------------

    @app.get("/api/v1/domains")
    def list_domains() -> dict:
        return {"items": service.list_domains()}

    @app.get("/api/v1/domains/{domain_id}")
    def get_domain(domain_id: str) -> dict:
        return service.get_domain(domain_id)

    @app.get("/api/v1/domains/{domain_id}/raw")
    def get_domain_raw(domain_id: str) -> Response:
        return Response(content=service.get_domain_yaml(domain_id), media_type="text/yaml")

    @app.post("/api/v1/domains")
    async def create_domain(request: Request) -> dict:
        body = await request.json()
        domain_id = body.get("domain_id")
        if not domain_id:
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail="domain_id is required")
        return service.create_domain(domain_id, body)

    @app.put("/api/v1/domains/{domain_id}/raw")
    async def update_domain_raw(domain_id: str, request: Request) -> dict:
        body = await request.body()
        text = body.decode("utf-8")
        service.update_domain_yaml(domain_id, text)
        return {"ok": True}

    @app.delete("/api/v1/domains/{domain_id}")
    def delete_domain(domain_id: str) -> dict:
        service.delete_domain(domain_id)
        return {"ok": True}

    # ------------------------------------------------------------------
    # Scenario packs — YAML text passthrough
    # ------------------------------------------------------------------

    @app.get("/api/v1/domains/{domain_id}/scenario")
    def get_scenario(domain_id: str, section: str | None = None) -> dict:
        return service.get_scenario(domain_id, section)

    @app.get("/api/v1/domains/{domain_id}/scenario/raw")
    def get_scenario_raw(domain_id: str) -> Response:
        return Response(content=service.get_scenario_yaml(domain_id), media_type="text/yaml")

    @app.put("/api/v1/domains/{domain_id}/scenario/raw")
    async def update_scenario_raw(domain_id: str, request: Request) -> dict:
        body = await request.body()
        text = body.decode("utf-8")
        service.update_scenario_yaml(domain_id, text)
        return {"ok": True}

    # ------------------------------------------------------------------
    # Serving config snapshot — agent_serving pulls this on startup/reload
    # ------------------------------------------------------------------

    @app.get("/api/v1/serving-config")
    def get_serving_config() -> dict:
        return service.get_serving_config()

    # ------------------------------------------------------------------
    # Code sync — GitHub archive -> local Python services
    # ------------------------------------------------------------------

    @app.post("/api/v1/code-sync")
    def sync_code() -> dict:
        from main_control_service.code_sync import sync_from_github

        result = sync_from_github()
        return {
            "ok": result.ok,
            "updated_dirs": result.updated_dirs,
            "file_count": result.file_count,
            **({"error": result.error} if result.error else {}),
        }

    # ------------------------------------------------------------------
    # Service logs — read-only tail of /app/logs (written by supervisor)
    # ------------------------------------------------------------------

    @app.get("/api/v1/logs")
    def list_service_logs() -> dict:
        from main_control_service.logs import list_logs, log_dir

        return {
            "log_dir": str(log_dir()),
            "files": [
                {
                    "name": f.name,
                    "size_bytes": f.size_bytes,
                    "modified_at": f.modified_at,
                    "rotated_count": f.rotated_count,
                }
                for f in list_logs()
            ],
        }

    @app.get("/api/v1/logs/{name}")
    def read_service_log(
        name: str,
        lines: int = 200,
        q: str | None = None,
        level: str | None = None,
    ) -> Response:
        from main_control_service.logs import tail_log

        content = tail_log(name, lines=lines, keyword=q, level=level)
        if content is None:
            return JSONResponse(
                status_code=404,
                content={"error": "log_not_found", "name": name},
            )
        return JSONResponse(
            content={
                "name": content.name,
                "lines": content.lines,
                "returned_lines": content.returned_lines,
                "size_bytes": content.size_bytes,
                "truncated": content.truncated,
                "filtered": content.filtered,
            }
        )

    # ------------------------------------------------------------------
    # Reverse proxy — domain-aware routing to backend services
    # ------------------------------------------------------------------

    @app.api_route(
        "/api/v1/proxy/{domain_id}/{service}/{path:path}",
        methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
    )
    async def reverse_proxy(domain_id: str, service: str, path: str, request: Request) -> Response:
        svc: YamlConfigService = request.app.state.main_control  # type: ignore[attr-defined]
        domain_services = svc.get_domain_services(domain_id)
        return await proxy_request(request, domain_id, service, path, domain_services)

    # ------------------------------------------------------------------
    # Admin — IP whitelist hot-reload
    # ------------------------------------------------------------------

    def _find_ip_whitelist_mw(request: Request) -> IpWhitelistMiddleware | None:
        layer = request.app
        while hasattr(layer, "app"):
            if isinstance(layer, IpWhitelistMiddleware):
                return layer
            layer = layer.app
        return None

    @app.post("/api/v1/admin/reload-ip-whitelist")
    def reload_ip_whitelist(request: Request) -> dict:
        mw = _find_ip_whitelist_mw(request)
        if mw:
            return mw.reload()
        return {"error": "IpWhitelistMiddleware not found in middleware stack"}

    @app.get("/api/v1/admin/ip-whitelist-status")
    def ip_whitelist_status(request: Request) -> dict:
        mw = _find_ip_whitelist_mw(request)
        if mw:
            return mw.reload()  # reload() returns current state
        return {"error": "IpWhitelistMiddleware not found in middleware stack"}

    @app.post("/api/v1/admin/reload-auth")
    async def reload_auth(request: Request) -> dict:
        mw = _find_auth_mw(request)
        result = mw.reload() if mw else {"error": "AuthMiddleware not found in middleware stack"}
        # 扇出到 mining：让其强制重拉 auth.yaml 刷 internal_verify_secret 缓存。
        # 否则网关换新 secret、mining 仍验旧值 → 全部代理 401（mining 缓存启动期拉取、原本无 reload）。
        internal_secret = getattr(request.app.state, "internal_verify_secret", "")
        client = get_proxy_client()
        mining_hits: list[dict] = []
        if not internal_secret:
            mining_hits.append({"ok": False, "error": "internal_verify_secret not set on gateway"})
        else:
            seen: set[str] = set()
            for entry in service.list_domains():
                if not entry.get("enabled", True):
                    continue
                did = entry.get("domain_id")
                if not did:
                    continue
                try:
                    svcs = service.get_domain_services(did)
                except Exception:  # noqa: BLE001
                    continue
                url = svcs.get("mining_url")
                if not url:
                    continue
                base = str(url).rstrip("/")
                if base in seen:
                    continue
                seen.add(base)
                try:
                    resp = await client.post(
                        f"{base}/api/kb/admin/reload-auth-config",
                        headers={"X-Internal-Auth": internal_secret},
                        timeout=10.0,
                    )
                    mining_hits.append({
                        "url": base, "ok": resp.status_code < 400, "status": resp.status_code,
                    })
                except Exception as exc:  # noqa: BLE001 — best-effort fan-out
                    mining_hits.append({"url": base, "ok": False, "error": str(exc)})
        return {**result, "mining": mining_hits}

    # ------------------------------------------------------------------
    # Admin — fan out config hot-reload to agent_serving instances
    # ------------------------------------------------------------------

    @app.post("/api/v1/admin/reload-serving")
    async def reload_serving() -> dict:
        """Trigger config hot-reload on every enabled domain's serving instance.

        Called by the kb-ui "配置热重载" button after a config save. Each distinct
        serving_url is hit once; failures are reported, never raised.
        """
        client = get_proxy_client()
        results: list[dict] = []
        for url in service.serving_reload_targets():
            target = f"{url.rstrip('/')}/api/v1/admin/reload-config"
            try:
                resp = await client.post(target, timeout=30.0)
                results.append({
                    "url": url,
                    "ok": resp.status_code < 400,
                    "status": resp.status_code,
                    "detail": resp.text[:500],
                })
            except Exception as exc:  # noqa: BLE001 — best-effort fan-out
                results.append({"url": url, "ok": False, "error": str(exc)})
        return {"ok": all(r["ok"] for r in results) if results else True, "results": results}

    return app


app = create_app()


if __name__ == "__main__":
    import copy
    import logging
    import os

    import uvicorn
    from uvicorn.config import LOGGING_CONFIG

    _DATEFMT = "%Y-%m-%d %H:%M:%S"
    _LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=_LOG_LEVEL,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt=_DATEFMT,
    )
    _log_config = copy.deepcopy(LOGGING_CONFIG)
    _log_config["formatters"]["default"]["fmt"] = "%(asctime)s %(levelprefix)s %(message)s"
    _log_config["formatters"]["default"]["datefmt"] = _DATEFMT
    _log_config["formatters"]["access"]["fmt"] = (
        '%(asctime)s %(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s'
    )
    _log_config["formatters"]["access"]["datefmt"] = _DATEFMT
    for _logger in _log_config.get("loggers", {}).values():
        _logger["level"] = _LOG_LEVEL

    cfg = MainControlSettings()
    uvicorn.run(
        "main_control_service.main:app",
        host=cfg.host,
        port=cfg.port,
        reload=False,
        log_config=_log_config,
    )
