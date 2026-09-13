"""离线确定性替身：FakeEmbedding / FakeLLM（评测的 --mock 模式）。

用途：在没有网络、不想花 token、或想验证「评测脚手架本身」时，
把完整链路（入库 → 检索 → 门控 → 生成 → 判分 → 门禁 → 退出码）跑通。

⚠️ 两点必须说清楚（面试也会被问）：
1. mock 模式下的指标**没有业务意义** —— 它验证的是「评测机器能跑、门禁会红」，
   而不是「RAG 质量有多好」。真实数字必须在真实 embedding/LLM 下测。
2. FakeLLM 继承 SimpleChatModel 而不是「鸭子类型」：因为生产代码里有
   `prompt | llm` 的 LCEL 组合（rewriter），只有真正的 Runnable 才能被管道调用。

FakeEmbedding 为什么按「概念」而不是字符 n-gram 建索引：
    字符 n-gram 向量在长文档上会被长度稀释（查询 20 字 vs 文档 500 字，余弦普遍
    < 0.2），全部低于门控阈值 0.35 → mock 模式下所有可答问题都变成 retrieval_miss，
    评测链路根本走不到生成与判分环节。改成「商品名 + 领域关键词 + 字母数字型号」
    概念级索引后，共享概念的相似度落在 0.4~0.8（接近真实 embedding 量级），
    文档侧按 min(n, CAP) 归一以避免概念多的长文档被过度稀释。
"""
from __future__ import annotations

import hashlib
import math
import re
from functools import lru_cache

from langchain_core.embeddings import Embeddings
from langchain_core.language_models.chat_models import SimpleChatModel

from eval.metrics import extract_numbers

_ALNUM_RE = re.compile(r"[a-z0-9]{2,}")
_DOC_CONCEPT_CAP = 6  # 文档侧归一上限：超过此数量的概念不再进一步稀释相似度

# 领域关键词：覆盖价格/参数/售后/会员等提问维度，让 mock 检索有可用的匹配信号
_DOMAIN_TERMS: tuple[str, ...] = (
    "退货", "换货", "运费", "发票", "保修", "质保", "物流", "发货", "联保",
    "会员", "积分", "优惠券", "满减", "折扣", "权益", "等级",
    "价格", "售价", "电池", "续航", "降噪", "快充", "充电", "容量", "功率",
    "重量", "接口", "屏幕", "处理器", "显卡", "摄像头", "刷新率", "分辨率",
    "色域", "面积", "覆盖", "噪音", "麦克风", "扬声器", "音质",
    "指纹", "解锁", "人脸", "传感器", "配列", "轴体", "连接", "尺寸",
    "颜色", "配件", "库存", "型号", "规格", "材质", "防水", "支持",
)


@lru_cache
def _concept_terms() -> tuple[str, ...]:
    """商品名（复用 clarifier 词典）+ 领域关键词，全部小写。"""
    from app.rag.clarifier import CATEGORY_PRODUCTS

    products = [p for ps in CATEGORY_PRODUCTS.values() for p in ps]
    return tuple(t.lower() for t in (*products, *_DOMAIN_TERMS))


def _concepts(text: str) -> list[str]:
    """抽取文本中的「概念」：命中的商品名/关键词 + 字母数字型号（去重保序）。"""
    t = (text or "").lower()
    found = [term for term in _concept_terms() if term in t]
    found += [m.group(0) for m in _ALNUM_RE.finditer(t)]
    return list(dict.fromkeys(found))


class FakeEmbedding(Embeddings):
    """确定性假向量：概念哈希到固定维度，文档侧按 min(n, CAP) 归一。"""

    def __init__(self, dim: int = 1024) -> None:
        self.dim = dim

    def _vec(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        cs = _concepts(text)
        if not cs:
            return vec
        scale = 1.0 / math.sqrt(min(len(cs), _DOC_CONCEPT_CAP))
        for c in cs:
            h = hashlib.md5(c.encode("utf-8")).digest()
            idx = int.from_bytes(h[:4], "big") % self.dim
            vec[idx] += scale
        return vec

    # ---- LangChain Embeddings 接口 ----
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return self.embed_documents(texts)

    async def aembed_query(self, text: str) -> list[float]:
        return self._vec(text)


def _last_product(text: str) -> str | None:
    """从历史文本里找出最后提到的商品名（复用 clarifier 的商品词典）。"""
    from app.rag.clarifier import CATEGORY_PRODUCTS

    products = [p for ps in CATEGORY_PRODUCTS.values() for p in ps]
    hits = [(text.rfind(p), p) for p in products if p in text]
    return max(hits)[1] if hits else None


class FakeLLM(SimpleChatModel):
    """确定性假模型：按 prompt 类型返回可预测的内容。

    broken_judge=True 时对判分 prompt 返回**非法 JSON**，
    用来验证「判分器失效 → 覆盖率门禁变红」这条 fail-closed 回归。
    """

    broken_judge: bool = False

    @property
    def _llm_type(self) -> str:
        return "shopkb-fake-deterministic"

    def _call(self, messages, stop=None, run_manager=None, **kwargs) -> str:  # type: ignore[no-untyped-def]
        text = "\n".join(str(getattr(m, "content", "")) for m in messages)

        # 1) 判分 prompt
        if "grounded" in text or "fabricated" in text:
            if self.broken_judge:
                return "我无法确定，这条看起来还行吧。"  # 故意不是 JSON
            if "fabricated" in text:
                return '{"fabricated": false, "reason": "mock 判分"}'
            return '{"grounded": true, "reason": "mock 判分"}'

        # 2) 多轮改写 prompt（rewriter.py）
        if "改写后的查询" in text:
            question = text.rsplit("当前问题：", 1)[-1].split("改写后的查询")[0].strip()
            product = _last_product(text.split("当前问题：", 1)[0])
            if product and product not in question:
                return f"{product} {question}"
            return question or "（空）"

        # 3) 无关问题兜底 prompt
        if "无关" in text and "引导" in text:
            return "我主要擅长电商商品咨询哦～😊 你可以问我某款商品的价格、参数或售后政策。"

        # 4) 接地生成 prompt：把【知识库内容】里的数字回显
        #    只取知识库段落（避免回显系统提示词里的规则编号 1./2.）；
        #    mock 生成器的职责是「跑通评测脚手架」，不是模拟真实作答风格，
        #    所以这里采取"把证据里的数字都摆出来"的确定性策略。
        kb = text.split("【知识库内容】", 1)[-1].split("用户问题：", 1)[0]
        nums = sorted(extract_numbers(kb))
        if nums:
            return f"根据知识库：{('、'.join(nums[:40]))}。 [1]"
        return "知识库中暂未收录该信息。"


def build_fakes(dim: int = 1024, broken_judge: bool = False) -> tuple[FakeEmbedding, FakeLLM]:
    """返回 (embedding, llm) 供评测脚本注入。"""
    return FakeEmbedding(dim=dim), FakeLLM(broken_judge=broken_judge)
