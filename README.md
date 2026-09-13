# 电商商品知识库 RAG 问答系统

基于 **LangChain** 的电商商品知识库 RAG 问答系统。用户提问商品相关问题，系统**严格基于知识库**回答并**展示引用片段**，支持多用户多会话、历史持久化、知识库管理与权限控制。

> 完整设计文档见 [docs/DESIGN.md](docs/DESIGN.md)；评测结论见 [eval/RESULTS.md](eval/RESULTS.md)、[eval/HYBRID.md](eval/HYBRID.md)；踩坑记录见 [docs/BAD_CASES.md](docs/BAD_CASES.md)。

## 功能特性

- **知识库问答**：混合检索（dense 向量 + BM25 稀疏，RRF 融合）+ 相关度门控，反幻觉架构化（证据约束 + 拒答路径 + 引文白名单）。
- **交叉编码重排（可选）**：`bge-reranker-v2-m3` 已接入代码，但**默认关闭且未做过实测**，开关见 `RERANKER_ENABLED`。
- **引用片段展示**：回答附带来源（文档名/页码/相关度/片段），可点击查看。
- **多用户多会话**：每用户独立会话，历史跨登录持久化，会话自动标题/重命名/删除。
- **认证与权限**：注册/登录/改密；JWT access(15min) + refresh HttpOnly cookie 旋转 + argon2id；admin 专属知识库管理。
- **知识库管理（仅 admin）**：上传 PDF/docx/txt/md/csv → 异步入库（解析→切分→向量化→写索引），状态轮询、重新入库、删除。
- **流式输出**：SSE 流式回答 + 引用边流边出。
- **用户管理（仅 admin）**：用户列表、启用/禁用、角色管理、统计看板。
- **无关问题兜底引导**：与知识库无关的问题不再硬拒答，模型会友好接话、说明擅长范围并引导用户回到商品咨询。
- **输入安全**：用户名白名单正则校验 + SQLAlchemy 参数化查询，从 API 边界到 ORM 双层防 SQL 注入/XSS。
- **现代 UI**：科技蓝紫玻璃拟态、每页独立背景色、响应式布局、侧边栏可折叠、模型回复带表情。

## 技术栈

| 层 | 选型 |
|---|---|
| 生成 | DeepSeek `deepseek-flash`（OpenAI 兼容） |
| 向量化 | 通义 DashScope `text-embedding-v3`（1024 维） |
| 向量库 | Milvus Standalone（本地降级 Milvus Lite） |
| 关系库 | MySQL（本地降级 SQLite） |
| 缓存/队列 | Redis（本地缺省降级进程内） |
| 后端 | Python 3.12 + FastAPI + SQLAlchemy 2.0 async + LangChain |
| 前端 | Vue 3 + TypeScript + Element Plus + Vite + Pinia |
| 重排 | bge-reranker-v2-m3（可选） |

## 架构选型：为什么热路径不用 LangGraph 编排

问答链路的编排有两种选择：全流程套一层 LangGraph 图，或者用 LCEL 链 + 只在需要分支的地方上图。这里选了后者：

```
解析 → 切分 → 向量化 → 检索（dense + BM25 → RRF）→ 门控 → 接地生成 → 引文清洗   ← LCEL 链（热路径）

                    改写？ ─┬─→ 接地回答
                           ├─→ 兜底引导（知识库无关问题）
                           └─→ 歧义追问（命中品类未指定型号）                    ← LangGraph StateGraph（外层分支）
```

**理由**：

| 决策 | 选择 | 理由 |
|---|---|---|
| 热路径 | **LCEL 链** | 检索 → 生成是一条直线，没有状态回退、没有循环、不需要人工介入。上图只会带来多余的调度开销和一层抽象 |
| 分支 | **LangGraph StateGraph** | 只有"是否改写 / 兜底 / 追问"这三处是真正的条件分支，用图表达最清楚 |
| 会话持久化 | **MySQL，不用 LangGraph checkpoint** | 会话历史要能被前端按会话 CRUD、要能跨登录保留、要能自动起标题——这些是业务需求，checkpoint 不承担这个职责。**MySQL 是唯一真源** |

**什么时候该换成图编排**：如果需要多步自主检索（模型自己决定检索几次、要不要换词重试）、需要工具调用、需要人工介入闸门——那时候热路径上图才有收益。当前场景这三种需求都不存在。

## 可观测性

分两层：**请求级**（已有）与 **LLM 调用级**（新补）。

| 能力 | 现状 | 落点 |
|---|---|---|
| 请求级追踪 | ✅ 每个请求分配 `request_id`（也接受上游 `X-Request-ID`），存 ContextVar 并绑定 structlog 上下文 | `app/core/middleware.py` |
| 响应可关联 | ✅ 响应头回写 `X-Request-ID`；所有错误 envelope 带 `request_id` | 同上 |
| 结构化日志 | ✅ structlog（dev 控制台彩色 / prod JSON） | `app/core/logging_conf.py` |
| **LLM 调用级追踪** | ✅ 模型名 / 耗时 / token 用量 / 异常，经 LangChain 回调自动采集 | `app/core/observability.py` |
| **Token 成本统计** | ✅ 按可配置单价估算（`PRICE_INPUT_PER_M` / `PRICE_OUTPUT_PER_M`），**不写死价格** | 同上 |
| **失败步骤归因** | ✅ 检索 / 改写 / 生成 / LLM 调用各自计时，失败进 `failures_by_step` | 同上 |
| **用户是否接受结果** | ✅ `POST /api/v1/chat/messages/{message_id}/feedback` | `app/modules/chat/` |
| 指标查询 | ✅ `GET /api/v1/admin/metrics`（仅 admin，token 成本属运营信息） | `app/modules/admin/router.py` |

**trace_id 复用 `request_id`**，不另造一套 ID 体系 —— 日志里看到的 `request_id`、响应头里的 `X-Request-ID`、指标里的 `trace_id` 是同一个值，排查时不用做映射。

**用 LangChain 回调而不是逐点埋点**：`ChatOpenAI` 在 pipeline（生成）与 rewriter（多轮改写）两处被调用，还有流式与非流式两种姿势，回调一次全覆盖，业务代码几乎不用改。`on_llm_end` 同时兼容 `llm_output.token_usage`（非流式常见）与 `message.usage_metadata`（流式聚合后常见）—— 只看一处会让成本统计系统性偏低。

**LLM 调用同时落 JSONL**（`logs/llm_trace.jsonl`，一行一个事件，gitignore）：不依赖任何外部平台就能用 `grep` / `jq` / pandas 查问题。

**可观测性自身 fail-open**：日志写不进去（磁盘满 / 权限不足）只记一条 warning，绝不让业务请求失败；但阶段异常照常向上抛，不为了观测吞异常。两者都有测试覆盖。

> ⚠️ `config.py` 里定义了 `langfuse_host` / `langfuse_public_key` / `langfuse_secret_key` / `sentry_dsn` 四个配置项，但**全代码库没有任何地方引用它们**（见「已知限制」）。
> 另外指标是**单进程内存聚合**：uvicorn 多 worker 时每个 worker 各算各的，要全局数字需要外部聚合。

## 评测

离线评测是本项目投入最多的地方，也是它区别于 Demo 的地方：

| 语料 | 金标 | 用途 |
|---|---|---|
| `eval/corpus/` 14 篇 | `eval/golden.jsonl` 42 条（34 可答 + 8 不可答） | 主评测集，验反幻觉与引用 |
| `eval/hard/corpus/` 180 篇 | `eval/hard/golden.jsonl` 172 条 | 高干扰集，验混合检索的真实增益 |

**关键结论**（完整数据见 [eval/HYBRID.md](eval/HYBRID.md)）：

- 主评测集上 dense 与混合检索**都是满分**——`top_k` 与总 chunk 数相近时指标失去区分度（\*"天花板效应"\*）。这不是"效果完美"，而是**这把尺子量不出来**，所以另建了高干扰语料。
- 高干扰集（180 篇 / 172 条）上：**MRR 0.917 → 0.979、NDCG@K 0.930 → 0.976、FullCoverage@5 0.962 → 0.985**；按题型拆开，增益集中在**基础型号查询**（42 条，MRR 0.788 → 0.968）。
- **根因**：型号名是其他文档名的子串时（"星辰 X1" ⊂ "星辰X1Standard"），dense 会偏好后缀更长、语义更"完整"的那篇（"标准版"≈"基础款"，语义相似反而帮倒忙）；BM25 的词面精确匹配把这条系统性偏差纠了回来。
- 评测脚本带**门槛与退出码**（`eval/gate.py`），指标退化会让命令失败；`--mock` 模式用假 LLM/假 Embedding 跑，可零成本验证评测逻辑自身正确（含"裁判解析失败必须 fail-closed"的故障注入）。

## 已知限制

1. **模型名口径**：`.env` 里的 `LLM_MODEL` 若仍写 `deepseek-v4-flash`，那是 DeepSeek 的废弃别名（会路由到 `deepseek-flash`），建议直接改成 `deepseek-flash`。
2. **`langfuse_*` / `sentry_dsn` 是死配置**：定义了但没有任何代码引用，见上。
3. **重排未实测**：`bge-reranker-v2-m3` 的代码路径已接入，但默认关闭、没有跑过评测，因此简历/文档里不应声称有重排增益。
4. **高干扰语料是合成的**：型号名（星辰/星河/星环…）与参数均为程序生成，用于制造词面子串干扰，不代表真实商品分布；它证明的是**方法本身的偏差**，不是线上分布下的绝对指标。
5. **`backend/data/` 下有运行时产物**（Milvus Lite 库、SQLite、上传文件），已在 `.gitignore` 中排除，clone 后需要重新上传文档入库。
6. **单元测试 116 个但覆盖集中在纯函数层**（切分、引文、检索、评测指标、schema、可观测性）；路由 / 鉴权 / 异步入库等需要 DB 与外部服务的路径没有自动化测试。
7. **可观测性指标是单进程内存聚合**：uvicorn 多 worker 下每个 worker 各算各的，`/api/v1/admin/metrics` 只反映当前 worker。要全局数字需要接 Prometheus 或 Langfuse —— `config.py` 里预留了 `langfuse_*` 配置但尚未接线。
8. **生成阶段的流式耗时未单独计时**：检索与改写有阶段计时，但 SSE 流式生成的耗时目前只能从 LLM 回调的 `duration_ms` 侧面看出。
9. **非 HTTP 上下文下 `trace_id` 为空**：`new_trace()` 复用中间件注入的 `request_id`，在没有请求上下文时（评测脚本、离线批处理）返回空串而**不伪造 ID**。因此 `eval/` 跑出来的 JSONL 日志没有 trace 关联——这是刻意的取舍，但排查评测期的模型问题需要按时间戳对齐。

## 快速开始

### 方式一：本地开发（无需 Docker）

只需 Python（用 uv 管理）+ Node：

```bash
# 1. 配置环境变量（API Key 必填）
cp .env.example .env          # 编辑 .env 填入 LLM_API_KEY / EMBEDDING_API_KEY

# 2. 启动后端（自动建表 + 种子 admin/123456）
cd backend
uv sync --python 3.12
uv run uvicorn app.main:app --reload --port 8000

# 3. 启动前端（另一个终端）
cd frontend
npm install
npm run dev                   # http://localhost:5173
```

浏览器打开 http://localhost:5173，用 `admin / 123456` 登录，在「知识库管理」上传 `data/samples/` 里的示例文档，即可开始问答。

### 方式二：Docker 全栈（MySQL + Milvus Standalone + Redis）

```bash
# 配置 .env（含 JWT_SECRET + API Keys），然后：
docker compose up -d --build
# 前端 http://localhost  |  后端 API http://localhost:8000/api/docs
```

## 配置项（.env）

见 [.env.example](.env.example)，关键项：

| 变量 | 说明 |
|---|---|
| `DATABASE_URL` | 本地默认 SQLite；Docker 用 MySQL |
| `VECTORSTORE_URI` | 本地默认 `./data/milvus_lite.db`；Docker 用 `http://milvus:19530` |
| `LLM_API_KEY` | DeepSeek API Key |
| `EMBEDDING_API_KEY` | 阿里云百炼（DashScope）API Key |
| `JWT_SECRET` | 生产环境请改为随机 32 字节 |
| `RERANKER_ENABLED` | 是否启用 bge-reranker 重排（首次需下载模型） |

> 注意：不能用 `MILVUS_URI` 作为变量名（pymilvus 会从环境全局读取同名变量导致冲突），故用 `VECTORSTORE_URI`。

## 测试

```bash
cd backend
uv run pytest tests/unit -q          # 单元测试（116 个，无外部依赖）
uv run pytest tests/unit --cov=app --cov-report=html  # 覆盖率报告（生成 htmlcov/）
uv run ruff check app tests           # 静态检查
```

## 项目结构

```
shopkb/
├─ backend/app/
│  ├─ main.py              # 入口：中间件/CORS/异常处理/健康检查
│  ├─ core/                # config/database/security/deps/exceptions/logging/middleware
│  ├─ models.py            # SQLAlchemy 2.0 模型（关系真源）
│  ├─ modules/{auth,kb,chat,admin}/   # 按领域分层 router/service/repositories
│  └─ rag/                 # 纯 Python RAG 类：parser/chunker/embed/vectorstore/retriever/reranker/rewriter/citation/pipeline
├─ frontend/src/           # Vue3 + TS：api/stores/views/components
├─ data/samples/           # 示例商品文档（上传用）
├─ docs/DESIGN.md          # 完整设计文档
├─ docker-compose.yml      # MySQL + Milvus + Redis + 前后端
├─ eval/                   # 离线评测（见下方「评测」一节）
│  ├─ corpus/ + golden.jsonl         # 常规：14 篇语料 / 42 条金标
│  ├─ hard/                          # 高干扰：180 篇语料 / 172 条金标（1 个型号 1 篇文档）
│  └─ retrieval / e2e / sweep / features / hybrid_compare / breakdown_by_type / analyze_failures
```

## 主要 API

- `POST /api/v1/auth/{register,login,refresh,logout,change-password}` + `GET /auth/me`
- `POST /api/v1/chat/conversations/{id}/stream`（SSE：start/token/citations/usage/done/error）
- `GET/POST /api/v1/chat/conversations`、`GET /chat/conversations/{id}/messages`
- `POST /api/v1/kb/documents/upload`、`GET /kb/documents`、`DELETE /kb/documents/{id}`（admin）
- `GET /api/v1/admin/users`、`GET /admin/stats`、`GET /admin/metrics`（admin）
- `POST /api/v1/chat/messages/{message_id}/feedback`（回答点赞/点踩）
- `GET /healthz`、`GET /readyz`
