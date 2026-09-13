"""检索器单测：门控阈值 / 内容去重 / top_k 截断 / 检索模式与门控量纲。

为什么补这一组：v1 的 30 个单测全在纯函数（chunker/citation/security/clarifier），
RAG 核心路径（retriever/pipeline）零覆盖——改门控阈值、改去重策略没有任何回归保护。

**重点**：`test_hybrid_*` 这组钉的是一个真实缺陷 ——
混合检索返回的是 RRF 分数（≈0.01–0.03），与 dense 相似度（0–1）不同量纲；
早期实现把 GATE_THRESHOLD=0.35 无差别套上去，会让混合检索**永远返回空**。
该缺陷在本地 Milvus Lite（仅 dense）下不会触发，只有真正跑 Standalone/云集群才现形。
"""
from __future__ import annotations

from typing import Any

from app.rag.retriever import GATE_THRESHOLD, MODE_DENSE, MODE_HYBRID, Retriever


class _FakeEmbedding:
    """只回一个固定向量，避免任何网络调用。"""

    def __init__(self) -> None:
        self.queries: list[str] = []

    async def aembed_query(self, text: str) -> list[float]:
        self.queries.append(text)
        return [0.1, 0.2, 0.3]


class _FakeStore:
    """记录收到的 top_k/filters，并回放预置 hits；可选模拟支持 sparse 的远程集群。"""

    def __init__(self, hits: list[dict[str, Any]], hybrid_hits: list[dict[str, Any]] | None = None,
                 sparse: bool = False) -> None:
        self._hits = hits
        self._hybrid_hits = hybrid_hits if hybrid_hits is not None else hits
        self.supports_sparse = sparse
        self.calls: list[dict[str, Any]] = []
        self.hybrid_calls: list[dict[str, Any]] = []

    def search(self, vec: list[float], top_k: int = 10, filters: dict | None = None) -> list[dict]:
        self.calls.append({"top_k": top_k, "filters": filters})
        return self._hits[:top_k]

    def hybrid_search(
        self, vec: list[float], query_text: str, top_k: int = 20, filters: dict | None = None
    ) -> list[dict]:
        self.hybrid_calls.append({"top_k": top_k, "query_text": query_text, "filters": filters})
        return self._hybrid_hits[:top_k]


def _hit(score: float, text: str, source: str = "智能手机.md") -> dict[str, Any]:
    return {
        "chunk_id": f"c-{score}-{text[:4]}",
        "text": text,
        "doc_id": "1",
        "source_file": source,
        "doc_type": "md",
        "chunk_type": "text",
        "page": 0,
        "section_path": "星辰 X1 Pro",
        "score": score,
    }


def _retriever(hits: list[dict], hybrid_hits: list[dict] | None = None,
               sparse: bool = False) -> tuple[Retriever, _FakeStore, _FakeEmbedding]:
    store = _FakeStore(hits, hybrid_hits, sparse)
    emb = _FakeEmbedding()
    return Retriever(store, emb, reranker=None), store, emb  # type: ignore[arg-type]


# ---------- 门控 / 去重 / 截断（dense 路径） ----------


async def test_gate_drops_low_score_hits() -> None:
    """低于门控阈值的 chunk 必须被丢弃（防低置信噪声污染生成）。"""
    r, _, _ = _retriever(
        [
            _hit(0.90, "价格 4999 元"),
            _hit(0.50, "电池 5500mAh"),
            _hit(GATE_THRESHOLD - 0.01, "边界下方应被丢弃"),
            _hit(0.10, "完全无关内容"),
        ]
    )
    out = await r.retrieve("星辰 X1 Pro 价格", top_k=6)
    assert len(out) == 2
    assert all(c.score >= GATE_THRESHOLD for c in out)


async def test_gate_keeps_score_equal_to_threshold() -> None:
    """正好等于阈值应保留（门控是下限，不是开区间）。"""
    r, _, _ = _retriever([_hit(GATE_THRESHOLD, "刚好达标")])
    out = await r.retrieve("q", top_k=6)
    assert len(out) == 1


async def test_dedup_by_text_prefix() -> None:
    """前 50 字符相同的 chunk 视为重复，只留第一条。"""
    prefix = "重复内容" * 15
    r, _, _ = _retriever([_hit(0.9, prefix + "A"), _hit(0.8, prefix + "B"), _hit(0.7, "另一段内容")])
    out = await r.retrieve("q", top_k=6)
    assert len(out) == 2


async def test_top_k_truncation() -> None:
    """结果数受 top_k 限制。"""
    r, _, _ = _retriever([_hit(0.9 - i * 0.01, f"内容{i}") for i in range(10)])
    out = await r.retrieve("q", top_k=3)
    assert len(out) == 3


async def test_recall_stage_requests_20_candidates() -> None:
    """召回阶段固定取 20 条（给重排/门控留余量）——钉死这个契约。"""
    r, store, _ = _retriever([_hit(0.9, "内容")])
    await r.retrieve("q", top_k=6)
    assert store.calls and store.calls[0]["top_k"] == 20


async def test_empty_hits_returns_empty_list() -> None:
    """向量库无命中时返回空列表（而不是抛异常）。"""
    r, _, _ = _retriever([])
    assert await r.retrieve("q", top_k=6) == []


async def test_filters_passed_through_to_store() -> None:
    """filters 必须透传给向量库（知识库隔离/权限过滤依赖它）。"""
    r, store, _ = _retriever([_hit(0.9, "内容")])
    await r.retrieve("q", top_k=6, filters={"doc_id": "7"})
    assert store.calls[0]["filters"] == {"doc_id": "7"}


async def test_retrieved_chunk_to_source_shape() -> None:
    """引文结构契约：前端引用卡片依赖这些字段。"""
    r, _, _ = _retriever([_hit(0.9, "价格 4999 元" * 30)])
    out = await r.retrieve("q", top_k=6)
    src = out[0].to_source()
    assert set(src) == {"chunk_id", "doc_id", "doc_title", "snippet", "page", "section", "score"}
    assert len(src["snippet"]) == 200  # 片段截断长度


# ---------- 混合检索模式与门控量纲（真实缺陷的回归测试） ----------


async def test_hybrid_mode_does_not_apply_similarity_gate() -> None:
    """混合检索的 RRF 分数（≈0.03）不能被相似度门控（0.35）过滤掉。

    这是本项目最隐蔽的一个缺陷：本地 Milvus Lite 只有 dense，
    门控量纲错误只有在真正跑混合检索时才会暴露（表现为检索永远返回空）。
    """
    rrf_scores = [_hit(0.0328, "价格 4999 元"), _hit(0.0164, "电池 5500mAh"), _hit(0.0159, "重量 198g")]
    r, _, _ = _retriever([], hybrid_hits=rrf_scores, sparse=True)
    out = await r.retrieve("星辰 X1 Pro 价格", top_k=6, mode=MODE_HYBRID)
    assert len(out) == 3, "RRF 分数被相似度门控误杀 —— 门控量纲错误回归"
    assert out[0].score < GATE_THRESHOLD  # 确认测试数据确实低于门控阈值


async def test_auto_mode_selects_hybrid_on_remote_store() -> None:
    """远程集群（支持 sparse）下 auto 应走混合检索。"""
    r, store, _ = _retriever([_hit(0.9, "dense 结果")], hybrid_hits=[_hit(0.03, "hybrid 结果")], sparse=True)
    out = await r.retrieve("q", top_k=6)
    assert r.last_mode == MODE_HYBRID
    assert store.hybrid_calls, "远程集群应调用 hybrid_search"
    assert out[0].text == "hybrid 结果"


async def test_auto_mode_selects_dense_on_local_store() -> None:
    """本地 Lite（不支持 sparse）下 auto 应走 dense，且不调用 hybrid_search。"""
    r, store, _ = _retriever([_hit(0.9, "dense 结果")], sparse=False)
    await r.retrieve("q", top_k=6)
    assert r.last_mode == MODE_DENSE
    assert not store.hybrid_calls


async def test_explicit_hybrid_falls_back_to_dense_on_local_store() -> None:
    """本地 Lite 显式请求 hybrid 时降级为 dense（而不是报错）。"""
    r, store, _ = _retriever([_hit(0.9, "dense 结果")], sparse=False)
    out = await r.retrieve("q", top_k=6, mode=MODE_HYBRID)
    assert r.last_mode == MODE_DENSE
    assert store.calls and not store.hybrid_calls
    assert len(out) == 1


async def test_dense_mode_still_gates_on_remote_store() -> None:
    """远程集群上显式走 dense 时，门控依然生效（对比实验用得到）。"""
    r, _, _ = _retriever([_hit(0.9, "高分"), _hit(0.10, "低分应被丢弃")], sparse=True)
    out = await r.retrieve("q", top_k=6, mode=MODE_DENSE)
    assert len(out) == 1 and out[0].text == "高分"
