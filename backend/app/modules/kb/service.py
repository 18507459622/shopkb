"""知识库业务逻辑：上传/列表/删除/重灌/统计。事务边界在本层。"""
from __future__ import annotations

import asyncio
import hashlib
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import (
    ConflictError,
    DocumentBusyError,
    DocumentNotFoundError,
    ValidationError,
)
from app.models import Document, IngestionJob, User
from app.rag.ingestion import ingest_document, purge_document_vectors
from app.rag.parser import SUPPORTED_TYPES

from .repositories import DocumentRepository, IngestionJobRepository

MAX_UPLOAD_SIZE = 20 * 1024 * 1024  # 20MB


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def detect_doc_type(filename: str, content: bytes) -> str:
    ext = Path(filename).suffix.lower().lstrip(".")
    if content.startswith(b"%PDF"):
        return "pdf"
    if content.startswith(b"PK\x03\x04"):
        if ext == "docx":
            return "docx"
        raise ValidationError("不支持的压缩文档格式，仅支持 .docx")
    if ext in SUPPORTED_TYPES:
        return ext
    raise ValidationError(f"无法识别的文件类型: {filename}")


class KbService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def upload(self, user: User, filename: str, content: bytes) -> tuple[Document, IngestionJob]:
        if len(content) > MAX_UPLOAD_SIZE:
            raise ValidationError("文件超过 20MB 限制")
        doc_type = detect_doc_type(filename, content)
        checksum = _sha256(content)

        existing = await self._docs().get_by_checksum(checksum)
        if existing and existing.deleted_at is None:
            if existing.status == "completed":
                raise ConflictError("该文档已存在（内容相同）")
            raise DocumentBusyError()

        settings = get_settings()
        os.makedirs(settings.upload_dir, exist_ok=True)
        storage_key = uuid.uuid4().hex
        with open(os.path.join(settings.upload_dir, storage_key), "wb") as f:
            f.write(content)

        title = Path(filename).stem
        doc = await self._docs().create(
            uploader_id=user.id,
            filename=filename,
            title=title,
            doc_type=doc_type,
            storage_key=storage_key,
            file_size=len(content),
            checksum=checksum,
        )
        job = await IngestionJobRepository(self.db).create(doc.id, "upload")
        await self.db.commit()

        # 后台异步入库（生产可切 ARQ worker）
        asyncio.create_task(ingest_document(doc.id))
        return doc, job

    async def reingest(self, doc_id: int) -> IngestionJob:
        doc = await self._docs().get_by_id(doc_id)
        if doc is None or doc.deleted_at is not None:
            raise DocumentNotFoundError()
        if doc.status in ("parsing", "chunking", "embedding", "indexing"):
            raise DocumentBusyError()
        doc.status = "pending"
        job = await IngestionJobRepository(self.db).create(doc.id, "reingest")
        await self.db.commit()
        asyncio.create_task(ingest_document(doc.id))
        return job

    async def delete(self, doc_id: int) -> None:
        doc = await self._docs().get_by_id(doc_id)
        if doc is None or doc.deleted_at is not None:
            raise DocumentNotFoundError()
        if doc.status in ("parsing", "chunking", "embedding", "indexing"):
            raise DocumentBusyError()
        # 软删（保引文可解析）+ 清 Milvus
        doc.deleted_at = utcnow()
        doc.status = "deleted"
        await self.db.commit()
        asyncio.create_task(purge_document_vectors(doc_id))

    async def get(self, doc_id: int) -> Document:
        doc = await self._docs().get_by_id(doc_id)
        if doc is None or doc.deleted_at is not None:
            raise DocumentNotFoundError()
        return doc

    async def list(self, status: str | None = None, page: int = 1, size: int = 20):
        return await self._docs().list(status, page, size)

    async def stats(self) -> dict[str, int]:
        return await self._docs().stats()

    async def list_jobs(self, doc_id: int) -> list[IngestionJob]:
        return await IngestionJobRepository(self.db).list_by_doc(doc_id)

    def _docs(self) -> DocumentRepository:
        return DocumentRepository(self.db)
