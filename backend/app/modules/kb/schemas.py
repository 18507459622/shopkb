"""知识库模块 Pydantic 模型。"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    filename: str
    title: str
    doc_type: str
    file_size: int
    chunk_count: int
    status: str
    created_at: datetime
    deleted_at: datetime | None = None


class IngestionJobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    document_id: int
    trigger: str
    stage: str
    progress: int
    error_message: str | None = None
    created_at: datetime


class DocumentUploadOut(BaseModel):
    document: DocumentOut
    job: IngestionJobOut


class KbStats(BaseModel):
    total_documents: int
    total_chunks: int
    completed: int
    pending: int
    failed: int


class ChunkOut(BaseModel):
    chunk_id: str
    text: str
    doc_id: str
    source_file: str
    doc_type: str
    chunk_type: str
    page: int | None = None
    section_path: str
