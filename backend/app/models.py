"""SQLAlchemy 2.0 ORM 模型（关系真源，单一文件便于 FK 引用）。

约定：UTC 时间；BIGINT 自增主键；role/status 等用 VARCHAR 而非 ENUM（易演进）；
"我的会话/消息"查询一律在 repository 层强制 WHERE user_id=:me 以灭 IDOR。
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

# 主键类型：MySQL 用 BIGINT，SQLite 用 INTEGER（SQLite 只有 INTEGER PRIMARY KEY 才自增）
IdType = BigInteger().with_variant(Integer, "sqlite")


def utcnow() -> datetime:
    # 存 naive UTC：SQLite/MySQL 的 DATETIME 都不存时区，统一用无时区 UTC 避免比较报错
    return datetime.now(UTC).replace(tzinfo=None)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(IdType, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(16), default="user", nullable=False)  # admin | user
    nickname: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

    refresh_tokens: Mapped[list[RefreshToken]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    conversations: Mapped[list[Conversation]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class RefreshToken(Base):
    __tablename__ = "auth_refresh_tokens"

    id: Mapped[int] = mapped_column(IdType, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    family_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    device_label: Mapped[str | None] = mapped_column(String(128), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    replaced_by_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("auth_refresh_tokens.id"), nullable=True
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    user: Mapped[User] = relationship(back_populates="refresh_tokens")


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (Index("ix_conversations_user_last", "user_id", "last_message_at"),)

    id: Mapped[int] = mapped_column(IdType, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(255), default="新对话", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)  # active|archived|deleted
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

    user: Mapped[User] = relationship(back_populates="conversations")
    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", order_by="Message.id"
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(IdType, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # user | assistant | system
    content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="complete", nullable=False)  # complete|error|aborted
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sources_json: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    usage_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")
    feedback: Mapped[MessageFeedback | None] = relationship(
        back_populates="message", uselist=False, cascade="all, delete-orphan"
    )


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(IdType, primary_key=True, autoincrement=True)
    uploader_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    doc_type: Mapped[str] = mapped_column(String(16), nullable=False)  # pdf|docx|txt|md|csv
    storage_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)  # uuid4
    file_size: Mapped[int] = mapped_column(IdType, default=0, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)  # sha256
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    milvus_meta: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

    ingestion_jobs: Mapped[list[IngestionJob]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class IngestionJob(Base):
    __tablename__ = "ingestion_jobs"

    id: Mapped[int] = mapped_column(IdType, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    trigger: Mapped[str] = mapped_column(String(16), default="upload", nullable=False)  # upload|reingest|delete
    stage: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    document: Mapped[Document] = relationship(back_populates="ingestion_jobs")


class EmbeddingCache(Base):
    __tablename__ = "embedding_cache"

    sha256_text: Mapped[str] = mapped_column(String(64), primary_key=True)
    emb_model: Mapped[str] = mapped_column(String(64), nullable=False)
    dim: Mapped[int] = mapped_column(Integer, nullable=False)
    vector: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)  # msgpack 编码
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class MessageFeedback(Base):
    __tablename__ = "message_feedback"

    id: Mapped[int] = mapped_column(IdType, primary_key=True, autoincrement=True)
    message_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("messages.id", ondelete="CASCADE"), unique=True
    )
    user_id: Mapped[int] = mapped_column(IdType, ForeignKey("users.id", ondelete="CASCADE"))
    rating: Mapped[int] = mapped_column(Integer, nullable=False)  # +1 / -1
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    message: Mapped[Message] = relationship(back_populates="feedback")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(IdType, primary_key=True, autoincrement=True)
    actor: Mapped[str | None] = mapped_column(String(64), nullable=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)  # kb.upload / auth.login ...
    target_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
