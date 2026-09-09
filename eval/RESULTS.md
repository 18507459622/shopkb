# shopkb RAG 离线评测结果

> 评测脚本：`eval/retrieval.py`（检索层）+ `eval/e2e.py`（端到端）
> 复跑：`cd backend && ../.venv/Scripts/python.exe ../eval/retrieval.py`（e2e 同理）
> 日期：2026-09-08

## 评测集

- **语料**：`eval/corpus/` 6 份商品文档（手机/笔记本/家电/售后/手表/耳机），经真实 pipeline
  切分为 **10 chunks**（独立 collection `eval_product_kb`，独立 db 文件，不污染 `product_kb`）。
- **金标**：`eval/golden.jsonl` 共 **26 条** —— 20 条可答（单事实/多源/条件数值/比较）+ 6 条不可答。
- **模型**：DashScope `text-embedding-v3`（检索），DeepSeek `deepseek-v4-flash`（生成与 LLM-judge）。
- **检索模式**：dense-only（本地 Milvus Lite 不支持 BM25，混合检索需 Standalone）。

## 指标结果

### 检索层（doc-level，20 条可答问题）

| 指标 | 值 | 说明 |
|---|---|---|
| Recall@5 | **1.000** | top5 命中正确文档 |
| Recall@10 | 1.000 | ⚠ 语料仅 10 chunk，top10=全量，无区分度 |
| Hit@10 | 1.000 | 20/20 全部命中 |
| **MRR** | **1.000** | 首个相关文档 20/20 都排第 1 —— 排序质量 |
| NDCG@10 | 0.996 | q15 第二个相关文档排第 3 拉低 |

### 端到端（LLM-judge）

| 指标 | 值 | 说明 |
|---|---|---|
| 忠实度（faithfulness） | **≈ 0.95–1.00** | 20 条中 19–20 判为 grounded，两次运行分别 20/20 与 19/20 |
| 漏召回率 | 0.000 | 0/20 |
| **不可答不编造率** | **1.000** | 6/6 零编造（拒绝编造价格/参数/发布日期等） |
| 生成延迟 | P50 ≈ 1.3–2.1s，P95 ≈ 2.2–3.5s | DeepSeek 首包~完整生成 |

## 诚实边界（面试别踩坑）

1. **语料偏小（10 chunks）**：Recall@10 无区分度（top10 覆盖全量），真正的排序质量信号是
   **MRR / NDCG**。要更强置信度需扩充语料（目标 30+ chunks）。
2. **忠实度是区间不是点**：LLM-judge 有 ±5% 噪声（同一批答案两次判分 20/20 vs 19/20，
   且误判的是「接口」这类逐字正确回答）。人工抽查全部接地，真实忠实度接近 1.0。
3. **dense-only 基线**：BM25 混合检索是 Standalone 专属能力，本地没生效；切 Standalone 后
   可用 hybrid 复跑，做 dense vs hybrid 的 Recall 对比实验（这才是最有说服力的一句话）。
4. **「不可答不编造率 1.0」是生成层兜底的结果**，不是检索门控的结果——门控（0.35 相似度）
   只挡低相似噪声，不负责语义拒答（off-topic 问题分数 0.52–0.78 都会穿过门控，靠双提示词兜住）。

## 下一步（可选，提升数字说服力）

- 扩充语料到 30+ chunks，让 Recall@5/MRR 更具区分度。
- 跑 dense vs dense+BM25 对比实验（需 Docker Standalone）。
- LLM-judge 改多次采样投票，压低判分噪声。
- 引入 RAGAS 做 claim-level faithfulness / 引文准确率（更标准、可引用）。

## 一句话简历写法（真实数字）

> 构建 26 条金标离线评测集，自研脚本量化 RAG 质量：dense 检索 **Recall@5=1.00、MRR=1.00、
> NDCG@10=0.996**；端到端忠实度 ≈0.95、**不可答不编造率=1.00**；生成 P50≈1.5s。
