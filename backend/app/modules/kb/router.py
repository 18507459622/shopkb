"""知识库路由（仅 admin）。"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import require_admin
from app.core.schemas import ok
from app.models import User
from app.rag.vectorstore import get_vectorstore

from .schemas import ChunkOut, DocumentOut, DocumentUploadOut, IngestionJobOut, KbStats
from .service import KbService

router = APIRouter(prefix="/kb", tags=["kb"])


@router.post("/documents/upload")
async def upload(
    file: UploadFile,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    content = await file.read()
    service = KbService(db)
    doc, job = await service.upload(user, file.filename or "unnamed", content)
    return ok(
        DocumentUploadOut(
            document=DocumentOut.model_validate(doc), job=IngestionJobOut.model_validate(job)
        )
    )


@router.get("/documents")
async def list_documents(
    status: str | None = None,
    page: int = 1,
    size: int = 20,
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    items, total = await KbService(db).list(status, page, size)
    return ok(
        {
            "items": [DocumentOut.model_validate(d) for d in items],
            "total": total,
            "page": page,
            "size": size,
        }
    )


@router.get("/documents/{doc_id}")
async def get_document(
    doc_id: int, _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)
):
    doc = await KbService(db).get(doc_id)
    return ok(DocumentOut.model_validate(doc))


@router.post("/documents/{doc_id}/reingest")
async def reingest(
    doc_id: int, _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)
):
    job = await KbService(db).reingest(doc_id)
    return ok(IngestionJobOut.model_validate(job))


@router.delete("/documents/{doc_id}")
async def delete_document(
    doc_id: int, _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)
):
    await KbService(db).delete(doc_id)
    return ok()


@router.get("/documents/{doc_id}/jobs")
async def list_jobs(
    doc_id: int, _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)
):
    jobs = await KbService(db).list_jobs(doc_id)
    return ok([IngestionJobOut.model_validate(j) for j in jobs])


@router.get("/stats")
async def stats(_: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    data = await KbService(db).stats()
    return ok(KbStats(**data))


@router.get("/chunks/{chunk_id}")
async def get_chunk(
    chunk_id: str, _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)
):
    """原文查看：返回 chunk 全文 + 所在文件（admin 用，普通用户仅见 snippet）。"""
    chunk = await asyncio.to_thread(get_vectorstore().get_by_chunk_id, chunk_id)
    if chunk is None:
        from app.core.exceptions import NotFoundError

        raise NotFoundError("chunk 不存在")
    return ok(ChunkOut(**chunk))
