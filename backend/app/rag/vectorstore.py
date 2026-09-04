"""向量库：Milvus（MilvusClient，兼容 Standalone 与 Lite 本地文件）。

角色定位：Milvus 是"可重建的派生检索索引"，真源在 MySQL + 原始文件。
chunk 文本只在 Milvus `text` 字段 + messages.sources_json 快照，MySQL 不建 chunks 表。

混合检索（dense + BM25 sparse）依赖 Milvus Standalone 2.5+ 的 BM25 Function，
Milvus Lite 不支持 sparse/BM25，故以 `supports_sparse` 做能力开关：
Standalone(uri 以 http 开头) → 建 sparse + BM25 Function，走 hybrid search；
Lite(本地文件) → 仅 dense，走 ANN search。两路共用一个 MilvusClient 接口。
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any

from pymilvus import DataType, Function, FunctionType, MilvusClient

from app.core.config import get_settings

# 标量过滤字段（管理 + expr 预过滤）
_SCALAR_FIELDS: dict[str, tuple[str, int]] = {
    "doc_id": (DataType.VARCHAR, 64),
    "source_file": (DataType.VARCHAR, 255),
    "doc_type": (DataType.VARCHAR, 16),
    "chunk_type": (DataType.VARCHAR, 16),
    "page": (DataType.INT64, 0),
    "section_path": (DataType.VARCHAR, 512),
    "category": (DataType.VARCHAR, 64),
}


class MilvusStore:
    def __init__(self, uri: str, collection: str, dim: int) -> None:
        self.uri = uri
        self.collection = collection
        self.dim = dim
        # Lite 是本地文件路径；Standalone 是 http(s)。仅 Standalone 支持 sparse/BM25。
        self.supports_sparse = uri.startswith(("http://", "https://"))
        self._client = MilvusClient(uri=uri)

    def ensure_collection(self) -> None:
        if self._client.has_collection(self.collection):
            # 已存在但可能被 release（Milvus Lite 在 delete/空闲后）——重新 load 保活
            self._ensure_loaded()
            return
        schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field("pk", DataType.VARCHAR, is_primary=True, max_length=128)
        schema.add_field("dense_vector", DataType.FLOAT_VECTOR, dim=self.dim)
        schema.add_field("text", DataType.VARCHAR, max_length=65535)
        for name, (dtype, max_len) in _SCALAR_FIELDS.items():
            if dtype == DataType.VARCHAR:
                schema.add_field(name, dtype, max_length=max_len)
            else:
                schema.add_field(name, dtype)
        if self.supports_sparse:
            schema.add_field("sparse_vector", DataType.SPARSE_FLOAT_VECTOR)
            schema.add_function(
                Function(
                    name="bm25",
                    function_type=FunctionType.BM25,
                    input_field_names=["text"],
                    output_field_names="sparse_vector",
                )
            )

        index_params = self._client.prepare_index_params()
        index_params.add_index(
            field_name="dense_vector",
            index_type="HNSW",
            metric_type="IP",
            params={"M": 16, "efConstruction": 200},
        )
        if self.supports_sparse:
            index_params.add_index(
                field_name="sparse_vector", index_type="SPARSE_INVERTED_INDEX", metric_type="IP"
            )
        index_params.add_index(field_name="doc_id", index_type="INVERTED")

        self._client.create_collection(
            collection_name=self.collection, schema=schema, index_params=index_params
        )
        self._client.load_collection(self.collection)

    def _ensure_loaded(self) -> None:
        """读取前确保集合已加载（Milvus Lite 可能在 delete/空闲后把集合置为 released）。"""
        try:
            self._client.load_collection(self.collection)
        except Exception:  # noqa: BLE001
            pass

    def upsert_chunks(self, doc_id: str, chunks: list[dict[str, Any]]) -> int:
        """按 doc_id 先删后写（重传=全量重灌，幂等）。返回写入条数。"""
        self.ensure_collection()
        if self._client.has_collection(self.collection):
            self._client.delete(collection_name=self.collection, filter=f'doc_id == "{doc_id}"')
        if not chunks:
            return 0
        data = self._client.upsert(collection_name=self.collection, data=chunks)
        return data.get("upsert_count", len(chunks))

    def delete_by_doc(self, doc_id: str) -> None:
        if self._client.has_collection(self.collection):
            self._client.delete(collection_name=self.collection, filter=f'doc_id == "{doc_id}"')

    def _expr(self, filters: dict[str, Any] | None) -> str | None:
        """把 dict 过滤转成 Milvus expr（值加引号）。"""
        if not filters:
            return None
        parts = []
        for k, v in filters.items():
            if isinstance(v, str):
                parts.append(f'{k} == "{v}"')
            else:
                parts.append(f"{k} == {v}")
        return " and ".join(parts)

    def search(
        self, query_vector: list[float], top_k: int = 6, filters: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """dense ANN 检索（Lite 与 Standalone 均支持）。"""
        self.ensure_collection()
        if not self._client.has_collection(self.collection):
            return []
        output_fields = ["text", "doc_id", "source_file", "doc_type", "chunk_type", "page", "section_path"]
        res = self._client.search(
            collection_name=self.collection,
            data=[query_vector],
            limit=top_k,
            filter=self._expr(filters),
            output_fields=output_fields,
            search_params={"metric_type": "IP", "params": {"nprobe": 16}},
        )
        return self._normalize_hits(res[0])

    def hybrid_search(
        self,
        query_vector: list[float],
        query_text: str,
        top_k: int = 20,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """dense + BM25 sparse 混合检索（仅 Standalone）。"""
        if not self.supports_sparse:
            return self.search(query_vector, top_k, filters)
        from pymilvus import AnnSearchRequest, RRFRanker

        self.ensure_collection()
        output_fields = ["text", "doc_id", "source_file", "doc_type", "chunk_type", "page", "section_path"]
        dense_req = AnnSearchRequest(
            data=[query_vector],
            anns_field="dense_vector",
            param={"metric_type": "IP", "params": {"nprobe": 16}},
            limit=30,
        )
        sparse_req = AnnSearchRequest(
            data=[query_text],
            anns_field="sparse_vector",
            param={"metric_type": "IP"},
            limit=30,
        )
        res = self._client.hybrid_search(
            collection_name=self.collection,
            reqs=[dense_req, sparse_req],
            rerank=RRFRanker(),
            limit=top_k,
            filter=self._expr(filters),
            output_fields=output_fields,
        )
        return self._normalize_hits(res[0])

    def get_by_chunk_id(self, chunk_id: str) -> dict[str, Any] | None:
        """按 pk 查单条 chunk（原文查看）。"""
        if not self._client.has_collection(self.collection):
            return None
        res = self._client.get(
            collection_name=self.collection,
            ids=[chunk_id],
            output_fields=["text", "doc_id", "source_file", "doc_type", "chunk_type", "page", "section_path"],
        )
        return res[0] if res else None

    @staticmethod
    def _normalize_hits(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for h in hits:
            ent = h.get("entity", {})
            out.append(
                {
                    "chunk_id": h.get("id", ""),
                    "score": float(h.get("distance", 0.0)),
                    "text": ent.get("text", ""),
                    "doc_id": ent.get("doc_id", ""),
                    "source_file": ent.get("source_file", ""),
                    "doc_type": ent.get("doc_type", ""),
                    "chunk_type": ent.get("chunk_type", ""),
                    "page": ent.get("page"),
                    "section_path": ent.get("section_path", ""),
                }
            )
        return out

    def ready(self) -> bool:
        try:
            self.ensure_collection()
            return self._client.has_collection(self.collection)
        except Exception:
            return False


@lru_cache
def get_vectorstore() -> MilvusStore:
    s = get_settings()
    return MilvusStore(s.vectorstore_uri, s.milvus_collection, s.milvus_dim)
