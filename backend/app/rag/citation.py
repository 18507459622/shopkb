"""引文清洗：从答案中提取 [n]，仅保留映射到真实 chunk 的编号，剔除自造编号。"""
from __future__ import annotations

import re

_CITE_RE = re.compile(r"\[(\d+)\]")


def sanitize_citations(answer: str, valid_count: int) -> tuple[str, list[int]]:
    """返回 (清洗后答案, 实际使用的来源编号列表)。"""
    used: list[int] = []
    seen: set[int] = set()
    for m in _CITE_RE.finditer(answer):
        n = int(m.group(1))
        if 1 <= n <= valid_count and n not in seen:
            seen.add(n)
            used.append(n)

    cleaned = _CITE_RE.sub(
        lambda m: m.group(0) if 1 <= int(m.group(1)) <= valid_count else "", answer
    )
    return cleaned, used
