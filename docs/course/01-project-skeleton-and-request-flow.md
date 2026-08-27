# 01. 项目核心骨架与一次请求的完整调用链

## 1. 本课目标

这一课只解决一个问题：**用户在前端发出一句话之后，到最终回答流回前端，中间到底经过了哪些层？**

如果这条链路不清楚，后面直接读 RAG 会很容易陷入函数细节。

---

## 2. 先按“职责”理解目录

SuperMew 后端不是一个大脚本，而是典型分层结构：

```text
HTTP / API 层
    ↓
Chat Service 层
    ↓
Agent Runtime 层
    ↓
Tool 层
    ↓
RAG Workflow 层
    ↓
Retrieval / Indexing / Storage 层
```

对应源码：

```text
backend/api/          HTTP 入口
backend/chat/         对话业务编排
backend/tools/        Agent tools
backend/rag/          Agentic RAG 状态机
backend/indexing/     文档解析、embedding、向量库
backend/infra/        DB / auth / cache
backend/db/           ORM
backend/schemas/      数据结构
```

这种拆法的核心工程价值是：**Agent 逻辑和 RAG 逻辑没有耦死。**

---

## 3. 一次聊天请求的主链路

先记下面这条：

```text
Browser
  ↓
/chat/stream
  ↓
backend/api/routes/chat.py
  ↓
backend/chat/service.py
  ↓
create_agent_for_request(ctx)
  ↓
LangChain Agent
  ↓（如果模型决定使用知识库）
search_knowledge_base
  ↓
run_rag_graph(query, ctx)
  ↓
LangGraph RAG
  ↓
返回 chunks / HITL / no_knowledge
  ↓
Agent 生成 final answer
  ↓
SSE streaming
  ↓
frontend
```

面试时可以把它压缩成：

> 外层 Agent 负责意图判断与工具选择，RAG 被封装成一个知识库 Tool；Tool 内部不是简单向量检索，而是进入独立 LangGraph workflow，完成检索、证据判断、改写、复杂问题拆解和 HITL，再把可靠上下文返回给外层 Agent 生成答案。

---

## 4. 为什么要有两层“智能决策”

很多项目只有：

```text
LLM -> retrieve -> answer
```

SuperMew 则是：

```text
外层 Agent：我需不需要知识库？
        ↓
RAG workflow：如果需要，我该怎样检索？
```

这是两个不同层级的问题。

### 外层 Agent 决策

负责：

- 是否调用知识库；
- 是否调用天气等其他 Tool；
- 怎样基于 Tool 返回结果生成用户可见回答。

### 内层 RAG 决策

负责：

- simple 还是 complex；
- 是否拆子问题；
- 第一次检索是否足够；
- 是否需要 rewrite；
- rewrite 用 Step-back 还是 HyDE；
- 是否需要用户澄清；
- 是否最终无知识可答。

这就是为什么该项目更适合叫 **Agentic RAG**。

---

## 5. `backend/chat/runtime.py` 在架构中的位置

这是 Agent 的“装配层”。

它做三件核心事：

1. 初始化主模型；
2. 初始化 fast model；
3. 创建 Agent，并挂载 tools。

当前 Tool 至少包括：

```text
get_current_weather
search_knowledge_base
```

注意：`search_knowledge_base` 不是全局静态 Tool，而是通过 `make_search_knowledge_base(ctx)` 创建。

为什么？

因为每一次请求都需要自己的 `ChatRequestContext`，用于：

- 记录本轮是否已经调用过知识库；
- 防止重复 tool call；
- 保存 RAG trace；
- 保存 HITL resume state；
- 向前端发送检索过程事件。

这是一个很典型的生产设计：**Tool 不是纯函数，它往往需要 request-scoped context。**

---

## 6. `backend/tools/knowledge.py` 是关键边界

可以把这个文件理解为：

```text
Agent 世界  <---- knowledge.py ---->  RAG 世界
```

Tool 输入非常简单：

```text
query: str
```

但 Tool 内部会：

```text
run_rag_graph(query, ctx)
```

然后把 RAG 结果转换成 Agent 能理解的几类返回值。

### 情况 1：正常检索成功

返回：

```text
Retrieved Chunks:
[1] xxx
[2] xxx
...
```

### 情况 2：需要澄清

返回：

```text
NEEDS_CLARIFICATION: ...
```

### 情况 3：需要范围选择

返回：

```text
NEEDS_SCOPE_SELECTION: ...
```

### 情况 4：知识库没有可靠答案

返回：

```text
NO_KNOWLEDGE: ...
```

这是一种非常值得面试表达的设计：

> Tool 不只返回“数据”，还返回明确的语义状态，让 Agent 能基于协议做后续动作。

---

## 7. 为什么一轮只允许调用一次知识库 Tool

代码里明确做了 tool-call slot 限制。

原因主要有四个：

### 7.1 防止 Agent 自循环

如果模型觉得结果不满意，很可能：

```text
search -> search -> search -> search
```

造成无边界循环。

### 7.2 内层 RAG 已经负责自纠错

外层 Agent 没必要重复调用知识库，因为内部已经存在：

```text
retrieve -> grade -> rewrite -> retrieve
```

### 7.3 成本控制

一次知识库调用内部可能包含：

- query embedding；
- Milvus 搜索；
- rerank；
- evidence grading；
- rewrite model；
- 多子问题并发。

重复调用成本很高。

### 7.4 让状态边界更清楚

外层只做一次 Tool 调用，内部自己闭环，更容易 trace 和 debug。

---

## 8. `backend/chat/service.py` 的作用

这是聊天业务真正的“总调度层”。

它不仅仅做模型调用，还负责：

- 读取历史消息；
- 构造短期上下文；
- 管理 persistent note；
- 处理 SSE streaming；
- 保存 AI / Human 消息；
- 处理上一轮 HITL；
- 从 HITL breakpoint 继续 RAG；
- 最后生成用户答案。

因此它不是 Agent 本身，而是 **Agent application service**。

面试可以这样区分：

```text
Agent Runtime = 智能体能力装配
Chat Service   = 产品级对话生命周期管理
```

---

## 9. HITL 为什么放在 Chat Service 配合 RAG

RAG workflow 发现问题缺槽位时，不能凭空补全。

于是返回：

```text
route = clarify
```

Chat Service 会：

1. 把待补充状态保存下来；
2. 向用户提出问题；
3. 下一轮收到补充后，不把它当新问题；
4. 恢复原问题；
5. 带着补充继续 RAG。

这个设计非常关键，因为真实多轮 Agent 中：

```text
“下一条用户消息” ≠ “一个全新任务”
```

它可能是上一个 workflow 的 resume input。

---

## 10. 架构上的分层原则

你可以从项目提炼出以下 Agent 工程原则。

### 原则 A：Tool 要薄

Tool 负责协议转换，不承载全部业务。

### 原则 B：复杂流程独立 workflow

RAG 内部有分支、循环、并发，就应该独立成图，而不是堆 if/else。

### 原则 C：状态要显式

例如：

```text
route
retrieval_status
rewrite_count
complexity
hitl_prompt
```

不要让下一步靠字符串猜。

### 原则 D：用户交互与内部推理解耦

RAG 决定需要 HITL，Chat Service 决定怎样保存状态和下一轮 resume。

---

## 11. 面试题

### Q1：SuperMew 的 Agent 和 RAG 是什么关系？

推荐回答：

> 它是两层结构。外层是 LangChain Agent，负责工具选择；RAG 被封装成 `search_knowledge_base` Tool。知识库 Tool 内部再进入 LangGraph workflow，完成复杂度判断、混合检索、证据评分、query rewrite、复杂问题并行拆解和 HITL。这样 Agent 负责“要不要查”，RAG workflow 负责“怎么查”。

### Q2：为什么不把 RAG 节点直接放进 Agent 主图？

推荐回答：

> 主要为了关注点分离和复用。知识检索本身已经是一个有状态子系统，内部有循环和分支，独立成 workflow 更易测试、观测和替换；外层 Agent 只需要稳定 Tool 接口。

### Q3：为什么 Tool 返回 NEEDS_CLARIFICATION 而不是让模型自己判断？

> 因为是否需要澄清来自证据评估状态，是 RAG workflow 的确定性业务状态。显式协议比让外层模型再次推断更稳定。

---

## 12. 本课验收

不看代码画出下面两层：

```text
LangChain Agent
    ↓ tool
search_knowledge_base
    ↓
LangGraph RAG
```

然后完整解释一次：

```text
用户问题 -> Agent -> Tool -> RAG -> Tool result -> Agent answer -> SSE
```

如果这条链能讲顺，再进入下一课。
