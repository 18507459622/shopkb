"""聊天数据访问层：所有"我的会话/消息"查询强制 owner 限定（灭 IDOR）。"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Conversation, Message, MessageFeedback


class ConversationRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_by_id(self, conv_id: int, owner_id: int) -> Conversation | None:
        res = await self.db.execute(
            select(Conversation).where(
                Conversation.id == conv_id, Conversation.user_id == owner_id
            )
        )
        return res.scalar_one_or_none()

    async def list(self, owner_id: int, page: int = 1, size: int = 50) -> tuple[list[Conversation], int]:
        from sqlalchemy import func

        base = select(Conversation).where(Conversation.user_id == owner_id)
        total = (
            await self.db.execute(
                select(func.count()).select_from(Conversation).where(Conversation.user_id == owner_id)
            )
        ).scalar_one()
        stmt = (
            base.order_by(Conversation.last_message_at.desc().nulls_last(), Conversation.id.desc())
            .offset((page - 1) * size)
            .limit(size)
        )
        items = list((await self.db.execute(stmt)).scalars().all())
        return items, total

    async def create(self, owner_id: int, title: str | None = None) -> Conversation:
        conv = Conversation(user_id=owner_id, title=title or "新对话")
        self.db.add(conv)
        await self.db.flush()
        return conv


class MessageRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create(
        self,
        conversation_id: int,
        role: str,
        content: str,
        status: str = "complete",
        model: str | None = None,
        sources_json: list[Any] | None = None,
        usage_json: dict[str, Any] | None = None,
        latency_ms: int | None = None,
        error_code: str | None = None,
    ) -> Message:
        msg = Message(
            conversation_id=conversation_id,
            role=role,
            content=content,
            status=status,
            model=model,
            sources_json=sources_json,
            usage_json=usage_json,
            latency_ms=latency_ms,
            error_code=error_code,
        )
        self.db.add(msg)
        await self.db.flush()
        return msg

    async def list(
        self, conversation_id: int, limit: int = 50, before_id: int | None = None
    ) -> list[Message]:
        stmt = select(Message).where(Message.conversation_id == conversation_id)
        if before_id is not None:
            stmt = stmt.where(Message.id < before_id)
        stmt = stmt.order_by(Message.id.desc()).limit(limit)
        items = list((await self.db.execute(stmt)).scalars().all())
        return list(reversed(items))

    async def get_by_id(self, msg_id: int, owner_id: int) -> Message | None:
        res = await self.db.execute(
            select(Message)
            .join(Conversation, Message.conversation_id == Conversation.id)
            .where(Message.id == msg_id, Conversation.user_id == owner_id)
        )
        return res.scalar_one_or_none()


class FeedbackRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def upsert(self, message_id: int, user_id: int, rating: int, comment: str | None) -> None:

        res = await self.db.execute(
            select(MessageFeedback).where(MessageFeedback.message_id == message_id)
        )
        existing = res.scalar_one_or_none()
        if existing:
            existing.rating = rating
            existing.comment = comment
        else:
            self.db.add(
                MessageFeedback(message_id=message_id, user_id=user_id, rating=rating, comment=comment)
            )
        await self.db.flush()
