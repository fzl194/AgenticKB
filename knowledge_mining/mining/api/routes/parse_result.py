"""HTTP adapter: 文档绑定的解析结果只读视图（M5，前端「结构化数据」页）."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from knowledge_mining.mining.api.deps import get_parse_result_service
from knowledge_mining.mining.api.domain_scope import require_domain
from knowledge_mining.mining.snapshot_store.read_service import (
    ParseResultReadService,
)

router = APIRouter(prefix="/api/knowledge", tags=["parse-result"])


@router.get("/documents/{document_id}/parse-result")
async def get_parse_result(
    document_id: str,
    domain: str = Query(...),
    service: ParseResultReadService = Depends(get_parse_result_service),
):
    """该文档最新知识快照的结构化数据（大纲/元素/表格/切片/出生证明）.

    404 = 文档尚未走新链更新知识（无新链快照）——前端显示引导而非报错。
    """
    from knowledge_mining.mining.contracts.storage.errors import (
        StorageObjectMissing,
    )

    try:
        result = await service.get_parse_result(
            domain=require_domain(domain), document_id=document_id,
        )
    except StorageObjectMissing:
        # 对抗评审 MEDIUM-1：快照在而 IR 制品缺失 = 完整性事故 → 统一 404
        # （对照 document_lifecycle 的资源缺失语义，不抛裸 500）。
        raise HTTPException(
            status_code=404,
            detail="parsed snapshot artifact is missing",
        ) from None
    if result is None:
        raise HTTPException(
            status_code=404,
            detail="no parsed snapshot for this document yet",
        )
    return result
