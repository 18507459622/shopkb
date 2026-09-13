"""功能级评测：多轮改写增益 + 歧义追问决策 + 引文清洗。

补的是 v1 评测的**覆盖盲区**：rewriter / clarifier / citation 三个功能都写了代码，
但没有任何量化数据——面试被问「多轮改写效果如何」时只能答「实现了」。

三个评测项：
1. **多轮改写（rewriter）**：对同一批多轮问题，比较「原问题检索」与「改写后检索」的命中率，
   得到 rewrite_gain（原本漏召回、改写后命中）与 rewrite_loss（改写帮了倒忙）。
   需要 embedding（--mock 可离线跑）。
2. **歧义追问（clarifier）**：纯规则 + 静态词典，完全离线、零成本，直接算决策准确率。
3. **引文清洗（citation）**：纯字符串处理，离线验证「越界引用被剔除、合法引用保留」。

跑法（在 backend/ 目录下）：
    cd backend && ../.venv/Scripts/python.exe ../eval/features.py
    cd backend && ../.venv/Scripts/python.exe ../eval/features.py --mock --db ../eval/eval_db_tmp.db
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "backend"))

from app.core.config import get_settings  # noqa: E402
from app.rag.citation import sanitize_citations  # noqa: E402
from app.rag.clarifier import detect_clarify  # noqa: E402
from app.rag.embeddings import get_embedding  # noqa: E402
from app.rag.ingestion import build_chunks  # noqa: E402
from app.rag.parser import parse  # noqa: E402
from app.rag.retriever import Retriever  # noqa: E402
from app.rag.rewriter import needs_rewrite, rewrite_query  # noqa: E402
from app.rag.vectorstore import make_store  # noqa: E402
from eval.fakes import build_fakes  # noqa: E402
from eval.gate import Gate, judge_gates, print_rows  # noqa: E402
from eval.metrics import fmt, rate  # noqa: E402

CORPUS_DIR = REPO / "eval" / "corpus"
MULTITURN_PATH = REPO / "eval" / "cases_multiturn.jsonl"
CLARIFY_PATH = REPO / "eval" / "cases_clarify.jsonl"
DEFAULT_EVAL_DB = REPO / "backend" / "data" / "eval_milvus.db"
EVAL_COLLECTION = "eval_product_kb"

GATES_FEATURES: list[Gate] = [
    Gate("clarify_f1", "歧义追问 F1", 0.90, ">=", "该追问就追问，不该追问别打扰"),
    Gate("rewrite_hit_rate", "改写后检索命中率", 0.85, ">=", "改写必须真的把证据找回来"),
    Gate("rewrite_loss_rate", "改写帮倒忙率", 0.05, "<=", "改写不能把原本能命中的搞丢"),
]
# 说明：门禁盯「改写后的结果」而不是「增益」——增益受原始命中率基数影响，
# 原始已经很准时增益天然接近 0，用它当红线会误伤。增益只作报告指标。


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


async def ingest_corpus(store: MilvusStore, embedding) -> int:  # type: ignore[no-untyped-def]
    total = 0
    for doc_id, path in enumerate(sorted(CORPUS_DIR.glob("*.md")), start=1):
        doc_type = path.suffix.lstrip(".")
        blocks = await asyncio.to_thread(parse, str(path), doc_type)
        chunks = build_chunks(blocks, doc_id, path.name, doc_type)
        vectors = await embedding.aembed_documents([c["text"] for c in chunks])
        for c, v in zip(chunks, vectors, strict=False):
            c["dense_vector"] = v
        total += await asyncio.to_thread(store.upsert_chunks, str(doc_id), chunks)
    return total


async def eval_multiturn(cases: list[dict], retriever: Retriever, llm) -> dict:  # type: ignore[no-untyped-def]
    """比较原问题 vs 改写问题的检索命中率。

    命中判定 = 期望文档命中 **且** 检索到的证据里出现该商品的独有参数
    （expect_keywords）。只用 doc-level 期望太粗：问「那续航呢？」本来就能召回
    「无线耳机.md」，但它拿到的可能是别的耳机的续航——那样改写就没体现出价值。
    """
    hit_raw = hit_rw = gain = loss = 0
    detail: list[str] = []

    def evidence_ok(chunks: list, case: dict) -> bool:
        expected = set(case["expected_docs"])
        if not expected <= {ch.source_file for ch in chunks}:
            return False
        blob = "\n".join(ch.text for ch in chunks)
        return all(k in blob for k in (case.get("expect_keywords") or []))

    for c in cases:
        raw_chunks = await retriever.retrieve(c["question"], top_k=6)
        ok_raw = evidence_ok(raw_chunks, c)

        history = c.get("history") or []
        if needs_rewrite(c["question"]) and history:
            query = await rewrite_query(history, c["question"])
        else:
            query = c["question"]
        rw_chunks = await retriever.retrieve(query, top_k=6)
        ok_rw = evidence_ok(rw_chunks, c)

        hit_raw += ok_raw
        hit_rw += ok_rw
        if not ok_raw and ok_rw:
            gain += 1
        if ok_raw and not ok_rw:
            loss += 1
        detail.append(
            f"  {c['id']} 原={'命中' if ok_raw else '漏'} 改写={'命中' if ok_rw else '漏'} | {c['question']}"
            + (f"\n       改写后: {query}" if query != c["question"] else "")
        )

    n = len(cases)
    print(f"== 多轮改写（{n} 条，命中=期望文档+独有参数都在证据里）==")
    print("\n".join(detail) if detail else "  （无用例，跳过）")
    return {
        "n": n,
        "raw_hit_rate": rate(hit_raw, n),
        "rewritten_hit_rate": rate(hit_rw, n),
        "gain": rate(gain, n),
        "loss_rate": rate(loss, n),
    }


def eval_clarify(cases: list[dict]) -> dict:
    """规则式澄清决策的查准/查全。"""
    tp = fp = fn = tn = 0
    detail: list[str] = []
    for c in cases:
        got = detect_clarify(c["question"], c.get("history") or [])
        predicted = got is not None
        expected = bool(c["expect_clarify"])
        if predicted and expected:
            tp += 1
        elif predicted and not expected:
            fp += 1
        elif not predicted and expected:
            fn += 1
        else:
            tn += 1
        ok = predicted == expected
        detail.append(
            f"  {'OK  ' if ok else 'FAIL'} {c['id']} 期望={expected} 实际={predicted}"
            + (f" 品类={got.category}" if got else "")
            + f" | {c['question']}"
        )
    prec = rate(tp, tp + fp)
    rec = rate(tp, tp + fn)
    f1 = rate(2 * prec * rec, prec + rec) if (prec + rec) else 0.0
    print(f"\n== 歧义追问（{len(cases)} 条，规则式，离线）==")
    print("\n".join(detail) if detail else "  （无用例，跳过）")
    print(f"  TP={tp} FP={fp} FN={fn} TN={tn}  P={fmt(prec)} R={fmt(rec)} F1={fmt(f1)}")
    return {"precision": prec, "recall": rec, "f1": f1, "n": len(cases)}


def eval_citation() -> dict:
    """引文清洗的离线夹具：越界编号必须被剔除，合法编号必须保留。"""
    fixtures = [
        ("价格是 4999 元 [1]，容量 6000mAh [2]。", 3, [1, 2], 0),
        ("价格 4999 元 [1]，另外 [7] 也提到过。", 2, [1], 1),
        ("没有任何引用。", 2, [], 0),
        ("重复引用 [1][1][1]。", 3, [1], 0),
        ("全越界 [9][10]。", 3, [], 2),
    ]
    fails = 0
    print(f"\n== 引文清洗（{len(fixtures)} 条夹具，离线）==")
    for i, (text, valid, want_used, want_removed) in enumerate(fixtures, 1):
        cleaned, used = sanitize_citations(text, valid)
        removed = text.count("[") - cleaned.count("[")
        ok = used == want_used and removed == want_removed
        fails += 0 if ok else 1
        print(f"  {'OK  ' if ok else 'FAIL'} 夹具{i} 保留={used}（期望{want_used}）剔除={removed}（期望{want_removed}）")
    return {"n": len(fixtures), "fails": fails}


async def main() -> int:
    ap = argparse.ArgumentParser(description="功能级评测：改写 / 澄清 / 引文")
    ap.add_argument("--mock", action="store_true", help="离线确定性替身")
    ap.add_argument("--skip-ingest", action="store_true", help="跳过重新入库")
    ap.add_argument("--db", type=str, default=str(DEFAULT_EVAL_DB), help="评测用 Milvus 库路径")
    ap.add_argument("--no-gate", action="store_true", help="不按门禁返回退出码")
    args = ap.parse_args()

    settings = get_settings()
    if args.mock:
        embedding, llm = build_fakes(dim=settings.milvus_dim)
        print("!! MOCK 模式：改写/检索指标无业务意义；澄清与引文为规则式，结论有效 !!")
        # rewriter.rewrite_query 内部自己调 get_llm()（模块级函数），会绕过依赖注入
        # 直接打真实 API —— mock 模式下必须把它替换掉，否则「离线」是假的。
        import app.rag.rewriter as _rw

        _rw.get_llm = lambda: llm  # type: ignore[assignment]
    else:
        embedding = get_embedding()
        from app.rag.llm import get_llm

        llm = get_llm()

    store = make_store(uri=args.db, collection=EVAL_COLLECTION, dim=settings.milvus_dim)
    retriever = Retriever(store, embedding, reranker=None)

    if not args.skip_ingest:
        print("== 入库 eval/corpus ==")
        total = await ingest_corpus(store, embedding)
        print(f"  合计 {total} chunks\n")

    mt_cases = load_jsonl(MULTITURN_PATH)
    cl_cases = load_jsonl(CLARIFY_PATH)

    mt = await eval_multiturn(mt_cases, retriever, llm) if mt_cases else {"n": 0}
    if mt.get("n"):
        print(f"  原问题命中率  : {fmt(mt['raw_hit_rate'])}")
        print(f"  改写后命中率  : {fmt(mt['rewritten_hit_rate'])}")
        print(f"  改写增益(净)  : {fmt(mt['gain'])}   （漏召回被救回的比例）")
        print(f"  帮倒忙率      : {fmt(mt['loss_rate'])}   （原本命中却被改写搞丢）")

    cl = eval_clarify(cl_cases) if cl_cases else {"n": 0}
    cite = eval_citation()

    values = {
        "clarify_f1": cl.get("f1") if cl.get("n") else None,
        "rewrite_hit_rate": mt.get("rewritten_hit_rate") if mt.get("n") else None,
        "rewrite_loss_rate": mt.get("loss_rate") if mt.get("n") else None,
    }
    if cite["fails"]:
        values["rewrite_loss_rate"] = 1.0  # 引文清洗失败即门禁失败（复用同一红灯）

    rows, code = judge_gates(values, GATES_FEATURES)
    print()
    print_rows(rows)
    return 0 if args.no_gate else code


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
