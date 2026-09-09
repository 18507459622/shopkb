"""离线检索评测：doc-level Recall@K / MRR / NDCG@K + 门控拒答检查。

跑法（在 backend/ 目录下，保证读到 ../.env 的 API Key）：
    cd backend && ../.venv/Scripts/python.exe ../eval/retrieval.py

职责：
- 用真实 pipeline（parse → chunk → embed → upsert）把 eval/corpus/ 灌进独立
  collection `eval_product_kb`（独立 db 文件，不污染 product_kb）。
- 对金标集中每个可答问题做 dense 检索（本地 Milvus Lite 仅 dense），
  计算 doc-level Recall@5/10、MRR、NDCG@10。
- 对不可答问题走完整 Retriever（含相关度门控），统计「正确拒答率」。
"""
from __future__ import annotations

import asyncio
import json
import math
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

from app.core.config import get_settings  # noqa: E402
from app.rag.embeddings import get_embedding  # noqa: E402
from app.rag.ingestion import build_chunks  # noqa: E402
from app.rag.parser import parse  # noqa: E402
from app.rag.retriever import Retriever  # noqa: E402
from app.rag.vectorstore import MilvusStore  # noqa: E402

CORPUS_DIR = REPO / "eval" / "corpus"
GOLDEN_PATH = REPO / "eval" / "golden.jsonl"
EVAL_DB = REPO / "backend" / "data" / "eval_milvus.db"
EVAL_COLLECTION = "eval_product_kb"
TOP_K = 10


def load_golden() -> list[dict]:
    rows = [json.loads(line) for line in GOLDEN_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    return rows


async def ingest_corpus(store: MilvusStore, embedding) -> list[tuple[int, str, int]]:
    """把 eval/corpus/ 下每个文件走真实 pipeline 灌入。返回 [(doc_id, filename, chunk_count)]。"""
    result: list[tuple[int, str, int]] = []
    files = sorted(CORPUS_DIR.glob("*.md"))
    for doc_id, path in enumerate(files, start=1):
        doc_type = path.suffix.lstrip(".")
        blocks = await asyncio.to_thread(parse, str(path), doc_type)
        chunks = build_chunks(blocks, doc_id, path.name, doc_type)
        vectors = await embedding.aembed_documents([c["text"] for c in chunks])
        for c, v in zip(chunks, vectors, strict=False):
            c["dense_vector"] = v
        count = await asyncio.to_thread(store.upsert_chunks, str(doc_id), chunks)
        result.append((doc_id, path.name, count))
    return result


def dcg(rels: list[int]) -> float:
    return sum(r / math.log2(i + 2) for i, r in enumerate(rels))


async def main() -> None:
    settings = get_settings()
    store = MilvusStore(str(EVAL_DB), EVAL_COLLECTION, settings.milvus_dim)
    embedding = get_embedding()
    retriever = Retriever(store, embedding, reranker=None)

    print("== 入库 eval/corpus ===")
    docs = await ingest_corpus(store, embedding)
    total_chunks = sum(c for _, _, c in docs)
    for doc_id, name, cnt in docs:
        print(f"  doc#{doc_id}  {name}  -> {cnt} chunks")
    print(f"  合计 {len(docs)} 文档 / {total_chunks} chunks\n")

    golden = load_golden()
    answerable = [g for g in golden if g["answerable"]]
    unanswerable = [g for g in golden if not g["answerable"]]

    # ---- 检索层指标 ----
    recall5 = recall10 = mrr = ndcg10 = 0.0
    hit10 = 0
    detail: list[tuple] = []

    for g in answerable:
        expected = set(g["expected_docs"])
        qvec = await embedding.aembed_query(g["question"])
        raw = await asyncio.to_thread(store.search, qvec, TOP_K)
        # 按 doc 去重（保留首次出现顺序）
        docs_seen: list[str] = []
        for h in raw:
            f = h["source_file"]
            if f not in docs_seen:
                docs_seen.append(f)

        rels = [1 if f in expected else 0 for f in docs_seen[:TOP_K]]
        # Recall@K（doc-level）
        r5 = sum(rels[:5]) / max(1, len(expected))
        r10 = sum(rels[:10]) / max(1, len(expected))
        recall5 += r5
        recall10 += r10
        # MRR
        first = next((i + 1 for i, r in enumerate(rels) if r == 1), None)
        if first:
            mrr += 1.0 / first
            hit10 += 1
        # NDCG@10
        idcg = dcg(sorted(rels, reverse=True))
        ndcg10 += (dcg(rels) / idcg) if idcg > 0 else 0.0

        detail.append((g["id"], g["question"], g["expected_docs"], docs_seen[:5], bool(r10 > 0)))

    n = len(answerable)
    print("== 检索层指标（doc-level，dense-only，top10）===")
    print(f"  可答问题数      : {n}")
    print(f"  Recall@5        : {recall5 / n:.3f}")
    print(f"  Recall@10       : {recall10 / n:.3f}   ← 语料仅 {total_chunks} chunk，top10 覆盖全量，无区分度")
    print(f"  Hit@10（命中率） : {hit10 / n:.3f}")
    print(f"  MRR（首个相关排名）: {mrr / n:.3f}   ← 排序质量，最可靠")
    print(f"  NDCG@10         : {ndcg10 / n:.3f}\n")

    # ---- 观察：off-topic 问题是否穿过门控（门控是相似度下限，不是拒答分类器） ----
    print(f"== 观察：off-topic 问题的门控分数（不可答问题 {len(unanswerable)} 个）===")
    for g in unanswerable:
        chunks = await retriever.retrieve(g["question"], top_k=6)
        if chunks:
            print(f"  {g['id']} {g['question']} -> 穿过门控，命中 {chunks[0].source_file} score={chunks[0].score:.3f}")
        else:
            print(f"  {g['id']} {g['question']} -> 门控拒答（返回空）")
    print("  注：门控仅拦截低相似度噪声（<0.35），不负责语义拒答；")
    print("      真正「不编造」由生成层双提示词保证，见 e2e.py 的不编造率指标。\n")

    # ---- 逐题明细 ----
    print("== 逐题明细（命中@10 / 检索到的前5个doc）===")
    for qid, q, exp, got, hit in detail:
        mark = "✓" if hit else "✗"
        print(f"  {mark} {qid} 期望={exp} 命中={hit} 检索top5={got}  |  {q}")
        if not hit:
            print(f"        ⚠ 未命中，实际top5={got}")

    print("\n说明：本地 Milvus Lite 仅支持 dense（BM25 混合检索需 Standalone），")
    print("      此表为 dense-only 基线；切 Standalone 后可用 hybrid 复跑对比。")


if __name__ == "__main__":
    asyncio.run(main())
