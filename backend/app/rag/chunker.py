"""中文感知切分：自定义分隔符优先级，避免把中文句子在字腰处砍断。"""
from __future__ import annotations

from functools import lru_cache

from langchain_text_splitters import RecursiveCharacterTextSplitter

# 中文标点优先：先按段落，再按句末标点，再按分句标点，最后按字兜底
_SEPARATORS = ["\n\n", "\n", "。", "！", "？", "；", "，", "、", " ", ""]


@lru_cache
def get_chunker(
    chunk_size: int = 500, chunk_overlap: int = 60
) -> RecursiveCharacterTextSplitter:
    return RecursiveCharacterTextSplitter(
        separators=_SEPARATORS,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        keep_separator=True,
        length_function=len,
        is_separator_regex=False,
    )


def split_text(text: str, chunk_size: int = 500, chunk_overlap: int = 60) -> list[str]:
    """切分文本，返回 chunk 文本列表（去除空白碎片）。"""
    chunks = get_chunker(chunk_size, chunk_overlap).split_text(text)
    return [c.strip() for c in chunks if c.strip()]
