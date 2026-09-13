"""按查询类型分组统计 dense vs hybrid —— 定位差异**来自哪一类查询**。

为什么比"总体指标"更有价值：
    总体 Recall@5 只差 0.005，看起来无关紧要；但分组后可能发现
    "基础型号查询" 上 dense 只有 0.7x、hybrid 有 0.9x —— 差距集中在一个
    真实且高频的用户问法上。这比调权重凑数字诚实得多，也更能说明问题。

分组依据（按期望文档的型号后缀）：
    base   —— 基础型号（无后缀），其名称是其他 11 个变体的**子串**，最易被干扰
    short  —— 短后缀（Pro / Lite / SE / Mini / Air / Plus / 标准版）
    long   —— 长后缀（Pro Max / Pro Max+ / Ultra），名称最独特，最难被干扰
    compare—— 相邻变体比较（要求两篇都命中）

用法（在 backend/ 下）：
    python ../eval/breakdown_by_type.py --golden <file> --collection <name>
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
from app.rag.embeddings import get_embedding  # noqa: E402
from app.rag.retriever import MODE_DENSE, MODE_HYBRID, Retriever  # noqa: E402
from app.rag.vectorstore import make_store  # noqa: E402
from eval.metrics import doc_ranking_metrics, mean  # noqa: E402

SHORT = ("Pro", "Lite", "SE", "Mini", "Air", "Plus", "Standard")
LONG = ("ProMax", "ProMaxPlus", "Ultra")


def classify(doc: str) -> str:
    stem = doc[:-3] if doc.endswith(".md") else doc
    for s in LONG:
        if stem.endswith(s):
            return "long"
    for s in SHORT:
        if stem.endswith(s):
            return "short"
    return "base"


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--golden", required=True)
    ap.add_argument("--collection", required=True)
    ap.add_argument("--uri", default="")
    ap.add_argument("--token", default="")
    ap.add_argument("--top-k", type=int, default=6)
    args = ap.parse_args()

    settings = get_settings()
    golden = [json.loads(l) for l in Path(args.golden).read_text(encoding="utf-8").splitlines() if l.strip()]
    cases = [g for g in golden if g["answerable"]]

    store = make_store(uri=args.uri or settings.vectorstore_uri, collection=args.collection,
                       dim=settings.milvus_dim, token=args.token or settings.vectorstore_token)
    embedding = get_embedding()
    dense_r = Retriever(store, embedding, reranker=None)
    hybrid_r = Retriever(store, embedding, reranker=None)

    groups: dict[str, dict[str, list]] = {}
    for g in cases:
        exp = g["expected_docs"]
        kind = "compare" if len(exp) > 1 else classify(exp[0])
        groups.setdefault(kind, {"dense_r5": [], "hyb_r5": [], "dense_rr": [], "hyb_rr": []})
        for mode, r, pre in ((MODE_DENSE, dense_r, "dense"), (MODE_HYBRID, hybrid_r, "hyb")):
            chunks = await r.retrieve(g["question"], top_k=args.top_k, mode=mode)
            docs: list[str] = []
            for ch in chunks:
                if ch.source_file not in docs:
                    docs.append(ch.source_file)
            m = doc_ranking_metrics(docs, set(exp), k=args.top_k)
            groups[kind][f"{pre}_r5"].append(m["recall5"])
            groups[kind][f"{pre}_rr"].append(m["rr"])

    order = ["base", "short", "long", "compare"]
    label = {"base": "基础型号（无后缀，最易被干扰）", "short": "短后缀（Pro/Lite/SE…）",
             "long": "长后缀（Pro Max/Ultra）", "compare": "相邻变体比较（需两篇都命中）"}

    print(f"=== 按查询类型分组（可答 {len(cases)} 题，top_k={args.top_k}）===\n")
    print(f"  {'类型':<26}{'题数':>5}{'dense R@5':>11}{'hybrid R@5':>12}{'差':>8}{'dense MRR':>11}{'hybrid MRR':>12}{'差':>8}")
    rows = []
    for k in order:
        if k not in groups:
            continue
        v = groups[k]
        d5, h5 = mean(v["dense_r5"]) or 0, mean(v["hyb_r5"]) or 0
        dr, hr = mean(v["dense_rr"]) or 0, mean(v["hyb_rr"]) or 0
        n = len(v["dense_r5"])
        rows.append((k, n, d5, h5, dr, hr))
        print(f"  {label[k]:<26}{n:>5}{d5:>11.3f}{h5:>12.3f}{h5 - d5:>+8.3f}{dr:>11.3f}{hr:>12.3f}{hr - dr:>+8.3f}")

    tot_d5 = mean([x for k in groups for x in groups[k]["dense_r5"]]) or 0
    tot_h5 = mean([x for k in groups for x in groups[k]["hyb_r5"]]) or 0
    tot_dr = mean([x for k in groups for x in groups[k]["dense_rr"]]) or 0
    tot_hr = mean([x for k in groups for x in groups[k]["hyb_rr"]]) or 0
    print(f"  {'—' * 26}{'—':>5}{'—':>11}{'—':>12}{'—':>8}{'—':>11}{'—':>12}{'—':>8}")
    print(f"  {'总体':<26}{len(cases):>5}{tot_d5:>11.3f}{tot_h5:>12.3f}{tot_h5 - tot_d5:>+8.3f}"
          f"{tot_dr:>11.3f}{tot_hr:>12.3f}{tot_hr - tot_dr:>+8.3f}")

    out = Path(args.golden).parent / "BREAKDOWN.md"
    lines = ["# 按查询类型分组的 dense vs hybrid（自动生成）", "",
             f"> 可答 {len(cases)} 题，top_k={args.top_k}，collection=`{args.collection}`", "",
             "| 查询类型 | 题数 | dense R@5 | hybrid R@5 | Δ | dense MRR | hybrid MRR | Δ |",
             "|---|---|---|---|---|---|---|---|"]
    for k, n, d5, h5, dr, hr in rows:
        lines.append(f"| {label[k]} | {n} | {d5:.3f} | {h5:.3f} | {h5 - d5:+.3f} | {dr:.3f} | {hr:.3f} | {hr - dr:+.3f} |")
    lines.append(f"| **总体** | {len(cases)} | {tot_d5:.3f} | {tot_h5:.3f} | {tot_h5 - tot_d5:+.3f} | "
                 f"{tot_dr:.3f} | {tot_hr:.3f} | {tot_hr - tot_dr:+.3f} |")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n  报告已写入：{out}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
