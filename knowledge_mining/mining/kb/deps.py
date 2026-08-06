"""FastAPI dependencies for KB routes."""
from __future__ import annotations

from fastapi import Request

from knowledge_mining.mining.kb.db import KbDB
from knowledge_mining.mining.kb.services.kb_service import KbService


async def get_kb_db(request: Request) -> KbDB:
    return KbDB(request.app.state.pg_pool)


async def get_kb_service(request: Request) -> KbService:
    return KbService(KbDB(request.app.state.pg_pool))


async def get_document_service(request: Request) -> DocumentService:
    from knowledge_mining.mining.kb.services.document_service import DocumentService
    return DocumentService(KbDB(request.app.state.pg_pool))


async def get_folder_service(request: Request):
    from knowledge_mining.mining.kb.services.folder_service import FolderService
    return FolderService(KbDB(request.app.state.pg_pool))


async def get_user_service(request: Request) -> UserService:
    from knowledge_mining.mining.kb.services.user_service import UserService
    return UserService(KbDB(request.app.state.pg_pool))
