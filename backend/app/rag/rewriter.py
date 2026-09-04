"""多轮查询改写：把含指代的续问改写成自包含查询（检索侧）。"""
from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate

from app.rag.llm import get_llm

_REWRITE_TEMPLATE = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "你是电商客服对话改写助手。把用户的追问改写成不依赖上下文的独立查询。"
            "要求：不改变原意；把'它/那个/这个/前面提到的'等指代补全为具体商品或属性；"
            "若原文已经自包含则原样返回。只输出改写后的查询，不要解释。",
        ),
        ("human", "对话历史：\n{history}\n\n当前问题：{question}\n\n改写后的查询："),
    ]
)

_REFERENCE_WORDS = ("它", "他", "她", "那个", "这个", "这些", "那些", "上面", "前面", "刚才")


def needs_rewrite(question: str) -> bool:
    """启发式：短句或含指代词才需要改写（省一次 LLM 调用）。"""
    if len(question.strip()) <= 15:
        return True
    return any(w in question for w in _REFERENCE_WORDS)


async def rewrite_query(history: list[str], question: str) -> str:
    llm = get_llm()
    chain = _REWRITE_TEMPLATE | llm
    res = await chain.ainvoke({"history": "\n".join(history), "question": question})
    return (res.content or question).strip() or question
