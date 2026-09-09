"""clarifier 单元测试：歧义检测规则。"""
from app.rag.clarifier import detect_clarify


def test_named_product_not_ambiguous():
    assert detect_clarify("星辰 X1 Pro 多少钱") is None
    assert detect_clarify("星域 Book 16 用的什么显卡") is None


def test_category_ambiguous_lists_candidates():
    c = detect_clarify("手机多少钱")
    assert c is not None
    assert c.category == "手机"
    assert len(c.candidates) == 5
    assert "星辰 X1 Pro" in c.candidates
    assert "星云 Note 5" in c.candidates


def test_single_product_category_not_ambiguous():
    # 该类目下只有一款，无需追问
    assert detect_clarify("洗衣机保修几年") is None
    assert detect_clarify("净化器多少钱") is None


def test_missing_entity_generic_clarify():
    c = detect_clarify("多少钱")
    assert c is not None
    assert c.category is None
    assert c.candidates == []


def test_missing_entity_with_history_not_clarified():
    # 有历史可消解时，交给 rewriter 处理，不在这里追问
    assert detect_clarify("多少钱", history_questions=["星辰 X1 Pro 的电池多大"]) is None


def test_service_question_not_ambiguous():
    assert detect_clarify("售后政策是什么") is None
    assert detect_clarify("退货运费谁承担") is None
    assert detect_clarify("电子发票多久开具") is None
