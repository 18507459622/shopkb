"""聊天路由：会话 CRUD + 消息 + SSE 流式问答。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.schemas import ok
from app.models import User

from .schemas import (
    ConversationOut,
    CreateConversationRequest,
    FeedbackRequest,
    MessageOut,
    RenameConversationRequest,
    SendMessageRequest,
)
from .service import ChatService, stream_chat

router = APIRouter(prefix="/chat", tags=["chat"])


@router.get("/conversations")
async def list_conversations(
    page: int = 1,
    size: int = 50,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    items, total = await ChatService(db).list_conversations(user, page, size)
    return ok(
        {
            "items": [ConversationOut.model_validate(c) for c in items],
            "total": total,
            "page": page,
            "size": size,
        }
    )


@router.post("/conversations")
async def create_conversation(
    body: CreateConversationRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    conv = await ChatService(db).create_conversation(user, body.title)
    return ok(ConversationOut.model_validate(conv))


@router.get("/conversations/{conv_id}")
async def get_conversation(
    conv_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    conv = await ChatService(db).get_conversation(user, conv_id)
    return ok(ConversationOut.model_validate(conv))


@router.patch("/conversations/{conv_id}")
async def rename_conversation(
    conv_id: int,
    body: RenameConversationRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    conv = await ChatService(db).rename(user, conv_id, body.title)
    return ok(ConversationOut.model_validate(conv))


@router.delete("/conversations/{conv_id}")
async def delete_conversation(
    conv_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    await ChatService(db).delete_conversation(user, conv_id)
    return ok()


@router.get("/conversations/{conv_id}/messages")
async def list_messages(
    conv_id: int,
    limit: int = Query(50, ge=1, le=200),
    before_id: int | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    msgs = await ChatService(db).list_messages(user, conv_id, limit, before_id)
    return ok([MessageOut.from_orm_obj(m) for m in msgs])


@router.post("/conversations/{conv_id}/messages")
async def send_message(
    conv_id: int,
    body: SendMessageRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    msg = await ChatService(db).send_message(user, conv_id, body.content)
    return ok(MessageOut.from_orm_obj(msg))


@router.post("/conversations/{conv_id}/stream")
async def stream(
    conv_id: int,
    body: SendMessageRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # 预检所有权（SSE 开始后无法再改状态码）
    await ChatService(db).get_conversation(user, conv_id)
    gen = stream_chat(user.id, conv_id, body.content)
    return StreamingResponse(
        gen,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.post("/messages/{message_id}/feedback")
async def feedback(
    message_id: int,
    body: FeedbackRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await ChatService(db).add_feedback(user, message_id, body.rating, body.comment)
    return ok()
