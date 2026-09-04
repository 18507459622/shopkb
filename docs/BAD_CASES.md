# 开发过程中遇到的 Bad Case 与解决办法

> 本文记录从零搭建本项目时踩过的坑、根因分析、解决办法与可复用的经验。
> 这些既是"踩坑记录"，也是面试时能讲的"工程化 troubleshooting 能力"。

## 1. pymilvus 版本漂移到 3.0（API 不兼容）

**现象**：`pymilvus>=2.5.0` 在安装时被解析成了 `3.0.1`，导入 `pymilvus` 直接报错。

**根因**：我按 pymilvus 2.5 的 API 写的代码（`MilvusClient` / `DataType` / `Function` 等），但 `>=2.5.0` 的约束允许安装 3.x。3.0 是 breaking change 的大版本。

**解决**：锁死 `pymilvus>=2.5.0,<3.0`（最终装到 2.6.17）。

**经验**：对"新大版本"（0→1、2→3 这类）的依赖，永远用 `<主版本+1` 上界锁死；不要用裸 `>=`。AI/向量库生态迭代极快，这是高频坑。

## 2. `MILVUS_URI` 环境变量名与 pymilvus 全局变量冲突

**现象**：导入 pymilvus 时抛 `Illegal uri: [./data/milvus_lite.db], expected form 'http[s]://...'`。

**根因**：pymilvus 在 import 时会从**操作系统环境变量**读取 `MILVUS_URI` 并立即解析。而 `uv run` 会自动加载 `.env` 文件到进程环境，于是我 `.env` 里的 `MILVUS_URI=./data/milvus_lite.db`（本地文件路径）被 pymilvus 当成地址去解析，导致 import 阶段就崩。

**解决**：把配置项改名为 `VECTORSTORE_URI`，避开 pymilvus 保留的环境变量名。

**经验**：
- 第三方库会"偷读"环境变量（pymilvus 读 `MILVUS_URI/HOST/PORT/TOKEN` 等，openai 读 `OPENAI_API_KEY`），自己项目的配置项命名要避开这些保留名。
- `uv run` 会自动加载 `.env`（这是它内置的 dotenv 能力），所以 `.env` 里的变量会进进程环境——这也是为什么真正的密钥要放 `.env`（gitignored）而不是 `.env.example`。

## 3. SQLite 下 `BigInteger` 主键不自增

**现象**：`sqlite3.IntegrityError: NOT NULL constraint failed: users.id`。

**根因**：SQLite 只有 `INTEGER PRIMARY KEY` 才是 rowid 别名、才会自动自增；声明成 `BIGINT PRIMARY KEY` 时 SQLite 不会给它生成值，导致 id 为 NULL。

**解决**：`BigInteger().with_variant(Integer, "sqlite")` —— MySQL 用 BIGINT，SQLite 用 INTEGER（`with_variant` 让同一列类型按方言切换）。

**经验**：跨数据库（尤其 SQLite 作本地降级、MySQL 作生产）时，类型要按方言适配。`with_variant` 是 SQLAlchemy 处理这类差异的标准手段。

## 4. Milvus Lite 需要单独的 `milvus-lite` 包

**现象**：`MilvusClient(uri="./data/milvus_lite.db")` 报 `milvus-lite is required for local database connections. Please install: pip install pymilvus[milvus_lite]`。

**根因**：Milvus 本地嵌入式模式（Lite）是独立的二进制包，不随 `pymilvus` 核心安装。

**解决**：显式加 `milvus-lite>=2.4.0` 依赖。

**经验**：读第三方库的报错信息很关键——它直接告诉你要装什么。嵌入式/可选的能力通常拆成 extra，要用就得显式声明。

## 5. Milvus 标量字段为 `None` 导致 upsert 失败

**现象**：`MilvusException: FieldData 'page' has 0 rows, expected 2`，入库失败。

**根因**：md/txt/csv 这类没有页码的文档，chunk 里 `page=None`。Milvus 的 `upsert` 要求同批数据各字段行数一致，`None` 被当成"该字段无数据"（0 行），与其他字段的 N 行不一致。

**解决**：`page = block.page or 0`（无页码时用 0 兜底，0 表示"无页码"）。

**经验**：向量库的标量字段不要传 `None`，要么给默认值，要么在构建时把该字段省略。向量库和关系库对 NULL 的容忍度不同。

## 6. 真实 Bug：`IngestionJobRepository` 未导入（ruff F821 抓出）

**现象**：`upload` 时会 `NameError: name 'IngestionJobRepository' is not defined`（在跑 ruff 之前没暴露，因为冒烟测试没覆盖上传路径）。

**根因**：`kb/service.py` 里用了 `IngestionJobRepository` 和 `DocumentRepository`，但只在方法内部用 `from .repositories import DocumentRepository` 局部导入，`IngestionJobRepository` 根本没导入。

**解决**：在模块顶部统一 `from .repositories import DocumentRepository, IngestionJobRepository`，删掉方法内的局部导入。

**经验**：
- **静态检查（ruff/mypy）的价值就在这里**：这种"名字未定义"的错误，单测没覆盖到就漏了，但 lint 一次就能抓出来。所以 CI 里 lint 必须在前。
- 不要"偷懒"用函数内局部导入，除非真的有循环导入问题（本模块并没有）。

## 7. ruff 规范冲突的取舍（E501 / B008 / B905）

**现象**：ruff 报 103 个错，其中大头是三类。

**根因与解决**：
- **E501（行长 >100）**：主要是长字符串字面量（如 Milvus `output_fields` 列表），无法优雅换行。→ 加入 ignore（行长是风格，不是正确性）。
- **B008（函数调用在默认参数）**：FastAPI 的 `Depends(get_db)` 作默认参数是**官方惯用法**，B008 对 FastAPI 是误报。→ 加入 ignore（并注释说明原因）。
- **B905（`zip` 未加 `strict`）**：这是真问题，`zip` 默认静默截断长度不一致的序列。→ 显式加 `strict=False`（这里两列表长度一定一致，但显式声明意图）。

**经验**：lint 规则不是"全都要过"，要理解每条的语义——误报（B008）加 ignore 并注释；真问题（B905、F821）必须修。这才是成熟的工程态度，而不是"把 lint 全关了"或"无脑全改"。

## 8. StreamingResponse 与请求级 DB session 的生命周期陷阱（预防性设计）

**现象（预防）**：SSE 流式接口的 generator 在端点函数返回后才真正执行，此时 FastAPI 的 `Depends(get_db)` 依赖（yield 型）可能已随请求结束被回收，导致 generator 里用 session 时出错。

**根因**：`StreamingResponse` 的生成器生命周期与依赖注入的生命周期不完全重叠（历史上有版本差异）。

**解决**：流式 generator（`stream_chat`）**不依赖**请求级 `Depends` session，而是自己 `get_session_factory()` 开独立 session，分"落库→检索→生成→落库"三段各开短连接。

**经验**：凡是"请求结束后还在执行"的代码（后台任务、SSE generator、WebSocket），不要用请求级依赖；要自建资源、显式管理生命周期。

## 9. Windows 控制台 GBK 编码导致输出乱码（非代码问题）

**现象**：终端打印中文变 `����`，但数据本身是正确的 UTF-8。

**根因**：Windows 默认控制台编码是 GBK，而数据是 UTF-8。

**解决**：不影响功能（只是终端显示）；数据落库/返回都是 UTF-8。需要看中文时用 `PYTHONIOENCODING=utf-8` 或写入 UTF-8 文件再读。

**经验**：区分"数据错"和"显示错"——先确认编码在哪个环节出问题，别急着改代码。

## 10. Milvus Lite 集合被 release 导致 search 报错

**现象**：流式问答时报 `MilvusException: Collection 'product_kb' is in state 'released'; call load() before search/get/query`，检索失败、回答显示"回答失败"。

**根因**：Milvus Lite 在 `delete`（重灌时先删后写）或空闲后，会把 collection 从 loaded 置为 `released` 状态。而 `ensure_collection()` 只在「集合不存在」时才 `load_collection`，集合已存在时直接 return，没有重新 load。

**解决**：`ensure_collection()` 对"已存在"的分支也调用 `_ensure_loaded()`（try `load_collection`，幂等/异常吞掉），保证 search/get 前集合一定处于 loaded 状态。

**经验**：向量库的"集合存在"和"集合已加载(loaded)"是两个状态——存在不代表能查。这也是 Milvus Lite（嵌入式）和 Milvus Standalone（服务端）行为差异的典型坑：Lite 进程内资源会被释放，服务端则常驻。

## 11. SQLite 读回 naive datetime 导致 aware/naive 比较报错

**现象**：`POST /auth/refresh` 返回 500，报 `TypeError: can't compare offset-naive and offset-aware datetimes`。

**根因**：SQLite 的 DATETIME 不存时区，读出来是 **naive**（无时区）datetime；而代码里 `utcnow()` 返回 **aware**（有时区，`datetime.now(UTC)`）datetime。比较 `record.expires_at <= now` 时 naive vs aware 直接抛错。

**解决**：统一用 naive UTC——`utcnow()` 返回 `datetime.now(UTC).replace(tzinfo=None)`，模型列 `DateTime(timezone=True)` 改成 `DateTime`。

**经验**：SQLite / MySQL 的 DATETIME 都是"无时区"的。跨库项目（本地 SQLite + 生产 MySQL）应统一约定"所有时间都存 naive UTC"，避免 aware/naive 混用。PostgreSQL 才真正支持带时区。

## 12. Windows 记事本保存的 GBK 编码 txt 解析失败

**现象**：上传中文 txt 报"未解析出可入库的内容，请检查文件"，入库失败。

**根因**：解析器只用 UTF-8 读文件，GBK 编码的中文（Windows 记事本默认）被 `errors="ignore"` 全部丢弃，解析出空文本 → 无 chunk 可入库。

**解决**：增加 `_read_text_auto()`，按 `utf-8-sig → gb18030` 顺序尝试解码（gb18030 是 GBK 的超集，能兜住绝大多数中文编码）。

**经验**：中文文本文件编码五花八门（UTF-8 / GBK / GB2312），读取时要做编码探测——UTF-8 优先（严格模式），失败再回退 GB18030，而不是直接 `errors="ignore"` 静默丢字节。

## 13. el-upload 用 before-upload 手动上传导致文件 0 字节

**现象**：前端上传文件，后端收到 0 字节（`file_size=0`），入库报"未解析出可入库的内容"。

**根因**：前端用 `el-upload` 的 `before-upload` 钩子 + 手动 axios 上传（`return false` 取消默认上传），但该钩子传进来的 file 对象在这一模式下手动 FormData 序列化不可靠，文件内容没发出去。

**解决**：改用 Element Plus 官方推荐的 `:http-request` 自定义上传——`options.file` 是可靠的原始 File，`form.append('file', options.file)` 能正确发送。

**经验**：用 `before-upload` + `return false` 做手动上传是反模式；自定义上传应该用 `:http-request`（或 `:auto-upload="false"` + `on-change` + `file.raw`）。排查"文件 0 字节"类问题时，先确认是前端没发出去还是后端没读——用 curl 直接测后端接口能快速隔离。

---

## 总结

| # | 问题 | 类型 | 一句话解法 |
|---|---|---|---|
| 1 | pymilvus 3.0 不兼容 | 依赖 | 锁 `<3.0` |
| 2 | MILVUS_URI 撞名 | 配置 | 改名 VECTORSTORE_URI |
| 3 | SQLite BIGINT 不自增 | 跨库 | `with_variant(Integer, "sqlite")` |
| 4 | Milvus Lite 缺包 | 依赖 | 加 `milvus-lite` |
| 5 | page=None upsert 失败 | 数据 | `page or 0` |
| 6 | 未导入名字 | 真 bug | lint 抓出，顶部导入 |
| 7 | ruff 规范取舍 | 规范 | 误报 ignore、真问题修 |
| 8 | SSE session 生命周期 | 设计 | generator 自管 session |
| 9 | 控制台乱码 | 显示 | 非代码问题 |
| 10 | Milvus Lite released | 数据 | search 前确保 load |
| 11 | naive/aware datetime | 跨库 | 统一存 naive UTC |
| 12 | GBK 编码 txt | 数据 | 编码探测 UTF-8→GB18030 |
| 13 | el-upload 手动上传 0 字节 | 前端 | 改用 :http-request |
