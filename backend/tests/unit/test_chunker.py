from app.rag.chunker import split_text


def test_split_long_chinese_text():
    text = "这是一段用于测试中文切分的文本。" * 100
    chunks = split_text(text, chunk_size=200, chunk_overlap=20)
    assert len(chunks) > 1
    assert all(c.strip() for c in chunks)


def test_split_short_text_single_chunk():
    chunks = split_text("短文本", chunk_size=500, chunk_overlap=60)
    assert chunks == ["短文本"]


def test_no_empty_chunks():
    text = "第一段。\n\n\n\n第二段。"
    chunks = split_text(text, chunk_size=10, chunk_overlap=2)
    assert all(c for c in chunks)
