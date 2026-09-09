"""歧义检测与追问生成：query 命中品类词但未指定具体商品时，主动追问澄清。

设计（克制）：
- 规则 + 静态商品词典，确定性、零 LLM 成本、可单测；
- 检测顺序：已命名具体商品 → 命中品类（列候选）→ 缺实体（通用追问）；
- 追问做「一次」即可，配合现有多轮改写（rewriter）自然闭环：
  系统追问后用户回答型号，下一轮「多少钱」会被 rewriter 借助历史补全。

商品词典与 data/samples/、eval/corpus/ 的商品一致；
TODO：可由已入库文档的 `## 标题` 自动抽取，当前为内置静态词典。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ClarifyRequest:
    category: str | None  # 命中的品类（None = 通用追问）
    candidates: list[str]  # 候选商品名（通用追问时为空）
    question: str  # 追问话术


# 品类词 -> 该品类下的商品名（命中品类但未命中具体商品 = 歧义）
CATEGORY_PRODUCTS: dict[str, list[str]] = {
    "手机": ["星辰 X1 Pro", "星辰 X1 标准版", "星云 Note 5", "星云 Note 5 Pro", "星辰 X1 Lite"],
    "笔记本": ["星域 Book 14", "星域 Book 16", "星域 Air 13", "星域 Pro 17"],
    "耳机": ["星澜 Buds Pro", "星澜 Buds Air", "星澜 Headphone Max", "星澜 Buds Mini"],
    "手表": ["星环 Watch S1", "星环 Watch S1 Pro", "星环 Watch Lite", "星环 Watch Mini"],
    "空调": ["星河智能变频空调 1.5 匹"],
    "洗衣机": ["星河滚筒洗衣机 10kg"],
    "机器人": ["星河扫拖一体机器人"],
    "净化器": ["星河空气净化器 Pro"],
    "饮水机": ["星河即热式饮水机"],
}

_CATEGORY_LABEL: dict[str, str] = {
    "手机": "手机",
    "笔记本": "笔记本电脑",
    "耳机": "无线耳机",
    "手表": "智能手表",
    "空调": "空调",
    "洗衣机": "洗衣机",
    "机器人": "扫拖机器人",
    "净化器": "空气净化器",
    "饮水机": "饮水机",
}

# 缺实体追问的触发词（极短 query + 询问意图 + 无历史可消解）
_SHORT_ASK_WORDS = ("多少钱", "价格", "怎么卖", "保修", "库存", "有没有", "推荐", "哪款", "哪个")

_ALL_PRODUCTS: list[str] = [p for ps in CATEGORY_PRODUCTS.values() for p in ps]


def detect_clarify(question: str, history_questions: list[str] | None = None) -> ClarifyRequest | None:
    """判断 query 是否需要追问澄清；无需则返回 None。"""
    q = question.strip()
    history_questions = history_questions or []

    # 1. 已明确命名具体商品 -> 不追问
    for p in _ALL_PRODUCTS:
        if p in q:
            return None

    # 2. 命中品类词但未命名商品 -> 该品类候选
    for cat, products in CATEGORY_PRODUCTS.items():
        if cat in q:
            if len(products) >= 2:
                return ClarifyRequest(
                    category=cat,
                    candidates=products,
                    question=_build_category_question(cat, products),
                )
            return None  # 该品类仅一款，无歧义，直接答

    # 3. 缺实体：极短 + 询问词 + 无历史可消解
    if len(q) <= 8 and any(w in q for w in _SHORT_ASK_WORDS):
        if not history_questions:
            return ClarifyRequest(category=None, candidates=[], question=_build_generic_question())

    return None


def _build_category_question(category: str, products: list[str]) -> str:
    label = _CATEGORY_LABEL.get(category, category)
    items = " / ".join(products)
    return f"您想了解哪款{label}呢？😊 我这边有以下型号：{items}。请告诉我具体型号，我再为您查询～"


def _build_generic_question() -> str:
    cats = "、".join(_CATEGORY_LABEL.values())
    return (
        f"抱歉，我没太理解您的问题～ 😊 我可以帮您查询商品的价格、参数、库存、售后等信息，"
        f"范围包括：{cats}。您能具体说说是哪款商品吗？"
    )
