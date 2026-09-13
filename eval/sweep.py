"""门控阈值扫描：把 GATE_THRESHOLD=0.35 从「魔数」变成「有数据的决策」。

背景（v1 的问题）：
    retriever.py 里 `GATE_THRESHOLD = 0.35` 是硬编码常量，从未被实验验证过。
    RESULTS.md 里那句「off-topic 问题分数 0.52–0.78 都会穿过门控」其实已经是
    「这个阈值挡不住语义无关」的定性证据——但缺一张定量的曲线图。

本脚本做的事：
    1. 对每个金标问题取**门控前**的 top1 相似度；
    2. 扫描一组阈值 t，统计两个此消彼长的量：
         - 可答问题的保留率（retention）：t 越高，越多可答问题被误挡 → 漏召回
         - 不可答问题的拦截率（blocked）：t 越高，越多无关问题被挡住
    3. 检查是否存在「完美分离阈值」（可答全保留 且 不可答全拦截）；
    4. 输出可复现的表格 + 结论，写进 eval/SWEEP.md。

结论通常是：**不存在完美分离阈值** —— 因为门控是「相似度下限」，
而语义无关/不可答是「话题匹配」问题，两者不是同一个可分量。
这正好给生产设计提供了依据：门控负责挡低相似噪声，语义拒答交给生成层提示词。

跑法（在 backend/ 目录下）：
    cd backend && ../.venv/Scripts/python.exe ../eval/sweep.py
    cd backend && ../.venv/Scripts/python.exe ../eval/sweep.py --mock --report ../eval/SWEEP_mock.md
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
from app.rag.retriever import GATE_THRESHOLD  # noqa: E402
from app.rag.vectorstore import make_store  # noqa: E402
from eval.fakes import build_fakes  # noqa: E402
from eval.gate import Gate, judge_gates, print_rows  # noqa: E402
from eval.metrics import fmt, rate  # noqa: E402

CORPUS_DIR = REPO / "eval" / "corpus"
GOLDEN_PATH = REPO / "eval" / "golden.jsonl"
DEFAULT_EVAL_DB = REPO / "backend" / "data" / "eval_milvus.db"
DEFAULT_REPORT = REPO / "eval" / "SWEEP.md"
EVAL_COLLECTION = "eval_product_kb"

# 「当前阈值不能误挡超过 5% 的可答问题」——这是阈值真正该守住的底线。
# 注意：不把「存在完美分离阈值」写成门禁 —— 重叠是预期结论，不是缺陷。
GATES_SWEEP: list[Gate] = [
    Gate("retention_at_current", "当前阈值下可答保留率", 0.95, ">=", "门控别伤召回"),
]


def load_golden() -> list[dict]:
    return [json.loads(line) for line in GOLDEN_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]


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


async def top1_scores(golden: list[dict], store: MilvusStore, embedding) -> dict[str, float]:  # type: ignore[no-untyped-def]
    """门控前的 top1 相似度（每条问题一个分）。"""
    out: dict[str, float] = {}
    for g in golden:
        qvec = await embedding.aembed_query(g["question"])
        hits = await asyncio.to_thread(store.search, qvec, 1)
        out[g["id"]] = float(hits[0]["score"]) if hits else 0.0
    return out


def sweep_table(
    ans_scores: list[float], un_scores: list[float], thresholds: list[float]
) -> list[tuple[float, float, float]]:
    """返回 [(阈值, 可答保留率, 不可答拦截率)]。"""
    rows = []
    for t in thresholds:
        keep = rate(sum(1 for s in ans_scores if s >= t), len(ans_scores))
        block = rate(sum(1 for s in un_scores if s < t), len(un_scores))
        rows.append((t, keep, block))
    return rows


async def main() -> int:
    ap = argparse.ArgumentParser(description="门控阈值扫描")
    ap.add_argument("--mock", action="store_true", help="离线确定性替身（结论无业务意义）")
    ap.add_argument("--skip-ingest", action="store_true", help="跳过重新入库")
    ap.add_argument("--db", type=str, default=str(DEFAULT_EVAL_DB), help="评测用 Milvus 库路径")
    ap.add_argument("--report", type=str, default=str(DEFAULT_REPORT), help="Markdown 报告输出路径")
    ap.add_argument("--thr-min", type=float, default=0.0)
    ap.add_argument("--thr-max", type=float, default=0.80)
    ap.add_argument("--thr-step", type=float, default=0.05)
    args = ap.parse_args()

    settings = get_settings()
    if args.mock:
        embedding, _ = build_fakes(dim=settings.milvus_dim)
        print("!! MOCK 模式：假向量，扫描结论无业务意义（只演示脚本可用）!!")
    else:
        embedding = get_embedding()
    store = make_store(uri=args.db, collection=EVAL_COLLECTION, dim=settings.milvus_dim)

    total_chunks = 0
    if not args.skip_ingest:
        print("== 入库 eval/corpus ==")
        total_chunks = await ingest_corpus(store, embedding)
        print(f"  合计 {total_chunks} chunks\n")

    golden = load_golden()
    answerable = [g for g in golden if g["answerable"]]
    unanswerable = [g for g in golden if not g["answerable"]]
    scores = await top1_scores(golden, store, embedding)
    ans_scores = [scores[g["id"]] for g in answerable]
    un_scores = [scores[g["id"]] for g in unanswerable]

    thresholds = []
    t = args.thr_min
    while t <= args.thr_max + 1e-9:
        thresholds.append(round(t, 3))
        t += args.thr_step

    rows = sweep_table(ans_scores, un_scores, thresholds)
    print("== 阈值扫描（门控前 top1 相似度）==")
    print(f"  {'阈值':<8}{'可答保留率':<14}{'不可答拦截率':<14}")
    for thr, keep, block in rows:
        mark = "  <-- 当前" if abs(thr - GATE_THRESHOLD) < 1e-9 else ""
        print(f"  {thr:<8.2f}{keep:<14.3f}{block:<14.3f}{mark}")

    min_ans, max_un = min(ans_scores), max(un_scores)
    perfect = 1.0 if min_ans > max_un else 0.0
    retention = rate(sum(1 for s in ans_scores if s >= GATE_THRESHOLD), len(ans_scores))
    block_cur = rate(sum(1 for s in un_scores if s < GATE_THRESHOLD), len(un_scores))

    print("\n== 结论 ==")
    print(f"  可答问题 top1 最低分 : {fmt(min_ans)}")
    print(f"  不可答问题 top1 最高分: {fmt(max_un)}")
    print(f"  完美分离阈值是否存在  : {'存在' if perfect else '不存在'}")
    if not perfect:
        print("  → 可答与不可答的分数区间**重叠**，任何单一阈值都无法同时做到")
        print("    「不误挡可答」与「拦住不可答」。这是预期的：门控是相似度下限，")
        print("    而不可答是话题匹配问题——语义拒答应由生成层提示词负责。")
    print(f"  当前阈值 {GATE_THRESHOLD}：可答保留 {fmt(retention)}，不可答拦截 {fmt(block_cur)}")

    # ---- 写报告 ----
    report = Path(args.report)
    lines = [
        "# 门控阈值扫描报告（自动生成）",
        "",
        f"> 生成日期：{date.today().isoformat()}　脚本：`eval/sweep.py`",
        f"> 语料：{total_chunks or '（复用已有 collection）'} chunks　"
        f"金标：{len(answerable)} 可答 + {len(unanswerable)} 不可答",
        "",
        "## 扫描表（门控前 top1 相似度）",
        "",
        "| 阈值 | 可答保留率 | 不可答拦截率 |",
        "|---|---|---|",
    ]
    for thr, keep, block in rows:
        cur = " **← 当前**" if abs(thr - GATE_THRESHOLD) < 1e-9 else ""
        lines.append(f"| {thr:.2f}{cur} | {keep:.3f} | {block:.3f} |")
    lines += [
        "",
        "## 结论",
        "",
        f"- 可答问题 top1 最低分：**{fmt(min_ans)}**",
        f"- 不可答问题 top1 最高分：**{fmt(max_un)}**",
        f"- 完美分离阈值：**{'存在' if perfect else '不存在'}**",
        f"- 当前阈值 `GATE_THRESHOLD = {GATE_THRESHOLD}`：可答保留 **{fmt(retention)}**、不可答拦截 **{fmt(block_cur)}**",
        "",
    ]
    if not perfect:
        lines += [
            "两个分布**重叠**，因此任何单一相似度阈值都无法同时满足「不误挡可答」和",
            "「拦住不可答」。这解释了为什么 `RESULTS.md` 里 off-topic 分数（0.52–0.78）",
            "会穿过 0.35 的门控——**门控的职责边界是挡低相似噪声，不是做语义拒答**；",
            "语义拒答由生成层双提示词（接地提示 / 兜底引导）承担。",
            "",
            "### 对生产设计的启示",
            "",
            "1. 阈值只需要低到「不误伤可答问题」（本表 retention 列），不必高到拦 off-topic；",
            "2. 若要真正做语义拒答，应引入**独立的分类器或话题判定**（而不是抬高相似度阈值）；",
            "3. 语料扩充后应重跑本脚本，观察重叠区间是否收窄。",
        ]
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n  报告已写入：{report}")

    values = {"retention_at_current": retention}
    gate_rows, code = judge_gates(values, GATES_SWEEP)
    print()
    print_rows(gate_rows)
    return code


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
