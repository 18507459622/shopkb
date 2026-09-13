"""可观测性模块的单测。

可观测性代码有个共同特点：**它坏了不会报错，只会悄悄少记数据**——
等到真需要查「昨天那次为什么慢」时才发现日志是空的。
所以这里重点验证三件事：

1. 数字算得对（token 累加、成本换算、分位、失败归因）；
2. 两种 usage 位置都能取到（流式与非流式的差异会让成本统计偏低）；
3. **失败方向正确**：写不进日志不能影响业务（fail-open），
   但阶段异常必须照常向上抛（不能为了观测把异常吞掉）。
"""
from __future__ import annotations

import json

import pytest

from app.core import observability as obs


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """每个用例独立：重置聚合指标 + 把 JSONL 重定向到临时目录。"""
    monkeypatch.setattr(obs, "_TRACE_FILE", tmp_path / "llm_trace.jsonl")
    monkeypatch.setattr(obs, "_TRACE_ENABLED", True)
    obs.reset()
    yield
    obs.reset()


def read_jsonl(tmp_path):
    path = tmp_path / "llm_trace.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def make_response(prompt=100, completion=50, model="deepseek-flash", *, usage_in_llm_output=True):
    """构造一个 LangChain LLMResult 形状的替身，覆盖两种 usage 位置。"""

    class _Msg:
        usage_metadata = {"input_tokens": prompt, "output_tokens": completion}
        response_metadata = {"model_name": model}

    class _Gen:
        message = _Msg()

    class _Resp:
        if usage_in_llm_output:
            llm_output = {
                "model_name": model,
                "token_usage": {
                    "prompt_tokens": prompt,
                    "completion_tokens": completion,
                    "total_tokens": prompt + completion,
                },
            }
        else:
            llm_output = {}
        generations = [[_Gen()]]

    return _Resp()


# ---------------------------------------------------------------- usage 抽取
class TestExtractUsage:
    def test_从llm_output取用量(self):
        assert obs._extract_usage(make_response(120, 60))[:2] == (120, 60)

    def test_llm_output为空时回落到usage_metadata(self):
        # 流式调用常常没有 llm_output.token_usage，只看那一处会让成本统计系统性偏低
        assert obs._extract_usage(make_response(120, 60, usage_in_llm_output=False))[:2] == (120, 60)

    def test_模型名从llm_output取(self):
        assert obs._extract_usage(make_response(model="deepseek-flash"))[2] == "deepseek-flash"

    def test_模型名缺失时回落到response_metadata(self):
        resp = make_response(model="deepseek-flash", usage_in_llm_output=False)
        assert obs._extract_usage(resp)[2] == "deepseek-flash"

    def test_完全没有用量信息时返回零而不是报错(self):
        class Empty:
            llm_output = {}
            generations = []

        assert obs._extract_usage(Empty()) == (0, 0, "unknown")


# ---------------------------------------------------------------- LLM 回调
class TestTraceHandler:
    def test_记录调用耗时与token(self, tmp_path):
        h = obs.TraceCallbackHandler()
        h.on_llm_start({"kwargs": {"model": "deepseek-flash"}}, ["提示词"], run_id="r1")
        h.on_llm_end(make_response(100, 50), run_id="r1")

        end = [r for r in read_jsonl(tmp_path) if r.get("phase") == "end"][0]
        assert end["model"] == "deepseek-flash"
        assert end["prompt_tokens"] == 100
        assert end["completion_tokens"] == 50
        assert end["ok"] is True
        assert end["duration_ms"] >= 0

    def test_累计到聚合指标(self):
        h = obs.TraceCallbackHandler()
        for i in range(3):
            h.on_llm_start({}, ["p"], run_id=f"r{i}")
            h.on_llm_end(make_response(100, 50), run_id=f"r{i}")
        snap = obs.snapshot()
        assert snap["llm"]["calls"] == 3
        assert snap["cost"]["prompt_tokens"] == 300
        assert snap["cost"]["completion_tokens"] == 150

    def test_成本按单价换算(self, monkeypatch):
        monkeypatch.setattr(obs, "_PRICE_IN", 2.0)
        monkeypatch.setattr(obs, "_PRICE_OUT", 8.0)
        h = obs.TraceCallbackHandler()
        h.on_llm_start({}, ["p"], run_id="r")
        h.on_llm_end(make_response(1_000_000, 500_000), run_id="r")
        assert obs.snapshot()["cost"]["cost_cny"] == pytest.approx(2.0 + 4.0, abs=0.01)

    def test_单价可被配置覆盖(self, monkeypatch):
        monkeypatch.setattr(obs, "_PRICE_IN", 1.0)
        monkeypatch.setattr(obs, "_PRICE_OUT", 2.0)
        h = obs.TraceCallbackHandler()
        h.on_llm_start({}, ["p"], run_id="r")
        h.on_llm_end(make_response(1_000_000, 0), run_id="r")
        assert obs.snapshot()["cost"]["cost_cny"] == pytest.approx(1.0, abs=0.01)

    def test_记录模型分布(self):
        h = obs.TraceCallbackHandler()
        for i, m in enumerate(["deepseek-flash", "deepseek-flash", "other"]):
            h.on_llm_start({}, ["p"], run_id=f"r{i}")
            h.on_llm_end(make_response(model=m, usage_in_llm_output=False), run_id=f"r{i}")
        assert obs.snapshot()["llm"]["by_model"] == {"deepseek-flash": 2, "other": 1}

    def test_调用失败被记录并归因(self, tmp_path):
        h = obs.TraceCallbackHandler()
        h.on_llm_start({}, ["p"], run_id="r1")
        h.on_llm_error(TimeoutError("连接超时"), run_id="r1")

        row = read_jsonl(tmp_path)[-1]
        assert row["ok"] is False
        assert "TimeoutError" in row["error"]
        snap = obs.snapshot()
        assert snap["llm"]["failures"] == 1
        assert snap["failures_by_step"]["llm_call"] == 1

    def test_每次调用都算一次(self):
        """失败也要计入 calls —— 否则"调用次数"会漏掉失败的那些。"""
        h = obs.TraceCallbackHandler()
        h.on_llm_start({}, ["p"], run_id="ok")
        h.on_llm_end(make_response(), run_id="ok")
        h.on_llm_start({}, ["p"], run_id="bad")
        h.on_llm_error(RuntimeError("x"), run_id="bad")
        assert obs.snapshot()["llm"]["calls"] == 2


# ---------------------------------------------------------------- 阶段计时
class TestStage:
    def test_成功时记录耗时与字段(self, tmp_path):
        with obs.stage("retrieve", query="手机价格"):
            pass
        row = read_jsonl(tmp_path)[-1]
        assert row["kind"] == "stage"
        assert row["name"] == "retrieve"
        assert row["ok"] is True
        assert row["query"] == "手机价格"

    def test_可以在阶段内回填字段(self, tmp_path):
        with obs.stage("retrieve") as info:
            info["chunks"] = 5
        assert read_jsonl(tmp_path)[-1]["chunks"] == 5

    def test_异常照常向上抛且被记录(self, tmp_path):
        with pytest.raises(ValueError, match="炸了"):
            with obs.stage("retrieve"):
                raise ValueError("炸了")
        row = read_jsonl(tmp_path)[-1]
        assert row["ok"] is False
        assert "ValueError: 炸了" in row["error"]

    def test_失败进入failures_by_step(self):
        with pytest.raises(RuntimeError):
            with obs.stage("generate"):
                raise RuntimeError("x")
        assert obs.snapshot()["failures_by_step"] == {"generate": 1}

    def test_阶段统计包含次数与均值(self):
        for _ in range(4):
            with obs.stage("rewrite"):
                pass
        s = obs.snapshot()["stages"]["rewrite"]
        assert s["count"] == 4
        assert s["failures"] == 0
        assert s["avg_ms"] >= 0


# ---------------------------------------------------------------- 快照
class TestSnapshot:
    def test_空状态不报错(self):
        snap = obs.snapshot()
        assert snap["llm"]["calls"] == 0
        assert snap["llm"]["latency_ms"] == {"p50": 0.0, "p95": 0.0}
        assert snap["cost"]["total_tokens"] == 0
        assert snap["failures_by_step"] == {}

    def test_分位数计算(self):
        assert obs._percentile([], 0.5) == 0.0
        assert obs._percentile([10.0], 0.95) == 10.0
        assert obs._percentile([1.0, 2.0, 3.0, 4.0, 5.0], 0.5) == 3.0

    def test_快照可json序列化(self):
        with obs.stage("retrieve"):
            pass
        json.dumps(obs.snapshot(), ensure_ascii=False)

    def test_快照是深拷贝_外部改动不影响内部(self):
        snap = obs.snapshot()
        snap["llm"]["calls"] = 999
        assert obs.snapshot()["llm"]["calls"] == 0

    def test_标注单进程作用域(self):
        """多 worker 部署下每个 worker 各算各的，必须在返回里说清楚。"""
        assert "单进程" in obs.snapshot()["scope"]

    def test_成本说明可追溯(self):
        cost = obs.snapshot()["cost"]
        assert cost["price_per_m_input"] > 0
        assert cost["price_per_m_output"] > 0
        assert "估算" in cost["note"]


# ---------------------------------------------------------------- fail-open
class TestFailOpen:
    def test_日志写不进去不影响业务(self, tmp_path, monkeypatch):
        blocker = tmp_path / "not_a_dir"
        blocker.write_text("x", encoding="utf-8")
        monkeypatch.setattr(obs, "_TRACE_FILE", blocker / "trace.jsonl")

        # 不应抛异常
        with obs.stage("retrieve"):
            pass
        h = obs.TraceCallbackHandler()
        h.on_llm_start({}, ["p"], run_id="r")
        h.on_llm_end(make_response(), run_id="r")
        assert obs.snapshot()["llm"]["calls"] == 1

    def test_关闭开关后不落盘(self, tmp_path, monkeypatch):
        monkeypatch.setattr(obs, "_TRACE_ENABLED", False)
        with obs.stage("retrieve"):
            pass
        assert not (tmp_path / "llm_trace.jsonl").exists()
        # 但内存指标仍然要更新（否则开关一关就彻底瞎了）
        assert obs.snapshot()["stages"]["retrieve"]["count"] == 1

    def test_超长字段被截断(self, tmp_path):
        with obs.stage("retrieve", query="手" * 500):
            pass
        assert len(read_jsonl(tmp_path)[-1]["query"]) < 500


# ---------------------------------------------------------------- trace 关联
class TestTraceId:
    def test_没有请求上下文时返回空串而不造假id(self):
        obs.reset()
        assert obs.new_trace() == ""

    def test_复用中间件注入的request_id(self):
        from app.core.middleware import request_id_var

        token = request_id_var.set("req-abc123")
        try:
            assert obs.new_trace() == "req-abc123"
            with obs.stage("retrieve"):
                pass
            assert obs._metrics["stages"]["retrieve"]["count"] == 1
        finally:
            request_id_var.reset(token)

    def test_事件里带上trace_id(self, tmp_path):
        from app.core.middleware import request_id_var

        token = request_id_var.set("req-xyz")
        try:
            with obs.stage("retrieve"):
                pass
        finally:
            request_id_var.reset(token)
        assert read_jsonl(tmp_path)[-1]["trace_id"] == "req-xyz"
