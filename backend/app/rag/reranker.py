"""重排：bge-reranker-v2-m3 交叉编码（可选增强，懒加载）。

未安装 sentence-transformers 或模型下载失败时，优雅降级为原始检索分数。
"""
from __future__ import annotations

import math
from typing import Any


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


class Reranker:
    def __init__(self, model_name: str, enabled: bool = True) -> None:
        self.model_name = model_name
        self.enabled = enabled
        self._model = None
        self._load_failed = False

    def _load(self):
        if self._model is None and not self._load_failed:
            try:
                from sentence_transformers import CrossEncoder

                self._model = CrossEncoder(self.model_name)
            except Exception:
                self._load_failed = True
        return self._model

    def rerank(self, query: str, docs: list[dict[str, Any]]) -> list[tuple[int, float]]:
        """返回 [(原下标, 相关度 0-1)]，相关度已 sigmoid 归一。"""
        if not self.enabled or not docs:
            return [(i, float(d.get("score", 0.0))) for i, d in enumerate(docs)]
        model = self._load()
        if model is None:
            return [(i, float(d.get("score", 0.0))) for i, d in enumerate(docs)]
        scores = model.predict([(query, d["text"]) for d in docs])
        if hasattr(scores, "tolist"):
            scores = scores.tolist()
        probs = [_sigmoid(float(s)) for s in scores]
        return sorted(enumerate(probs), key=lambda x: x[1], reverse=True)
