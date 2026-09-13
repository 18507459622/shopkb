"""评测工具单测：确定性指标 + 门禁判定。

这两块是「评测的可信度地基」：
- metrics：数字命中率/引文准确率一旦算错，评测结论就是错的（且有隐蔽性）；
- gate：门禁必须真的会红——否则「防退化」只是文档里的一句话。
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from eval.gate import Gate, exit_code, judge_gates  # noqa: E402
from eval.metrics import (  # noqa: E402
    citation_stats,
    extract_numbers,
    mean,
    numeric_recall,
    percentile,
    rate,
)

# ---------- 数字抽取 / 命中 ----------


def test_extract_numbers_strips_thousands_separator() -> None:
    assert extract_numbers("4,999 元") == {"4999"}


def test_extract_numbers_handles_multiple_specs() -> None:
    assert extract_numbers("12GB+256GB") == {"12", "256"}


def test_extract_numbers_normalizes_fullwidth_digits() -> None:
    assert extract_numbers("４９９９ 元") == {"4999"}


def test_numeric_recall_full_hit() -> None:
    assert numeric_recall("4999 元", "价格是 4999 元。") == 1.0


def test_numeric_recall_partial_hit() -> None:
    assert numeric_recall("4999 元 与 5699 元", "只要 4999 元") == 0.5


def test_numeric_recall_does_not_match_substring() -> None:
    """子串陷阱：gold 里的 8 不能被 18999 命中（集合比较而非子串匹配）。"""
    assert numeric_recall("8GB", "售价 18999 元") == 0.0


def test_numeric_recall_returns_none_without_gold_numbers() -> None:
    """gold 本身没有数字（如「有 ECG 功能」）→ 该题不适用，返回 None。"""
    assert numeric_recall("有 ECG 心电图功能", "支持 ECG。") is None


# ---------- 引文统计 ----------


def test_citation_stats_counts_invalid_and_precision() -> None:
    stats = citation_stats("价格 [1]，另外 [7] 提过。", valid_count=3)
    assert stats["total"] == 2
    assert stats["invalid_unique"] == [7]
    assert stats["invalid_count"] == 1
    assert stats["precision"] == 0.5


def test_citation_stats_all_valid() -> None:
    stats = citation_stats("[1][2]", valid_count=3)
    assert stats["precision"] == 1.0
    assert stats["invalid_count"] == 0


def test_citation_stats_no_citation_returns_none_precision() -> None:
    """没有引用时准确率无意义（不是 0）——避免把「没引用」误判成「引用全错」。"""
    assert citation_stats("没有任何引用", valid_count=3)["precision"] is None


# ---------- 基础统计 ----------


def test_rate_returns_zero_on_empty_denominator() -> None:
    """0/0 记为 0.0：让覆盖率类门禁能失败，而不是静默跳过。"""
    assert rate(0, 0) == 0.0


def test_mean_and_percentile() -> None:
    assert mean([]) is None
    assert mean([1.0, 3.0]) == 2.0
    assert percentile([1.0, 2.0, 3.0, 4.0], 0.5) == 3.0


# ---------- 门禁 ----------


def test_gate_fails_when_below_threshold() -> None:
    gates = [Gate("faithfulness", "忠实度", 0.85)]
    rows, code = judge_gates({"faithfulness": 0.60}, gates)
    assert code == 1 and rows[0].status == "FAIL"


def test_gate_passes_at_threshold() -> None:
    gates = [Gate("no_fabrication", "不编造率", 1.0, "==")]
    rows, code = judge_gates({"no_fabrication": 1.0}, gates)
    assert code == 0 and rows[0].status == "PASS"


def test_gate_skips_missing_metric_without_failing() -> None:
    """指标无法计算（None）记为 SKIP，不当作失败——但会打印出来提醒覆盖不足。"""
    rows, code = judge_gates({"faithfulness": None}, [Gate("faithfulness", "忠实度", 0.85)])
    assert code == 0 and rows[0].status == "SKIP"


def test_exit_code_is_one_if_any_row_failed() -> None:
    gates = [Gate("a", "A", 0.9), Gate("b", "B", 0.9)]
    rows, _ = judge_gates({"a": 0.95, "b": 0.10}, gates)
    assert exit_code(rows) == 1
