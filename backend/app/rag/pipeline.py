"""RAG 编排：检索（含多轮改写）→ 组装接地提示词 → 引文上下文。

热路径用显式函数编排（检索/门控/引文需要精细控制与可观测），
提示词与 LLM 走 LangChain（ChatPromptTemplate + ChatOpenAI）。
"""
from __future__ import annotations

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from app.core.config import get_settings
from app.rag.llm import get_llm
from app.rag.retriever import RetrievedChunk, get_retriever
from app.rag.rewriter import needs_rewrite, rewrite_query

RAG_SYSTEM_PROMPT = """你是电商商品知识库导购客服。请严格依据下方【知识库内容】回答用户问题。

硬性规则：
1. 只依据【知识库内容】作答，不使用你自己的商品常识（尤其是价格、参数、库存、保修，你的记忆可能过时）。
2. 数字（价格/参数/库存/保修期限等）必须逐字出自知识库原文，不得推断或换算。
3. 若知识库内容不足以回答，明确回复"知识库中暂未收录该信息"，并尽量指出最相关的已有内容，绝不编造。
4. 涉及多商品比较时用 Markdown 表格呈现。
5. 引用来源时只在提供的编号内使用 [n] 标注（n 为来源编号）。
6. 回答自包含、简洁，不依赖"如上文所述"。
7. 语气友好亲切，可适当使用表情符号（emoji）让对话更生动，但不要过度堆砌；关键数字和信息仍要清晰准确。"""

OFFTOPIC_SYSTEM_PROMPT = """你是电商商品知识库客服助手。当前用户的问题与知识库无关，或知识库中暂未收录相关内容。

请用友好、自然的语气回应：
1. 先简单接住用户的话题（可表达理解或礼貌回应），但绝不要编造商品价格、参数、库存、保修等信息。
2. 委婉说明你主要擅长的是"电商商品咨询"（价格、参数、库存、售后政策、商品对比等）。
3. 引导用户回到你的能力范围，给出 1-2 个具体可问的问题示例。
4. 回答简短（1-3 句），不要长篇大论。
5. 适当使用表情符号（emoji）让回复更亲切有趣。"""

NO_INFO_ANSWER = "抱歉，知识库中暂未收录与该问题相关的信息，无法准确回答。"

# 离线守卫：问候/感谢/告别/自我认知 直接回复，不走 RAG（避免闲聊被"拒答"）
_CHIT_CHAT_RULES: list[tuple[tuple[str, ...], str]] = [
    (
        ("你好", "您好", "在吗", "hi", "hello", "嗨"),
        "你好呀！👋 我是电商商品知识库客服助手，可以帮你查询商品的价格、参数、库存、售后等信息，请问有什么可以帮你？😊",
    ),
    (("谢谢", "感谢", "多谢"), "不客气！😊 还有其他商品问题可以随时问我～"),
    (("再见", "拜拜"), "再见！👋 欢迎随时回来咨询商品问题～"),
]

_IDENTITY_KEYWORDS = (
    "你是谁",
    "你是什么",
    "你叫什么",
    "什么模型",
    "哪个模型",
    "用的什么模型",
    "什么大模型",
)


def get_direct_reply(question: str) -> str | None:
    """离线守卫：短句的问候/感谢/告别/自我认知直接回复；未命中返回 None（走正常 RAG）。"""
    q = question.strip()
    if len(q) > 20:
        return None  # 长问题大概率是真实商品问题，不走守卫
    ql = q.lower()
    if any(k in ql for k in _IDENTITY_KEYWORDS):
        model = get_settings().llm_model
        return (
            f"🤖 我使用的是 {model} 模型（DeepSeek 提供）。"
            "我是电商商品知识库客服助手，通过知识库检索来回答商品的价格、规格、库存、售后等问题。😊"
        )
    for keywords, reply in _CHIT_CHAT_RULES:
        if any(k in ql for k in keywords):
            return reply
    return None


def build_context(chunks: list[RetrievedChunk]) -> str:
    lines: list[str] = []
    for i, c in enumerate(chunks, 1):
        loc = c.source_file
        if c.page:
            loc += f" 第{c.page}页"
        if c.section_path:
            loc += f" / {c.section_path}"
        lines.append(f"[来源{i}] {loc}\n{c.text}")
    return "\n\n".join(lines)


class RagPipeline:
    def __init__(self) -> None:
        self.retriever = get_retriever()
        self.llm = get_llm()

    async def retrieve(
        self, question: str, history: list[str] | None = None
    ) -> tuple[str, list[RetrievedChunk]]:
        """返回 (用于检索的 query, 命中的 chunk 列表)。"""
        query = question
        if history and needs_rewrite(question):
            try:
                query = await rewrite_query(history, question)
            except Exception:
                query = question
        chunks = await self.retriever.retrieve(query)
        return query, chunks

    def build_messages(
        self,
        question: str,
        chunks: list[RetrievedChunk],
        history: list[dict[str, str]] | None = None,
    ) -> list[BaseMessage]:
        context = build_context(chunks)
        messages: list[BaseMessage] = [SystemMessage(content=RAG_SYSTEM_PROMPT)]
        # 生成侧：最近几轮 QA 原样并入，保持指代连贯
        if history:
            for m in history[-6:]:
                if m.get("role") == "user":
                    messages.append(HumanMessage(content=m["content"]))
                else:
                    messages.append(SystemMessage(content=f"[助手回答] {m['content']}"))
        messages.append(
            HumanMessage(content=f"【知识库内容】\n{context}\n\n用户问题：{question}")
        )
        return messages

    def build_offtopic_messages(
        self,
        question: str,
        history: list[dict[str, str]] | None = None,
    ) -> list[BaseMessage]:
        """无关问题/无检索结果时的兜底：友好接话 + 说明擅长范围 + 引导回流。"""
        messages: list[BaseMessage] = [SystemMessage(content=OFFTOPIC_SYSTEM_PROMPT)]
        if history:
            for m in history[-6:]:
                if m.get("role") == "user":
                    messages.append(HumanMessage(content=m["content"]))
                else:
                    messages.append(SystemMessage(content=f"[助手回答] {m['content']}"))
        messages.append(HumanMessage(content=question))
        return messages
