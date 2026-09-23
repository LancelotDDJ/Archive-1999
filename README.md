# 重返未来：1999知识库（v2.0 完全重制）

基于 [res1999.huijiwiki.com](https://res1999.huijiwiki.com/wiki/首页)（灰机 wiki）全站内容构建的
**多用户全栈知识问答系统**：游戏知识问答、剧情推理分析、剧情推演模拟与数据可视化。

> 本项目在原单机版（`1999数据库`）基础上完全重制：除游戏真实资料数据外，
> 全部代码、架构与文档从零编写。整体架构设计见 `docs/architecture.md`。

## 功能总览

| 能力 | 说明 |
|------|------|
| 知识问答 | 严格模式（数值/查询，防幻觉）+ 推理模式（剧情因果，事实/推断显式区分），全部附 wiki 来源引用 |
| Agent 深度分析 | ReAct 工具循环（混合检索/全库扫描/内链反查/整页读取/实体清单），统计/盘点/列举类问题自动触发 |
| 剧情推演 | 五阶段工作流：全库收集 → 角色卡 → 回合制多角色推演 → 多立场辩论 → 结构化报告（含完整故事与分支故事） |
| 用户系统 | 注册/登录/JWT 双令牌/角色权限（普通用户·管理员）；用户数据仅本人与管理员可见 |
| 自动更新 | 内置调度器：每月 1 日 03:00 自动增量更新知识库（recentchanges），索引热重载无需重启 |
| 界面 | 基金会制式界面（复刻官方视觉）、开门动画、徽标三连击彩蛋与项目简介、背景视频三模式播放，桌面+移动端响应式 |

## 快速开始

```bash
# 1. 环境：Python 3.11+，安装依赖
pip install fastapi "uvicorn[standard]" pydantic requests numpy jieba rank_bm25 \
            fastembed onnxruntime beautifulsoup4 lxml PyJWT

# 2. 配置 LLM（可选；无 Key 自动走证据模式）
cp config.example.json config.json   # 填入 DeepSeek 等 OpenAI 兼容 Key

# 3. 启动（首次自动建表并创建管理员，初始密码打印到控制台与 artifacts/logs/bootstrap.txt）
python run.py        # 或双击 run.bat

# 4. 打开浏览器
#    http://127.0.0.1:8765  → 登录后进入问答大厅
```

局域网访问：`ARCHIVE_HOST=0.0.0.0 python run.py`。

## 数据管线

```bash
python -m server.pipeline build      # 首建：解析 → 链接图 → 索引（本项目已内置 2026-09 快照种子）
python -m server.pipeline crawl      # 全量重抓 wiki（断点续传，Cloudflare 自适应限速）
python -m server.pipeline update     # 手动增量更新（通常无需执行：每月 1 日自动）
python -m server.tests.regression    # 端到端回归（服务需已启动）
```

## 目录结构

```
├── docs/            # 架构设计与运维文档
├── server/          # 后端（FastAPI）
│   ├── app/         #   api(路由) · services(业务) · core(领域) · infra(设施) · schemas(DTO)
│   ├── pipeline/    #   数据管线：爬虫 / 解析 / 索引 / 增量更新（CLI）
│   └── tests/       #   回归测试
├── webui/           # 前端（原生 ES Modules，无构建步骤）
│   ├── css/         #   tokens · base · layout · components · chat · pages · responsive
│   ├── js/modules/  #   api · auth · theme · markdown · charts · ui/* · pages/*
│   └── assets/      #   主题纹理 / 昼夜视频 / 徽标
├── data/            # 运行时数据：raw(wiki 抓取) · kb(索引) · models(向量模型) · app.db
├── artifacts/       # 非源码产物（与源码严格分离）
│   ├── logs/        #   服务/调度/更新/引导日志
│   ├── reports/     #   建库报告 · 推演存档(simulations/)
│   ├── test-output/ #   回归测试报告
│   └── analysis/    #   分析草稿
└── run.py / run.bat
```

## 技术栈

FastAPI 0.141 · SQLite(WAL) · PyJWT · fastembed(bge-small-zh-v1.5, ONNX 本地推理) ·
rank_bm25 + jieba · 原生 ES Modules + ECharts · DeepSeek（OpenAI 兼容，可插拔）

## 数据与更新

- 知识库：**73,631 个 wiki 页面 → 101,810 个结构化知识块；5,990 个实体；192 个别名**（2026-09 快照）。
- 每月 1 日 03:00 内置调度自动增量更新；管理员亦可在后台手动触发（增量/全量）。
- 回答强制附带来源引用，可溯源至原 wiki 页面。
