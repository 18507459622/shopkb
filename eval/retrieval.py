"""离线检索评测 v2：doc-level Recall@K / FullCoverage@K / MRR / NDCG@K + 门控观察 + 门禁。

相对 v1 的修正：
1. **指标有效性自检**：语料只有 10 chunks 时 Recall@10 恒等于 1（top10 覆盖全量），
   这种「没有区分度的指标」会把「检索很好」和「检索没被真正测到」混为一谈。
   v2 在打印指标时直接标注该指标本次是否具备区分度（top_k vs 语料规模）。
2. **新增 FullCoverage@K**：doc-level Recall 会把「多源问题只命中一半」平均掉
   （例如期望两篇文档，命中一篇也算 Recall=0.5），FullCoverage 要求期望文档全部命中。
3. **新增精确率 Precision@5**：只看召回会掩盖「塞了一堆无关文档」。
4. **门禁 + 退出码**：见 eval/gate.py，可直接进 CI。

跑法（在 backend/ 目录下）：
    cd backend && ../.venv/Scripts/python.exe ../eval/retrieval.py
    cd backend && ../.venv/Scripts/python.exe ../eval/retrieval.py --mock       # 离线（指标无业务意义）
    cd backend && ../.venv/Scripts/python.exe ../eval/retrieval.py --skip-ingest
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "backend"))

from app.core.config import get_settings  # noqa: E402
from app.rag.embeddings import get_embedding  # noqa: E402
from app.rag.ingestion import build_chunks  # noqa: E402
from app.rag.parser import parse  # noqa: E402
from app.rag.retriever import Retriever  # noqa: E402
from app.rag.vectorstore import make_store  # noqa: E402
from eval.fakes import build_fakes  # noqa: E402
from eval.gate import GATES_RETRIEVAL, judge_gates, print_rows  # noqa: E402
from eval.metrics import fmt, mean, rate  # noqa: E402

CORPUS_DIR = REPO / "eval" / "corpus"
GOLDEN_PATH = REPO / "eval" / "golden.jsonl"
DEFAULT_EVAL_DB = REPO / "backend" / "data" / "eval_milvus.db"
EVAL_COLLECTION = "eval_product_kb"


def load_golden() -> list[dict]:
    return [json.loads(line) for line in GOLDEN_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]


async def ingest_corpus(store: MilvusStore, embedding) -> list[tuple[int, str, int]]:  # type: ignore[no-untyped-def]
    """把 eval/corpus/ 下每个文件走真实 pipeline 灌入。返回 [(doc_id, filename, chunk_count)]。"""
    result: list[tuple[int, str, int]] = []
    for doc_id, path in enumerate(sorted(CORPUS_DIR.glob("*.md")), start=1):
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


async def main() -> int:
    ap = argparse.ArgumentParser(description="离线检索评测 v2")
    ap.add_argument("--mock", action="store_true", help="离线确定性替身（指标无业务意义）")
    ap.add_argument("--top-k", type=int, default=10, help="评估用的 K")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 条（0=全部）")
    ap.add_argument("--skip-ingest", action="store_true", help="跳过重新入库")
    ap.add_argument("--db", type=str, default=str(DEFAULT_EVAL_DB), help="评测用 Milvus 库路径")
    ap.add_argument("--no-gate", action="store_true", help="不按门禁返回退出码")
    args = ap.parse_args()
    top_k = args.top_k

    settings = get_settings()
    if args.mock:
        embedding, _ = build_fakes(dim=settings.milvus_dim)
        print("!! MOCK 模式：确定性假向量（等价词形重叠检索），指标无业务意义 !!")
    else:
        embedding = get_embedding()
    store = make_store(uri=args.db, collection=EVAL_COLLECTION, dim=settings.milvus_dim)
    retriever = Retriever(store, embedding, reranker=None)

    total_chunks = 0
    if not args.skip_ingest:
        print("== 入库 eval/corpus ==")
        docs = await ingest_corpus(store, embedding)
        total_chunks = sum(c for _, _, c in docs)
        for doc_id, name, cnt in docs:
            print(f"  doc#{doc_id}  {name}  -> {cnt} chunks")
        print(f"  合计 {len(docs)} 文档 / {total_chunks} chunks\n")
    else:
        print("== 跳过入库（--skip-ingest）==\n")

    golden = load_golden()
    answerable = [g for g in golden if g["answerable"]]
    unanswerable = [g for g in golden if not g["answerable"]]
    if args.limit:
        answerable, unanswerable = answerable[: args.limit], unanswerable[: args.limit]

    recall5 = recall10 = mrr = ndcg10 = full5 = prec5 = 0.0
    hit10 = 0
    detail: list[tuple] = []

    for g in answerable:
        expected = set(g["expected_docs"])
        qvec = await embedding.aembed_query(g["question"])
        raw = await asyncio.to_thread(store.search, qvec, top_k)
        docs_seen: list[str] = []
        for h in raw:
            f = h["source_file"]
            if f not in docs_seen:
                docs_seen.append(f)

        rels = [1 if f in expected else 0 for f in docs_seen[:top_k]]
        r5 = sum(rels[:5]) / max(1, len(expected))
        r10 = sum(rels[:10]) / max(1, len(expected))
        recall5 += r5
        recall10 += r10
        # 全覆盖：期望文档是否全部出现在 top5
        full5 += 1.0 if expected and expected <= set(docs_seen[:5]) else 0.0
        # 精确率：top5 里相关文档占比
        top5 = docs_seen[:5]
        prec5 += (sum(1 for f in top5 if f in expected) / len(top5)) if top5 else 0.0

        first = next((i + 1 for i, r in enumerate(rels) if r == 1), None)
        if first:
            mrr += 1.0 / first
            hit10 += 1
        idcg = dcg(sorted(rels, reverse=True))
        ndcg10 += (dcg(rels) / idcg) if idcg > 0 else 0.0

        detail.append((g["id"], g["question"], sorted(expected), docs_seen[:5], bool(r10 > 0)))

    n = len(answerable)
    values = {
        "recall_at_5": rate(recall5, n),
        "mrr": rate(mrr, n),
        "full_coverage_at_5": rate(full5, n),
    }

    print(f"== 检索层指标（doc-level，top{top_k}，可答 {n} 题）==")
    print(f"  Recall@5            : {fmt(values['recall_at_5'])}")
    print(f"  Recall@{top_k:<2}           : {fmt(rate(recall10, n))}")
    print(f"  FullCoverage@5      : {fmt(values['full_coverage_at_5'])}   ← 多源问题需全部命中")
    print(f"  Precision@5         : {fmt(rate(prec5, n))}   ← 防「靠塞无关文档刷召回」")
    print(f"  Hit@10（命中率）     : {fmt(rate(hit10, n))}")
    print(f"  MRR（首个相关排名）  : {fmt(values['mrr'])}   ← 排序质量，语料小时最可靠")
    print(f"  NDCG@{top_k:<2}          : {fmt(rate(ndcg10, n))}")

    # ---- 指标有效性自检（v2 新增）----
    print("\n== 指标有效性自检 ==")
    if total_chunks:
        if top_k >= total_chunks:
            print(f"  [!] top_k({top_k}) >= 语料规模({total_chunks} chunks)：")
            print(f"      Recall@{top_k} 恒等于 1（检索全量），**该指标本次没有区分度**，")
            print("      请只看 MRR / FullCoverage@5，或扩充 eval/corpus/ 后再评。")
        else:
            print(f"  [ok] top_k({top_k}) < 语料规模({total_chunks} chunks)：Recall 指标具备区分度。")
    else:
        print("  [--] 本次跳过了入库，未获得语料规模，无法自检（建议不加 --skip-ingest 跑一次）。")

    # ---- off-topic 门控观察 ----
    print(f"\n== 观察：off-topic 问题的门控分数（不可答 {len(unanswerable)} 题）==")
    un_scores: list[float] = []
    for g in unanswerable:
        chunks = await retriever.retrieve(g["question"], top_k=6)
        if chunks:
            un_scores.append(chunks[0].score)
            print(f"  {g['id']} 穿过门控 -> {chunks[0].source_file} score={chunks[0].score:.3f}")
        else:
            print(f"  {g['id']} 门控拒答（返回空）")
    if un_scores:
        print(f"  不可答问题最高相似度均值：{fmt(mean(un_scores))}（这些分数说明门控挡不住语义无关）")
    print("  注：门控只是相似度下限，不负责语义拒答；「不编造」由生成层双提示词保证（见 e2e.py）。")

    print("\n== 逐题明细（命中@10 / 检索到的前5个doc）==")
    for qid, q, exp, got, hit in detail:
        print(f"  {'OK  ' if hit else 'FAIL'} {qid} 期望={exp} 检索top5={got}  |  {q}")

    print("\n说明：本地 Milvus Lite 仅支持 dense（BM25 混合检索需 Standalone），此表为 dense-only 基线；")
    print("      阈值依据见 eval/sweep.py 的扫描结果（eval/SWEEP.md）。")

    rows, code = judge_gates(values, GATES_RETRIEVAL)
    print()
    print_rows(rows)
    return 0 if args.no_gate else code


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
