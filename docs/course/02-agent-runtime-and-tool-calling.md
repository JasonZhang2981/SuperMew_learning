# 02. 外层 Agent：模型、Tool Calling、上下文与边界

## 1. 本课目标

理解 SuperMew 的外层 Agent 为什么要保持“简单”，以及它和内层 RAG workflow 如何分工。

---

## 2. Agent 的装配入口

核心文件：

```text
backend/chat/runtime.py
```

这里通过 LangChain 的 `create_agent` 创建 Agent，挂载主模型与 Tool。

架构上可以理解为：

```text
model + tools + system prompt = Agent Runtime
```

其中：

- 主模型负责工具选择和最终答案；
- fast model 用于一些低成本辅助任务；
- Tool 提供外部能力；
- system prompt 约束 Agent 行为边界。

---

## 3. Tool Calling 在这个项目里不是“万能 ReAct”

SuperMew 并没有鼓励模型自由地无限循环调用工具。

相反，它做了明显约束：

```text
一轮最多调用一次 search_knowledge_base
```

而且拿到知识库结果后，应直接形成最终回答。

这体现了一个工程经验：

> 生产 Agent 不一定越自治越好。对高成本、高状态复杂度的 Tool，常常需要明确调用预算和边界。

---

## 4. 为什么知识库 Tool 要做成 request-scoped

`make_search_knowledge_base(ctx)` 会把当前请求的 `ChatRequestContext` 注入 Tool。

这让 Tool 可以访问本轮请求状态。

典型用途：

```text
knowledge_tool_called?
rag_trace
hitl_resume_state
rag step events
```

如果直接写成全局 Tool：

```python
@tool
def search_knowledge_base(query): ...
```

那么每次调用之间很难安全共享“本轮请求”的状态。

因此这里使用闭包构造 Tool，本质是依赖注入。

---

## 5. 外层 Agent 的 system prompt 解决哪些问题

系统提示词包含几类关键规则。

### 5.1 Tool 选择规则

文档/知识问题使用知识库工具。

### 5.2 Tool 调用预算

同一轮不要重复调用同一 Tool。

### 5.3 Tool 结果协议

如果结果是：

```text
NEEDS_CLARIFICATION
NEEDS_SCOPE_SELECTION
NO_KNOWLEDGE
```

Agent 不应该像普通上下文一样直接生成答案。

### 5.4 Grounding

最终事实只能基于 retrieved chunks。

### 5.5 引用

要求用 `[1]`、`[2]` 标注来源片段。

### 5.6 不把检索辅助文本当事实

Step-back 和 HyDE 只是 query transformation，不能作为 evidence。

这一点非常重要。

---

## 6. HyDE 为什么不能当证据

HyDE 会先生成一个“假设答案文档”，再把它作为检索 query 的一部分。

例如用户问：

```text
X 模块为什么延迟高？
```

HyDE 可能生成：

```text
可能与缓存穿透、数据库连接池或同步 IO 有关……
```

这段内容是模型生成的，不是真实知识库证据。

它的作用只是把 query 映射到更可能命中文档的语义空间。

如果最终回答引用 HyDE，就会形成“模型自己生成假设，然后把假设当证据”的闭环幻觉。

所以系统提示词明确隔离：

```text
retrieval aid != factual evidence
```

这是一个很好的面试点。

---

## 7. ChatRequestContext 可以怎么理解

把它看成一次 Agent 请求的“运行时黑板”。

```text
Request
  ↓
ChatRequestContext
  |- tool usage state
  |- rag trace
  |- hitl state
  `- streaming events
```

为什么不全放 LangGraph state？

因为这些状态不完全属于 RAG。

例如：

- 是否已经调用知识库 Tool，是外层 Agent 生命周期状态；
- SSE 推送，是应用层能力；
- HITL resume 需要跨轮对话保存。

所以一个生产系统通常存在多种状态范围：

```text
Graph state
Request state
Session state
Persistent DB state
```

面试时必须能区分。

---

## 8. 短期上下文与长期摘要

`backend/chat/service.py` 中会控制历史消息窗口。

思路类似：

```text
最近 N 条消息 + persistent note
```

这样做是因为不能无限把所有历史消息塞进模型上下文。

### 短期上下文

保留最近对话原文，保证细节精度。

### persistent note

压缩较早信息，保证长期连续性。

这可以理解为一种简单的 memory hierarchy：

```text
recent raw memory
+
compressed long-term conversational memory
```

它和 RAG 不同：

- memory 解决“对话里发生过什么”；
- RAG 解决“知识库里有什么事实”。

面试中不要把二者混为一谈。

---

## 9. Agent 与 Workflow 的区别在这里很清楚

### 外层 Agent

模型决定：

```text
是否调用 Tool
```

路径相对开放。

### 内层 RAG LangGraph

节点与边是工程师预先定义的：

```text
classify -> retrieve -> grade -> rewrite ...
```

路径受控。

所以 SuperMew 实际是：

```text
Agent + controlled workflow
```

而不是把所有事情都交给自由 Agent。

这也是当前生产 Agent 很常见的架构。

---

## 10. 为什么这种混合架构比纯 ReAct 更稳

如果全部交给纯 ReAct：

```text
Thought -> tool -> observation -> thought -> tool -> ...
```

存在几个问题：

- tool call 次数不稳定；
- 检索纠错策略不确定；
- 无法方便做节点级 trace；
- 很难保证只 rewrite 一次；
- HITL breakpoint 难管理；
- 并行子问题调度不透明。

而 workflow 可以把这些变成确定性结构。

因此比较成熟的回答是：

> Agent 负责需要开放决策的部分，Workflow 负责需要可靠执行和可观测的部分。

---

## 11. 外层 Tool 接口为什么保持简单

知识库 Tool 只需要：

```text
query: string
```

而没有让外层 Agent直接传：

```text
top_k
rerank_model
rewrite_method
chunk_level
```

这是有意的。

原因：

### 11.1 降低模型参数错误

参数越多，模型越容易构造错误值。

### 11.2 检索策略属于 RAG 子系统

`top_k`、chunk level 等不应该由通用 Agent 随意控制。

### 11.3 保持 Tool contract 稳定

内部可以替换 Milvus、reranker、graph，而 Tool schema 不变。

这就是封装。

---

## 12. 生产级 Agent Tool 设计原则

从 SuperMew 可以提炼：

### 原则 1：Tool schema 尽量小

只暴露模型真正需要控制的参数。

### 原则 2：权限和预算不要只依赖 Prompt

Prompt 写“一轮一次”还不够，代码也要做 slot 限制。

### 原则 3：复杂业务 Tool 内部继续分层

不要在 Tool 函数里写数百行逻辑。

### 原则 4：返回状态要机器可解释

例如：

```text
NO_KNOWLEDGE
NEEDS_CLARIFICATION
```

比一段自由文本稳定。

### 原则 5：Tool 结果和最终答案分离

Tool 返回 evidence，Agent 负责最终自然语言生成。

---

## 13. 面试题

### Q1：为什么搜索知识库一轮只允许调用一次？

回答要点：

- 防止 Agent loop；
- 内部 RAG 已经有 rewrite/self-correction；
- 控制成本和延迟；
- 提高 trace 可解释性。

### Q2：Tool Calling 的参数 schema 是越丰富越好吗？

不是。

> Tool schema 应只暴露模型真正需要决策的业务参数。技术参数应该由服务端策略控制，否则会增加参数幻觉、边界绕过和线上不稳定性。

### Q3：Agent Memory 和 RAG 有什么区别？

> Memory 主要维护任务/会话过去状态，RAG 主要从外部知识源检索事实证据。两者都向模型提供 context，但数据生命周期和目标不同。

### Q4：为什么 Prompt 限制还要代码限制？

> Prompt 是软约束，代码是硬约束。涉及权限、次数、预算、数据访问范围等生产边界，不能只依赖模型服从指令。

---

## 14. 本课验收

你应当能解释下面一句话：

> SuperMew 不是自由 ReAct Agent，而是“受控外层 Agent + request-scoped tools + 内层确定性 Agentic RAG workflow”的混合架构。
