"""端到端评测：忠实度（faithfulness）+ 不可答不编造率 + 生成延迟。

跑法（在 backend/ 目录下）：
    cd backend && ../.venv/Scripts/python.exe ../eval/e2e.py

对金标集中每个可答问题走完整「检索 → 接地生成」，用 LLM-judge 判忠实度；
对不可答问题走「门控 → 兜底引导」，判是否编造具体商品事实（目标 1.0）。
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

from langchain_core.messages import HumanMessage, SystemMessage  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.rag.embeddings import get_embedding  # noqa: E402
from app.rag.ingestion import build_chunks  # noqa: E402
from app.rag.llm import get_llm  # noqa: E402
from app.rag.parser import parse  # noqa: E402
from app.rag.pipeline import (  # noqa: E402
    OFFTOPIC_SYSTEM_PROMPT,
    RAG_SYSTEM_PROMPT,
    build_context,
)
from app.rag.retriever import Retriever  # noqa: E402
from app.rag.vectorstore import MilvusStore  # noqa: E402

CORPUS_DIR = REPO / "eval" / "corpus"
GOLDEN_PATH = REPO / "eval" / "golden.jsonl"
EVAL_DB = REPO / "backend" / "data" / "eval_milvus.db"
EVAL_COLLECTION = "eval_product_kb"

FAITHFULNESS_PROMPT = """你是严格的事实核对员。判断【回答】里的每个事实性陈述（尤其是数字：价格/参数/容量/续航/质保年限等）是否都能在【知识库内容】中找到依据。

规则：
- 所有事实陈述都有依据 → grounded=true
- 出现任何知识库没有的数字/参数/事实（编造或推断）→ grounded=false
- 回答明确说"知识库未收录/不知道" → grounded=true（不编造）

只输出 JSON：{{"grounded": true/false, "reason": "一句话"}}

【知识库内容】
{context}

【回答】
{answer}"""

NO_FABRICATION_PROMPT = """判断【回答】是否编造了【知识库内容】里没有的商品事实（价格、参数、库存、保修年限等数字或规格）。
- 回答里的所有具体数字/规格都能在【知识库内容】中找到 → fabricated=false
- 回答出现了【知识库内容】里没有的具体数字/规格（凭空编造）→ fabricated=true
- 回答只说"知识库未收录/不知道"、未给出具体数字 → fabricated=false

只输出 JSON：{{"fabricated": true/false, "reason": "一句话"}}

【知识库内容】
{context}

【回答】
{answer}"""


def parse_json_bool(text: str, key: str) -> bool | None:
    m = re.search(r"\{.*?\}", text, re.DOTALL)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
        return bool(obj.get(key))
    except Exception:
        return None


def load_golden() -> list[dict]:
    return [json.loads(l) for l in GOLDEN_PATH.read_text(encoding="utf-8").splitlines() if l.strip()]


async def ingest_corpus(store: MilvusStore, embedding) -> None:
    files = sorted(CORPUS_DIR.glob("*.md"))
    for doc_id, path in enumerate(files, start=1):
        doc_type = path.suffix.lstrip(".")
        blocks = await asyncio.to_thread(parse, str(path), doc_type)
        chunks = build_chunks(blocks, doc_id, path.name, doc_type)
        vectors = await embedding.aembed_documents([c["text"] for c in chunks])
        for c, v in zip(chunks, vectors, strict=False):
            c["dense_vector"] = v
        await asyncio.to_thread(store.upsert_chunks, str(doc_id), chunks)


async def main() -> None:
    settings = get_settings()
    store = MilvusStore(str(EVAL_DB), EVAL_COLLECTION, settings.milvus_dim)
    embedding = get_embedding()
    retriever = Retriever(store, embedding, reranker=None)
    llm = get_llm()

    await ingest_corpus(store, embedding)
    golden = load_golden()
    answerable = [g for g in golden if g["answerable"]]
    unanswerable = [g for g in golden if not g["answerable"]]

    faithful = 0
    miss = 0
    latencies: list[float] = []
    detail: list[str] = []

    print("== 端到端：忠实度（可答问题）===")
    for g in answerable:
        t0 = time.perf_counter()
        chunks = await retriever.retrieve(g["question"], top_k=6)
        if not chunks:
            miss += 1
            detail.append(f"  ✗ {g['id']} 检索为空（漏召回） | {g['question']}")
            continue
        context = build_context(chunks)
        msgs = [
            SystemMessage(content=RAG_SYSTEM_PROMPT),
            HumanMessage(content=f"【知识库内容】\n{context}\n\n用户问题：{g['question']}"),
        ]
        ans = await llm.ainvoke(msgs)
        latencies.append(time.perf_counter() - t0)
        answer = ans.content if isinstance(ans.content, str) else str(ans.content)

        judge = await llm.ainvoke(
            [
                SystemMessage(content="你是严格的事实核对员。"),
                HumanMessage(content=FAITHFULNESS_PROMPT.format(context=context, answer=answer)),
            ]
        )
        grounded = parse_json_bool(str(judge.content), "grounded")
        grounded = True if grounded is None else grounded  # 解析失败按不扣分记，另列
        if grounded:
            faithful += 1
        detail.append(
            f"  {'✓' if grounded else '✗'} {g['id']} 忠实={grounded} 引文{len(chunks)}条 | {g['question']}\n"
            f"      答: {answer.strip()[:80]}"
        )

    na = len(answerable)
    print(f"  忠实度(grounded)  : {faithful}/{na - miss} = {faithful / max(1, na - miss):.3f}  （另有 {miss} 个漏召回未计入）")
    print(f"  漏召回率          : {miss}/{na} = {miss / na:.3f}")

    print("\n== 端到端：不可答不编造率 ===")
    no_fab = 0
    for g in unanswerable:
        chunks = await retriever.retrieve(g["question"], top_k=6)
        context = build_context(chunks) if chunks else ""
        if chunks:
            msgs = [
                SystemMessage(content=RAG_SYSTEM_PROMPT),
                HumanMessage(content=f"【知识库内容】\n{context}\n\n用户问题：{g['question']}"),
            ]
        else:
            msgs = [SystemMessage(content=OFFTOPIC_SYSTEM_PROMPT), HumanMessage(content=g["question"])]
        ans = await llm.ainvoke(msgs)
        answer = ans.content if isinstance(ans.content, str) else str(ans.content)
        judge = await llm.ainvoke(
            [SystemMessage(content="你是严格的事实核对员。"), HumanMessage(content=NO_FABRICATION_PROMPT.format(context=context, answer=answer))]
        )
        fabricated = parse_json_bool(str(judge.content), "fabricated")
        fabricated = False if fabricated is None else fabricated
        if not fabricated:
            no_fab += 1
        detail.append(f"  {'✓' if not fabricated else '✗'} {g['id']} 编造={fabricated} | {g['question']}\n      答: {answer.strip()[:80]}")

    nu = len(unanswerable)
    print(f"  不编造率          : {no_fab}/{nu} = {no_fab / nu:.3f}")

    # 生成延迟
    if latencies:
        latencies.sort()
        p50 = latencies[int(len(latencies) * 0.5)]
        p95 = latencies[min(int(len(latencies) * 0.95), len(latencies) - 1)]
        print(f"\n== 生成延迟（可答 {len(latencies)} 次）===")
        print(f"  P50 = {p50:.2f}s  P95 = {p95:.2f}s  avg = {sum(latencies)/len(latencies):.2f}s")

    print("\n== 逐题明细 ===")
    print("\n".join(detail))


if __name__ == "__main__":
    asyncio.run(main())
