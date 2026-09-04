"""向量化：DashScope text-embedding-v3（经 OpenAI 兼容端点）。

自写而非直接用 OpenAIEmbeddings 的原因：
- 需要 L2 归一化（配 Milvus IP 度量，等价余弦且更快）；
- 需要维度校验与坏向量跳单条（不整批失败）；
- 需要显式批量 + 指数退避重试，控 429。
"""
from __future__ import annotations

import math
from functools import lru_cache

import httpx
from langchain_core.embeddings import Embeddings
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.core.config import get_settings


def _l2_normalize(v: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in v))
    if norm == 0.0:
        return v
    return [x / norm for x in v]


def _chunked(items: list[str], size: int) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


class DashScopeEmbedding(Embeddings):
    def __init__(
        self,
        model: str,
        api_key: str,
        dim: int,
        batch_size: int = 10,
        base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1",
    ) -> None:
        self.model = model
        self.api_key = api_key
        self.dim = dim
        self.batch_size = batch_size
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=60)

    @retry(
        retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.TransportError)),
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=1, max=30),
        reraise=True,
    )
    async def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        resp = await self._client.post(
            f"{self.base_url}/embeddings",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": self.model, "input": texts},
        )
        resp.raise_for_status()
        data = resp.json()["data"]
        data.sort(key=lambda x: x["index"])
        return [item["embedding"] for item in data]

    async def _embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for batch in _chunked(texts, self.batch_size):
            vecs = await self._embed_batch(batch)
            for v in vecs:
                if len(v) != self.dim:
                    continue  # 坏向量跳单条，不整批失败
                out.append(_l2_normalize(v))
        return out

    # ---- LangChain Embeddings 同步接口（供 CLI/离线 eval）----
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        import asyncio

        return asyncio.run(self._embed(texts))

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]

    # ---- 异步接口（生产热路径）----
    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return await self._embed(texts)

    async def aembed_query(self, text: str) -> list[float]:
        vecs = await self._embed([text])
        if not vecs:
            raise RuntimeError("embedding 返回空向量")
        return vecs[0]


@lru_cache
def get_embedding() -> DashScopeEmbedding:
    s = get_settings()
    return DashScopeEmbedding(
        model=s.embedding_model,
        api_key=s.embedding_api_key,
        dim=s.embedding_dim,
        batch_size=s.embedding_batch_size,
    )
