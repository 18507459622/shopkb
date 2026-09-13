"""生成「高干扰密度」合成语料 + 配套金标，用于让检索评测产生区分度。

## 为什么需要它

原语料 14 篇下，dense 与 hybrid 的 Recall@5 / MRR 双双击穿 1.000 —— 天花板效应，
**无法分辨两种检索策略**。检索的难点不在文档数量，而在**干扰密度**。

## 设计要点（决定了实验能否分出差异）

1. **1 型号 = 1 文档**（关键）。如果把相邻型号放进同一篇文档，doc-level 检索必然命中，
   什么都测不出来。必须让每个变体独立成篇，检索才有"挑对哪一篇"的难度。
2. **文档结构完全同构**：都是"型号名 + 同一组属性"，只有型号名与参数值不同
   → 语义向量高度重合，dense 难以区分。
3. **属性值取自小池子**（电池只有 6 个候选值）→ 大量文档共享同一个值，
   只能靠"型号名"这个 token 区分 → BM25 的精确匹配优势才有发挥空间。
4. **变体后缀只差一个 token**：Pro / Pro+ / Pro Max / Pro Max+ / Lite / SE / Ultra
   → 型号密集查询是 dense 的软肋、BM25 的强项。
5. 固定随机种子 → 完全可复现；**0 token 成本**（不调用任何 LLM）。

用法：
    python gen_hard_corpus.py <输出目录>
    → <输出目录>/corpus/*.md      合成语料（每篇一个型号）
    → <输出目录>/golden.jsonl     配套金标（与 eval/golden.jsonl 同 schema）
"""
from __future__ import annotations

import json
import random
import re
import sys
from pathlib import Path

SEED = 20260912

# 5 品牌 × 15 产品线 × 12 变体 = 180 个型号 → 180 篇文档
LINES: list[tuple[str, str, str]] = [
    ("星辰", "X1", "phone"), ("星辰", "X2", "phone"), ("星辰", "X3", "phone"),
    ("星云", "Note", "phone"), ("星云", "Fold", "phone"),
    ("星域", "Book", "laptop"), ("星域", "Air", "laptop"), ("星域", "Pad", "tablet"),
    ("星澜", "Buds", "audio"), ("星澜", "Headphone", "audio"), ("星澜", "Sound", "speaker"),
    ("星环", "Watch", "watch"),
    ("星河", "空调", "appliance"), ("星河", "洗衣机", "appliance"), ("星河", "净化器", "appliance"),
]

VARIANTS: list[tuple[str, str]] = [
    # (展示后缀, 文件名后缀) —— 必须成对给出：slug() 会把 "+" 归一掉，
    # 直接用它生成文件名会让 "Pro" 与 "Pro+" 撞成同一个文件（实测踩过）
    ("", ""), (" Pro", "Pro"), (" Pro+", "ProPlus"), (" Pro Max", "ProMax"),
    (" Pro Max+", "ProMaxPlus"), (" Lite", "Lite"), (" SE", "SE"), (" Ultra", "Ultra"),
    (" Mini", "Mini"), (" Air", "Air"), (" Plus", "Plus"), (" 标准版", "Standard"),
]

# 属性池故意做小 → 大量文档共享同一数值，只能靠型号名区分
POOLS: dict[str, dict] = {
    "phone": {
        "屏幕": ["{a} 英寸 {b}，{c}Hz 刷新率", "{a} 英寸 {b}，{c}Hz 自适应刷新率"],
        "a": ["6.1", "6.5", "6.7", "6.8", "6.9"], "b": ["OLED", "AMOLED", "LTPO OLED"],
        "c": ["60", "90", "120", "144"],
        "处理器": ["骁龙 7 Gen 3", "骁龙 8 Gen 2", "骁龙 8 Gen 3", "天玑 8300", "天玑 9300"],
        "内存与存储": ["8GB + 128GB", "8GB + 256GB", "12GB + 256GB", "12GB + 512GB", "16GB + 512GB", "16GB + 1TB"],
        "电池": ["4500mAh", "4800mAh", "5000mAh", "5500mAh", "6000mAh", "6200mAh"],
        "快充": ["33W", "66W", "80W", "100W", "120W", "150W"],
        "重量": ["178g", "188g", "198g", "205g", "212g"],
        "价格": ["1599", "2299", "2999", "3999", "4999", "5699", "6999"],
        "质保年限": ["1", "2"],
        "attrs": ["屏幕", "处理器", "内存与存储", "电池", "快充", "重量", "价格", "质保年限"],
        "intro": "{name} 是面向{seg}市场的机型，主打{hl}。",
        "segs": ["主流", "入门", "高端", "年轻用户"], "hls": ["长续航与快充", "影像能力", "轻薄手感", "高性能游戏"],
    },
    "laptop": {
        "屏幕": ["{a} 英寸 {b}，{c}Hz"], "a": ["13.3", "14", "15.6", "16", "17"],
        "b": ["2.5K", "2.8K", "3.2K", "3.5K"], "c": ["60", "90", "120", "165", "240"],
        "处理器": ["Intel Core Ultra 5", "Intel Core Ultra 7", "Intel Core i9-14900HX", "AMD Ryzen 7 8840U"],
        "内存与存储": ["16GB + 512GB", "16GB + 1TB", "32GB + 1TB", "32GB + 2TB", "64GB + 2TB"],
        "显卡": ["集成显卡", "NVIDIA RTX 4050 6GB", "NVIDIA RTX 4060 8GB", "NVIDIA RTX 4090 16GB"],
        "电池": ["60Wh", "75Wh", "99Wh"],
        "重量": ["0.99kg", "1.29kg", "1.59kg", "1.99kg", "2.6kg"],
        "价格": ["4999", "5499", "6999", "8999", "9999", "13999", "18999"],
        "质保年限": ["1", "2"],
        "attrs": ["屏幕", "处理器", "显卡", "内存与存储", "重量", "价格", "质保年限"],
        "intro": "{name} 定位{seg}笔记本，强调{hl}。",
        "segs": ["轻薄办公", "高性能创作", "入门学习", "移动工作站"], "hls": ["便携与续航", "散热与性能释放", "屏幕素质", "扩展性"],
    },
    "tablet": {
        "屏幕": ["{a} 英寸，{c}Hz 刷新率"], "a": ["8.4", "11", "12.9", "13.2"], "c": ["60", "120", "144"],
        "处理器": ["骁龙 7 Gen 3", "骁龙 8+ Gen 1", "骁龙 8 Gen 3", "天玑 9300"],
        "内存与存储": ["8GB + 128GB", "12GB + 256GB", "16GB + 512GB", "16GB + 1TB"],
        "电池": ["5100mAh", "8600mAh", "11200mAh"],
        "重量": ["320g", "460g", "560g", "682g"],
        "价格": ["1299", "2299", "2899", "4299", "5499", "6299"],
        "质保年限": ["1", "2"],
        "attrs": ["屏幕", "处理器", "内存与存储", "电池", "重量", "价格", "质保年限"],
        "intro": "{name} 是{seg}平板，适合{hl}。",
        "segs": ["小尺寸便携", "大屏办公", "影音娱乐", "手写笔记"], "hls": ["追剧与阅读", "文档批注", "绘画创作", "轻办公"],
    },
    "audio": {
        "类型": ["入耳式真无线", "半入耳式真无线", "头戴式"],
        "降噪深度": ["35dB", "40dB", "45dB", "48dB", "50dB"],
        "续航": ["单次 5 小时", "单次 6 小时", "单次 8 小时", "单次 30 小时", "单次 40 小时"],
        "防水等级": ["IPX4", "IPX5", "IP54"],
        "价格": ["199", "399", "599", "899", "1299"],
        "质保年限": ["1", "2"],
        "attrs": ["类型", "降噪深度", "续航", "防水等级", "价格", "质保年限"],
        "intro": "{name} 面向{seg}用户，突出{hl}。",
        "segs": ["通勤", "运动", "音质发烧", "办公通话"], "hls": ["主动降噪", "低延迟", "佩戴舒适度", "通话清晰度"],
    },
    "speaker": {
        "扬声器": ["5W 全频", "20W 低音 + 2×10W 高音", "60W 输出"],
        "麦克风": ["双麦克风阵列", "六麦克风环形阵列"],
        "连接": ["Wi-Fi 2.4GHz + 蓝牙 5.2", "Wi-Fi 双频 + 蓝牙 5.3", "Wi-Fi 双频 + 蓝牙 5.3 + 光纤"],
        "价格": ["199", "399", "699", "1299", "1899"],
        "质保年限": ["1", "2"],
        "attrs": ["扬声器", "麦克风", "连接", "价格", "质保年限"],
        "intro": "{name} 用于{seg}场景，优势是{hl}。",
        "segs": ["客厅", "卧室", "桌面", "多房间组网"], "hls": ["低频表现", "远场唤醒", "多设备连接", "语音助手"],
    },
    "watch": {
        "屏幕": ["1.2 英寸 LCD", "1.43 英寸 AMOLED", "1.5 英寸 LTPO AMOLED"],
        "表体": ["塑料表壳", "铝合金表壳", "不锈钢表壳", "钛合金表壳"],
        "续航": ["典型模式 7 天", "典型模式 10 天", "典型模式 14 天", "典型模式 21 天"],
        "防水等级": ["50 米防水", "100 米防水"],
        "价格": ["199", "399", "999", "1499", "2299"],
        "质保年限": ["1", "2"],
        "attrs": ["屏幕", "表体", "续航", "防水等级", "价格", "质保年限"],
        "intro": "{name} 定位于{seg}人群，主打{hl}。",
        "segs": ["运动健身", "日常通勤", "健康监测", "轻量佩戴"], "hls": ["续航时长", "健康传感器", "材质与质感", "轻便"],
    },
    "appliance": {
        "能效等级": ["一级能效", "二级能效"],
        "噪音": ["最低 18 分贝", "最低 22 分贝", "最低 25 分贝", "最低 33 分贝"],
        "功率": ["1200W", "2200W", "3500W"],
        "容量": ["4 升", "10kg", "1.5 匹"],
        "价格": ["399", "1299", "1999", "2899", "3299"],
        "质保年限": ["1", "2", "3", "6"],
        "attrs": ["能效等级", "噪音", "功率", "容量", "价格", "质保年限"],
        "intro": "{name} 面向{seg}家庭，特点是{hl}。",
        "segs": ["中小户型", "大户型", "母婴", "租房"], "hls": ["静音", "节能", "大容量", "易清洁"],
    },
}

CATEGORY_TITLE = {
    "phone": "智能手机", "laptop": "笔记本电脑", "tablet": "平板电脑",
    "audio": "无线耳机", "speaker": "智能音箱", "watch": "智能手表", "appliance": "家用电器",
}

# 带单位的展示格式
UNIT = {"价格": "{v} 元", "质保年限": "整机保修 {v} 年", "快充": "支持 {v} 有线快充"}


def slug(name: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fa5]+", "_", name).strip("_")


def gen_specs(rng: random.Random, cat: str) -> dict[str, str]:
    pool = POOLS[cat]
    specs: dict[str, str] = {}
    for attr in pool["attrs"]:
        tmpl = pool.get(attr)
        if isinstance(tmpl, list) and tmpl and isinstance(tmpl[0], str) and "{" in tmpl[0]:
            specs[attr] = rng.choice(tmpl).format(
                a=rng.choice(pool.get("a", [""])), b=rng.choice(pool.get("b", [""])),
                c=rng.choice(pool.get("c", [""])),
            )
        else:
            specs[attr] = rng.choice(tmpl)
    return specs


def display(attr: str, value: str) -> str:
    return UNIT.get(attr, "{v}").format(v=value)


def render_doc(name: str, specs: dict[str, str], cat: str, rng: random.Random) -> str:
    pool = POOLS[cat]
    intro = pool["intro"].format(name=name, seg=rng.choice(pool["segs"]), hl=rng.choice(pool["hls"]))
    lines = [f"# {name} 产品资料", "", intro, "", f"## {name}", ""]
    for k, v in specs.items():
        lines.append(f"- {k}：{display(k, v)}")
    return "\n".join(lines) + "\n"


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    out = Path(sys.argv[1])
    corpus = out / "corpus"
    if corpus.exists():
        for f in corpus.glob("*.md"):
            f.unlink()
    corpus.mkdir(parents=True, exist_ok=True)
    rng = random.Random(SEED)

    # 1) 1 型号 = 1 文档
    models: dict[str, tuple[str, dict]] = {}      # name -> (file, specs)
    by_line: dict[tuple[str, str, str], list[str]] = {}
    for brand, line, cat in LINES:
        for suffix, fname_suffix in VARIANTS:
            name = f"{brand} {line}{suffix}"
            specs = gen_specs(rng, cat)
            fname = f"{slug(brand)}{slug(line)}{fname_suffix}.md"
            (corpus / fname).write_text(render_doc(name, specs, cat, rng), encoding="utf-8")
            models[name] = (fname, specs)
            by_line.setdefault((brand, line, cat), []).append(name)

    # 2) 金标
    golden: list[dict] = []
    qid = 0
    names = list(models)

    def pick_attr(name: str) -> str:
        cat = next(c for _b, _l, c in LINES if name.startswith(_b) and _l in name)
        return rng.choice(POOLS[cat]["attrs"])

    # 2a) 单事实 · 型号密集
    #     查询分布按**真实用户问法**设定，而不是对 180 个型号均匀抽样：
    #     用户更常直接问基础型号（"星辰 X1 多少钱"），而不是完整变体名
    #     （"星辰 X1 Pro Max+ 多少钱"）。基础型号的名称是其他 11 个变体的**子串**，
    #     是干扰最强的场景 —— 故按较高比例采样，并保证样本量足够得出结论。
    LONG_SUF = (" Pro Max+", " Pro Max", " Ultra")
    SHORT_SUF = (" Pro+", " Pro", " Lite", " SE", " Mini", " Air", " Plus", " 标准版")

    def kind_of(n: str) -> str:
        if any(n.endswith(s) for s in LONG_SUF):
            return "long"
        if any(n.endswith(s) for s in SHORT_SUF):
            return "short"
        return "base"

    base = [n for n in names if kind_of(n) == "base"]
    short = [n for n in names if kind_of(n) == "short"]
    lng = [n for n in names if kind_of(n) == "long"]

    def add_single(name: str) -> None:
        nonlocal qid
        attr = pick_attr(name)
        qid += 1
        golden.append({
            "id": f"h{qid:03d}", "question": f"{name} 的{attr}是多少？", "answerable": True,
            "expected_docs": [models[name][0]], "gold_answer": display(attr, models[name][1][attr]),
        })

    for name in base:                      # 每个基础型号问 3 个属性 → 45 条
        for _ in range(3):
            add_single(name)
    for name in rng.sample(short, 30):     # 短后缀 30 条（中等难度）
        add_single(name)
    for name in rng.sample(lng, 20):       # 长后缀 20 条（名称独特，作为对照）
        add_single(name)

    # 2b) 相邻变体比较（40）—— 同产品线内两个变体，要求两篇都命中
    #     先枚举所有候选对，再抽样（每条产品线有 12 个变体 → C(12,2)=66 对）
    pairs: list[tuple[str, str]] = []
    for ms in by_line.values():
        ms = list(ms)
        for i in range(len(ms)):
            for j in range(i + 1, len(ms)):
                pairs.append((ms[i], ms[j]))
    for a, b in rng.sample(pairs, min(40, len(pairs))):
        # 必须挑两款**取值不同**的属性，否则"分别是多少"答案是同一个值，没有区分度
        diff_attrs = [k for k in models[a][1] if models[a][1][k] != models[b][1][k]]
        if not diff_attrs:
            continue
        attr = rng.choice(diff_attrs)
        qid += 1
        golden.append({
            "id": f"h{qid:03d}",
            "question": f"{a} 和 {b} 的{attr}分别是多少？请两款都给出。",
            "answerable": True,
            "expected_docs": sorted({models[a][0], models[b][0]}),
            "gold_answer": f"{a}：{display(attr, models[a][1][attr])}；{b}：{display(attr, models[b][1][attr])}",
        })

    # 2c) 不可答（40）—— 问不存在的属性 / 不存在的型号
    absent = ["防水等级", "上市年份", "月销量", "用户评分", "产地", "售后网点数量"]
    for name in rng.sample(names, 20):
        qid += 1
        golden.append({"id": f"h{qid:03d}", "question": f"{name} 的{rng.choice(absent)}是多少？",
                       "answerable": False, "expected_docs": [], "gold_answer": ""})
    for _ in range(20):
        brand, line, _c = rng.choice(LINES)
        qid += 1
        golden.append({"id": f"h{qid:03d}", "question": f"{brand} {line} Max Ultra Pro 的价格是多少？",
                       "answerable": False, "expected_docs": [], "gold_answer": ""})

    (out / "golden.jsonl").write_text(
        "\n".join(json.dumps(g, ensure_ascii=False) for g in golden) + "\n", encoding="utf-8")

    total_bytes = sum((corpus / f).stat().st_size for f, _ in models.values())
    ans = sum(1 for g in golden if g["answerable"])
    print(f"[OK] 语料: {len(models)} 篇（1 型号 = 1 文档）  合计 {total_bytes / 1024:.0f} KB")
    print(f"     平均每篇 {total_bytes / len(models):.0f} 字节 → 约 {total_bytes / len(models) / 500:.1f} 个 chunk")
    print(f"[OK] 金标: {len(golden)} 条（可答 {ans} / 不可答 {len(golden) - ans}）")
    print(f"     基础型号 {len(base) * 3} / 短后缀 30 / 长后缀 20 / 相邻变体比较 40 / 不可答 40")
    print(f"     目录: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
