from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

from main_control_service.auth import AuthMiddleware, SiteAdminValidator
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
    site_admin_validator: SiteAdminValidator | None = None,
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
    app.add_middleware(
        AuthMiddleware,
        config_path=auth_yaml_path,
        site_admin_validator=site_admin_validator,
    )
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

    async def require_domain_access(request: Request, domain_id: str) -> dict:
        """Resolve current DB-backed access for one target domain.

        The domain in a URL is caller controlled.  Every detail/proxy request
        therefore re-checks the gateway identity against mining rather than
        trusting that the caller first loaded the filtered domain list.
        """
        if getattr(request.state, "internal_call", False):
            return {"domain": domain_id, "domain_role": "admin", "capabilities": []}
        user = getattr(request.state, "user", None) or {}
        if user.get("role") == "admin":
            return {
                "domain": domain_id,
                "domain_role": "admin",
                "capabilities": ["domain.users.manage", "domain.kbs.manage"],
            }
        internal_secret = getattr(request.app.state, "internal_verify_secret", "") or ""
        access, reason = await service.domain_access_for(
            str(user.get("username") or ""), internal_secret,
        )
        if access is None:
            raise HTTPException(
                status_code=503, detail=f"mining_unavailable:{reason or 'unknown'}"
            )
        capabilities_by_domain = access.get("capabilities_by_domain") or {}
        for grant in access.get("grants") or []:
            if isinstance(grant, dict) and grant.get("domain") == domain_id:
                return {
                    **grant,
                    "capabilities": list(capabilities_by_domain.get(domain_id) or []),
                }
        # Match the resource-level 404 convention: do not disclose unassigned
        # domain existence to a caller that guessed its id.
        raise HTTPException(status_code=404, detail="domain_not_found")

    @app.get("/api/v1/domains")
    async def list_domains(request: Request) -> dict:
        # 51号批次1：内部旁路调用（无用户态，X-Internal-Auth 已在中间件验过）与
        # admin 全量；普通用户按 mining 绑定域过滤（fail-closed：不可达 503）。
        user = getattr(request.state, "user", None)
        if user is None or user.get("role") == "admin":
            return {"items": service.list_domains()}
        internal_secret = getattr(request.app.state, "internal_verify_secret", "") or ""
        access, reason = await service.domain_access_for(
            str(user.get("username") or ""), internal_secret
        )
        if access is None:
            raise HTTPException(
                status_code=503, detail=f"mining_unavailable:{reason or 'unknown'}"
            )
        grants = {
            str(grant["domain"]): str(grant.get("domain_role") or "member")
            for grant in access.get("grants") or []
            if isinstance(grant, dict) and grant.get("domain")
        }
        capabilities = access.get("capabilities_by_domain") or {}
        return {"items": [
            {
                **domain,
                "domain_role": grants[str(domain.get("domain_id"))],
                "capabilities": list(capabilities.get(str(domain.get("domain_id"))) or []),
            }
            for domain in service.list_domains()
            if str(domain.get("domain_id")) in grants
        ]}

    @app.get("/api/v1/domains/{domain_id}")
    async def get_domain(domain_id: str, request: Request) -> dict:
        await require_domain_access(request, domain_id)
        return service.get_domain(domain_id)

    @app.get("/api/v1/domains/{domain_id}/raw")
    async def get_domain_raw(domain_id: str, request: Request) -> Response:
        await require_domain_access(request, domain_id)
        return Response(content=service.get_domain_yaml(domain_id), media_type="text/yaml")

    @app.post("/api/v1/domains")
    async def create_domain(request: Request) -> dict:
        body = await request.json()
        domain_id = body.get("domain_id")
        if not domain_id:
            raise HTTPException(status_code=400, detail="domain_id is required")
        return service.create_domain(domain_id, body)

    @app.put("/api/v1/domains/{domain_id}/raw")
    async def update_domain_raw(domain_id: str, request: Request) -> dict:
        body = await request.body()
        text = body.decode("utf-8")
        service.update_domain_yaml(domain_id, text)
        return {"ok": True}

    @app.delete("/api/v1/domains/{domain_id}")
    async def delete_domain(domain_id: str, request: Request) -> dict:
        # 51号批次1：删域保护——域下仍有 KB 时 409；mining 不可达时 503（fail-closed）。
        internal_secret = getattr(request.app.state, "internal_verify_secret", "") or ""
        count = await service.kb_count_for(domain_id, internal_secret)
        if count is None:
            raise HTTPException(status_code=503, detail="mining_unavailable")
        if count > 0:
            raise HTTPException(status_code=409, detail=f"domain_has_kbs:{count}")
        service.delete_domain(domain_id)
        return {"ok": True}

    # ------------------------------------------------------------------
    # Scenario packs — YAML text passthrough
    # ------------------------------------------------------------------

    @app.get("/api/v1/domains/{domain_id}/scenario")
    async def get_scenario(
        domain_id: str, request: Request, section: str | None = None,
    ) -> dict:
        await require_domain_access(request, domain_id)
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
    # Admin — one-click backend restart（nginx 不动，前端是重启期间的观测面）
    # ------------------------------------------------------------------

    @app.post("/api/v1/admin/restart")
    def restart_backend(request: Request) -> Response:
        from main_control_service import restart_services

        if not restart_services.supervisor_available():
            return JSONResponse(
                status_code=503,
                content={"detail": "supervisor 不可用：一键重启仅支持容器化部署"},
            )
        if restart_services.is_active(restart_services.read_status()):
            return JSONResponse(
                status_code=409,
                content={"detail": "restart_in_progress"},
            )
        user = getattr(request.state, "user", None) or {}
        triggered_by = str(user.get("username") or "unknown")
        # 先写 running 占位再拉编排进程：双击竞态下第二个请求能看到 running。
        restart_services.write_status({
            "state": "running",
            "triggered_by": triggered_by,
            "started_at": restart_services.now_iso(),
            "finished_at": None,
            "plan": [s.program for s in restart_services.restart_plan()],
            "completed": [],
            "current": None,
            "error": None,
        })
        restart_services.spawn_orchestrator(triggered_by)
        return JSONResponse(
            status_code=202,
            content={"ok": True, "triggered_by": triggered_by},
        )

    @app.get("/api/v1/admin/restart/status")
    def restart_status() -> dict:
        from main_control_service import restart_services

        status = restart_services.read_status()
        if not status:
            return {"state": "idle", "active": False}
        return {**status, "active": restart_services.is_active(status)}

    # ------------------------------------------------------------------
    # Reverse proxy — domain-aware routing to backend services
    # ------------------------------------------------------------------

    @app.api_route(
        "/api/v1/proxy/{domain_id}/{service}/{path:path}",
        methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
    )
    async def reverse_proxy(domain_id: str, service: str, path: str, request: Request) -> Response:
        await require_domain_access(request, domain_id)
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
