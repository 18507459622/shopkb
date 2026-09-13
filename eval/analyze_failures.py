"""分析 dense 与 hybrid 的**逐题胜负**，找出 dense 失败的具体模式。

为什么需要它：汇总指标只告诉我们"hybrid 略优"，但说不出**为什么**。
面试时真正有说服力的是具体案例 —— "dense 把『X1 Pro Max』认成了『X1 Pro』，
BM25 靠精确 token 匹配纠正过来"。本脚本把这类 case 挖出来。

用法（在 backend/ 下）：
    python ../eval/analyze_failures.py --corpus <dir> --golden <file> --collection <name>
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


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--golden", required=True)
    ap.add_argument("--collection", required=True)
    ap.add_argument("--uri", default="")
    ap.add_argument("--token", default="")
    ap.add_argument("--top-k", type=int, default=6)
    ap.add_argument("--show", type=int, default=15, help="最多展示多少条差异案例")
    args = ap.parse_args()

    settings = get_settings()
    uri = args.uri or settings.vectorstore_uri
    token = args.token or settings.vectorstore_token
    golden = [json.loads(l) for l in Path(args.golden).read_text(encoding="utf-8").splitlines() if l.strip()]
    answerable = [g for g in golden if g["answerable"]]

    store = make_store(uri=uri, collection=args.collection, dim=settings.milvus_dim, token=token)
    embedding = get_embedding()

    async def rank_of(retriever: Retriever, case: dict, mode: str) -> tuple[int | None, list[str]]:
        chunks = await retriever.retrieve(case["question"], top_k=args.top_k, mode=mode)
        docs: list[str] = []
        for ch in chunks:
            if ch.source_file not in docs:
                docs.append(ch.source_file)
        exp = set(case["expected_docs"])
        for i, d in enumerate(docs, 1):
            if d in exp:
                return i, docs
        return None, docs

    dense_r = Retriever(store, embedding, reranker=None)
    hybrid_r = Retriever(store, embedding, reranker=None)

    wins_h, wins_d, ties, both_fail = [], [], 0, []
    for g in answerable:
        rd, dd = await rank_of(dense_r, g, MODE_DENSE)
        rh, dh = await rank_of(hybrid_r, g, MODE_HYBRID)
        rank_d = rd if rd is not None else 999
        rank_h = rh if rh is not None else 999
        if rank_h < rank_d:
            wins_h.append((g, rank_d, rank_h, dd, dh))
        elif rank_d < rank_h:
            wins_d.append((g, rank_d, rank_h, dd, dh))
        elif rank_d == 999:
            both_fail.append((g, dd, dh))
        else:
            ties += 1

    n = len(answerable)
    print(f"=== 逐题胜负（可答 {n} 题，top_k={args.top_k}）===")
    print(f"  hybrid 排名更靠前 : {len(wins_h):3d} 题")
    print(f"  dense  排名更靠前 : {len(wins_d):3d} 题")
    print(f"  排名相同（含并列）: {ties:3d} 题")
    print(f"  两者都没命中      : {len(both_fail):3d} 题")

    print(f"\n=== hybrid 赢的案例（最多 {args.show} 条）—— dense 失败的具体模式 ===")
    for g, rd, rh, dd, dh in wins_h[: args.show]:
        exp = g["expected_docs"]
        print(f"\n  [{g['id']}] {g['question']}")
        print(f"     期望文档 : {exp}")
        print(f"     dense  # {rd if rd != 999 else '未命中'}  top3={dd[:3]}")
        print(f"     hybrid # {rh if rh != 999 else '未命中'}  top3={dh[:3]}")

    if wins_d:
        print(f"\n=== dense 赢的案例（最多 5 条）===")
        for g, rd, rh, dd, dh in wins_d[:5]:
            print(f"\n  [{g['id']}] {g['question']}")
            print(f"     期望={g['expected_docs']}  dense#{rd}  hybrid#{rh}")

    if both_fail:
        print(f"\n=== 两者都未命中（最多 5 条）===")
        for g, dd, dh in both_fail[:5]:
            print(f"\n  [{g['id']}] {g['question']}")
            print(f"     期望={g['expected_docs']}  dense_top3={dd[:3]}  hybrid_top3={dh[:3]}")

    # 汇总写入文件，便于贴到报告里
    out = Path(args.golden).parent / "FAILURE_ANALYSIS.md"
    lines = [
        "# dense vs hybrid 逐题胜负分析（自动生成）", "",
        f"> 可答 {n} 题，top_k={args.top_k}，collection=`{args.collection}`", "",
        "| 结果 | 题数 |", "|---|---|",
        f"| hybrid 排名更靠前 | {len(wins_h)} |",
        f"| dense 排名更靠前 | {len(wins_d)} |",
        f"| 排名相同 | {ties} |",
        f"| 两者都未命中 | {len(both_fail)} |", "",
        "## hybrid 赢的案例（dense 失败模式）", "",
    ]
    for g, rd, rh, dd, dh in wins_h[:30]:
        lines += [
            f"**{g['id']}**　{g['question']}", "",
            f"- 期望：`{g['expected_docs']}`",
            f"- dense 排名：{rd if rd != 999 else '未命中'}　top3：`{dd[:3]}`",
            f"- hybrid 排名：{rh if rh != 999 else '未命中'}　top3：`{dh[:3]}`", "",
        ]
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n  报告已写入：{out}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
