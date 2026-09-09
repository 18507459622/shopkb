"""聊天业务逻辑：会话 CRUD + 消息 + 流式问答编排。

流式用模块级 async generator（自管 session，不依赖请求级 Depends session，
避免 StreamingResponse 下依赖生命周期陷阱）。
"""
from __future__ import annotations

import json
import time
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_session_factory
from app.core.exceptions import ConversationNotFoundError, NotFoundError
from app.models import Conversation, Message, User
from app.rag.citation import sanitize_citations
from app.rag.clarifier import detect_clarify
from app.rag.pipeline import NO_INFO_ANSWER, RagPipeline, get_direct_reply

from .repositories import ConversationRepository, FeedbackRepository, MessageRepository


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def sse_event(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


class ChatService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.conversations = ConversationRepository(db)
        self.messages = MessageRepository(db)
        self.feedback_repo = FeedbackRepository(db)

    # ---- 会话 ----
    async def list_conversations(self, user: User, page: int = 1, size: int = 50):
        return await self.conversations.list(user.id, page, size)

    async def create_conversation(self, user: User, title: str | None = None) -> Conversation:
        conv = await self.conversations.create(user.id, title)
        await self.db.commit()
        return conv

    async def get_conversation(self, user: User, conv_id: int) -> Conversation:
        conv = await self.conversations.get_by_id(conv_id, user.id)
        if conv is None:
            raise ConversationNotFoundError()
        return conv

    async def rename(self, user: User, conv_id: int, title: str) -> Conversation:
        conv = await self.get_conversation(user, conv_id)
        conv.title = title
        await self.db.commit()
        return conv

    async def delete_conversation(self, user: User, conv_id: int) -> None:
        conv = await self.get_conversation(user, conv_id)
        await self.db.delete(conv)
        await self.db.commit()

    async def list_messages(
        self, user: User, conv_id: int, limit: int = 50, before_id: int | None = None
    ) -> list[Message]:
        await self.get_conversation(user, conv_id)
        return await self.messages.list(conv_id, limit, before_id)

    # ---- 非流式消息（curl 友好 / 测试）----
    async def send_message(self, user: User, conv_id: int, content: str) -> Message:
        conv = await self.get_conversation(user, conv_id)
        user_msg = await self.messages.create(conv_id, "user", content)
        if conv.title == "新对话":
            conv.title = content[:20]
        conv.last_message_at = utcnow()
        await self.db.commit()

        direct = get_direct_reply(content)
        if direct:
            assistant = await self.messages.create(
                conv_id, "assistant", direct, status="complete", sources_json=[]
            )
            conv.last_message_at = utcnow()
            await self.db.commit()
            return assistant

        history, user_questions = await self._load_history(conv_id, user_msg.id)
        answer, sources, usage, latency = await self._generate(content, history, user_questions)

        assistant = await self.messages.create(
            conv_id,
            "assistant",
            answer,
            status="complete",
            model=get_settings().llm_model,
            sources_json=sources,
            usage_json=usage,
            latency_ms=latency,
        )
        conv.last_message_at = utcnow()
        await self.db.commit()
        return assistant

    # ---- 反馈 ----
    async def add_feedback(self, user: User, message_id: int, rating: int, comment: str | None) -> None:
        msg = await self.messages.get_by_id(message_id, user.id)
        if msg is None:
            raise NotFoundError("消息不存在")
        await self.feedback_repo.upsert(message_id, user.id, rating, comment)
        await self.db.commit()

    # ---- 内部 ----
    async def _load_history(self, conv_id: int, exclude_msg_id: int):
        recent = await self.messages.list(conv_id, limit=8)
        prior = [m for m in recent if m.id != exclude_msg_id]
        history = [
            {"role": m.role, "content": m.content} for m in prior if m.role in ("user", "assistant")
        ]
        user_questions = [m.content for m in prior if m.role == "user"]
        return history, user_questions

    async def _generate(self, content: str, history: list[dict], user_questions: list[str]):
        pipeline = RagPipeline()
        start = time.perf_counter()
        query, chunks = await pipeline.retrieve(content, user_questions)
        clarify = detect_clarify(query, user_questions)
        if clarify:
            return clarify.question, [], None, int((time.perf_counter() - start) * 1000)
        sources = [c.to_source() for c in chunks]
        if not chunks:
            # 无相关内容：LLM 兜底引导（聊天 + 引导回流），失败则回退固定话术
            usage = None
            try:
                res = await pipeline.llm.ainvoke(
                    pipeline.build_offtopic_messages(content, history)
                )
                answer = res.content or NO_INFO_ANSWER
                usage = getattr(res, "usage_metadata", None)
            except Exception:
                answer = NO_INFO_ANSWER
            return answer, [], usage, int((time.perf_counter() - start) * 1000)
        messages = pipeline.build_messages(content, chunks, history)
        res = await pipeline.llm.ainvoke(messages)
        answer, _ = sanitize_citations(res.content or "", len(chunks))
        usage = getattr(res, "usage_metadata", None)
        return answer, sources, usage, int((time.perf_counter() - start) * 1000)


async def stream_chat(user_id: int, conv_id: int, content: str) -> AsyncGenerator[str, None]:
    """流式问答：SSE named events（start/citations/token/usage/done/error）。"""
    factory = get_session_factory()
    pipeline = RagPipeline()
    settings = get_settings()
    start = time.perf_counter()

    # 阶段1：校验所有权 + 持久化用户消息 + 取历史
    async with factory() as db:
        conv = await ConversationRepository(db).get_by_id(conv_id, user_id)
        if conv is None:
            return
        msgs = MessageRepository(db)
        user_msg = await msgs.create(conv_id, "user", content)
        if conv.title == "新对话":
            conv.title = content[:20]
        conv.last_message_at = utcnow()
        await db.commit()
        recent = await msgs.list(conv_id, limit=8)
        prior = [m for m in recent if m.id != user_msg.id]
        history = [
            {"role": m.role, "content": m.content} for m in prior if m.role in ("user", "assistant")
        ]
        user_questions = [m.content for m in prior if m.role == "user"]

    yield sse_event("start", {"conversation_id": conv_id})

    # 离线守卫：闲聊/自我认知直接回复
    direct = get_direct_reply(content)
    if direct:
        yield sse_event("citations", {"sources": []})
        yield sse_event("token", {"delta": direct})
        async with factory() as db:
            assistant_msg = await MessageRepository(db).create(
                conv_id, "assistant", direct, status="complete",
                model=settings.llm_model, sources_json=[],
            )
            conv = await db.get(Conversation, conv_id)
            if conv is not None:
                conv.last_message_at = utcnow()
            await db.commit()
            yield sse_event("done", {"message_id": assistant_msg.id, "sources": []})
        return

    # 阶段2：检索
    try:
        query, chunks = await pipeline.retrieve(content, user_questions)
    except Exception as exc:  # noqa: BLE001
        yield sse_event("error", {"code": "RETRIEVAL_ERROR", "message": str(exc)[:200]})
        return
    sources = [c.to_source() for c in chunks]

    # 阶段2.5：歧义追问（query 命中品类但未指定具体商品）
    clarify = detect_clarify(query, user_questions)
    if clarify:
        yield sse_event("clarify", {
            "question": clarify.question,
            "category": clarify.category,
            "candidates": clarify.candidates,
        })
        async with factory() as db:
            assistant_msg = await MessageRepository(db).create(
                conv_id, "assistant", clarify.question, status="complete",
                model=settings.llm_model, sources_json=[],
            )
            conv = await db.get(Conversation, conv_id)
            if conv is not None:
                conv.last_message_at = utcnow()
            await db.commit()
            yield sse_event("done", {"message_id": assistant_msg.id, "sources": []})
        return

    # 阶段2.6：检索过程可视化（暴露改写 query + 检索模式 + 命中片段与相关度）
    yield sse_event("retrieval", {
        "query": query,
        "rewritten": bool(query != content),
        "mode": "hybrid" if pipeline.retriever.store.supports_sparse else "dense",
        "sources": sources,
    })

    # 阶段3：生成
    answer: str
    usage: dict[str, Any] = {}
    if not chunks:
        # 无相关内容：LLM 兜底引导（聊天 + 引导回流），失败则回退固定话术
        yield sse_event("citations", {"sources": []})
        try:
            parts: list[str] = []
            async for chunk in pipeline.llm.astream(
                pipeline.build_offtopic_messages(content, history)
            ):
                delta = chunk.content or ""
                if delta:
                    parts.append(delta)
                    yield sse_event("token", {"delta": delta})
                um = getattr(chunk, "usage_metadata", None)
                if um:
                    usage = dict(um)
            answer = "".join(parts) or NO_INFO_ANSWER
        except Exception:
            answer = NO_INFO_ANSWER
            yield sse_event("token", {"delta": answer})
    else:
        yield sse_event("citations", {"sources": sources})
        try:
            messages = pipeline.build_messages(content, chunks, history)
            parts: list[str] = []
            async for chunk in pipeline.llm.astream(messages):
                delta = chunk.content or ""
                if delta:
                    parts.append(delta)
                    yield sse_event("token", {"delta": delta})
                um = getattr(chunk, "usage_metadata", None)
                if um:
                    usage = dict(um)
            answer = "".join(parts)
            answer, _ = sanitize_citations(answer, len(chunks))
        except Exception as exc:  # noqa: BLE001
            async with factory() as db:
                await MessageRepository(db).create(
                    conv_id, "assistant", "抱歉，回答生成失败，请重试。",
                    status="error", error_code="LLM_ERROR",
                )
                await db.commit()
            yield sse_event("error", {"code": "LLM_ERROR", "message": str(exc)[:200]})
            return

    latency_ms = int((time.perf_counter() - start) * 1000)
    yield sse_event("usage", {"prompt_tokens": usage.get("input_tokens"), "completion_tokens": usage.get("output_tokens"), "latency_ms": latency_ms})

    # 阶段4：持久化 assistant 消息
    async with factory() as db:
        assistant_msg = await MessageRepository(db).create(
            conv_id,
            "assistant",
            answer,
            status="complete",
            model=settings.llm_model,
            sources_json=sources if chunks else [],
            usage_json=usage or None,
            latency_ms=latency_ms,
        )
        conv = await db.get(Conversation, conv_id)
        if conv is not None:
            conv.last_message_at = utcnow()
        await db.commit()
        yield sse_event("done", {"message_id": assistant_msg.id, "sources": sources})
