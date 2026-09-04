"""生成模型：DeepSeek（OpenAI 兼容）经 langchain-openai ChatOpenAI。"""
from __future__ import annotations

from functools import lru_cache

from langchain_openai import ChatOpenAI

from app.core.config import get_settings


@lru_cache
def get_llm() -> ChatOpenAI:
    s = get_settings()
    return ChatOpenAI(
        model=s.llm_model,
        base_url=s.llm_base_url,
        api_key=s.llm_api_key,
        temperature=s.llm_temperature,
        top_p=0.7,
        max_tokens=1024,
        streaming=True,
        timeout=60,
        max_retries=2,
    )
