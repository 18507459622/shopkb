"""文档解析：parse(path, doc_type) -> list[Block]，格式可插拔。

约定：解析层只负责把文件拆成带结构的文本块（Block），不做切分；
表格原则：不可切分单元，序列化为 Markdown 管道表 + 章节标题一起注入。
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from pathlib import Path

from app.core.exceptions import ValidationError


@dataclass
class Block:
    text: str
    page: int | None = None
    section: str = ""
    is_table: bool = False
    meta: dict = field(default_factory=dict)


SUPPORTED_TYPES = {"txt", "md", "pdf", "docx", "csv"}


def parse(path: str | Path, doc_type: str) -> list[Block]:
    p = Path(path)
    if doc_type not in SUPPORTED_TYPES:
        raise ValidationError(f"不支持的文件类型: {doc_type}")

    if doc_type == "txt":
        return _parse_txt(p)
    if doc_type == "md":
        return _parse_md(p)
    if doc_type == "pdf":
        return _parse_pdf(p)
    if doc_type == "docx":
        return _parse_docx(p)
    if doc_type == "csv":
        return _parse_csv(p)
    return []


def _read_text_auto(p: Path) -> str:
    """按常见编码顺序解码（UTF-8 → GB18030），兼容 Windows 记事本保存的 GBK 中文文件。"""
    data = p.read_bytes()
    for enc in ("utf-8-sig", "gb18030"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="ignore")


def _parse_txt(p: Path) -> list[Block]:
    return [Block(text=_read_text_auto(p))]


def _parse_md(p: Path) -> list[Block]:
    return [Block(text=_read_text_auto(p))]


def _parse_pdf(p: Path) -> list[Block]:
    import fitz  # PyMuPDF

    doc = fitz.open(str(p))
    blocks: list[Block] = []
    for i, page in enumerate(doc):
        text = page.get_text("text").strip()
        if text:
            blocks.append(Block(text=text, page=i + 1))
    doc.close()
    return blocks


def _parse_docx(p: Path) -> list[Block]:
    import docx

    document = docx.Document(str(p))
    blocks: list[Block] = []
    # 按 body 顺序遍历段落与表格（保持原序）
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    for child in document.element.body.iterchildren():
        if child.tag.endswith("}p"):
            para = Paragraph(child, document)
            text = para.text.strip()
            if text:
                blocks.append(Block(text=text))
        elif child.tag.endswith("}tbl"):
            table = Table(child, document)
            blocks.append(Block(text=_table_to_markdown(table), is_table=True))
    return blocks


def _parse_csv(p: Path) -> list[Block]:
    rows = list(csv.reader(io.StringIO(_read_text_auto(p))))
    if not rows:
        return []
    header = rows[0]
    # 每行作为一个自然 chunk（SKU/产品行）
    blocks: list[Block] = []
    for row in rows[1:]:
        line = " | ".join(f"{header[i]}: {cell}" for i, cell in enumerate(row) if cell)
        if line.strip():
            blocks.append(Block(text=line))
    return blocks


def _table_to_markdown(table) -> str:
    rows = [[cell.text.strip() for cell in row.cells] for row in table.rows]
    if not rows:
        return ""
    header = rows[0]
    width = max(len(r) for r in rows)
    lines = ["| " + " | ".join(h or " " for h in header) + " |"]
    lines.append("| " + " | ".join("---" for _ in range(width)) + " |")
    for r in rows[1:]:
        padded = (r + [""] * width)[:width]
        lines.append("| " + " | ".join(c or " " for c in padded) + " |")
    return "\n".join(lines)
