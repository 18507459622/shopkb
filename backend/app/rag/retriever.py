"""检索器：embed query → 混合/密集检索 → 重排 → 门控 + 内容去重。

门控的量纲纪律（实测踩坑）：
    GATE_THRESHOLD 是**相似度下限**（dense 的 IP/余弦，值域约 0–1），
    只能套在 dense 检索的分数上。

    混合检索走 RRFRanker，返回的是 **RRF 分数**（≈ 1/(60+rank) 量级，典型值 0.01–0.03），
    与余弦**不同量纲**。若把 0.35 的门控套到 RRF 分数上，会把全部结果过滤掉、
    混合检索永远返回空 —— 这个缺陷在本地 Milvus Lite（仅 dense）下不会暴露，
    只有在真正跑 Standalone / Zilliz Cloud 时才会现形。
    因此本模块按 mode 区分：dense 才套门控，hybrid 交给 ranker 排序 + top_k 截断。
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from app.core.config import get_settings
from app.rag.embeddings import DashScopeEmbedding, get_embedding
from app.rag.reranker import Reranker
from app.rag.vectorstore import MilvusStore, get_vectorstore

GATE_THRESHOLD = 0.35  # 相关度门控（仅适用于 dense 相似度分数）：低于此丢弃，防低置信召回污染生成

MODE_AUTO = "auto"
MODE_DENSE = "dense"
MODE_HYBRID = "hybrid"


@dataclass
class RetrievedChunk:
    chunk_id: str
    text: str
    doc_id: str
    source_file: str
    doc_type: str
    chunk_type: str
    page: int | None
    section_path: str
    score: float

    def to_source(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "doc_id": self.doc_id,
            "doc_title": self.source_file,
            "snippet": self.text[:200],
            "page": self.page,
            "section": self.section_path,
            "score": round(self.score, 4),
        }


class Retriever:
    def __init__(
        self,
        store: MilvusStore,
        embedding: DashScopeEmbedding,
        reranker: Reranker | None = None,
    ) -> None:
        self.store = store
        self.embedding = embedding
        self.reranker = reranker
        self.last_mode: str = MODE_DENSE  # 可观测：本次实际用的检索模式

    def _resolve_mode(self, mode: str) -> str:
        if mode == MODE_AUTO:
            return MODE_HYBRID if self.store.supports_sparse else MODE_DENSE
        if mode == MODE_HYBRID and not self.store.supports_sparse:
            return MODE_DENSE  # 本地 Lite 不支持 sparse，降级为 dense
        return mode

    async def retrieve(
        self,
        query: str,
        top_k: int = 6,
        filters: dict[str, Any] | None = None,
        mode: str = MODE_AUTO,
    ) -> list[RetrievedChunk]:
        effective = self._resolve_mode(mode)
        self.last_mode = effective
        query_vec = await self.embedding.aembed_query(query)

        # Stage1 召回：hybrid = dense + BM25 sparse RRF 融合；dense = 纯向量
        if effective == MODE_HYBRID:
            hits = self.store.hybrid_search(query_vec, query, top_k=20, filters=filters)
        else:
            hits = self.store.search(query_vec, top_k=20, filters=filters)
        if not hits:
            return []

        # Stage2 重排（可选；重排分数同样是相似度量纲，与 dense 门控配套）
        if self.reranker is not None:
            ranked = self.reranker.rerank(query, hits)
            ordered = [(hits[idx], score) for idx, score in ranked]
        else:
            ordered = [(h, float(h.get("score", 0.0))) for h in hits]

        # 门控（**仅 dense**：RRF 分数不是相似度，套用会误杀全部结果）+ 内容去重
        apply_gate = effective == MODE_DENSE
        result: list[RetrievedChunk] = []
        seen: set[str] = set()
        for h, score in ordered:
            if apply_gate and score < GATE_THRESHOLD:
                continue
            dedup_key = h["text"][:50]
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            result.append(
                RetrievedChunk(
                    chunk_id=h["chunk_id"],
                    text=h["text"],
                    doc_id=h["doc_id"],
                    source_file=h["source_file"],
                    doc_type=h["doc_type"],
                    chunk_type=h["chunk_type"],
                    page=h["page"],
                    section_path=h["section_path"],
                    score=score,
                )
            )
            if len(result) >= top_k:
                break
        return result


@lru_cache
def get_retriever() -> Retriever:
    s = get_settings()
    reranker = Reranker(s.reranker_model, enabled=s.reranker_enabled) if s.reranker_enabled else None
    return Retriever(get_vectorstore(), get_embedding(), reranker)
