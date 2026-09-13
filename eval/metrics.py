"""纯函数指标工具：数字抽取/命中、引文统计、分位数。

这些是评测里「确定性那半边」——不调用 LLM、结果可复现，用来和 LLM-judge
交叉验证：judge 说忠实、但 gold 里的数字一个都没命中时，人应该去看一眼。

为什么用「数字集合交集」而不是子串匹配：
    子串匹配时 "8" 会命中 "18999"（假阳性）；抽成集合后比较，8 ∉ {18999}。
"""
from __future__ import annotations

import math
import re

_FULLWIDTH = str.maketrans("０１２３４５６７８９．，", "0123456789.,")
_NUM_RE = re.compile(r"\d+(?:\.\d+)?")
_CITE_RE = re.compile(r"\[(\d+)\]")


def doc_ranking_metrics(docs_seen: list[str], expected: set[str], k: int = 10) -> dict[str, float]:
    """doc-level 检索指标（单题）。

    docs_seen 是**按排名去重后**的文档名列表；expected 是期望文档集合。
    返回 recall@5 / recall@k / full_coverage@5 / precision@5 / rr / ndcg@k。
    """
    rels = [1 if d in expected else 0 for d in docs_seen[:k]]
    n_exp = max(1, len(expected))
    top5 = docs_seen[:5]
    idcg = sum(r / math.log2(i + 2) for i, r in enumerate(sorted(rels, reverse=True)))
    dcg = sum(r / math.log2(i + 2) for i, r in enumerate(rels))
    return {
        "recall5": sum(rels[:5]) / n_exp,
        "recallk": sum(rels) / n_exp,
        "full5": 1.0 if expected and expected <= set(top5) else 0.0,
        "prec5": (sum(1 for d in top5 if d in expected) / len(top5)) if top5 else 0.0,
        "rr": next((1.0 / (i + 1) for i, r in enumerate(rels) if r), 0.0),
        "ndcg": (dcg / idcg) if idcg > 0 else 0.0,
    }


def normalize(text: str) -> str:
    """全角→半角；去掉千分位与数字间的空白（4,999 / 4 999 → 4999）。"""
    t = (text or "").translate(_FULLWIDTH)
    t = re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", t)
    t = re.sub(r"(?<=\d)\s+(?=\d)", "", t)
    return t


def extract_numbers(text: str) -> set[str]:
    """抽取文本中的全部数字 token（去重集合）。"""
    return set(_NUM_RE.findall(normalize(text)))


def numeric_recall(gold_answer: str, answer: str) -> float | None:
    """答案覆盖 gold 数字的比例；gold 里没有数字时返回 None（该题不适用）。"""
    gold = extract_numbers(gold_answer)
    if not gold:
        return None
    return len(gold & extract_numbers(answer)) / len(gold)


def citation_stats(answer: str, valid_count: int) -> dict:
    """引文统计。

    返回 total（引用出现次数）、valid_unique / invalid_unique（去重编号）、
    invalid_count、precision（按出现次数算的准确率；无引用时为 None）。
    """
    nums = [int(m.group(1)) for m in _CITE_RE.finditer(answer or "")]
    valid = [n for n in nums if 1 <= n <= valid_count]
    invalid = [n for n in nums if not (1 <= n <= valid_count)]
    return {
        "total": len(nums),
        "valid_unique": sorted(set(valid)),
        "invalid_unique": sorted(set(invalid)),
        "invalid_count": len(invalid),
        "precision": (len(valid) / len(nums)) if nums else None,
    }


def rate(num: int | float, den: int | float) -> float:
    """比例；分母为 0 时返回 0.0（让覆盖率类门禁能失败，而不是静默跳过）。"""
    return (num / den) if den else 0.0


def mean(values: list[float]) -> float | None:
    """均值；空列表返回 None（调用方决定是跳过还是判失败）。"""
    return (sum(values) / len(values)) if values else None


def percentile(values: list[float], q: float) -> float | None:
    """分位数（最近秩法，够用且不引入 numpy）。q 取 0~1。"""
    if not values:
        return None
    s = sorted(values)
    return s[min(int(len(s) * q), len(s) - 1)]


def fmt(value: float | None, digits: int = 3) -> str:
    """统一格式化：None 显示为 N/A。"""
    return "N/A" if value is None else f"{value:.{digits}f}"
