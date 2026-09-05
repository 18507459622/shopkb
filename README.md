# 电商商品知识库 RAG 问答系统

基于 **LangChain** 的电商商品知识库 RAG 问答系统，浏览器操作。用户提问商品相关问题，系统**严格基于知识库**回答并**展示引用片段**，支持多用户多会话、历史持久化、知识库管理与权限控制。

> 生产级 / 可写进简历的项目。完整设计文档见 [docs/DESIGN.md](docs/DESIGN.md)。

## 功能特性

- **知识库问答**：混合检索（dense 向量 + BM25 稀疏）+ 交叉编码重排 + 相关度门控，反幻觉架构化（证据约束 + 拒答路径 + 引文白名单）。
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
| 生成 | DeepSeek `deepseek-v4-flash`（OpenAI 兼容） |
| 向量化 | 通义 DashScope `text-embedding-v3`（1024 维） |
| 向量库 | Milvus Standalone（本地降级 Milvus Lite） |
| 关系库 | MySQL（本地降级 SQLite） |
| 缓存/队列 | Redis（本地缺省降级进程内） |
| 后端 | Python 3.12 + FastAPI + SQLAlchemy 2.0 async + LangChain |
| 前端 | Vue 3 + TypeScript + Element Plus + Vite + Pinia |
| 重排 | bge-reranker-v2-m3（可选） |

## 快速开始

### 方式一：本地开发（无需 Docker）

只需 Python（用 uv 管理）+ Node：

```bash
# 1. 配置环境变量（API Key 必填）
cp .env.example .env          # 编辑 .env 填入 DEEPSEEK_API_KEY / EMBEDDING_API_KEY

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
uv run pytest tests/unit -q          # 单元测试（30 个，无外部依赖）
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
└─ eval/                   # 评测金标集（Phase 2 扩展）
```

## 主要 API

- `POST /api/v1/auth/{register,login,refresh,logout,change-password}` + `GET /auth/me`
- `POST /api/v1/chat/conversations/{id}/stream`（SSE：start/token/citations/usage/done/error）
- `GET/POST /api/v1/chat/conversations`、`GET /chat/conversations/{id}/messages`
- `POST /api/v1/kb/documents/upload`、`GET /kb/documents`、`DELETE /kb/documents/{id}`（admin）
- `GET /api/v1/admin/users`、`GET /admin/stats`（admin）
- `GET /healthz`、`GET /readyz`
