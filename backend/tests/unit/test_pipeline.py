"""RAG 编排单测：上下文组装 / 提示词装配 / 离线守卫。

为什么补这一组：`build_context` 决定了喂给模型的证据格式（来源编号、页码、章节），
`build_messages` 决定了历史轮次如何拼接——这两处一旦改动就会影响生成质量与引文准确性，
但 v1 完全没有测试覆盖。

技巧：`RagPipeline.__new__` 绕过 `__init__`，避免为了测两个纯装配方法
去连 Milvus / 建 LLM 客户端（这两个方法本身不依赖 self）。
"""
from __future__ import annotations

from app.rag.pipeline import RAG_SYSTEM_PROMPT, RagPipeline, build_context, get_direct_reply
from app.rag.retriever import RetrievedChunk


def _chunk(text: str = "价格 4999 元", page: int | None = None, section: str = "") -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id="c1",
        text=text,
        doc_id="1",
        source_file="智能手机.md",
        doc_type="md",
        chunk_type="text",
        page=page,
        section_path=section,
        score=0.9,
    )


def _pipeline() -> RagPipeline:
    """未初始化实例：只用于调用不依赖 self 的装配方法。"""
    return RagPipeline.__new__(RagPipeline)


# ---------- build_context ----------


def test_build_context_includes_source_index_and_location() -> None:
    ctx = build_context([_chunk(page=2, section="星辰 X1 Pro")])
    assert ctx.startswith("[来源1] 智能手机.md 第2页 / 星辰 X1 Pro")
    assert "价格 4999 元" in ctx


def test_build_context_omits_page_and_section_when_absent() -> None:
    ctx = build_context([_chunk()])
    first_line = ctx.splitlines()[0]
    assert first_line == "[来源1] 智能手机.md"
    assert "第" not in first_line


def test_build_context_numbers_multiple_sources() -> None:
    ctx = build_context([_chunk("A"), _chunk("B")])
    assert "[来源1]" in ctx
    assert "[来源2]" in ctx


# ---------- build_messages ----------


def test_build_messages_uses_grounded_system_prompt_and_context() -> None:
    msgs = _pipeline().build_messages("星辰 X1 Pro 多少钱？", [_chunk()])
    assert msgs[0].content == RAG_SYSTEM_PROMPT
    assert "【知识库内容】" in msgs[-1].content
    assert "星辰 X1 Pro 多少钱？" in msgs[-1].content


def test_build_messages_prepends_history_and_marks_assistant_turns() -> None:
    history = [
        {"role": "user", "content": "星辰 X1 Pro 多少钱？"},
        {"role": "assistant", "content": "4999 元起。"},
    ]
    msgs = _pipeline().build_messages("它的电池呢？", [_chunk()], history=history)
    assert len(msgs) == 4  # system + 2 轮历史 + 当前提问
    assert msgs[1].content == "星辰 X1 Pro 多少钱？"
    assert msgs[2].content.startswith("[助手回答]")


def test_build_messages_keeps_only_recent_six_history_turns() -> None:
    history = [{"role": "user", "content": f"问题{i}"} for i in range(10)]
    msgs = _pipeline().build_messages("当前问题", [_chunk()], history=history)
    assert len(msgs) == 1 + 6 + 1
    assert msgs[1].content == "问题4"  # 最近 6 轮


def test_offtopic_messages_do_not_inject_knowledge_context() -> None:
    msgs = _pipeline().build_offtopic_messages("你们有卖智能眼镜吗？")
    assert "【知识库内容】" not in msgs[-1].content
    assert msgs[-1].content == "你们有卖智能眼镜吗？"


# ---------- get_direct_reply 离线守卫 ----------


def test_direct_reply_handles_greeting() -> None:
    reply = get_direct_reply("你好")
    assert reply is not None and "客服助手" in reply


def test_direct_reply_handles_identity_question() -> None:
    reply = get_direct_reply("你是什么模型")
    assert reply is not None and "模型" in reply


def test_direct_reply_passes_through_long_real_question() -> None:
    """超过 20 字的问题不走守卫（避免把真实商品问题当闲聊）。"""
    assert get_direct_reply("星辰 X1 Pro 的价格是多少？另外它的电池容量和快充功率分别是多少") is None


def test_direct_reply_passes_through_short_unknown_question() -> None:
    assert get_direct_reply("有货吗") is None
