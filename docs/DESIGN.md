# 电商商品知识库 RAG 问答系统 —— 设计文档

> 定位：可写进简历、经得起面试深挖的**生产级** LangChain RAG 项目，而非"能跑通的毕设"。
> 本文档是系统设计的唯一权威（Single Source of Truth），所有技术选型均附理由（decision record），可作为开发蓝图，也可作为面试逐条讲解的决策记录。

> **当前实现状态（截至 2026-09）**：核心链路已跑通，本地零基础设施即可运行（SQLite + Milvus Lite）。已实现：Auth 全套（argon2id/JWT/refresh 旋转/改密）、KB 上传入库（解析→切分→embed→Milvus）、混合检索 + 门控、流式 SSE + 结构化引文、多用户多会话持久化、前端「科技蓝紫玻璃拟态」UI、登录输入校验、无关问题 LLM 兜底引导、歧义追问澄清、检索过程可视化。规划中：Redis/ARQ、bge 重排、可观测三件套、集成测试与离线 eval、CI/CD（见 §10）。

## 1. 目标与需求

用 **LangChain** 开发一套**电商商品知识库 RAG 问答系统**，浏览器操作。用户提问商品相关问题（规格/价格/库存/售后等），系统**严格基于知识库**回答并**展示引用片段**。

功能需求：
1. 浏览器知识库管理（仅 admin）
2. 知识库问答 + 引用片段展示
3. 多用户多会话（每用户独立会话）
4. 会话记录持久化、跨登录找回
5. 注册/登录/改密
6. admin/123456 专属 KB 管理，普通用户仅问答
7. 其余必要功能（流式输出/引用侧栏/会话管理/统计/可观测/评测等）

## 2. 技术选型

| 维度 | 选型 | 关键点 |
|---|---|---|
| 生成 LLM | DeepSeek `deepseek-v4-flash`（OpenAI 兼容 `ChatOpenAI(base_url=api.deepseek.com)`） | temperature=0.1, streaming=True |
| 向量化 | 通义 DashScope `text-embedding-v3`（1024 维，L2 归一） | 自写 `DashScopeTextEmbedding` 区分 document/query text_type |
| 向量库 | Milvus 2.5 Standalone（pymilvus 2.5）｜本地降级 Milvus Lite | 显式 schema + BM25 Function 混合检索 |
| 关系库 | MySQL 8.4（SQLAlchemy 2.0 **async** + asyncmy）｜本地降级 SQLite | 系统真源 |
| 缓存/队列 | Redis 7（ARQ 任务队列 + embedding 缓存 + 限流） | 三逻辑库；缺省降级进程内 |
| 后端 | Python 3.12 + FastAPI + Uvicorn + LangChain（LCEL 热路径 + LangGraph 外层分支壳） | modular monolith |
| 前端 | Vue 3.5 + TS strict + Element Plus + Vite + Pinia | contract-first via OpenAPI |
| 重排 | bge-reranker-v2-m3（本地 CrossEncoder） | 免费自托管 |
| 鉴权 | JWT access(15min) + refresh HttpOnly cookie 旋转 + argon2id | RBAC admin/user |
| 可观测 | structlog + Prometheus + Langfuse(自托管) + Sentry | 四支柱 |

## 3. 总体架构（modular monolith + 派生向量索引）

**单一 FastAPI modular monolith**（按 feature domain 纵向分层 router→service→repository），不拆微服务——面试可辩护、无分布式税。

### 数据平面三层（职责不重叠）
- **MySQL = 关系真源**：users / auth_refresh_tokens / conversations / messages / documents / ingestion_jobs / embedding_cache / message_feedback / audit_logs。**不做 chunks 表**（chunk 文本只在 Milvus `text` 字段 + `messages.sources_json` 快照，避免双写漂移）。
- **Milvus = 派生检索索引**：collection `product_kb`，可由 MySQL + 原始文件随时重建——"Milvus 是 disposable index"。
- **Redis 三库**：DB0=ARQ 队列、DB1=缓存（embedding 热层 + retrieval + denylist）、DB2=slowapi 限流。

### 推理平面
Embedding(DashScope 1024d) + 生成(DeepSeek) + 检索（dense IP ANN + BM25 sparse → RRF 融合 → bge-reranker 重排 → sigmoid 门控）。

### 编排
LCEL 承担"检索+生成"热路径；LangGraph StateGraph 只做外层条件分支（rewrite? → kb_answer | no_info_fallback）。会话持久化不用 langgraph checkpoint，MySQL messages 为唯一真源。

### 关键裁决（decision record）
1. 异步队列用 **ARQ**（全栈 asyncio，避免 Celery sync/async 割裂）；进度真源在 MySQL `ingestion_jobs`；可靠性用 durable outbox + 启动重入队。
2. Refresh token 走 **HttpOnly cookie + 旋转**（否决 localStorage）。
3. 口令哈希 **argon2id**（passlib 无人维护）。
4. 检索融合默认 **RRF(k=60)**（dense 与 BM25 量纲不可直接加权）。
5. 重排 **bge-reranker-v2-m3 进程内常驻**（免费自托管）。
6. Embedding 缓存 **MySQL 持久(sha256→向量) + Redis LRU 热层**。
7. **SSE 线协议唯一权威**：`POST /api/v1/conversations/{id}/stream` + named events（start/token/citations/usage/done/error）。
8. 上传单文件 ≤20MB + sha256 去重。
9. Chunk 默认 ~500 中文字、句级 overlap 60-100、上限 900（由 eval 定）。
10. 生成 temperature=0.1、embedding batch=10。

## 4. 项目结构（monorepo）

```
shopkb/
├─ docker-compose.yml              # prod 形态
├─ docker-compose.override.yml     # 本地 dev
├─ docker-compose.observability.yml# Langfuse+Postgres（--profile）
├─ .env.example / .env(ignored) / Makefile
├─ backend/
│  ├─ pyproject.toml               # uv；ruff/mypy/pytest/coverage
│  ├─ alembic/                     # async template
│  ├─ app/
│  │  ├─ main.py                   # create_app + lifespan
│  │  ├─ core/  config.py database.py redis.py security.py deps.py
│  │  │        exceptions.py logging_conf.py middleware.py schemas.py
│  │  ├─ modules/{auth,kb,chat,users,admin}/   # router/service/repositories/schemas
│  │  │  └─ chat/rag.py            # retriever+reranker+rewriter+generation+citation+SSE
│  │  ├─ models/                   # SQLAlchemy 2.0 Mapped[] 唯一真源
│  │  ├─ workers/                  # ARQ worker
│  │  └─ rag/                      # 纯 Python 自研类（可单测/离线 eval 复用）
│  └─ tests/{unit,integration,eval}/
├─ frontend/src/  api/ types/ stores/ composables/ components/ views/{,admin}/
├─ eval/  data/golden.jsonl  scripts/
├─ deploy/  grafana/dashboards/rag.json  nginx.conf  runbook-*.md
└─ .github/workflows/  ci.yml eval-nightly.yml deploy.yml
```

### 分层纪律
- Repository：永不抛 HTTPException、入参显式 `owner_id`、抛 typed DomainError（结构上消灭 IDOR）。
- Service：持 Unit-of-Work（每请求一个 AsyncSession）。
- Router：零业务逻辑，只做字段映射、DI、调 service。
- 依赖只向下 router→service→repository。
- 阻塞 CPU 活走 `asyncio.to_thread`/worker，LLM/embed 用 async `ainvoke/aembed/astream`。

## 5. 数据模型

### MySQL（8.4 / InnoDB / utf8mb4 / UTC）
1. **users**：id, username(UK), password_hash(argon2id), role(admin|user), nickname, is_active, password_changed_at, last_login_at。
2. **auth_refresh_tokens**：user_id(FK), token_hash=sha256(refresh)(UK), family_id, device_label, ip, expires_at(30d), revoked_at, replaced_by_id(旋转链), last_used_at。
3. **conversations**：user_id(FK), title, status, last_message_at。索引 `(user_id,last_message_at DESC)`。
4. **messages**（append-only）：conversation_id(FK), role, content, status, model, sources_json(引文快照), usage_json, latency_ms。
5. **documents**：uploader_id, filename, title, doc_type, storage_key(uuid4), file_size, checksum=sha256(UK), chunk_count, status, milvus_meta, deleted_at。
6. **ingestion_jobs**：document_id, trigger, stage, progress, attempt, error_message。
7. **embedding_cache**：sha256_text PK, vector BLOB(msgpack), dim。只存向量不存文本。
8. 辅助：message_feedback、audit_logs。

### Milvus `product_kb`
| 字段 | 类型 | 说明 |
|---|---|---|
| pk | VarChar(128) PK | `${doc_id}::${chunk_id}::${content_hash[:8]}` |
| dense_vector | FloatVector 1024d | unit vector |
| text | VarChar(max) | chunk 原文（BM25 源 + 引文全文） |
| sparse_vector | SparseFloatVector | `Function(BM25, input=[text])` 派生 |
| 标量 | doc_id, source_file, doc_type, page, section_path, category | expr 预过滤 |

索引：dense→HNSW(IP, M=16, efConstruction=200)；sparse→SPARSE_INVERTED_INDEX。

## 6. RAG 管线

### 6.1 入库（ARQ 异步作业，MySQL 为真源）
格式 .pdf/.docx/.txt/.csv/.md；`parse(path)->list[Block]`。
- PDF：PyMuPDF 按页；扫描判定→RapidOCR。
- docx python-docx 保原序；csv pandas 按行；md 按标题留 section；txt 直读。
- **表格铁律**：表格是不可切分单元，序列化 Markdown 管道表 + 章节标题注入。
- 幂等：content_hash 未变跳过重嵌；落库前 `delete(expr doc_id==X)` 再 upsert。

### 6.2 切分（中文感知 + doc-type-aware + Parent-Child）
分隔符 `["\n\n","。！？…!?","；;\n","，,、"," ",""]`，句级 overlap；chunk_size≈500 中文字、overlap 60-100、上限 900；md 按标题不跨标题；Parent-Child（child 400 字 embed，parent ≤1500 字喂 LLM）。

### 6.3 Embedding（DashScope 1024d）
自写 `DashScopeTextEmbedding` 区分 text_type；batch=10、Semaphore(8)；tenacity 重试 429/5xx；sha256→向量缓存；L2 normalize；校验 len==1024。

### 6.4 检索（两阶段 + 门控）
- Stage1 混合召回：dense top30 + BM25 top30 → RRF(k=60) → top20。
- Stage2 重排：bge-reranker-v2-m3 → top6，sigmoid 化。
- 门控：sigmoid<0.35 丢弃；空→拒答分支。

### 6.5 Query 前处理（克制）
离线守卫（问候/感谢/告别/身份认知 直接回复，零 LLM 调用）→ 多轮改写 → 高置信实体抽取（正则 SKU + 词典）→ 直接 embed。**跳过** HyDE / Multi-query / 独立 ML 分类器。

**无关问题兜底**：检索门控后无结果时，不再硬拒答，而是走独立的「闲聊引导」提示词调用 LLM——友好接住话题 + 说明擅长范围 + 举例引导用户回到商品咨询（见 §6.7）。

**歧义追问（clarify）**：命中品类词但未指定具体商品（如「手机多少钱」）时，规则 + 静态商品词典检测歧义 → 返回候选追问（一次性，配合多轮改写闭环），不猜错、不编造。见 `app/rag/clarifier.py`。

### 6.6 多轮（检索与生成分离）
- 检索侧：最近 6 条 + 当前问 → 自包含 query；启发式跳过；短句/代词句才改写；双路召回兜底。
- 生成侧：最近 3 轮 QA + 当前问并入 prompt。

### 6.7 生成与接地
System prompt：只依据 context；数字逐字出自原文；不足→"知识库未收录"；引用仅编号内 [1][2]。
接地五道：context 限窗口 / system 强约束 / 引文白名单 / 门控空 context 模板 / 数字后检。

**双提示词**：命中知识库 → `RAG_SYSTEM_PROMPT`（严格接地）；门控后无结果 → `OFFTOPIC_SYSTEM_PROMPT`（闲聊引导 + 引导回流，仍禁止编造商品信息）。

### 6.8 引文（结构化数据）
top6 编号注入；生成后引文清洗（仅保留真实编号）；持久化 sources_json。

### 6.9 评测（双层解耦）
金标集 ~150 条（人工，60% 单事实/20% 多源/10% 条件数值/10% 不可答）。
- 检索层：Recall@5/10、MRR、NDCG；参数矩阵。
- 端到端：claim-level faithfulness / 引文准确率 / 不可答不编造率=1.0。
- CI gate：Recall@10≥0.9、faithfulness≥0.85、no-fabrication==1.0。

## 7. 后端设计

**请求流**：中间件(RequestID/SecurityHeaders/CORS/slowapi) → Router(Pydantic v2 + RBAC) → Service → Repository(owner 限定) → MySQL。

**DI**：FastAPI 原生 `Depends` + `AppState`（进程单例）；`require_role("admin")` 依赖工厂。

**错误/日志**：typed DomainError → 统一 envelope `{code,message,detail,request_id}`；structlog + contextvars Request-ID。

**Auth**：JWT access(15min, PyJWT) + refresh(256-bit opaque, 只存 sha256, 旋转链, HttpOnly cookie 30d)；argon2id；RBAC；登录限速 5/min。

**安全**：SQL 参数化（SQLAlchemy ORM）；**输入校验**（用户名白名单正则 `[A-Za-z0-9_一-龥]{3,64}` + 密码规则，Pydantic 在 API 边界完成，作为参数化之外的第二道防线）；LLM prompt 用户文本作不可信数据；IDOR 靠 owner 限定；上传 MIME sniff + storage_key=uuid4；pydantic-settings 分组 + prod 坏配置 fail-fast；安全头齐全。

**API**：auth / KB(admin) / chat / health(/healthz /readyz /diagnostics)。

**SSE 线协议**：`POST /api/v1/conversations/{id}/stream`；named events start/retrieval/citations/clarify/token/usage/done/error；心跳 ~15s；断连持久化 aborted。

## 8. 前端设计

- feature-first 纵向切片；TS strict；**contract-first via OpenAPI**（openapi-typescript 生成类型 + CI 漂移门）+ zod 运行时校验。
- 视觉：**科技蓝紫玻璃拟态**（glassmorphism 玻璃卡片 + 蓝紫渐变 #4F7CFF→#A855F7）；**每页独立背景色**（路由级切换）；Noto Sans SC 字体；**侧边栏可折叠 + 响应式**（<768px 滑出式 + 遮罩）。
- Pinia 四 store（auth/chat/kb/app）；仅 app 持久化；refresh 只进 HttpOnly cookie。
- 401 单飞行刷新；区分 401/403。
- SSE 用 fetch + ReadableStream（不用 EventSource）；token 增量 rAF 合并 flush；看门狗；显式幂等重试。
- Chat 三栏 UI；markdown 消毒 + GFM 表格 + `[n]` 后处理；流式期间不逐帧重编译。
- 检索过程可视化：每条回答下方展示「🔍 检索 query + 改写标记 + 命中片段相关度条」，回答可追溯（呼应 §9 可观测）。
- KB 管理：状态轮询 useDocStatusPoller；ChunkViewer 停用坏块。
- Vite dev proxy streaming-safe；prod nginx proxy_buffering off。

## 9. DevOps / 可观测 / 测试

- 容器拓扑：三网络 tier 隔离；版本全 pin；mem_limit（milvus 6g）；非 root；GET_LOCK 保护迁移。
- 可观测四支柱：structlog / Prometheus(RAG 指标 incl. retrieval-miss canary) / Langfuse 自托管 / Sentry。
- 健康检查：/healthz 进程级、/readyz 组件级、/diagnostics 深检。
- CI/CD：trunk-based；PR 触发 job 零 API key（全 mock）；eval-nightly；deploy 带 readyz 健康门。
- 测试三 tier：unit(全 mock) / integration(真 MySQL/Redis/Milvus + Fake LLM/Embedding) / RAG eval(nightly 真模型)。

## 10. 分阶段路线图

- **Phase 1 骨架+Auth+数据模型+裸 happy path**：compose、Auth 全套、models、最小入库、单轮 ask、最小 Vue shell。
- **Phase 2 检索质量+多轮（ROI 最高）**：ARQ 异步入库、hybrid RRF、query 改写、多会话 UI、SSE 流式、第一个 eval harness。
- **Phase 3 生产级入库+admin 完备**：幂等重灌、删除 purge、改密+设备会话、reranker、admin 文档管理。
- **Phase 4 加固+可观测+测试+打磨（senior 信号）**：可观测、测试、CI、种子语料、README+架构图+eval 结果表。

**必须砍掉**：agentic sprawl、付费 rerank、自建文档管理 SaaS、Kafka/Celery fleet/K8s 提前基建、过度抽象、仪表盘过早、手搓框架已有能力、i18n/移动端镀金、云依赖。

## 11. 面试卖点（按杀伤力排序）

1. 反幻觉是架构出来的，不是一句 prompt。
2. 离线 eval harness 驱动每个配置决策（参数矩阵 + 结果表）。
3. 混合检索 dense+BM25 + RRF 是承重墙。
4. 入库即 async pipeline：MySQL 真源、Milvus 可重建派生索引（增量更新）。
5. 多轮靠 query rewrite + 真会话模型。
6. 可观测性能回答"它为什么这么答"。
7. 安全不是贴纸（argon2id / refresh 旋转 / owner 限定灭 IDOR / 对抗文档入 eval）。
8. 流式 SSE + 结构化引文 UX + admin KB manager 闭环。
9. 每个选型备好的裁决单。
10. 跨层一致性（OpenAPI 契约 + zod + 角色分工 + 健康/就绪分离）。

## 12. 风险与缓解

1. 外部 API 依赖 → 温度/token 受限 + embedding 缓存 + 最小化出境。
2. LLM 质量波动 → 多道削弱 + nightly eval + trace 可审计。
3. 扫描 PDF OCR 坏块 → sha256 缓存 + ChunkViewer 停用坏块。
4. 单机单点 / Milvus OOM → mem_limit + runbook + managed 超限路径。
5. RRF vs 加权 → 全配置化 + eval 数据决定。
6. prompt injection → corpus 视为数据 + 对抗文档入 eval。
7. MySQL↔Milvus 残留 → 作业终态校准 + 软删保引文。
8. ARQ 认知度 → 如实答 sync/async 代价。
9. 契约漂移 → gen:api + CI 门 + zod。
10. 范围蔓延 → avoid 清单 + 路线图硬约束。

## 13. 本地运行（无 Docker）与生产（Docker）

本地开发默认：SQLite（aiosqlite）+ Milvus Lite（嵌入式）+ Redis 缺省降级进程内 → `uv run uvicorn app.main:app` 即可跑，零基础设施。

生产形态：`docker compose up -d` 起 MySQL 8.4 + Milvus Standalone + Redis 7，`DATABASE_URL` / `MILVUS_URI` / `REDIS_URL` 切到对应服务（代码零改动）。

验证：`make test`(unit) / `make test-integration`(真依赖) / `make eval`(RAG 质量门)。
