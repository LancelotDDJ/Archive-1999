# 《重返未来：1999》知识库 — 整体架构设计

> 版本 v2.0（完全重制）｜ 2026-09-23
> 本设计在原项目（`1999数据库`，本地单机 RAG）基础上完全重制：除游戏真实资料数据外，
> 全部代码、架构、文档从零编写。目标：**多用户、可部署、模块化、数据每月自更新的全栈知识问答系统**。

---

## 1. 设计目标与原则

| # | 目标 | 落实方式 |
|---|------|---------|
| 1 | 数据准确性最高优先 | 全量抓取灰机 wiki（res1999.huijiwiki.com），每月 1 日自动增量更新；回答强制证据引用；数值题严格模式防幻觉 |
| 2 | 真正多用户的 Web 服务 | 注册/登录/JWT/RBAC；普通用户数据仅本人与管理员可见（仓储层强制隔离） |
| 3 | 先进全栈架构 | FastAPI 异步后端 + 原生 ES Module 前端（无构建依赖）+ SQLite(WAL) + ONNX 本地向量推理 |
| 4 | 完全模块化 | 分层架构：api → services → core / infra；单向依赖，接口隔离，无循环依赖 |
| 5 | 沿用并美化原 UI | 三主题设计令牌、开门动画、徽标交互、昼夜背景视频全部保留；新增登录页/后台；完整响应式 |
| 6 | 源码与产物严格分离 | `artifacts/` 独立存放日志/测试输出/分析报告，内部再按类别分子目录 |

## 2. 技术选型

| 层 | 选型 | 理由 |
|----|------|------|
| 后端框架 | FastAPI 0.141 + Uvicorn | 异步、自动 OpenAPI 文档、依赖注入天然支持模块化 |
| 认证 | PyJWT（access+refresh 双令牌）+ `hashlib.pbkdf2_hmac`（26 万轮加盐） | 标准、零额外重依赖 |
| 数据库 | SQLite（WAL 模式）+ 自研轻量 DAL（stdlib sqlite3 + 仓储模式） | 单文件部署、事务可靠、零运维；DAL 层隔离 SQL |
| 向量检索 | fastembed 0.8 / ONNX Runtime + `BAAI/bge-small-zh-v1.5`（512 维，本地权重） | CPU 离线推理，中文 SOTA 小模型 |
| 关键词检索 | rank_bm25 + jieba(cut_for_search) | 与向量互补的中文召回 |
| 数据管线 | requests + BeautifulSoup4(lxml)，自研全局限速/断点续传爬虫 | MediaWiki API 三通道（render/wikitext/DataJSON） |
| 前端 | 原生 HTML/CSS/ES Modules（无构建步骤），ECharts 本地副本做可视化 | 部署即拷贝；设计令牌体系与原版一致 |
| LLM | OpenAI 兼容协议（默认 DeepSeek `deepseek-chat`），可插拔，证据模式零依赖兜底 | 与原项目实测结论一致 |
| 调度 | 内置轻量调度线程（每日检查，每月 1 日 03:00 触发增量更新）+ 管理员手动触发 | 服务进程内自洽，无需外部 cron |

## 3. 总体架构图

```
┌──────────────────────────── 浏览器客户端 ────────────────────────────┐
│ webui/  登录页 · 问答大厅 · 管理后台（ES Modules, 三主题）           │
└──────────────────────────────┬───────────────────────────────────────┘
                       HTTP/JSON · NDJSON 流 · JWT
┌──────────────────────────────▼───────────────────────────────────────┐
│ server.app.api        路由层：请求校验 / 依赖注入 / 响应组装（不含业务） │
│  v1/auth · v1/users · v1/qa · v1/story · v1/conversations ·          │
│  v1/stats · v1/system                                                │
├──────────────────────────────▼───────────────────────────────────────┤
│ server.app.services   业务服务层（彼此不互相 import，仅依赖 core/infra）│
│  auth_service · user_service · qa_service · agent_service ·          │
│  story_service · conversation_service · kb_admin_service             │
├──────────────────▼───────────────────────────▼───────────────────────┤
│ server.app.core                       server.app.infra               │
│  领域核心（无框架依赖）                 技术设施（对接外部世界）         │
│  retrieval/ 引擎·实体·意图·提示词       db · llm_client · embedder   │
│  config · security · errors           scheduler · kb_store(热重载)   │
├───────────────────────────────────────▼───────────────────────────────┤
│ data/   kb/(chunks·entities·aliases·links·index) · app.db · models/   │
│ server/pipeline/  离线数据管线：probe→crawl→parse→index→update（CLI）  │
└────────────────────────────────────────────────────────────────────────┘
```

**依赖规则（强制）**：
- `api` 只能调 `services`；`services` 只能调 `core`/`infra`；`core` 不依赖 FastAPI；
- `services` 之间禁止互相 import（共享能力下沉到 `core`/`infra`）；
- `pipeline` 复用 `core` 的解析/索引接口，但不被运行时服务 import（离线工具链）；
- 所有跨层数据用 `schemas/` 中的 pydantic DTO，禁止把 DB 行对象/检索内部结构泄漏到 api 层。

## 4. 目录结构

```
F:/Archive-1999/
├── docs/                        # 文档（本文件、RUNBOOK、API）
├── server/
│   ├── app/
│   │   ├── main.py              # 应用组装（仅挂载与中间件）
│   │   ├── api/
│   │   │   ├── deps.py          # 依赖注入：当前用户/管理员/检索器/限流
│   │   │   └── v1/              # auth.py users.py qa.py story.py
│   │   │                        # conversations.py stats.py system.py
│   │   ├── core/
│   │   │   ├── config.py        # 配置加载/校验（单例）
│   │   │   ├── security.py      # 密码哈希 / JWT 签发与校验
│   │   │   ├── errors.py        # 统一异常与错误码
│   │   │   └── retrieval/
│   │   │       ├── engine.py    # 混合检索（向量+BM25+实体+意图+多跳）
│   │   │       ├── store.py     # KB 数据装载（chunks/entities/aliases/links）
│   │   │       └── prompts.py   # 严格/推理/Agent/推演 提示词
│   │   ├── infra/
│   │   │   ├── db.py            # SQLite DAL + schema 迁移 + 仓储
│   │   │   ├── llm_client.py    # OpenAI 兼容客户端（chat/stream/tools）
│   │   │   ├── embedder.py      # bge ONNX 嵌入（懒加载单例）
│   │   │   ├── scheduler.py     # 每月 1 日自动更新调度线程
│   │   │   └── update_runner.py # 更新任务执行与日志
│   │   ├── services/            # 8 个业务服务（见 §6）
│   │   └── schemas/             # pydantic DTO（auth/qa/story/admin）
│   ├── pipeline/
│   │   ├── probe.py             # 站点探测（统计/命名空间/样本）
│   │   ├── crawler.py           # 全量抓取（限速/重试/断点续传）
│   │   ├── parser.py            # 渲染HTML→章节块 / wikitext→别名 / DataJSON→文本
│   │   ├── indexer.py           # 嵌入 + BM25 + 链接图 + meta
│   │   ├── updater.py           # recentchanges 增量更新
│   │   └── cli.py               # python -m server.pipeline <cmd>
│   └── tests/                   # 回归与 API 测试（输出落 artifacts/）
├── webui/
│   ├── index.html               # 问答大厅（沿用原视觉）
│   ├── login.html               # 登录/注册
│   ├── admin.html               # 管理后台
│   ├── css/  tokens.css · base.css · layout.css · components.css
│   │         · chat.css · pages.css · responsive.css
│   ├── js/
│   │   ├── lib/echarts.min.js
│   │   └── modules/  api.js auth.js theme.js store.js stream.js
│   │                 markdown.js charts.js ui/ pages/
│   └── assets/                  # 主题纹理 / 昼夜视频 / 徽标 SVG（源自原项目美术）
├── data/                        # 运行时数据（不视为源码）
│   ├── raw/                     # wiki 抓取存档（render/ data_ns/ wikitext.jsonl）
│   ├── kb/                      # chunks.jsonl entities.json aliases.json links_map.json index/
│   ├── models/bge-small-zh-v1.5/# ONNX 权重（本地，离线加载）
│   └── app.db                   # 用户/会话/日志/任务
├── artifacts/                   # 非源码产物（与源码严格分离）
│   ├── logs/        # 运行日志、调度日志、首启引导信息
│   ├── reports/     # 建库报告、更新报告、自检报告
│   ├── test-output/ # 回归/测试结果
│   └── analysis/    # 数据分析草稿等
├── config.example.json
├── run.py / run.bat             # 启动入口
└── README.md
```

## 5. 数据库设计（SQLite，WAL）

| 表 | 关键字段 | 说明 |
|----|---------|------|
| `users` | id PK, username UNIQUE, password_hash, display_name, role(`user`/`admin`), status(`active`/`disabled`), must_change_pwd, created_at, last_login_at | 账号与角色 |
| `refresh_tokens` | id PK, user_id FK, token_hash, expires_at, revoked, created_at | 可吊销刷新令牌（只存哈希） |
| `conversations` | id TEXT PK(uuid), user_id FK, title, kind(`qa`/`story`), created_at, updated_at | 会话归属用户 |
| `messages` | id PK, conversation_id FK, role, content, meta_json, created_at | 消息与元数据（来源/模式/步骤） |
| `qa_logs` | id, user_id FK, question, mode, model, cached, latency_ms, created_at | 问答统计与审计 |
| `update_jobs` | id, kind(`full`/`incremental`), status(`queued`/`running`/`ok`/`fail`), trigger(`schedule`/`manual`), started_at, finished_at, detail_json, log_path | 数据更新任务台账 |

**数据隔离**：所有按用户的数据查询在仓储方法签名上强制 `user_id` 参数；管理员走独立的 `*_as_admin` 方法；api 层无从绕过。会话/消息读取先校验归属（404 而非 403，防枚举）。

## 6. 服务层模块契约（摘要）

| 服务 | 职责（单一） | 主要接口 |
|------|-------------|---------|
| `auth_service` | 注册、登录、令牌签发/刷新/吊销、改密 | `register/login/refresh/logout/change_password` |
| `user_service` | 用户资料、管理员用户管理 | `get_profile/update_profile/list_users/set_role/set_status/reset_password` |
| `qa_service` | 严格/推理双模式问答编排、流式输出、答案缓存、多轮指代 | `ask(body, user) -> events 生成器` |
| `agent_service` | ReAct 深度分析（5 工具：search/scan/backlinks/page/entities） | `run_collect(question, history) -> events` |
| `story_service` | 剧情推演五阶段（收集→角色卡→回合推演→分支→两段式报告） | `run(theme, turns) -> events`，产物存档 `artifacts/reports/simulations/` |
| `conversation_service` | 会话与消息持久化（按用户） | `list/get/save/delete` |
| `kb_admin_service` | 索引状态、手动触发更新、任务台账、全站问答活跃 | `status/trigger_update/list_jobs/qa_activity` |

## 7. 检索与问答管线

- **混合检索**：向量 0.55 + BM25 0.45（min-max 归一）；命中实体页核心块保底召回 +0.35；意图规则（语音/技能/养成/属性/剧情/获取/攻略/推理）对章节与类型加权；纯 ASCII/数字实体要求分词边界；重定向别名归一。
- **问题路由**：`strict`（数值/查询）→ 严格提示词（只答资料所载）；`reason`（为什么/结局/命运…）→ 推理提示词（事实[n] / 推断显式标注）+ 多跳扩展（从首轮证据扫描关联设定页补检）；`agent`（多少/几个/统计/盘点/列出…或前端强制）→ ReAct 循环（≤10 轮）。
- **流式协议（NDJSON）**：`meta`(含来源/实体卡/模式) → `delta`* → `done`；Agent 额外 `agent` 步骤事件；推演额外 `phase/collect_step/worldview/turn_start/action/narrator/branch` 事件。事件协议与原版兼容，前端按 type 分发渲染。
- **缓存**：答案缓存 1h（键含问题+top_k+历史指纹）；索引热重载（mtime 监视）时先清缓存再作答。

## 8. 安全模型

1. 密码：`pbkdf2_hmac(sha256, 260_000)`，每用户随机盐，哈希串含算法与轮次（可升级）。
2. 令牌：access JWT 12h（`sub, role, type=access`）；refresh 30d，仅哈希入库，可吊销；刷新即旋转（旧令牌作废）。
3. RBAC：`require_user` / `require_admin` 依赖；管理端点二次校验 `status=active`。
4. 数据可见性：普通用户仅能读写本人会话/消息/统计；管理员可读全部并管理用户与数据更新。
5. 输入：所有路径/文件名参数白名单正则；SQL 全部参数化（DAL 强制）；前端输出统一转义。
6. 限流：`/qa/*` 每用户滑动窗口（默认 30 次/5 分钟，可配）。
7. 首启引导：`users` 为空时自动创建 `admin`，初始密码取 `ARCHIVE_ADMIN_PASSWORD`，否则随机生成并仅打印到控制台与 `artifacts/logs/bootstrap.txt`，登录后强制改密。
8. LLM Key：仅存服务端 `config.json`（实例配置，不入库/不入前端），接口不回显明文。

## 9. 数据更新机制

- **全量**：`pipeline.crawler` 四阶段（清单 → DataJSON 批量 → wikitext 批量 → 渲染 HTML 逐页），全局限速自适应（403 退避 ×1.45，成功 ×0.97），断点续传以磁盘为准。
- **增量**：`updater` 基于 `recentchanges`（ns0+ns3500，edit|new）自 `last_update` 起拉取变更页，整页重抓重切；未变更块按 `md5(page|section|text)` 复用向量；BM25 全量重建；写完 `meta.json` 触发服务端热重载。
- **调度**：`scheduler` 线程每小时检查一次——当日为每月 1 日且本月无成功记录时执行增量更新；全程写 `update_jobs` 台账与 `artifacts/logs/update-*.log`；管理员可在后台手动触发（full/incremental）或查看任务。
- **首建引导**：以原项目真实抓取存档（81,484 个原始文件，2026-09-13 快照）为种子数据，经**新管线**重新解析/索引建库；随后的月度更新使数据持续保鲜。

## 10. 前端设计

- **视觉沿用**：`tokens.css` 完整保留三主题（st-pavlov / manus / vertin）设计令牌、开门动画、徽标组件、昼夜背景视频、Art Deco 装饰母题、官方语义色（灵感/稀有度）。
- **美化与交互**：消息入场错落动画、来源卡片悬浮、主题切换全局过渡、骨架屏加载、输入区聚焦光晕。
- **响应式**：≥1200 完整大厅；768–1200 收紧侧栏；≤768 侧栏改抽屉（遮罩+滑出）、顶栏精简、消息全宽、触控目标 ≥44px、 composer 适配安全区与移动端键盘。
- **页面**：login（登录/注册双态）、index（问答大厅，含 Agent 开关与推演模式）、admin（用户管理、更新任务、全站问答活跃、系统状态）。

## 11. 部署与运行

- `run.py`（或 `run.bat`）一键启动：自检（KB/模型/DB）→ 建表迁移 → 引导管理员 → Uvicorn 服务（默认 `127.0.0.1:8765`，`ARCHIVE_HOST/PORT` 可覆盖；局域网 `0.0.0.0`）。
- 配置：`config.json`（实例，含 LLM），`config.example.json` 为模板。
- 回归：`python -m server.tests.regression`（10+ 固定场景断言），输出落 `artifacts/test-output/`。
- 验收门禁：认证流 / 权限隔离 / 三类问答 / 推演 / 会话 / 统计 / 更新台账 全链路冒烟。

## 12. 里程碑

1. **M1 骨架与基础设施**：目录/配置/日志/DAL/KB 装载/数据种子迁移
2. **M2 数据管线**：解析→索引→更新→调度（以种子数据建库）
3. **M3 用户系统**：auth/users/RBAC/隔离
4. **M4 检索问答**：引擎/qa/agent/story 服务与 API
5. **M5 前端三页**：登录/大厅/后台 + 响应式
6. **M6 验证与交付**：回归/冒烟/文档/脚本
