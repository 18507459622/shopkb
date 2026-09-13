"""健康检查：不只看「连得上」，还要看「里面有没有东西」。

这个模块的存在源于一次真实故障
------------------------------
`.env` 的 `VECTORSTORE_URI` 从本地 Milvus Lite 换成 Zilliz Cloud 之后，
应用自己的 collection 没有被重新灌数据，而 SQLite 的 `documents` 表仍然写着
「已完成 / 2 chunks」。于是：

* 管理界面一切正常 —— 它读的是 SQLite
* `/readyz` 报 `{"database":"ok","milvus":"ok"}` —— 但 milvus 的 "ok"
  只代表**连接正常**、collection 存在，不代表里面有数据
* 只有用户提问时才发现所有商品问题都答「暂未收录」

**两个存储各说各话，而没有任何检查会告诉你它们不一致。**
所以这里补上：把「关系库声明有多少 chunk」与「向量库真实有多少条」对一次账。
"""
from __future__ import annotations

import asyncio

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session_factory
from app.models import Document

# 入库中的文档状态：这些文档的向量可能已经写进向量库，但 documents 行的
# 状态还没提交成 completed，此时直接比对会产生假警报。
_PENDING_STATUSES = ("pending", "parsing", "chunking", "embedding", "indexing")


def is_problem(verdict: str) -> bool:
    """这条结论要不要让 `/readyz` 变成 503。

    只有 mismatch / error 算失败：
    * `checking` —— 入库正在进行，结论还没出来，判失败会让探针正常流量被打断
    * `unknown`  —— 向量库暂时问不出来，判失败会让编排系统**反复重启服务**，
      在本就抖动的时候放大故障

    「不确定」不等于「不健康」—— 这条边界搞反了，健康检查就成了故障源。
    """
    return verdict.startswith(("mismatch", "error"))


def kb_verdict(declared: int, actual: int | None, in_flight: int = 0) -> str:
    """把三个数字变成一句人和机器都能读的结论。

    刻意用字符串前缀而不是布尔值：健康检查要能回答「为什么不行」，
    只说 false 等于没说。前缀约定（`check_readiness` 依赖它）：
        ok        通过
        checking  有入库在进行，暂不比对（**不算失败**）
        unknown   问不出来（**不算失败**，否则会引发无意义的重启循环）
        mismatch  不一致（**算失败**）
        error     检查本身出错（算失败）
    """
    if in_flight:
        return f"checking ({in_flight} doc(s) ingesting)"
    if actual is None:
        return f"unknown (vector count unavailable; db declares {declared})"
    if declared == actual:
        return f"ok ({declared} chunks)"
    return f"mismatch: db declares {declared}, vector store has {actual}"


async def _declared_chunks(db: AsyncSession) -> tuple[int, int]:
    """返回 (关系库声明的 chunk 总数, 正在入库的文档数)。"""
    in_flight = (
        await db.execute(
            select(func.count()).select_from(Document).where(Document.status.in_(_PENDING_STATUSES))
        )
    ).scalar_one()
    # 软删除的文档不计：它们的向量也已被 purge
    declared = (
        await db.execute(
            select(func.coalesce(func.sum(Document.chunk_count), 0)).where(
                Document.status == "completed", Document.deleted_at.is_(None)
            )
        )
    ).scalar_one()
    return int(declared), int(in_flight)


async def check_knowledge_base() -> str:
    """知识库一致性检查，返回 `kb_verdict` 说的那种字符串。"""
    from app.rag.vectorstore import get_vectorstore

    factory = get_session_factory()
    async with factory() as db:
        declared, in_flight = await _declared_chunks(db)

    if in_flight:
        return kb_verdict(declared, None, in_flight)

    actual = await asyncio.to_thread(get_vectorstore().count)
    return kb_verdict(declared, actual)
