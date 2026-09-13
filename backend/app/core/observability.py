"""可观测性：把「这次请求慢在哪、花了多少 token、失败在检索还是生成」变成可查的数字。

已有的能力与本模块的分工
------------------------
`RequestIDMiddleware` 已经给每个请求分配了 `request_id`（也接受上游 `X-Request-ID`），
存进 ContextVar 并绑定到 structlog 上下文，还会回写到响应头 ——
这解决的是**「能不能定位到某一次请求」**。

缺的是**「这一次请求里，模型被调了几次、各花了多久、烧了多少 token、失败在哪一步」**。
本模块补的就是这一层，并且**复用已有的 request_id 作为 trace_id**，不另造一套 ID 体系。

三块内容
--------
1. `TraceCallbackHandler` —— 挂在 `ChatOpenAI` 上采集 LLM 调用（模型 / 耗时 / token / 异常）
2. `stage()` —— 给检索、门控、生成、引文等阶段计时，回答「失败发生在哪一步」
3. `snapshot()` —— 聚合指标，由 `GET /api/v1/admin/metrics` 暴露

设计取舍
--------
* **用 LangChain 回调而不是逐点埋点**：`ChatOpenAI` 在 pipeline（生成）和 rewriter（多轮改写）
  两处被调用，还有流式与非流式两种姿势，回调一次全覆盖，且业务代码几乎不用改。
* **用 structlog 输出**（与 `logging_conf.py` 一致）：自动带上 request_id，
  dev 下是控制台彩色、prod 下是 JSON，不需要两套日志。
* **聚合指标放进程内存**：单机演示够用。多 worker 部署时每个 worker 各算各的，
  要跨 worker 聚合需要上 Prometheus / Langfuse —— 这一点在 README「已知限制」里写明。
* **LLM 调用同时落 JSONL**（`logs/llm_trace.jsonl`）：一行一个事件，
  grep / jq / pandas 都能直接消费，不依赖任何外部平台就能查问题。
* **可观测性自身必须 fail-open**：日志写不进去绝不能让业务请求失败。
"""
from __future__ import annotations

import json
import os
import threading
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from langchain_core.callbacks import BaseCallbackHandler

from app.core.logging_conf import get_logger
from app.core.middleware import request_id_var

logger = get_logger("shopkb.observability")

# ---------------------------------------------------------------- 配置

_TRACE_ENABLED = os.getenv("TRACE_ENABLED", "1").lower() not in ("0", "false", "no")
_TRACE_FILE = Path(os.getenv("TRACE_FILE", "./logs/llm_trace.jsonl"))

# 单价（元 / 百万 token）。默认值按 DeepSeek 官方价目表估算，
# 但价格会变，所以一律允许用环境变量覆盖 —— 成本数字必须能追溯到来源。
_PRICE_IN = float(os.getenv("PRICE_INPUT_PER_M", "2.0"))
_PRICE_OUT = float(os.getenv("PRICE_OUTPUT_PER_M", "8.0"))

_lock = threading.Lock()


def _empty() -> dict:
    return {
        "llm_calls": 0,
        "llm_failures": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "llm_latencies_ms": [],
        "by_model": {},
        "stages": {},  # 阶段名 -> {"count", "total_ms", "failures"}
        "failures_by_step": {},
    }


_metrics: dict = _empty()


def reset() -> None:
    """清空聚合指标（供测试使用）。"""
    with _lock:
        _metrics.clear()
        _metrics.update(_empty())


def new_trace() -> str:
    """取当前请求的 trace_id；没有请求上下文时返回空串而不是造假 ID。"""
    return request_id_var.get()


# ---------------------------------------------------------------- 事件落盘


def _clip(value):
    """把任意值转成可 JSON 序列化、长度受控的形式。"""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= 200 else value[:200] + "…"
    return str(value)[:200]


def _append_jsonl(record: dict) -> None:
    """追加一行 JSON Lines。失败只记日志，绝不向上抛 —— 可观测性是 fail-open 的。"""
    if not _TRACE_ENABLED:
        return
    try:
        _TRACE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with _TRACE_FILE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:  # 磁盘满 / 权限不足 / 路径非法
        logger.warning("trace_sink_failed", error=repr(exc))


def _emit(event: dict) -> dict:
    record = {
        "ts": datetime.now(UTC).astimezone().isoformat(timespec="milliseconds"),
        "trace_id": new_trace(),
        **{k: _clip(v) for k, v in event.items()},
    }
    _append_jsonl(record)
    return record


# ---------------------------------------------------------------- 阶段计时


@contextmanager
def stage(name: str, **fields):
    """给一个处理阶段计时并记录成败（检索 / 改写 / 生成 / 引文）。

    异常照常向上抛 —— 这里只观测，不吞异常。

    `with` 的目标是一个可变字典，可以在阶段执行过程中回填「跑完才知道」的字段：

        with stage("retrieve", query=q) as info:
            chunks = await retriever.retrieve(q)
            info["chunks"] = len(chunks)
    """
    extra: dict = dict(fields)
    started = time.perf_counter()
    try:
        yield extra
    except Exception as exc:  # noqa: BLE001
        elapsed = round((time.perf_counter() - started) * 1000, 1)
        _record_stage(name, elapsed, ok=False, error=f"{type(exc).__name__}: {exc}", **extra)
        raise
    else:
        elapsed = round((time.perf_counter() - started) * 1000, 1)
        _record_stage(name, elapsed, ok=True, **extra)


def _record_stage(name: str, elapsed_ms: float, ok: bool, error: str = "", **fields) -> None:
    log = logger.bind(**{"stage": name, "duration_ms": elapsed_ms, "ok": ok, **fields})
    if ok:
        log.info("stage_done")
    else:
        log.warning("stage_failed", error=error)
    _emit({"kind": "stage", "name": name, "ok": ok, "duration_ms": elapsed_ms, "error": error, **fields})
    with _lock:
        bucket = _metrics["stages"].setdefault(name, {"count": 0, "total_ms": 0.0, "failures": 0})
        bucket["count"] += 1
        bucket["total_ms"] += elapsed_ms
        if not ok:
            bucket["failures"] += 1
            _metrics["failures_by_step"][name] = _metrics["failures_by_step"].get(name, 0) + 1


# ---------------------------------------------------------------- LLM 回调


def _extract_usage(response) -> tuple[int, int, str]:
    """返回 (prompt_tokens, completion_tokens, model)。

    兼容两种 usage 位置：`llm_output.token_usage`（非流式常见）
    与 `message.usage_metadata`（流式聚合后常见）—— 少任何一条都会让成本统计偏低。
    """
    llm_output = getattr(response, "llm_output", None) or {}
    usage = llm_output.get("token_usage") or llm_output.get("usage") or {}
    if not usage:
        try:
            msg = response.generations[0][0].message
            usage = getattr(msg, "usage_metadata", None) or {}
        except (AttributeError, IndexError, TypeError):
            usage = {}
    model = llm_output.get("model_name") or ""
    if not model:
        try:
            model = getattr(response.generations[0][0].message, "response_metadata", {}).get(
                "model_name", ""
            )
        except (AttributeError, IndexError, TypeError):
            model = ""
    prompt = usage.get("prompt_tokens") or usage.get("input_tokens") or 0
    completion = usage.get("completion_tokens") or usage.get("output_tokens") or 0
    return int(prompt), int(completion), str(model or "unknown")


class TraceCallbackHandler(BaseCallbackHandler):
    """采集 LLM 调用：模型、耗时、token 用量、异常。

    挂在 `get_llm()` 返回的模型实例上。这里与 health-planner 的情况不同：
    shopkb 的 RAG 链路里没有 `create_agent` 式的工具循环，
    模型实例就是唯一的调用入口，所以模型级回调已经足够。
    """

    def __init__(self) -> None:
        super().__init__()
        self._started: dict[str, float] = {}

    def on_llm_start(self, serialized, prompts, *, run_id=None, **kwargs):  # noqa: D102
        self._started[str(run_id)] = time.perf_counter()
        _emit(
            {
                "kind": "llm",
                "phase": "start",
                "model": (serialized or {}).get("kwargs", {}).get("model")
                or (serialized or {}).get("name", "unknown"),
                "prompt_chars": sum(len(p) for p in (prompts or [])),
            }
        )

    def on_llm_end(self, response, *, run_id=None, **kwargs):  # noqa: D102
        started = self._started.pop(str(run_id), time.perf_counter())
        elapsed = round((time.perf_counter() - started) * 1000, 1)
        prompt, completion, model = _extract_usage(response)
        cost = prompt / 1_000_000 * _PRICE_IN + completion / 1_000_000 * _PRICE_OUT
        logger.info(
            "llm_call",
            model=model,
            duration_ms=elapsed,
            prompt_tokens=prompt,
            completion_tokens=completion,
            cost_cny=round(cost, 6),
        )
        _emit(
            {
                "kind": "llm",
                "phase": "end",
                "ok": True,
                "model": model,
                "duration_ms": elapsed,
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "cost_cny": round(cost, 6),
            }
        )
        with _lock:
            _metrics["llm_calls"] += 1
            _metrics["prompt_tokens"] += prompt
            _metrics["completion_tokens"] += completion
            _metrics["llm_latencies_ms"].append(elapsed)
            _metrics["by_model"][model] = _metrics["by_model"].get(model, 0) + 1

    def on_llm_error(self, error, *, run_id=None, **kwargs):  # noqa: D102
        started = self._started.pop(str(run_id), time.perf_counter())
        elapsed = round((time.perf_counter() - started) * 1000, 1)
        err = f"{type(error).__name__}: {error}"
        logger.warning("llm_call_failed", duration_ms=elapsed, error=err)
        _emit({"kind": "llm", "phase": "error", "ok": False, "duration_ms": elapsed, "error": err})
        with _lock:
            _metrics["llm_calls"] += 1
            _metrics["llm_failures"] += 1
            _metrics["failures_by_step"]["llm_call"] = (
                _metrics["failures_by_step"].get("llm_call", 0) + 1
            )


# 单例：get_llm() 被 lru_cache 缓存，回调实例也应只有一份
TRACE_HANDLER = TraceCallbackHandler()


# ---------------------------------------------------------------- 聚合查询


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 1)
    k = (len(ordered) - 1) * pct
    lo = int(k)
    hi = min(lo + 1, len(ordered) - 1)
    return round(ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo), 1)


def snapshot() -> dict:
    """聚合指标：直接回答「延迟多少、花了多少、失败在哪一步」。

    注意这是**单进程**指标。uvicorn 多 worker 下每个 worker 各有一份，
    要得到全局数字需要外部聚合（见 README 已知限制）。
    """
    with _lock:
        m = {
            "llm_calls": _metrics["llm_calls"],
            "llm_failures": _metrics["llm_failures"],
            "prompt_tokens": _metrics["prompt_tokens"],
            "completion_tokens": _metrics["completion_tokens"],
            "latencies": list(_metrics["llm_latencies_ms"]),
            "by_model": dict(_metrics["by_model"]),
            "stages": {k: dict(v) for k, v in _metrics["stages"].items()},
            "failures_by_step": dict(_metrics["failures_by_step"]),
        }

    cost_in = m["prompt_tokens"] / 1_000_000 * _PRICE_IN
    cost_out = m["completion_tokens"] / 1_000_000 * _PRICE_OUT
    return {
        "llm": {
            "calls": m["llm_calls"],
            "failures": m["llm_failures"],
            "by_model": m["by_model"],
            "latency_ms": {
                "p50": _percentile(m["latencies"], 0.5),
                "p95": _percentile(m["latencies"], 0.95),
            },
        },
        "cost": {
            "prompt_tokens": m["prompt_tokens"],
            "completion_tokens": m["completion_tokens"],
            "total_tokens": m["prompt_tokens"] + m["completion_tokens"],
            "cost_cny": round(cost_in + cost_out, 6),
            "price_per_m_input": _PRICE_IN,
            "price_per_m_output": _PRICE_OUT,
            "note": "按 PRICE_INPUT_PER_M / PRICE_OUTPUT_PER_M 估算，单价可用环境变量覆盖",
        },
        "stages": {
            name: {
                "count": b["count"],
                "failures": b["failures"],
                "avg_ms": round(b["total_ms"] / b["count"], 1) if b["count"] else 0.0,
            }
            for name, b in m["stages"].items()
        },
        "failures_by_step": m["failures_by_step"],
        "scope": "单进程内存聚合；多 worker 部署需外部聚合",
    }
