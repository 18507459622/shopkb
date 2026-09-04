"""知识库数据访问层。"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Document, IngestionJob


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class DocumentRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_by_id(self, doc_id: int) -> Document | None:
        return await self.db.get(Document, doc_id)

    async def get_by_checksum(self, checksum: str) -> Document | None:
        res = await self.db.execute(select(Document).where(Document.checksum == checksum))
        return res.scalar_one_or_none()

    async def list(
        self, status: str | None = None, page: int = 1, size: int = 20
    ) -> tuple[list[Document], int]:
        stmt = select(Document).where(Document.deleted_at.is_(None))
        count_stmt = select(func.count()).select_from(Document).where(Document.deleted_at.is_(None))
        if status:
            stmt = stmt.where(Document.status == status)
            count_stmt = count_stmt.where(Document.status == status)
        total = (await self.db.execute(count_stmt)).scalar_one()
        stmt = stmt.order_by(Document.id.desc()).offset((page - 1) * size).limit(size)
        items = list((await self.db.execute(stmt)).scalars().all())
        return items, total

    async def create(
        self,
        uploader_id: int,
        filename: str,
        title: str,
        doc_type: str,
        storage_key: str,
        file_size: int,
        checksum: str,
    ) -> Document:
        doc = Document(
            uploader_id=uploader_id,
            filename=filename,
            title=title,
            doc_type=doc_type,
            storage_key=storage_key,
            file_size=file_size,
            checksum=checksum,
            status="pending",
        )
        self.db.add(doc)
        await self.db.flush()
        return doc

    async def stats(self) -> dict[str, int]:
        base = select(func.count()).select_from(Document).where(Document.deleted_at.is_(None))
        total = (await self.db.execute(base)).scalar_one()
        total_chunks = (
            await self.db.execute(
                select(func.coalesce(func.sum(Document.chunk_count), 0)).where(
                    Document.deleted_at.is_(None)
                )
            )
        ).scalar_one()
        completed = (await self.db.execute(base.where(Document.status == "completed"))).scalar_one()
        pending = (
            await self.db.execute(
                base.where(Document.status.in_(["pending", "parsing", "chunking", "embedding", "indexing"]))
            )
        ).scalar_one()
        failed = (await self.db.execute(base.where(Document.status == "failed"))).scalar_one()
        return {
            "total_documents": total,
            "total_chunks": total_chunks,
            "completed": completed,
            "pending": pending,
            "failed": failed,
        }


class IngestionJobRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create(self, doc_id: int, trigger: str) -> IngestionJob:
        job = IngestionJob(document_id=doc_id, trigger=trigger, stage="pending", progress=0)
        self.db.add(job)
        await self.db.flush()
        return job

    async def list_by_doc(self, doc_id: int) -> list[IngestionJob]:
        res = await self.db.execute(
            select(IngestionJob)
            .where(IngestionJob.document_id == doc_id)
            .order_by(IngestionJob.id.desc())
            .limit(20)
        )
        return list(res.scalars().all())
