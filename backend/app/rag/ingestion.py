"""入库流水线：parse → chunk → embed → index，异步作业，MySQL 为真源。

幂等：按 doc_id 先删 Milvus 再 upsert（重传=全量重灌）。
进度真源在 documents.status + ingestion_jobs.stage（前端轮询），不依赖 broker。
生产可用 ARQ worker 复用本函数（自建 DB session，可独立进程运行）。
"""
from __future__ import annotations

import asyncio
import hashlib
import os
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_session_factory
from app.models import Document, IngestionJob, utcnow
from app.rag.chunker import split_text
from app.rag.embeddings import get_embedding
from app.rag.parser import Block, parse
from app.rag.vectorstore import get_vectorstore


def _sha8(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:8]


def build_chunks(
    blocks: list[Block], doc_id: int, filename: str, doc_type: str
) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    seq = 0
    for block in blocks:
        for t in split_text(block.text):
            seq += 1
            chunks.append(
                {
                    "pk": f"{doc_id}::c{seq}::{_sha8(t)}",
                    "text": t,
                    "doc_id": str(doc_id),
                    "source_file": filename,
                    "doc_type": doc_type,
                    "chunk_type": "table" if block.is_table else "text",
                    "page": block.page or 0,
                    "section_path": block.section or "",
                    "category": "",
                }
            )
    return chunks


async def _latest_job(db: AsyncSession, doc_id: int) -> IngestionJob | None:
    res = await db.execute(
        select(IngestionJob)
        .where(IngestionJob.document_id == doc_id)
        .order_by(IngestionJob.id.desc())
        .limit(1)
    )
    return res.scalar_one_or_none()


async def _set_stage(
    db: AsyncSession, doc: Document, job: IngestionJob | None, stage: str, progress: int
) -> None:
    doc.status = stage
    if job:
        job.stage = stage
        job.progress = progress
    await db.commit()


async def ingest_document(doc_id: int) -> None:
    """入库一个文档。自建 session，可被 FastAPI 后台任务或 ARQ worker 调用。"""
    factory = get_session_factory()
    async with factory() as db:
        doc = await db.get(Document, doc_id)
        if doc is None:
            return
        job = await _latest_job(db, doc_id)
        try:
            settings = get_settings()
            path = os.path.join(settings.upload_dir, doc.storage_key)

            await _set_stage(db, doc, job, "parsing", 10)
            blocks = await asyncio.to_thread(parse, path, doc.doc_type)

            await _set_stage(db, doc, job, "chunking", 30)
            chunks = build_chunks(blocks, doc.id, doc.filename, doc.doc_type)
            if not chunks:
                raise ValueError("未解析出可入库的内容，请检查文件")

            await _set_stage(db, doc, job, "embedding", 60)
            vectors = await get_embedding().aembed_documents([c["text"] for c in chunks])
            for c, v in zip(chunks, vectors, strict=False):
                c["dense_vector"] = v

            await _set_stage(db, doc, job, "indexing", 90)
            # TODO: Milvus 调用改 asyncio.to_thread，避免阻塞事件循环（Phase 4）
            count = await asyncio.to_thread(
                get_vectorstore().upsert_chunks, str(doc.id), chunks
            )

            doc.chunk_count = count
            doc.status = "completed"
            doc.milvus_meta = {"collection": settings.milvus_collection, "dim": settings.milvus_dim}
            if job:
                job.stage = "completed"
                job.progress = 100
                job.finished_at = utcnow()
            await db.commit()
        except Exception as exc:  # noqa: BLE001
            doc.status = "failed"
            if job:
                job.stage = "failed"
                job.error_message = str(exc)[:2000]
            await db.commit()


async def purge_document_vectors(doc_id: int) -> None:
    """删除文档时清理 Milvus 向量。"""
    await asyncio.to_thread(get_vectorstore().delete_by_doc, str(doc_id))
