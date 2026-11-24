from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from llm_service.models import (
    EmbeddingRequest,
    EmbeddingResponse,
    RerankRequest,
    RerankResponse,
)
from llm_service.providers.model_base import ModelProviderError

router = APIRouter(prefix="/api/v1/models", tags=["models"])

_DEPRECATED_HEADER = {
    "X-Deprecation-Notice": "Use POST /api/v1/tasks/embed or /api/v1/tasks/rerank for async queued processing",
}


def _to_http_error(exc: ModelProviderError) -> HTTPException:
    if exc.error_type == "not_configured":
        return HTTPException(status_code=503, detail=exc.message)
    if exc.error_type == "rate_limited":
        return HTTPException(status_code=429, detail=exc.message)
    if exc.error_type in ("timeout", "connection_error", "server_error"):
        return HTTPException(status_code=502, detail=exc.message)
    return HTTPException(status_code=400, detail=exc.message)


@router.get("/vision-default")
async def vision_default(request: Request):
    """默认视觉模型名（模型选择归 llm_service 管，挖掘侧不存模型名）。

    回落链：provider.vision_model → provider.active_model → null。
    只返回模型名与来源，不含地址/密钥。
    """
    resolver = getattr(request.app.state, "provider_resolver", None)
    if resolver is None:
        return {"success": True, "data": {"model": None, "source": "none"}}
    vision = resolver.vision_default_model()
    if vision:
        return {"success": True, "data": {"model": vision, "source": "vision_model"}}
    active = resolver.active_model_name()
    if active:
        return {"success": True, "data": {"model": active, "source": "active_model"}}
    return {"success": True, "data": {"model": None, "source": "none"}}


@router.post("/embeddings", response_model=EmbeddingResponse)
async def create_embeddings(body: EmbeddingRequest, request: Request):
    svc = request.app.state.model_service
    try:
        result = await svc.embed(body)
        return JSONResponse(
            content=result.model_dump(),
            headers=_DEPRECATED_HEADER,
        )
    except ModelProviderError as exc:
        raise _to_http_error(exc) from exc


@router.post("/rerank", response_model=RerankResponse)
async def create_rerank(body: RerankRequest, request: Request):
    svc = request.app.state.model_service
    try:
        result = await svc.rerank(body)
        return JSONResponse(
            content=result.model_dump(),
            headers=_DEPRECATED_HEADER,
        )
    except ModelProviderError as exc:
        raise _to_http_error(exc) from exc
