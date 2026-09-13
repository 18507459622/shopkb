"""端到端评测 v2：忠实度 + 数字规则校验 + 引文准确率 + 不编造率 + 延迟 + 失败归因 + 门禁。

相对 v1 的四处修正（每一条都有明确动机）：
1. **judge 解析失败 → fail-closed**：v1 写的是 `grounded = True if grounded is None else grounded`，
   即判分器返回非法 JSON 时白送一个「通过」——指标被系统性高估且无人察觉。
   v2 把它单列为 judge_error，**从分子分母同时剔除**，并用 judge_coverage 门禁约束覆盖率：
   「判分器坏了」必须表现为指标不可信，而不是虚假的好成绩。
2. **新增确定性指标**（不依赖 LLM）：数字命中率 numeric_recall、
   引文准确率 citation_precision。用来交叉验证 judge：judge 说忠实但数字一个没命中 → 人工复核。
3. **失败归因**：每道题给出 failure_type，失败可回流到具体环节而不是一句「没通过」。
4. **门禁 + 退出码**：跑完按 gate.py 的阈值判定，失败即 exit 1，可直接进 CI。

跑法（在 backend/ 目录下，保证读到 ../.env 的 API Key）：
    cd backend && ../.venv/Scripts/python.exe ../eval/e2e.py                  # 真实 API
    cd backend && ../.venv/Scripts/python.exe ../eval/e2e.py --mock           # 离线确定性（指标无业务意义）
    cd backend && ../.venv/Scripts/python.exe ../eval/e2e.py --mock --mock-judge-garbage
        ↑ 故障注入：验证「判分器失效 → 门禁变红」，即上面第 1 条的回归测试
    cd backend && ../.venv/Scripts/python.exe ../eval/e2e.py --limit 4 --top-k 6  # 小样本快跑
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))  # 让 `import eval.*` 可用
sys.path.insert(0, str(REPO / "backend"))  # 让 `import app.*` 可用

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
from app.rag.vectorstore import make_store  # noqa: E402
from eval.fakes import build_fakes  # noqa: E402
from eval.gate import GATES_E2E, exit_code, judge_gates, print_rows  # noqa: E402
from eval.metrics import (  # noqa: E402
    citation_stats,
    fmt,
    mean,
    numeric_recall,
    percentile,
    rate,
)

CORPUS_DIR = REPO / "eval" / "corpus"
GOLDEN_PATH = REPO / "eval" / "golden.jsonl"
DEFAULT_EVAL_DB = REPO / "backend" / "data" / "eval_milvus.db"
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
    """解析判分器返回的 JSON 布尔值；解析失败返回 None（**不再默认判通过**）。"""
    m = re.search(r"\{.*?\}", text, re.DOTALL)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except Exception:
        return None
    val = obj.get(key)
    return val if isinstance(val, bool) else None


def load_golden(path: Path = GOLDEN_PATH) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@dataclass
class CaseResult:
    """单题结果：既存指标，也存失败归因。"""

    cid: str
    question: str
    answerable: bool
    expected: list[str]
    retrieved: list[str] = field(default_factory=list)
    answer: str = ""
    grounded: bool | None = None
    fabricated: bool | None = None
    num_recall: float | None = None
    cite_precision: float | None = None
    cite_fabricated: int = 0
    latency_s: float = 0.0
    failure: str | None = None


def classify_answerable(c: CaseResult) -> str | None:
    """可答问题的失败归因（顺序即优先级）。"""
    if not c.retrieved:
        return "retrieval_miss"  # 检索为空：链路根本没拿到证据
    if c.expected and not set(c.expected) <= set(c.retrieved):
        return "retrieval_noise"  # 命中了别处的文档：上下文是错的
    if c.grounded is None:
        return "judge_error"  # 判分器失效：结论不可用（不算通过也不算失败）
    if c.grounded is False:
        return "generation_hallucination"  # 有正确上下文却说了没依据的话
    if c.num_recall is not None and c.num_recall < 1.0:
        return "answer_incomplete"  # 忠实但漏了 gold 里的关键数字
    return None


async def ingest_corpus(store: MilvusStore, embedding) -> list[tuple[int, str, int]]:  # type: ignore[no-untyped-def]
    """把 eval/corpus/ 下每个文件走真实 pipeline 灌入独立 collection。"""
    out: list[tuple[int, str, int]] = []
    for doc_id, path in enumerate(sorted(CORPUS_DIR.glob("*.md")), start=1):
        doc_type = path.suffix.lstrip(".")
        blocks = await asyncio.to_thread(parse, str(path), doc_type)
        chunks = build_chunks(blocks, doc_id, path.name, doc_type)
        vectors = await embedding.aembed_documents([c["text"] for c in chunks])
        for c, v in zip(chunks, vectors, strict=False):
            c["dense_vector"] = v
        count = await asyncio.to_thread(store.upsert_chunks, str(doc_id), chunks)
        out.append((doc_id, path.name, count))
    return out


async def run_case_answerable(g: dict, retriever: Retriever, llm, top_k: int) -> CaseResult:  # type: ignore[no-untyped-def]
    c = CaseResult(cid=g["id"], question=g["question"], answerable=True, expected=list(g.get("expected_docs") or []))
    t0 = time.perf_counter()
    chunks = await retriever.retrieve(g["question"], top_k=top_k)
    if not chunks:
        c.latency_s = time.perf_counter() - t0
        c.failure = classify_answerable(c)
        return c

    c.retrieved = list(dict.fromkeys(ch.source_file for ch in chunks))
    context = build_context(chunks)
    ans = await llm.ainvoke(
        [
            SystemMessage(content=RAG_SYSTEM_PROMPT),
            HumanMessage(content=f"【知识库内容】\n{context}\n\n用户问题：{g['question']}"),
        ]
    )
    c.latency_s = time.perf_counter() - t0
    raw = ans.content if isinstance(ans.content, str) else str(ans.content)
    c.answer = raw

    # 确定性指标（不依赖 judge）
    c.num_recall = numeric_recall(g.get("gold_answer", ""), raw)
    stats = citation_stats(raw, len(chunks))
    c.cite_precision = stats["precision"]
    c.cite_fabricated = stats["invalid_count"]  # 生产链路会清洗，这里记录清洗前的越界数量

    judge = await llm.ainvoke(
        [
            SystemMessage(content="你是严格的事实核对员。"),
            HumanMessage(content=FAITHFULNESS_PROMPT.format(context=context, answer=raw)),
        ]
    )
    c.grounded = parse_json_bool(str(judge.content), "grounded")
    c.failure = classify_answerable(c)
    return c


async def run_case_unanswerable(g: dict, retriever: Retriever, llm, top_k: int) -> CaseResult:  # type: ignore[no-untyped-def]
    c = CaseResult(cid=g["id"], question=g["question"], answerable=False, expected=[])
    chunks = await retriever.retrieve(g["question"], top_k=top_k)
    if chunks:
        c.retrieved = list(dict.fromkeys(ch.source_file for ch in chunks))
        context = build_context(chunks)
        msgs = [
            SystemMessage(content=RAG_SYSTEM_PROMPT),
            HumanMessage(content=f"【知识库内容】\n{context}\n\n用户问题：{g['question']}"),
        ]
    else:
        context = ""
        msgs = [SystemMessage(content=OFFTOPIC_SYSTEM_PROMPT), HumanMessage(content=g["question"])]

    ans = await llm.ainvoke(msgs)
    raw = ans.content if isinstance(ans.content, str) else str(ans.content)
    c.answer = raw
    c.cite_precision = citation_stats(raw, max(1, len(chunks)))["precision"]

    judge = await llm.ainvoke(
        [
            SystemMessage(content="你是严格的事实核对员。"),
            HumanMessage(content=NO_FABRICATION_PROMPT.format(context=context or "（无检索结果）", answer=raw)),
        ]
    )
    c.fabricated = parse_json_bool(str(judge.content), "fabricated")
    if c.fabricated is None:
        c.failure = "judge_error"
    elif c.fabricated:
        c.failure = "fabrication"
    return c


def print_failure_distribution(results: list[CaseResult]) -> None:
    dist = Counter(c.failure for c in results if c.failure)
    print("== 失败归因分布 ==")
    if not dist:
        print("  无失败")
    for name, cnt in dist.most_common():
        print(f"  {name:<24} {cnt}")
    print("  说明：retrieval_miss/noise → 改检索；generation_hallucination → 改提示词；")
    print("        answer_incomplete → 检查是否漏答；judge_error → 判分器本身要修。")


async def main() -> int:
    ap = argparse.ArgumentParser(description="端到端 RAG 评测 v2")
    ap.add_argument("--mock", action="store_true", help="离线确定性替身（指标无业务意义，只验证脚手架）")
    ap.add_argument("--mock-judge-garbage", action="store_true", help="故障注入：让判分器返回非法 JSON")
    ap.add_argument("--limit", type=int, default=0, help="每组只跑前 N 条（0=全部）")
    ap.add_argument("--top-k", type=int, default=6, help="检索条数")
    ap.add_argument("--skip-ingest", action="store_true", help="跳过重新入库（复用已有 collection）")
    ap.add_argument("--db", type=str, default=str(DEFAULT_EVAL_DB), help="评测用 Milvus 库路径")
    ap.add_argument("--no-gate", action="store_true", help="不按门禁返回退出码（探索性运行）")
    args = ap.parse_args()

    mock = args.mock or args.mock_judge_garbage
    settings = get_settings()
    if mock:
        embedding, llm = build_fakes(dim=settings.milvus_dim, broken_judge=args.mock_judge_garbage)
        print("!! MOCK 模式：使用确定性替身，指标无业务意义，仅验证评测脚手架与门禁逻辑 !!")
    else:
        embedding, llm = get_embedding(), get_llm()

    store = make_store(uri=args.db, collection=EVAL_COLLECTION, dim=settings.milvus_dim)
    retriever = Retriever(store, embedding, reranker=None)

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

    print(f"== 可答问题（{len(answerable)} 条）==")
    ans_results: list[CaseResult] = []
    for g in answerable:
        c = await run_case_answerable(g, retriever, llm, args.top_k)
        ans_results.append(c)
        mark = "OK  " if c.failure is None else "FAIL"
        print(f"  [{mark}] {c.cid} 忠实={c.grounded} 数字={fmt(c.num_recall, 2)} 失败={c.failure or '-'}")
        print(f"         答: {c.answer.strip()[:70]}")

    print(f"\n== 不可答问题（{len(unanswerable)} 条）==")
    un_results: list[CaseResult] = []
    for g in unanswerable:
        c = await run_case_unanswerable(g, retriever, llm, args.top_k)
        un_results.append(c)
        mark = "OK  " if c.failure is None else "FAIL"
        print(f"  [{mark}] {c.cid} 编造={c.fabricated} 失败={c.failure or '-'}")
        print(f"         答: {c.answer.strip()[:70]}")

    # ---------- 指标 ----------
    all_results = ans_results + un_results
    n_ans, n_un = len(ans_results), len(un_results)

    miss = sum(1 for c in ans_results if c.failure == "retrieval_miss")
    noise = sum(1 for c in ans_results if c.failure == "retrieval_noise")
    judged_ok = [c for c in ans_results if c.grounded is not None]
    faithful = sum(1 for c in judged_ok if c.grounded)
    num_vals = [c.num_recall for c in ans_results if c.num_recall is not None]
    cite_vals = [c.cite_precision for c in all_results if c.cite_precision is not None]
    lat = [c.latency_s for c in ans_results if c.latency_s > 0]

    un_judged = [c for c in un_results if c.fabricated is not None]
    no_fab = sum(1 for c in un_judged if not c.fabricated)

    values = {
        "faithfulness": rate(faithful, len(judged_ok)) if judged_ok else None,
        "numeric_recall": mean([v for v in num_vals if v is not None]),
        "citation_precision": mean([v for v in cite_vals if v is not None]),
        "judge_coverage": rate(len(judged_ok), n_ans),
        "no_fabrication": rate(no_fab, len(un_judged)) if un_judged else None,
        "judge_coverage_unanswerable": rate(len(un_judged), n_un),
    }

    print("\n== 端到端指标 ==")
    print(f"  忠实度 (grounded/可判分数)   : {fmt(values['faithfulness'])}  （{faithful}/{len(judged_ok)}）")
    print(f"  数字命中率 (确定性/规则)     : {fmt(values['numeric_recall'])}  （{len(num_vals)} 题适用）")
    print(f"  引文准确率 (确定性/规则)     : {fmt(values['citation_precision'])}  （越界引用 {sum(c.cite_fabricated for c in all_results)} 处）")
    print(f"  判分覆盖率 (可答)            : {fmt(values['judge_coverage'])}  （{len(judged_ok)}/{n_ans}，判分器失效会拉低它）")
    print(f"  漏召回率                     : {fmt(rate(miss, n_ans))}  （{miss}/{n_ans}）")
    print(f"  检索噪声率 (命中错误文档)     : {fmt(rate(noise, n_ans))}  （{noise}/{n_ans}）")
    print(f"  不可答不编造率               : {fmt(values['no_fabrication'])}  （{no_fab}/{len(un_judged)}）")
    if lat:
        print(f"  生成延迟                     : P50 = {fmt(percentile(lat, 0.5), 2)}s  P95 = {fmt(percentile(lat, 0.95), 2)}s")

    print()
    print_failure_distribution(all_results)

    rows, code = judge_gates(values, GATES_E2E)
    print()
    print_rows(rows)
    if mock:
        print("  （MOCK 模式：以上判定只证明评测脚手架与门禁在工作）")
        print("  （忠实度/数字命中率由假模型决定，数值本身无意义；真实数字请去掉 --mock 重跑）")
    return 0 if args.no_gate else code


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
