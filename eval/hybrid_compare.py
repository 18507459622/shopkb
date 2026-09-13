"""dense vs hybrid（dense + BM25 RRF）检索对比实验 —— 需要支持 BM25 的 Milvus。

为什么需要这个脚本：
    README 与 DESIGN 都把「混合检索（dense + BM25 稀疏向量 + RRF 融合）」写成卖点，
    但本地 Milvus Lite **不支持 sparse/BM25**，所以这条能力**从未被实测**——
    属于典型的"宣称 vs 实测"缺口。本脚本把同一批金标问题在两种检索模式下各跑一遍，
    输出 Recall@5 / MRR / NDCG 对比，把卖点变成数据。

它同时会验证一件容易被忽略的事：
    混合检索返回的是 **RRF 分数**（≈0.01–0.03），与 dense 的相似度（0–1）**不同量纲**。
    若把 GATE_THRESHOLD=0.35 无差别套到 RRF 分数上，混合检索会永远返回空 ——
    这个缺陷在本地 Lite 下不会暴露，只有真正跑起来才现形（见 docs/BAD_CASES.md #15）。

前置条件：
    1. 一个支持 BM25 Function 的 Milvus：**Zilliz Cloud 免费实例** 或 Docker Milvus Standalone
    2. `.env` 配置（注意：**不要**用 `MILVUS_TOKEN` 变量名，pymilvus 会直接读它，见 BAD_CASES #2）：
           VECTORSTORE_URI=https://in03-xxxx.serverless.<region>.cloud.zilliz.com
           VECTORSTORE_TOKEN=<user>:<password>
    3. Embedding key 可用（语料要嵌入远程 collection）

跑法（在 backend/ 目录下）：
    ../.venv/Scripts/python.exe ../eval/hybrid_compare.py                # 用 .env 配置
    ../.venv/Scripts/python.exe ../eval/hybrid_compare.py --reingest     # 先清空重建 collection
    ../.venv/Scripts/python.exe ../eval/hybrid_compare.py --limit 10     # 小样本快跑
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "backend"))

from app.core.config import get_settings  # noqa: E402
from app.rag.embeddings import get_embedding  # noqa: E402
from app.rag.ingestion import build_chunks  # noqa: E402
from app.rag.parser import parse  # noqa: E402
from app.rag.retriever import GATE_THRESHOLD, MODE_DENSE, MODE_HYBRID, Retriever  # noqa: E402
from app.rag.vectorstore import make_store  # noqa: E402
from eval.gate import Gate, exit_code, judge_gates, print_rows  # noqa: E402
from eval.metrics import doc_ranking_metrics, fmt, mean  # noqa: E402

CORPUS_DIR = REPO / "eval" / "corpus"
GOLDEN_PATH = REPO / "eval" / "golden.jsonl"
DEFAULT_REPORT = REPO / "eval" / "HYBRID.md"
EVAL_COLLECTION = "eval_product_kb"
REQUIRED_FIELD = "sparse_vector"

# 可在 main() 里被 --corpus / --golden 覆盖（用于跑合成高干扰语料）
_corpus_dir = CORPUS_DIR
_golden_path = GOLDEN_PATH

# 混合检索的价值底线：不能比 dense 明显更差（否则说明融合/门控有问题）
GATES_HYBRID: list[Gate] = [
    Gate("mrr_delta", "MRR 相对 dense 的变化", -0.05, ">=", "混合检索不该劣化排序"),
    Gate("recall5_delta", "Recall@5 相对 dense 的变化", -0.05, ">=", "混合检索不该丢召回"),
]


def load_golden(path: Path | None = None) -> list[dict]:
    src = path or _golden_path
    return [json.loads(line) for line in src.read_text(encoding="utf-8").splitlines() if line.strip()]


async def ingest_corpus(store, embedding) -> int:  # type: ignore[no-untyped-def]
    total = 0
    for doc_id, path in enumerate(sorted(_corpus_dir.glob("*.md")), start=1):
        doc_type = path.suffix.lstrip(".")
        blocks = await asyncio.to_thread(parse, str(path), doc_type)
        chunks = build_chunks(blocks, doc_id, path.name, doc_type)
        vectors = await embedding.aembed_documents([c["text"] for c in chunks])
        for c, v in zip(chunks, vectors, strict=False):
            c["dense_vector"] = v
        total += await asyncio.to_thread(store.upsert_chunks, str(doc_id), chunks)
    return total


async def eval_mode(retriever: Retriever, golden: list[dict], mode: str, top_k: int) -> dict:
    """在指定检索模式下跑一遍可答问题，返回平均指标。"""
    acc = {"recall5": [], "recallk": [], "full5": [], "prec5": [], "rr": [], "ndcg": []}
    detail: list[tuple[str, list[str], bool]] = []
    for g in golden:
        chunks = await retriever.retrieve(g["question"], top_k=top_k, mode=mode)
        docs_seen: list[str] = []
        for ch in chunks:
            if ch.source_file not in docs_seen:
                docs_seen.append(ch.source_file)
        m = doc_ranking_metrics(docs_seen, set(g["expected_docs"]), k=top_k)
        for key in acc:
            acc[key].append(m[key])
        detail.append((g["id"], docs_seen[:5], bool(m["recallk"] > 0)))
    out = {k: (mean(v) or 0.0) for k, v in acc.items()}
    out["_detail"] = detail  # type: ignore[assignment]
    return out


async def main() -> int:
    ap = argparse.ArgumentParser(description="dense vs hybrid 检索对比")
    ap.add_argument("--uri", type=str, default="", help="远程 Milvus URI（默认取 .env 的 VECTORSTORE_URI）")
    ap.add_argument("--token", type=str, default="", help="鉴权 token（默认取 .env 的 VECTORSTORE_TOKEN）")
    ap.add_argument("--collection", type=str, default=EVAL_COLLECTION)
    ap.add_argument("--reingest", action="store_true", help="先删除 collection 再重灌（换集群时用）")
    ap.add_argument("--skip-ingest", action="store_true", help="跳过入库（复用已有 collection）")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 条（0=全部）")
    ap.add_argument("--top-k", type=int, default=6, help="检索条数")
    ap.add_argument("--report", type=str, default=str(DEFAULT_REPORT))
    ap.add_argument("--corpus", type=str, default="", help="语料目录（默认 eval/corpus）")
    ap.add_argument("--golden", type=str, default="", help="金标文件（默认 eval/golden.jsonl）")
    args = ap.parse_args()

    global _corpus_dir, _golden_path
    if args.corpus:
        _corpus_dir = Path(args.corpus)
    if args.golden:
        _golden_path = Path(args.golden)

    settings = get_settings()
    uri = args.uri or settings.vectorstore_uri
    token = args.token or settings.vectorstore_token

    # ---- 前置校验（失败要给出可执行的提示，而不是抛栈） ----
    if not uri.startswith(("http://", "https://")):
        print("[X] 当前 VECTORSTORE_URI 不是远程地址，混合检索需要 Milvus Standalone 或 Zilliz Cloud。")
        print(f"    当前值：{uri}")
        print("    请在 .env 里设置（注意不要用 MILVUS_TOKEN 变量名）：")
        print("        VECTORSTORE_URI=https://in03-xxxx.serverless.<region>.cloud.zilliz.com")
        print("        VECTORSTORE_TOKEN=<user>:<password>")
        return 2
    if not token:
        print("[X] 远程集群需要鉴权 token，但 VECTORSTORE_TOKEN 为空。")
        print("    请在 Zilliz Cloud 控制台复制「API Key / Token」，格式通常为 <user>:<password>。")
        return 2

    print(f"== 连接远程 Milvus ==\n  URI: {uri}\n  Collection: {args.collection}")
    store = make_store(uri=uri, collection=args.collection, dim=settings.milvus_dim, token=token)
    if args.reingest:
        print("  --reingest：删除已存在的 collection")
        await asyncio.to_thread(store.drop_collection)

    embedding = get_embedding()
    if not args.skip_ingest:
        print("\n== 入库 eval/corpus ==")
        total = await ingest_corpus(store, embedding)
        print(f"  合计 {total} chunks")

    fields = await asyncio.to_thread(store.describe_fields)
    print(f"\n== 能力自检 ==\n  collection 字段: {fields}")
    if REQUIRED_FIELD not in fields:
        print(f"  [X] 缺少 {REQUIRED_FIELD} 字段 → 该集群没建成 BM25 稀疏索引，无法做混合检索对比。")
        print("      可能原因：集群 Milvus 版本 < 2.5、或创建 collection 时未启用 Function。")
        return 2
    print(f"  [ok] BM25 稀疏字段已建（{REQUIRED_FIELD}），可以做混合检索对比")

    golden = [g for g in load_golden() if g["answerable"]]
    if args.limit:
        golden = golden[: args.limit]

    retriever = Retriever(store, embedding, reranker=None)
    print(f"\n== 跑两种检索模式（可答 {len(golden)} 题，top_k={args.top_k}）==")
    print(f"  dense 模式套门控（GATE_THRESHOLD={GATE_THRESHOLD}）；hybrid 模式不套门控（RRF 分数与相似度不同量纲）")
    dense = await eval_mode(retriever, golden, MODE_DENSE, args.top_k)
    hybrid = await eval_mode(retriever, golden, MODE_HYBRID, args.top_k)

    rows = [
        ("Recall@5", dense["recall5"], hybrid["recall5"]),
        ("Recall@K", dense["recallk"], hybrid["recallk"]),
        ("FullCoverage@5", dense["full5"], hybrid["full5"]),
        ("Precision@5", dense["prec5"], hybrid["prec5"]),
        ("MRR", dense["rr"], hybrid["rr"]),
        ("NDCG@K", dense["ndcg"], hybrid["ndcg"]),
    ]
    print(f"\n== 对比结果 ==\n  {'指标':<16}{'dense':>10}{'hybrid':>10}{'变化':>10}")
    for name, d, h in rows:
        print(f"  {name:<16}{d:>10.3f}{h:>10.3f}{h - d:>+10.3f}")

    mrr_delta = hybrid["rr"] - dense["rr"]
    r5_delta = hybrid["recall5"] - dense["recall5"]
    # 天花板效应检测：两个模式在所有主指标上都触顶时，评测集其实**无法区分**优劣。
    # 这时候说"两者相当"是误导 —— 准确说法是"这批评测集区分不出来"。
    ceiling = (
        dense["recall5"] >= 0.999 and hybrid["recall5"] >= 0.999
        and dense["rr"] >= 0.999 and hybrid["rr"] >= 0.999
    )
    if ceiling:
        verdict = "两者均触顶（当前评测集无法区分优劣）"
    elif mrr_delta > 0.005:
        verdict = "混合检索更优"
    elif mrr_delta > -0.005:
        verdict = "两者相当"
    else:
        verdict = "混合检索更差（需排查融合权重/门控）"
    print(f"\n== 结论 ==\n  MRR 变化 {mrr_delta:+.3f}，Recall@5 变化 {r5_delta:+.3f} → {verdict}")
    if ceiling:
        print("  说明：两项主指标双双触顶 = ceiling effect，说明评测集难度不足，")
        print("        不能说'混合检索没用'；要看差异需扩语料/加难查询（见报告 HYBRID.md 末节）。")

    # ---- 写报告 ----
    report = Path(args.report)
    lines = [
        "# dense vs hybrid 检索对比（自动生成）",
        "",
        f"> 生成日期：{date.today().isoformat()}　脚本：`eval/hybrid_compare.py`",
        f"> 集群：`{uri}`　Collection：`{args.collection}`　可答问题：{len(golden)} 条　top_k={args.top_k}",
        "",
        "| 指标 | dense-only | dense+BM25(RRF) | 变化 |",
        "|---|---|---|---|",
    ]
    for name, d, h in rows:
        lines.append(f"| {name} | {d:.3f} | {h:.3f} | {h - d:+.3f} |")
    lines += [
        "",
        "## 结论",
        "",
        f"- MRR 变化 **{mrr_delta:+.3f}**，Recall@5 变化 **{r5_delta:+.3f}** → **{verdict}**",
        f"- 口径说明：dense 模式套用相似度门控（`GATE_THRESHOLD={GATE_THRESHOLD}`）；"
        "hybrid 模式**不套门控** —— RRF 分数（≈0.01–0.03）与余弦相似度不同量纲，套用会让结果全空。",
        "",
    ]
    if ceiling:
        lines += [
            "### ⚠ 本次结果触顶（评测集无区分度）",
            "",
            "两个模式在 Recall@5 与 MRR 上**双双达到 1.000**，说明这批评测集**无法区分**两种检索策略 —— "
            "这是 **ceiling effect（天花板效应）**，不能解读为「混合检索没有价值」。",
            "",
        ]
    lines += [
        "## 诚实边界",
        "",
        f"- 语料仅 {len(list(_corpus_dir.glob('*.md')))} 篇文档 / 20 chunks，且问题以单事实、单文档为主，"
        "两种检索都容易命中 —— 指标触顶是评测集难度不足，不是检索质量的上限。",
        "- 本实验只评检索层（doc-level Recall/MRR/NDCG），未评端到端生成质量。",
        "- Zilliz Cloud 的索引参数与自建 Standalone 可能有差异，结论不能直接外推到所有部署形态。",
        "",
        "## 下一步：如何做出有区分度的对比",
        "",
        "要让实验真正分辨出 dense 与 hybrid 的差异，需要提高评测难度（按性价比排序）：",
        "",
        "1. **扩语料**：20 chunks → 100+ chunks。同一品类下多个相似 SKU 互相干扰时，"
        "词面精确匹配的价值才体现出来。",
        "2. **加「型号/专有名词密集」查询**：如「星云 Note 5 Pro 和星辰 X1 Pro 谁的快充功率更高」"
        "—— BM25 对型号、参数名这类稀有词更敏感。",
        "3. **加「改写/别名/无空格」查询**：如「X1Pro」「星云note5」"
        "—— 这类查询 dense 通常更稳，可反过来界定 hybrid 的边界。",
        "4. **改 chunk 级评测**：doc-level 指标在文档数少时容易触顶，chunk 级更能分辨排序质量。",
        "5. **加干扰文档**：引入同品牌相似型号、过期价格表等，制造真实的检索噪声。",
    ]
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n  报告已写入：{report}")

    values = {"mrr_delta": mrr_delta, "recall5_delta": r5_delta}
    gate_rows, code = judge_gates(values, GATES_HYBRID)
    print()
    print_rows(gate_rows)
    return exit_code(gate_rows)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
