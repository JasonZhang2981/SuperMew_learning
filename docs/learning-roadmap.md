# SuperMew Learning Roadmap

本文档用于快速学习并上手 `SuperMew_learning` 项目。路线设计目标是：先跑通，再读懂主链路，再深入 RAG 和 Agent，最后通过小改动完成实践闭环。

## 1. 项目定位

`SuperMew_learning` 是一个完整的 Agent + RAG 应用项目，核心形态是：

- 后端：FastAPI + LangChain Agent + LangGraph RAG pipeline
- 前端：Vite + Vue 3 + TypeScript + Pinia
- 数据层：PostgreSQL + Redis
- 向量检索：Milvus 2.5+，支持 Dense + BM25 Sparse 混合检索
- 体验层：SSE 流式回答、RAG 检索过程实时展示、回答中断、References 展示

你可以把它理解为一个带用户系统、文档知识库、RAG 检索、Agent 工具调用和现代前端交互的完整学习项目。

## 2. 学习完成后的目标

完成本路线后，你应该能够做到：

1. 在本地跑起完整项目，包括后端、前端、PostgreSQL、Redis、Milvus。
2. 解释一次聊天请求从前端到 Agent、RAG、数据库、SSE 返回的完整链路。
3. 看懂并修改一个 Agent 工具。
4. 看懂并修改一个 RAG 检索策略或 trace 字段。
5. 看懂前端如何解析流式 SSE 并展示思考过程。
6. 能基于该项目继续扩展自己的 Agent/RAG 应用。

## 3. 总体架构图

```text
用户浏览器
  |
  v
Vue 3 前端
  |
  |  HTTP / SSE
  v
FastAPI 后端
  |
  +-- Auth / Session / Document / Chat 路由
  |
  +-- LangChain Agent
        |
        +-- get_current_weather
        |
        +-- search_knowledge_base
              |
              v
          LangGraph RAG Pipeline
              |
              +-- Milvus Hybrid Search
              +-- PostgreSQL ParentChunk
              +-- Redis Cache
              +-- Jina Rerank
              |
              v
          RAG Trace + Context
              |
              v
          Agent 生成最终回答
              |
              v
          SSE 流式返回前端
```

## 4. 第一阶段：先跑通项目

建议时间：0.5-1 天。

### 重点文件

- `README.md`
- `.env.example`
- `docker-compose.yml`
- `backend/app.py`
- `frontend/package.json`

### 学习目标

你需要先确认项目能启动，而不是直接阅读复杂代码。

### 操作路径

1. 阅读 `README.md` 的本地部署部分。
2. 复制环境变量文件：

```bash
cp .env.example .env
```

3. 根据你的模型服务配置 `.env`：

```text
ARK_API_KEY=
MODEL=
FAST_MODEL=
GRADE_MODEL=
BASE_URL=
DATABASE_URL=
REDIS_URL=
MILVUS_HOST=
MILVUS_PORT=
MILVUS_COLLECTION=
JWT_SECRET_KEY=
ADMIN_INVITE_CODE=
```

4. 启动依赖服务：

```bash
docker compose up -d
docker compose ps
```

5. 安装 Python 依赖并启动后端：

```bash
uv sync
uv run uvicorn backend.app:app --host 0.0.0.0 --port 8000 --reload
```

6. 构建前端：

```bash
cd frontend
npm install
npm run build
```

7. 访问：

```text
http://127.0.0.1:8000/
http://127.0.0.1:8000/docs
```

### 验收标准

- 能打开前端页面。
- 能打开 FastAPI `/docs`。
- 能注册或登录用户。
- 能看到接口列表。

## 5. 第二阶段：建立项目目录地图

建议时间：0.5 天。

### 后端目录

```text
backend/
  app.py                  FastAPI 应用入口
  api/                    HTTP 路由层
  chat/                   Agent 对话服务
  rag/                    RAG 检索工作流
  indexing/               文档解析、分块、向量写入
  tools/                  Agent 可调用工具
  infra/                  数据库、缓存、鉴权基础设施
  db/                     SQLAlchemy ORM 模型
  schemas/                Pydantic 请求响应模型
  jobs/                   上传/删除任务进度管理
```

### 前端目录

```text
frontend/src/
  App.vue                 页面总入口
  stores/                 Pinia 状态管理
  utils/api.ts            HTTP/SSE 请求封装
  components/Chat/        聊天、消息、References、RAG trace
  components/Documents/   文档上传和知识库管理
```

### 核心链路先记住

```text
POST /chat/stream
  -> backend/api/routes/chat.py
  -> backend/chat/service.py
  -> backend/chat/runtime.py
  -> backend/tools/knowledge.py
  -> backend/rag/pipeline.py
  -> backend/rag/utils.py
  -> Milvus / PostgreSQL / Redis
  -> SSE 返回前端
  -> frontend/src/stores/chat.ts
  -> Chat 组件渲染
```

### 验收标准

你能不看代码说出：

- API 路由在哪里。
- Agent 在哪里创建。
- RAG pipeline 在哪里。
- 前端流式解析在哪里。
- 数据库模型在哪里。

## 6. 第三阶段：理解 FastAPI 后端基础

建议时间：1 天。

### 重点文件

- `backend/app.py`
- `backend/api/router.py`
- `backend/api/routes/auth.py`
- `backend/api/routes/chat.py`
- `backend/api/routes/sessions.py`
- `backend/api/routes/documents.py`
- `backend/infra/auth.py`
- `backend/infra/database.py`
- `backend/db/models.py`
- `backend/schemas/`

### 学习顺序

1. 从 `backend/app.py` 看应用如何创建。
2. 从 `backend/api/router.py` 看路由如何聚合。
3. 从 `auth.py` 看注册、登录、当前用户获取。
4. 从 `chat.py` 看 `/chat` 和 `/chat/stream`。
5. 从 `documents.py` 看文档上传、删除、任务查询。
6. 从 `db/models.py` 看 ORM 数据模型。

### 必须理解的模型

```text
User
  |
  +-- ChatSession
        |
        +-- ChatMessage

ParentChunk
```

### 关键概念

- `User`：用户与角色。
- `ChatSession`：一个用户下的一组对话。
- `ChatMessage`：每条 Human/AI 消息，AI 消息可保存 `rag_trace`。
- `ParentChunk`：文档父级分块，服务于 Auto-merging。

### 练习任务

1. 找出普通用户和管理员权限的区别。
2. 找出哪些接口需要登录。
3. 找出哪些接口需要 admin。
4. 解释 `/chat/stream` 为什么返回 `StreamingResponse`。

### 验收标准

你能解释：

- 用户身份如何从 Bearer Token 解析。
- 为什么普通用户不能上传文档。
- 聊天记录如何与用户隔离。

## 7. 第四阶段：理解 Agent 主链路

建议时间：1 天。

### 重点文件

- `backend/chat/runtime.py`
- `backend/chat/service.py`
- `backend/tools/__init__.py`
- `backend/tools/knowledge.py`
- `backend/tools/weather.py`
- `backend/chat/storage.py`
- `backend/chat/rag_context.py`
- `backend/chat/streaming.py`

### 主链路

```text
用户发送消息
  -> chat_with_agent_stream()
  -> 读取历史消息与 persistent_note
  -> 构造 context_messages
  -> agent.astream()
  -> Agent 决定是否调用工具
  -> 工具返回结果
  -> Agent 生成最终回答
  -> 保存消息和 rag_trace
  -> SSE 流式返回
```

### 重点理解

`runtime.py` 里创建 Agent：

```text
create_agent(
  model=model,
  tools=[get_current_weather, search_knowledge_base],
  system_prompt=SYSTEM_PROMPT
)
```

`SYSTEM_PROMPT` 定义了关键行为：

- 文档问题调用 `search_knowledge_base`。
- 同一轮不要重复调用知识库工具。
- 检索上下文不足时诚实说明不知道。
- 基于知识库回答时引用来源编号。

### 练习任务

1. 修改 `SYSTEM_PROMPT` 的一句话，观察回答风格变化。
2. 新增一个简单工具，例如当前时间工具。
3. 找出 `reset_knowledge_tool_calls()` 的作用。
4. 找出 `persistent_note` 是如何生成和注入上下文的。

### 验收标准

你能解释：

- Agent 是在哪里初始化的。
- 工具是如何注册给 Agent 的。
- 一轮聊天为什么要保存历史消息。
- persistent note 和短期消息窗口分别解决什么问题。

## 8. 第五阶段：理解 RAG 文档入库链路

建议时间：1-1.5 天。

### 重点文件

- `backend/api/routes/documents.py`
- `backend/api/resources.py`
- `backend/indexing/document_loader.py`
- `backend/indexing/embedding.py`
- `backend/indexing/milvus_client.py`
- `backend/indexing/milvus_writer.py`
- `backend/indexing/parent_chunk_store.py`

### 入库链路

```text
上传 PDF/Word/Excel
  -> 保存原文件
  -> 清理同名旧文档
  -> document_loader 解析
  -> 三级滑动窗口分块
  -> L1/L2 父块写入 PostgreSQL ParentChunk
  -> L3 叶子块写入 Milvus
  -> Milvus 根据 text 字段自动生成 BM25 sparse_embedding
```

### 关键概念

- L1：更大的父级上下文块。
- L2：中等粒度上下文块。
- L3：叶子检索块，写入 Milvus 参与向量检索。
- `chunk_id`：当前块 ID。
- `parent_chunk_id`：父块 ID。
- `root_chunk_id`：根块 ID。
- `chunk_level`：分块层级。

### 为什么只向量化叶子块

项目采用 Leaf-only 向量化：

- 只把 L3 写入 Milvus，减少向量冗余。
- L1/L2 写 PostgreSQL，检索后按父子关系合并。
- 命中多个叶子块时，可以合并为更完整的父级上下文。

### 练习任务

1. 上传一个小 PDF 或 Markdown 转 PDF 文档。
2. 查看上传任务进度。
3. 找出父块和叶子块数量。
4. 删除同名文档后重新上传，确认旧 chunk 被清理。

### 验收标准

你能解释：

- 为什么需要三级分块。
- 为什么父块不直接写 Milvus。
- 文档重复上传为什么要先清理旧数据。

## 9. 第六阶段：理解 RAG 检索链路

建议时间：2 天。

### 重点文件

- `backend/rag/pipeline.py`
- `backend/rag/utils.py`
- `backend/tools/knowledge.py`
- `backend/chat/rag_context.py`
- `backend/chat/streaming.py`

### RAG Pipeline 主流程

```text
用户问题
  -> complexity 分类
  -> 简单问题直接检索
  -> 复杂问题拆成 2-4 个子问题
  -> 每个子问题独立执行 RAG 子流程
  -> 初次召回 retrieve_initial
  -> 相关性评分 grade_documents
  -> 若不足，rewrite_question
  -> 二次召回 retrieve_expanded
  -> 去重合并
  -> 返回上下文和 rag_trace
```

### 初次召回

`retrieve_initial` 调用 `retrieve_documents()`，主要做：

1. Milvus Dense 检索。
2. Milvus Sparse/BM25 检索。
3. RRF 排名融合。
4. Auto-merging。
5. Rerank。
6. 返回 top_k。

### Corrective RAG

如果初次召回相关性不足，会进入纠错流程：

```text
grade_documents = no
  -> rewrite_question
  -> step_back / hyde / complex
  -> retrieve_expanded
  -> 合并结果
```

### 三种重写策略

- `step_back`：退一步生成更通用的问题，适合具体问题缺乏背景时。
- `hyde`：生成假设性文档，适合概念模糊问题。
- `complex`：复杂问题扩展，适合多步骤、多方面问题。

### Hybrid Search

项目的检索不是单一向量检索，而是：

```text
Dense embedding 负责语义相似
Sparse/BM25 负责关键词匹配
RRF 负责融合排序
Rerank 负责最终精排
```

### Auto-merging

当多个 L3 叶子块属于同一个父块时，系统会合并到 L2 或 L1：

```text
多个 L3 命中
  -> 满足 AUTO_MERGE_THRESHOLD
  -> 合并为父级 L2
  -> 继续可能合并为 L1
```

### 练习任务

1. 找出 `retrieve_documents()` 的完整实现。
2. 修改 `RETRIEVAL_CANDIDATE_MULTIPLIER`，观察候选数量变化。
3. 修改 `AUTO_MERGE_THRESHOLD`，观察父块合并变化。
4. 关闭 rerank，比较 References 质量。
5. 上传一个文档后，问一个需要跨段落综合的问题。

### 验收标准

你能解释：

- Dense、Sparse、RRF、Rerank 的区别。
- 为什么检索后还要 grade。
- 为什么相关性不足时要 query rewrite。
- Auto-merging 如何增强上下文。

## 10. 第七阶段：理解流式输出和实时思考展示

建议时间：1 天。

### 重点文件

- `backend/chat/service.py`
- `backend/chat/streaming.py`
- `frontend/src/utils/api.ts`
- `frontend/src/stores/chat.ts`
- `frontend/src/components/Chat/ThinkingTrace.vue`
- `frontend/src/components/Chat/RetrievalTraceDetails.vue`
- `frontend/src/components/Chat/MessageItem.vue`

### 后端事件类型

```text
content       模型文本 token
rag_step      RAG 检索阶段进度
trace         完整 RAG trace
error         错误
[DONE]        流结束
```

### 后端关键设计

`chat_with_agent_stream()` 里有一个统一的 `asyncio.Queue`：

```text
Agent token -> queue -> SSE
RAG step    -> queue -> SSE
trace       -> queue -> SSE
```

这样前端能在工具执行期间看到检索步骤，而不是等最终答案出来才更新。

### 前端关键设计

`utils/api.ts` 负责：

- `fetch`
- `response.body.getReader()`
- `TextDecoder`
- 按 `\n\n` 拆 SSE 事件
- 解析 `data: {...}`
- 回调给 store

`stores/chat.ts` 负责：

- 创建用户消息。
- 创建 AI 思考气泡。
- 追加 `ragSteps`。
- 追加流式文本。
- 保存完整 `rag_trace`。

### 练习任务

1. 在 `pipeline.py` 增加一个新的 `emit_rag_step()`。
2. 看前端是否实时展示。
3. 修改 ThinkingTrace 展示文案。
4. 点击终止按钮，确认前端进入已终止状态。

### 验收标准

你能解释：

- SSE 和普通 HTTP 响应有什么区别。
- 为什么需要统一 output queue。
- 前端如何从思考状态切换到正式回答。

## 11. 第八阶段：理解前端工程

建议时间：1-2 天。

### 阅读顺序

1. `frontend/src/App.vue`
2. `frontend/src/stores/auth.ts`
3. `frontend/src/stores/sessions.ts`
4. `frontend/src/stores/chat.ts`
5. `frontend/src/stores/documents.ts`
6. `frontend/src/utils/api.ts`
7. `frontend/src/components/Chat/`
8. `frontend/src/components/Documents/`

### 页面结构

```text
App.vue
  +-- Sidebar
  +-- AuthPanel
  +-- DocumentSettings
  +-- HistorySidebar
  +-- ChatArea
```

### Store 职责

- `auth.ts`：登录、注册、token、当前用户。
- `sessions.ts`：会话列表、会话消息、删除会话。
- `chat.ts`：当前聊天消息、流式状态、RAG steps、trace。
- `documents.ts`：文档列表、上传任务、删除任务、轮询进度。

### 练习任务

1. 给 References 增加一个字段显示，如 `chunk_level`。
2. 给上传成功状态增加更明显的提示。
3. 改一个按钮文案或交互状态。
4. 找出所有请求如何携带 Bearer Token。

### 验收标准

你能解释：

- 登录后页面为什么切换。
- 发送消息后 store 如何变化。
- SSE 数据如何驱动消息气泡更新。
- 文档上传进度为什么需要轮询。

## 12. 第九阶段：做一个完整小改动

建议时间：1-2 天。

### 推荐任务 A：新增一个 Agent 工具

目标：新增一个 `get_current_time` 工具。

涉及文件：

- `backend/tools/`
- `backend/chat/runtime.py`

验收：

- Agent 能根据用户问题调用该工具。
- 不影响知识库检索工具。

### 推荐任务 B：增强 RAG trace 展示

目标：在前端 References 中更清楚展示：

- `retrieval_mode`
- `score`
- `rerank_score`
- `chunk_level`
- `auto_merge_applied`

涉及文件：

- `backend/rag/utils.py`
- `frontend/src/components/Chat/RetrievalTraceDetails.vue`
- `frontend/src/components/Chat/References.vue`

验收：

- 前端能看到更完整的检索信息。
- 不影响回答生成。

### 推荐任务 C：增强文档上传保护

目标：增加文件大小限制和更清楚的错误提示。

涉及文件：

- `backend/api/routes/documents.py`
- `frontend/src/stores/documents.ts`
- `frontend/src/components/Documents/UploadSection.vue`

验收：

- 超大文件无法上传。
- 前端显示明确错误。
- 正常文件上传不受影响。

## 13. 7 天快速上手计划

```text
Day 1
  跑通项目
  看 README
  画整体架构图

Day 2
  学 FastAPI 路由
  学鉴权和数据库模型
  看 auth/chat/documents/sessions 四类接口

Day 3
  学 Agent runtime
  学 chat service
  学工具调用
  理解 persistent_note 和历史消息

Day 4
  学文档上传
  学 document_loader
  学三级分块
  学 Milvus 写入

Day 5
  学 RAG pipeline
  学 Hybrid Search
  学 Auto-merging
  学 Rerank 和 Corrective RAG

Day 6
  学 SSE 流式输出
  学前端 ReadableStream
  学 ThinkingTrace 和 References

Day 7
  做一个完整小改动
  写一页自己的项目理解总结
```

## 14. 14 天深入掌握计划

```text
Day 1-2
  跑通项目
  熟悉后端路由和数据库模型

Day 3-4
  深入 Agent、工具调用、会话持久化

Day 5-6
  深入文档入库、分块、Milvus schema、ParentChunk

Day 7-8
  深入 RAG pipeline、Hybrid Search、RRF、Rerank

Day 9
  深入 Corrective RAG、Step-back、HyDE、query rewrite

Day 10
  深入复杂问题分解和 LangGraph 并行子 Agent

Day 11
  深入 SSE、异步队列、跨线程 RAG step 推送

Day 12
  深入 Vue/Pinia 前端状态流

Day 13
  做一个可展示的小功能

Day 14
  整理作品说明：架构图、核心亮点、关键代码、你的改动
```

## 15. 文件阅读优先级

### 第一优先级

- `README.md`
- `backend/app.py`
- `backend/api/routes/chat.py`
- `backend/chat/service.py`
- `backend/chat/runtime.py`
- `backend/tools/knowledge.py`
- `backend/rag/pipeline.py`
- `backend/rag/utils.py`
- `frontend/src/stores/chat.ts`
- `frontend/src/utils/api.ts`

### 第二优先级

- `backend/api/routes/documents.py`
- `backend/indexing/document_loader.py`
- `backend/indexing/milvus_client.py`
- `backend/indexing/milvus_writer.py`
- `backend/indexing/parent_chunk_store.py`
- `backend/db/models.py`
- `frontend/src/components/Chat/`
- `frontend/src/components/Documents/`

### 第三优先级

- `docker-compose.yml`
- `backend/jobs/upload_jobs.py`
- `backend/infra/cache.py`
- `backend/infra/database.py`
- `frontend/src/assets/styles/main.css`

### 暂时不用深挖

- `.git/`
- Git hooks 样例
- `uv.lock`
- 前端样式细节
- 所有异常分支

## 16. 学习时的常见误区

### 误区 1：一上来读 RAG

不要这样做。先跑通项目，再看 API 和 Agent，最后看 RAG。否则会被 LangGraph、Milvus、Rerank、Auto-merging 同时淹没。

### 误区 2：只读 README，不跑项目

这个项目的价值在交互链路。必须实际上传文档、提问、看 References、看 RAG steps。

### 误区 3：把 RAG 当成单次向量检索

本项目不是简单的 embedding search。它包含：

- 混合召回
- RRF 融合
- 父块合并
- rerank
- 相关性评分
- 查询重写
- trace 可观测

### 误区 4：忽视前端

前端不是附属品。这个项目的展示价值很大一部分来自：

- 实时思考过程
- 流式回答
- References
- 任务进度
- 终止回答

## 17. 面试或作品集表达

你可以这样介绍这个项目：

```text
这是一个完整的 Agentic RAG 应用。我基于 FastAPI、LangChain、LangGraph、Milvus、PostgreSQL、Redis 和 Vue 3 实现了一个支持文档上传、混合检索、Agent 工具调用、流式回答和 RAG 过程可视化的知识库问答系统。

核心亮点是：
1. 使用 Milvus 2.5+ 原生 BM25 Function 实现 Dense + Sparse Hybrid Search。
2. 采用 L1/L2/L3 三级分块和 Auto-merging 策略，在召回叶子块后自动合并父级上下文。
3. 通过 LangGraph 实现 Corrective RAG，包括相关性评分、Step-back、HyDE 和复杂问题分解。
4. 使用 SSE 将 Agent token 和 RAG 检索步骤统一流式推送到前端。
5. 前端用 Vue 3 + Pinia 实现思考过程、References、上传任务和回答中断等交互。
```

## 18. 最后建议

最有效的学习方式是：

```text
跑通一次
  -> 读一条链路
  -> 改一个小点
  -> 观察现象
  -> 写下自己的理解
```

不要试图一次性读完所有代码。这个项目应该按链路学习：

1. 启动链路。
2. 登录链路。
3. 聊天链路。
4. RAG 链路。
5. 文档入库链路。
6. 前端流式展示链路。
7. 自己扩展一个能力。

当你能完成一个小功能并解释它经过哪些模块时，就说明你已经真正上手了。
