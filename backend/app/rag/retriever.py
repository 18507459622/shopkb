"""检索器：embed query → 混合/密集检索 → 重排 → 门控 + 内容去重。"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from app.core.config import get_settings
from app.rag.embeddings import DashScopeEmbedding, get_embedding
from app.rag.reranker import Reranker
from app.rag.vectorstore import MilvusStore, get_vectorstore

GATE_THRESHOLD = 0.35  # 相关度门控：低于此丢弃，防低置信召回污染生成


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

    async def retrieve(
        self,
        query: str,
        top_k: int = 6,
        filters: dict[str, Any] | None = None,
    ) -> list[RetrievedChunk]:
        query_vec = await self.embedding.aembed_query(query)
        # Stage1 召回：Standalone 走 hybrid（dense+BM25），Lite 走 dense
        if self.store.supports_sparse:
            hits = self.store.hybrid_search(query_vec, query, top_k=20, filters=filters)
        else:
            hits = self.store.search(query_vec, top_k=20, filters=filters)
        if not hits:
            return []

        # Stage2 重排（可选）
        if self.reranker is not None:
            ranked = self.reranker.rerank(query, hits)
            ordered = [(hits[idx], score) for idx, score in ranked]
        else:
            ordered = [(h, float(h.get("score", 0.0))) for h in hits]

        # 门控 + 内容去重
        result: list[RetrievedChunk] = []
        seen: set[str] = set()
        for h, score in ordered:
            if score < GATE_THRESHOLD:
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
