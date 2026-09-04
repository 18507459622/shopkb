"""聊天模块 Pydantic 模型。"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    status: str
    last_message_at: datetime | None = None
    created_at: datetime


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    conversation_id: int
    role: str
    content: str
    status: str
    sources: list[Any] | None = None
    created_at: datetime

    @classmethod
    def from_orm_obj(cls, m) -> MessageOut:
        return cls(
            id=m.id,
            conversation_id=m.conversation_id,
            role=m.role,
            content=m.content,
            status=m.status,
            sources=m.sources_json,
            created_at=m.created_at,
        )


class CreateConversationRequest(BaseModel):
    title: str | None = None


class RenameConversationRequest(BaseModel):
    title: str = Field(min_length=1, max_length=100)


class SendMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=4000)


class FeedbackRequest(BaseModel):
    rating: int = Field(ge=-1, le=1)
    comment: str | None = None
