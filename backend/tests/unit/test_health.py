"""健康检查的单测。

这里的价值不在「代码能不能跑」，而在**约定**：
`ok / checking / unknown / mismatch / error` 这几个前缀决定了 `/readyz`
是否返回 503。把 checking 判成失败会打断正常入库时的流量；把 unknown 判成失败
会让编排系统在向量库抖动时反复重启服务、放大故障；把 mismatch 判成通过
则等于这条检查白加 —— 而那正是当初出事的形态。
"""
from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from app.core.health import is_problem, kb_verdict
from app.rag.vectorstore import MilvusStore


# ---------------------------------------------------------------- 结论文案
class TestKbVerdict:
    def test_一致时通过并报出条数(self):
        v = kb_verdict(declared=24, actual=24)
        assert v.startswith("ok")
        assert "24" in v
        assert not is_problem(v)

    def test_关系库多出向量库时是不一致(self):
        """这正是当初出事的方向：documents 说完成，向量库是空的。"""
        v = kb_verdict(declared=24, actual=0)
        assert v.startswith("mismatch")
        assert "24" in v and "0" in v
        assert is_problem(v)

    def test_向量库多出关系库时也是不一致(self):
        """反向：删了文档但向量没清干净。"""
        v = kb_verdict(declared=10, actual=12)
        assert v.startswith("mismatch")
        assert is_problem(v)

    def test_空知识库算通过(self):
        """0 == 0 是合法的健康状态，不能因为"没有数据"就报不健康。"""
        v = kb_verdict(declared=0, actual=0)
        assert v.startswith("ok")
        assert not is_problem(v)

    def test_入库进行中时跳过比对且不算失败(self):
        v = kb_verdict(declared=0, actual=None, in_flight=3)
        assert v.startswith("checking")
        assert "3" in v
        assert not is_problem(v)

    def test_入库进行中时优先于未知(self):
        """有文档在入库 -> 报 checking（有意义），而不是 unknown。"""
        assert kb_verdict(0, None, in_flight=1).startswith("checking")

    def test_问不出条数时报unknown且不算失败(self):
        v = kb_verdict(declared=24, actual=None)
        assert v.startswith("unknown")
        assert "24" in v  # 至少把关系库的说法带上，便于排查
        assert not is_problem(v), "不确定不等于不健康，判失败会导致重启循环"

    @pytest.mark.parametrize(
        "verdict,expected",
        [
            ("ok (24 chunks)", False),
            ("checking (1 doc(s) ingesting)", False),
            ("unknown (vector count unavailable; db declares 24)", False),
            ("mismatch: db declares 24, vector store has 0", True),
            ("error: connection refused", True),
        ],
    )
    def test_哪些结论算失败(self, verdict, expected):
        assert is_problem(verdict) is expected


# ---------------------------------------------------------------- 条数查询
def _stub_store(client):
    """绕过 __init__（它会真的去连 Milvus），只注入一个假 client。"""
    s = MilvusStore.__new__(MilvusStore)
    s.uri = "http://fake"
    s.collection = "kb"
    s.dim = 4
    s.analyzer_tokenizer = "jieba"
    s._ranker_kw = None
    s._count_cache = None
    s._count_at = 0.0
    s._count_lock = threading.Lock()
    s._client = client
    return s


class FakeClient:
    def __init__(self, rows=24, *, has=True, raises=False):
        self.rows = rows
        self._has = has
        self._raises = raises
        self.query_calls = 0

    def has_collection(self, name):
        return self._has

    def query(self, **kwargs):
        self.query_calls += 1
        if self._raises:
            raise RuntimeError("connection reset")
        return [{"count(*)": self.rows}]


class TestMilvusCount:
    def test_返回真实条数(self):
        store = _stub_store(FakeClient(rows=24))
        assert store.count() == 24

    def test_collection不存在时返回0而不是None(self):
        """0 是「确实为空」，None 是「问不出来」—— 两者必须区分。"""
        store = _stub_store(FakeClient(has=False))
        assert store.count() == 0

    def test_查询失败返回None而不是抛异常(self):
        """健康检查不能因为查不到就把自己搞崩。"""
        store = _stub_store(FakeClient(raises=True))
        assert store.count() is None

    def test_命中缓存时不重复查询(self):
        """每次 count(*) 在 Zilliz Cloud 上都是一次计费查询，探针会被高频调用。"""
        client = FakeClient(rows=24)
        store = _stub_store(client)
        for _ in range(5):
            assert store.count() == 24
        assert client.query_calls == 1

    def test_缓存过期后重新查询(self):
        client = FakeClient(rows=24)
        store = _stub_store(client)
        store.count()
        client.rows = 30
        assert store.count(max_age_s=0) == 30
        assert client.query_calls == 2

    def test_失败结果不进缓存(self):
        """否则一次网络抖动会让 30 秒内的检查全部报 unknown。"""
        client = FakeClient(raises=True)
        store = _stub_store(client)
        assert store.count() is None
        client._raises = False
        assert store.count() == 24

    def test_空结果集按0处理(self):
        class Empty(FakeClient):
            def query(self, **kwargs):
                self.query_calls += 1
                return []

        store = _stub_store(Empty())
        assert store.count() == 0


# ---------------------------------------------------------------- 端到端（假依赖）
class TestCheckKnowledgeBase:
    def test_把两个存储的数字对上(self, monkeypatch):
        import asyncio

        from app.core import health

        async def fake_declared(db):
            return (24, 0)

        monkeypatch.setattr(health, "_declared_chunks", fake_declared)
        monkeypatch.setattr(
            health, "get_session_factory", lambda: (lambda: _AsyncCtx())
        )

        import app.rag.vectorstore as vs

        monkeypatch.setattr(vs, "get_vectorstore", lambda: SimpleNamespace(count=lambda: 24))
        assert asyncio.run(health.check_knowledge_base()).startswith("ok")

    def test_失同步时给出mismatch(self, monkeypatch):
        import asyncio

        from app.core import health

        async def fake_declared(db):
            return (24, 0)

        monkeypatch.setattr(health, "_declared_chunks", fake_declared)
        monkeypatch.setattr(health, "get_session_factory", lambda: (lambda: _AsyncCtx()))

        import app.rag.vectorstore as vs

        monkeypatch.setattr(vs, "get_vectorstore", lambda: SimpleNamespace(count=lambda: 0))
        verdict = asyncio.run(health.check_knowledge_base())
        assert verdict.startswith("mismatch")
        assert is_problem(verdict)

    def test_有文档在入库时不查向量库(self, monkeypatch):
        import asyncio

        from app.core import health

        async def fake_declared(db):
            return (0, 2)

        monkeypatch.setattr(health, "_declared_chunks", fake_declared)
        monkeypatch.setattr(health, "get_session_factory", lambda: (lambda: _AsyncCtx()))

        def boom():
            raise AssertionError("入库进行中不应该去查向量库")

        import app.rag.vectorstore as vs

        monkeypatch.setattr(vs, "get_vectorstore", lambda: SimpleNamespace(count=boom))
        assert asyncio.run(health.check_knowledge_base()).startswith("checking")


class _AsyncCtx:
    """最小可用的 async context manager，替代 AsyncSession。"""

    async def __aenter__(self):
        return None

    async def __aexit__(self, *exc):
        return False
