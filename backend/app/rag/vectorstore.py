"""向量库：Milvus（MilvusClient，兼容 Standalone / Zilliz Cloud / Lite 本地文件）。

角色定位：Milvus 是"可重建的派生检索索引"，真源在 MySQL + 原始文件。
chunk 文本只在 Milvus `text` 字段 + messages.sources_json 快照，MySQL 不建 chunks 表。

混合检索（dense + BM25 sparse）依赖 Milvus Standalone 2.5+ 的 BM25 Function：
- Standalone / Zilliz Cloud（uri 以 http(s) 开头）→ 建 sparse + BM25 Function，走 hybrid search
- Milvus Lite（本地文件路径）→ 仅 dense，走 ANN search
两路共用一个 MilvusClient 接口，`supports_sparse` 做能力开关。

**BM25 的两条硬性要求**（本地 Lite 跑不到这条分支，实测时才发现，见 docs/BAD_CASES.md #15/#16）：
1. 作为 BM25 输入的 `text` 字段必须 `enable_analyzer=True`，否则 create_collection 直接报错
   `BM25 function input field must set enable_analyzer to true`；
2. 必须指定分词器：中文语料用 `jieba`（标准分词器按空白切分，中文会整句当一个 token，BM25 失效）。
   若目标集群不支持 jieba，这里会自动降级到 `standard` 并告警。

鉴权：Zilliz Cloud 等远程集群需要 token（API Key 或 `<user>:<password>`）。
token 只在远程 URI 下透传 —— 本地文件模式传 token 会被 pymilvus 拒绝。
"""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

from pymilvus import DataType, Function, FunctionType, MilvusClient

try:  # pymilvus 2.6 未在顶层导出 IndexParams
    from pymilvus import IndexParams
except ImportError:  # pragma: no cover
    from pymilvus.milvus_client.index import IndexParams

from app.core.config import get_settings

logger = logging.getLogger(__name__)

TEXT_FIELD = "text"
DENSE_FIELD = "dense_vector"
SPARSE_FIELD = "sparse_vector"
DEFAULT_TOKENIZER = "jieba"  # 中文分词；英文/多语可换 "standard"
FALLBACK_TOKENIZER = "standard"
# BM25 Function 输出字段的索引 metric 必须是 BM25（用 IP 会被服务端拒绝：
# `index metric type of BM25 function output field must be BM25, got IP`）
SPARSE_METRIC_BM25 = "BM25"
DENSE_METRIC = "IP"
DENSE_INDEX = "HNSW"
SPARSE_INDEX = "SPARSE_INVERTED_INDEX"

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


def build_schema(dim: int, supports_sparse: bool, tokenizer: str = DEFAULT_TOKENIZER):
    """构造 collection schema（独立成函数以便单测钉住 BM25 的字段约束）。"""
    schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=False)
    schema.add_field("pk", DataType.VARCHAR, is_primary=True, max_length=128)
    schema.add_field("dense_vector", DataType.FLOAT_VECTOR, dim=dim)
    if supports_sparse:
        # BM25 要求：开启分析器 + 指定分词器（否则建表直接失败 / 中文 BM25 失效）
        schema.add_field(
            TEXT_FIELD,
            DataType.VARCHAR,
            max_length=65535,
            enable_analyzer=True,
            analyzer_params={"tokenizer": tokenizer},
        )
    else:
        schema.add_field(TEXT_FIELD, DataType.VARCHAR, max_length=65535)
    for name, (dtype, max_len) in _SCALAR_FIELDS.items():
        if dtype == DataType.VARCHAR:
            schema.add_field(name, dtype, max_length=max_len)
        else:
            schema.add_field(name, dtype)
    if supports_sparse:
        schema.add_field(SPARSE_FIELD, DataType.SPARSE_FLOAT_VECTOR)
        schema.add_function(
            Function(
                name="bm25",
                function_type=FunctionType.BM25,
                input_field_names=[TEXT_FIELD],
                output_field_names=SPARSE_FIELD,
            )
        )
    return schema


def build_index_params(supports_sparse: bool) -> IndexParams:
    """构造索引参数（独立成函数以便单测钉住 metric 约束）。

    注意 sparse 路的 metric 是 **BM25**（不是 IP）——BM25 Function 的输出字段有硬性要求，
    用 IP 会被服务端拒绝。这个约束同样是"本地 Lite 跑不到、实测才现形"的坑。
    """
    index_params = IndexParams()
    index_params.add_index(
        field_name="dense_vector",
        index_type=DENSE_INDEX,
        metric_type=DENSE_METRIC,
        params={"M": 16, "efConstruction": 200},
    )
    if supports_sparse:
        index_params.add_index(
            field_name=SPARSE_FIELD, index_type=SPARSE_INDEX, metric_type=SPARSE_METRIC_BM25
        )
    index_params.add_index(field_name="doc_id", index_type="INVERTED")
    return index_params


class MilvusStore:
    def __init__(
        self,
        uri: str,
        collection: str,
        dim: int,
        token: str | None = None,
        analyzer_tokenizer: str = DEFAULT_TOKENIZER,
    ) -> None:
        self.uri = uri
        self.collection = collection
        self.dim = dim
        self.analyzer_tokenizer = analyzer_tokenizer
        self._ranker_kw: str | None = None  # hybrid_search 的 ranker 参数名（运行时探测）
        # Lite 是本地文件路径；Standalone / Zilliz Cloud 是 http(s)。仅后者支持 sparse/BM25。
        self.supports_sparse = uri.startswith(("http://", "https://"))
        # token 只在远程集群下透传（本地文件模式传 token 会被 pymilvus 拒绝）
        if token and self.supports_sparse:
            self._client = MilvusClient(uri=uri, token=token)
        else:
            self._client = MilvusClient(uri=uri)

    @property
    def is_remote(self) -> bool:
        """是否远程集群（Standalone / Zilliz Cloud）。"""
        return self.supports_sparse

    def ensure_collection(self) -> None:
        if self._client.has_collection(self.collection):
            # 已存在但可能被 release（Milvus Lite 在 delete/空闲后）——重新 load 保活
            self._ensure_loaded()
            return
        try:
            self._create(self.analyzer_tokenizer)
        except Exception as exc:  # noqa: BLE001
            # 仅在「分词器不受支持」时降级重试 —— 其他错误（metric 写错、权限等）必须原样抛出，
            # 否则会用错误的配置反复重试、并留下半成品 collection（实测踩过：duplicate collection）。
            msg = str(exc).lower()
            tokenizer_issue = "tokenizer" in msg or "analyzer" in msg
            if self.supports_sparse and tokenizer_issue and self.analyzer_tokenizer != FALLBACK_TOKENIZER:
                logger.warning(
                    "分词器 %s 不被支持：%s；降级为 %s 重试（中文 BM25 效果会变差）",
                    self.analyzer_tokenizer,
                    exc,
                    FALLBACK_TOKENIZER,
                )
                self.drop_collection()  # 清掉创建失败的半成品，避免 duplicate collection 冲突
                self._create(FALLBACK_TOKENIZER)
                self.analyzer_tokenizer = FALLBACK_TOKENIZER
            else:
                raise

    def _create(self, tokenizer: str) -> None:
        schema = build_schema(self.dim, self.supports_sparse, tokenizer)
        self._client.create_collection(
            collection_name=self.collection,
            schema=schema,
            index_params=build_index_params(self.supports_sparse),
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

    def drop_collection(self) -> None:
        """删除整个 collection（评测重灌用；生产慎用）。"""
        if self._client.has_collection(self.collection):
            self._client.drop_collection(self.collection)

    def describe_fields(self) -> list[str]:
        """返回 collection 的字段名列表（用于确认 BM25 sparse 字段是否真的建上了）。"""
        try:
            desc = self._client.describe_collection(self.collection)
            return [f.get("name", "") for f in desc.get("fields", [])]
        except Exception:  # noqa: BLE001
            return []

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
        """dense ANN 检索（Lite 与 Standalone / 云集群均支持）。

        **必须显式指定 anns_field**：一旦 collection 里还有 sparse_vector，
        服务端就不知道要查哪个向量字段，会报
        `multiple anns_fields exist, please specify a anns_field in search_params`。
        （又一个"本地单向量字段跑得通、加了 BM25 才现形"的坑。）
        """
        self.ensure_collection()
        if not self._client.has_collection(self.collection):
            return []
        output_fields = ["text", "doc_id", "source_file", "doc_type", "chunk_type", "page", "section_path"]
        res = self._client.search(
            collection_name=self.collection,
            data=[query_vector],
            anns_field=DENSE_FIELD,
            limit=top_k,
            filter=self._expr(filters),
            output_fields=output_fields,
            search_params={"metric_type": DENSE_METRIC, "params": {"nprobe": 16}},
        )
        return self._normalize_hits(res[0])

    def _hybrid_ranker_kwarg(self) -> str:
        """探测 hybrid_search 的融合器参数名。

        pymilvus 2.6 把它从 `rerank` 改名为 `ranker`（必填位置参数），
        2.5.x 仍叫 `rerank` —— 项目依赖范围是 >=2.5,<3.0，所以运行时探测一次并缓存。
        """
        if self._ranker_kw is None:
            import inspect

            params = inspect.signature(self._client.hybrid_search).parameters
            self._ranker_kw = "ranker" if "ranker" in params else "rerank"
        return self._ranker_kw

    def hybrid_search(
        self,
        query_vector: list[float],
        query_text: str,
        top_k: int = 20,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """dense + BM25 sparse 混合检索（仅 Standalone / Zilliz Cloud）。"""
        if not self.supports_sparse:
            return self.search(query_vector, top_k, filters)
        from pymilvus import AnnSearchRequest, RRFRanker

        self.ensure_collection()
        output_fields = ["text", "doc_id", "source_file", "doc_type", "chunk_type", "page", "section_path"]
        dense_req = AnnSearchRequest(
            data=[query_vector],
            anns_field=DENSE_FIELD,
            param={"metric_type": DENSE_METRIC, "params": {"nprobe": 16}},
            limit=30,
        )
        sparse_req = AnnSearchRequest(
            data=[query_text],
            anns_field=SPARSE_FIELD,
            param={"metric_type": SPARSE_METRIC_BM25},
            limit=30,
        )
        kwargs: dict[str, Any] = {
            "collection_name": self.collection,
            "reqs": [dense_req, sparse_req],
            "limit": top_k,
            "filter": self._expr(filters),
            "output_fields": output_fields,
        }
        kwargs[self._hybrid_ranker_kwarg()] = RRFRanker()
        res = self._client.hybrid_search(**kwargs)
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


def make_store(
    uri: str | None = None,
    collection: str | None = None,
    dim: int | None = None,
    token: str | None = None,
    analyzer_tokenizer: str = DEFAULT_TOKENIZER,
) -> MilvusStore:
    """按 settings 构造 store（生产线与离线 eval 共用，避免各处漏传 token）。"""
    s = get_settings()
    return MilvusStore(
        uri=uri if uri is not None else s.vectorstore_uri,
        collection=collection or s.milvus_collection,
        dim=dim or s.milvus_dim,
        token=token if token is not None else s.vectorstore_token,
        analyzer_tokenizer=analyzer_tokenizer,
    )


@lru_cache
def get_vectorstore() -> MilvusStore:
    return make_store()
