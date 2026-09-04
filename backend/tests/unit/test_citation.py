from app.rag.citation import sanitize_citations


def test_sanitize_removes_fabricated_citations():
    text = "价格 4999 元 [1]，保修 1 年 [2]，续航很好 [7]。"
    cleaned, used = sanitize_citations(text, 2)
    assert "[7]" not in cleaned
    assert "[1]" in cleaned
    assert used == [1, 2]


def test_sanitize_no_sources():
    cleaned, used = sanitize_citations("知识库中暂未收录该信息", 0)
    assert cleaned == "知识库中暂未收录该信息"
    assert used == []


def test_sanitize_dedups():
    cleaned, used = sanitize_citations("参考 [1] 和 [1] 和 [2]", 2)
    assert used == [1, 2]
