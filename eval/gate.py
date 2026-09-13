"""评测门禁：把指标变成 PASS/FAIL 判定 + 进程退出码。

为什么需要它：评测如果只打印数字，改完提示词/阈值/切分策略后「看不出退化」——
人眼在一堆小数里看不出 0.95 → 0.88。门禁把「可接受的底线」写成断言，
让脚本用退出码告诉 CI（和你自己）：这次改动是好是坏。

阈值来源：docs/DESIGN.md §6.9（CI gate），并按 v2 新增指标扩充：
    - 新增 judge_coverage：判分器失效率受门禁约束（fail-closed 之后，
      「判分器坏了」必须表现为指标不可信，而不是白送通过）
    - 新增 citation_precision / numeric_recall：两个确定性指标，
      不依赖 LLM-judge，作为交叉验证。
"""
from __future__ import annotations

from dataclasses import dataclass

OPS = {
    ">=": lambda v, t: v >= t,
    "<=": lambda v, t: v <= t,
    "==": lambda v, t: abs(v - t) < 1e-9,
    ">": lambda v, t: v > t,
}


@dataclass
class Gate:
    """一条门禁：把某个指标的底线写成可执行断言。"""

    key: str
    label: str
    threshold: float
    op: str = ">="
    hint: str = ""


@dataclass
class Row:
    """门禁判定结果（用于打印）。"""

    label: str
    value: float | None
    threshold: str
    status: str  # PASS / FAIL / SKIP
    hint: str = ""


# ---- 门禁定义（改这里就能调 CI 底线） ----
GATES_RETRIEVAL: list[Gate] = [
    Gate("recall_at_5", "Recall@5（doc-level）", 0.90, ">=", "检索召回底线"),
    Gate("mrr", "MRR（首个相关排名）", 0.90, ">=", "排序质量，语料小时最可靠"),
    Gate("full_coverage_at_5", "FullCoverage@5（多源全覆盖）", 0.85, ">=", "多文档问题需全部命中"),
]

GATES_E2E: list[Gate] = [
    Gate("faithfulness", "忠实度（LLM-judge）", 0.85, ">=", "DESIGN §6.9"),
    Gate("numeric_recall", "数字命中率（规则）", 0.85, ">=", "确定性指标，交叉验证 judge"),
    Gate("citation_precision", "引文准确率（规则）", 0.95, ">=", "防自造 [n] 引用"),
    Gate("judge_coverage", "判分覆盖率（可答）", 0.95, ">=", "判分器失效=指标不可信"),
    Gate("no_fabrication", "不可答不编造率", 1.00, "==", "安全底线，必须满分"),
    Gate("judge_coverage_unanswerable", "判分覆盖率（不可答）", 0.95, ">=", ""),
]


def judge_gates(values: dict[str, float | None], gates: list[Gate]) -> tuple[list[Row], int]:
    """按 gate 定义判定 values；返回 (行列表, 退出码)。

    值为 None 表示「本次无法计算」（例如该批用例里没有可适用的样本）——记为 SKIP，
    不计入失败，但会打印出来提醒：SKIP 太多说明评测集覆盖不足。
    """
    rows: list[Row] = []
    failed = 0
    for g in gates:
        v = values.get(g.key)
        thr = f"{g.op} {g.threshold:g}"
        if v is None:
            rows.append(Row(g.label, None, thr, "SKIP", g.hint))
            continue
        ok = OPS[g.op](v, g.threshold)
        if not ok:
            failed += 1
        rows.append(Row(g.label, v, thr, "PASS" if ok else "FAIL", g.hint))
    return rows, (1 if failed else 0)


def print_rows(rows: list[Row]) -> None:
    """打印门禁表（ASCII 状态标记，避免 Windows 控制台编码问题）。"""
    print("== 门禁判定 ==")
    for r in rows:
        val = "N/A" if r.value is None else f"{r.value:.3f}"
        line = f"  [{r.status:4}] {r.label:<28} 实测 {val:>7}  底线 {r.threshold}"
        if r.hint and r.status != "PASS":
            line += f"   ({r.hint})"
        print(line)
    n_fail = sum(1 for r in rows if r.status == "FAIL")
    n_skip = sum(1 for r in rows if r.status == "SKIP")
    print(f"  小结：{len(rows) - n_fail - n_skip} 通过 / {n_fail} 失败 / {n_skip} 跳过")


def exit_code(rows: list[Row]) -> int:
    """任一 FAIL → 1（CI 红灯）。"""
    return 1 if any(r.status == "FAIL" for r in rows) else 0
