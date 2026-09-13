"""Milvus collection schema 单测：钉住 BM25 的字段约束。

为什么需要这一组：本地开发用 Milvus Lite（仅 dense），**建 sparse/BM25 schema 的代码路径
从来没被执行过** —— 实测切到 Zilliz Cloud 时才暴露：

    1. `text` 字段必须 `enable_analyzer=True`，否则 create_collection 直接失败：
       `BM25 function input field must set enable_analyzer to true`
    2. 必须指定分词器：中文语料要用 jieba，标准分词器按空白切分会让中文整句成为一个 token，
       BM25 形同失效

这两个约束以前只存在于文档里，现在由测试守着（见 docs/BAD_CASES.md #16）。
"""
from __future__ import annotations

import json

from app.rag.vectorstore import (
    DENSE_FIELD,
    DENSE_METRIC,
    SPARSE_FIELD,
    SPARSE_METRIC_BM25,
    TEXT_FIELD,
    MilvusStore,
    build_index_params,
    build_schema,
)


def _field(schema, name: str):
    return next(f for f in schema.fields if f.name == name)


def _index_of(params, field_name: str):
    """按字段取索引定义（IndexParam 用私有属性存字段名/配置，这里直接读以钉住约束）。"""
    return next(p for p in params if p._field_name == field_name)


def _tokenizer(schema) -> str | None:
    params = _field(schema, TEXT_FIELD).params.get("analyzer_params")
    if isinstance(params, str):
        return json.loads(params).get("tokenizer")
    if isinstance(params, dict):
        return params.get("tokenizer")
    return None


# ---------- 远程集群（支持 sparse / BM25） ----------


def test_sparse_schema_enables_analyzer_on_text_field() -> None:
    """BM25 输入字段必须开启分析器，否则建表会被服务端拒绝。"""
    schema = build_schema(1024, supports_sparse=True)
    assert _field(schema, TEXT_FIELD).params.get("enable_analyzer") is True


def test_sparse_schema_uses_jieba_tokenizer_by_default() -> None:
    """中文语料必须用 jieba 分词（standard 按空白切分，中文 BM25 会失效）。"""
    assert _tokenizer(build_schema(1024, supports_sparse=True)) == "jieba"


def test_tokenizer_is_configurable() -> None:
    """英文/多语场景可换成 standard。"""
    assert _tokenizer(build_schema(1024, supports_sparse=True, tokenizer="standard")) == "standard"


def test_sparse_schema_adds_sparse_field_and_bm25_function() -> None:
    """必须有 sparse 字段 + BM25 Function，否则 hybrid_search 没有稀疏路可走。"""
    schema = build_schema(1024, supports_sparse=True)
    assert SPARSE_FIELD in [f.name for f in schema.fields]
    assert "bm25" in [getattr(fn, "name", "") for fn in getattr(schema, "functions", [])]


# ---------- 本地 Lite（仅 dense） ----------


def test_local_schema_omits_sparse_field_and_analyzer() -> None:
    """本地 Lite 不支持 sparse/BM25：不应建 sparse 字段，也不必开分析器。"""
    schema = build_schema(1024, supports_sparse=False)
    names = [f.name for f in schema.fields]
    assert SPARSE_FIELD not in names
    assert _field(schema, TEXT_FIELD).params.get("enable_analyzer") is None
    assert not getattr(schema, "functions", [])


def test_dim_is_applied_to_dense_vector() -> None:
    """向量维度必须来自配置（DashScope text-embedding-v3 = 1024）。"""
    schema = build_schema(768, supports_sparse=False)
    assert _field(schema, "dense_vector").params.get("dim") == 768


# ---------- 索引 metric 约束（同样只有真实跑 BM25 才会暴露） ----------


def test_sparse_index_uses_bm25_metric() -> None:
    """BM25 Function 输出字段的索引 metric 必须是 BM25 —— 用 IP 会被服务端拒绝：

        index metric type of BM25 function output field must be BM25, got IP
    """
    params = build_index_params(supports_sparse=True)
    sparse = _index_of(params, SPARSE_FIELD)
    assert sparse._index_type == "SPARSE_INVERTED_INDEX"
    assert sparse._configs.get("metric_type") == SPARSE_METRIC_BM25 == "BM25"


def test_dense_index_uses_ip_metric() -> None:
    """dense 路仍是 IP（配 L2 归一化的 embedding，等价余弦）。"""
    params = build_index_params(supports_sparse=True)
    dense = _index_of(params, "dense_vector")
    assert dense._index_type == "HNSW"
    assert dense._configs.get("metric_type") == DENSE_METRIC == "IP"


def test_local_index_params_omit_sparse_index() -> None:
    """本地 Lite：只建 dense 与标量索引，不建 sparse 索引。"""
    params = build_index_params(supports_sparse=False)
    fields = [p._field_name for p in params]
    assert SPARSE_FIELD not in fields
    assert "dense_vector" in fields and "doc_id" in fields


# ---------- dense search 必须显式指定 anns_field ----------


class _FakeMilvusClient:
    """只记录调用参数的假客户端（避免单测连真实集群）。"""

    def __init__(self) -> None:
        self.search_calls: list[dict] = []

    def has_collection(self, name: str) -> bool:
        return True

    def load_collection(self, name: str) -> None:
        return None

    def search(self, **kwargs):  # type: ignore[no-untyped-def]
        self.search_calls.append(kwargs)
        return [[]]


def _store_with_fake_client(supports_sparse: bool = True) -> MilvusStore:
    """绕过 __init__（不建立真实连接），注入假客户端。"""
    store = object.__new__(MilvusStore)
    store.uri = "https://example.invalid"
    store.collection = "c"
    store.dim = 1024
    store.analyzer_tokenizer = "jieba"
    store.supports_sparse = supports_sparse
    store._client = _FakeMilvusClient()  # type: ignore[assignment]
    return store


def test_dense_search_specifies_anns_field() -> None:
    """collection 里有 dense + sparse 两个向量字段时，必须显式指定查哪个。

    否则服务端报：multiple anns_fields exist, please specify a anns_field in search_params
    —— 这个错误同样是"本地只有 dense 一个向量字段时不会出现"的坑。
    """
    store = _store_with_fake_client(supports_sparse=True)
    store.search([0.1, 0.2], top_k=5)
    call = store._client.search_calls[0]  # type: ignore[attr-defined]
    assert call["anns_field"] == DENSE_FIELD
    assert call["search_params"]["metric_type"] == DENSE_METRIC


def test_dense_search_also_specifies_anns_field_on_local() -> None:
    """本地单向量字段也显式指定（行为一致，避免两套路径分叉）。"""
    store = _store_with_fake_client(supports_sparse=False)
    store.search([0.1, 0.2], top_k=5)
    call = store._client.search_calls[0]  # type: ignore[attr-defined]
    assert call["anns_field"] == DENSE_FIELD
