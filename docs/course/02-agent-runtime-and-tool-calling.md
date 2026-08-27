# 02. 外层 Agent：Runtime、Tool Calling、Request Context 与工程边界

## 1. 本课目标

这一课只解决外层 Agent，不进入 Milvus 和 LangGraph 内部。

完成后需要能够从源码层面回答：

1. Agent 在哪里创建？
2. 模型、system prompt、tools 如何组装？
3. 为什么 `search_knowledge_base` 不是全局函数，而是 `make_search_knowledge_base(ctx)`？
4. 为什么“一轮最多调用一次知识库”既写在 Prompt，也写在代码？
5. RAG 内部 trace 为什么不直接作为 Tool 返回值，而要通过 `ChatRequestContext` 旁路传递？
6. 同步请求和流式请求如何复用同一套 Agent/RAG 逻辑？

核心文件：

```text
backend/chat/runtime.py
backend/chat/request_context.py
backend/tools/knowledge.py
backend/chat/service.py
```

---

# 2. 先建立调用关系

一次普通知识库问答可以先抽象为：

```text
chat_with_agent / chat_with_agent_stream
        |
        | 创建本轮 ChatRequestContext
        v
create_agent_for_request(ctx)
        |
        | model + tools + system_prompt
        v
LangChain Agent
        |
        | 模型决定调用 search_knowledge_base
        v
make_search_knowledge_base(ctx)
        |
        | acquire tool slot
        | run_rag_graph(query, ctx)
        v
Agentic RAG
        |
        | docs + rag_trace + optional HITL resume state
        v
knowledge tool
        |
        | docs -> Tool observation
        | rag_trace -> ctx.store_rag_trace(...)
        v
Outer Agent
        |
        | 基于 retrieved chunks 生成最终回答
        v
service.py
        |
        | ctx.take_rag_trace()
        | 保存 message + trace
        v
前端 / API
```

这张图非常重要，因为这里存在两条不同的数据通道：

```text
通道 A：Tool observation
RAG docs -> Agent -> 最终自然语言答案

通道 B：Application metadata
RAG trace -> ChatRequestContext -> service -> storage / frontend
```

不要把两者混为一谈。

---

# 3. `runtime.py`：Agent 的装配入口

源码核心：

```python
from langchain.agents import create_agent
from langchain.chat_models import init_chat_model

from backend.chat.request_context import ChatRequestContext
from backend.tools import get_current_weather, make_search_knowledge_base
```

随后从环境变量读取模型配置：

```python
API_KEY = os.getenv("ARK_API_KEY")
MODEL = os.getenv("MODEL")
FAST_MODEL = os.getenv("FAST_MODEL")
BASE_URL = os.getenv("BASE_URL")
```

这里先区分两个模型角色。

## 3.1 `model`

```python
model = init_chat_model(
    model=MODEL,
    model_provider="openai",
    api_key=API_KEY,
    base_url=BASE_URL,
    temperature=0.3,
    stream_usage=True,
)
```

这是主模型。

主要承担：

```text
理解用户意图
    ↓
判断是否调用 Tool
    ↓
构造 Tool 参数
    ↓
消费 Tool observation
    ↓
生成最终回答
```

所以它属于 Outer Agent 的 reasoning/generation model。

## 3.2 `fast_model`

```python
fast_model = init_chat_model(...)
```

它不是外层 Agent 的主推理模型，而是低成本辅助模型。

例如 `service.py` 中 persistent note 的更新，会调用 `fast_model` 做上下文压缩。

这体现一种生产工程常见思路：

> 不同复杂度任务不一定全部使用同一高成本模型。

可以把模型分层：

```text
主推理 / 最终生成 -> stronger model
分类 / 摘要 / query rewrite -> faster cheaper model
```

---

# 4. `create_agent_for_request(ctx)`：为什么每个请求创建一个 Agent

核心源码：

```python
def create_agent_for_request(ctx: ChatRequestContext):
    return create_agent(
        model=model,
        tools=[
            get_current_weather,
            make_search_knowledge_base(ctx),
        ],
        system_prompt=SYSTEM_PROMPT,
    )
```

表面看只是：

```text
model + tools + prompt
```

真正重要的是这一句：

```python
make_search_knowledge_base(ctx)
```

而不是：

```python
search_knowledge_base
```

这是整个外层 Agent 工程设计最值得学习的地方之一。

---

# 5. 为什么知识库 Tool 是一个 Factory

如果只是普通 Tool，最简单可能写成：

```python
@tool
def search_knowledge_base(query: str):
    ...
```

但 SuperMew 写成：

```python
def make_search_knowledge_base(ctx):
    @tool("search_knowledge_base")
    def search_knowledge_base(query: str):
        ...

    return search_knowledge_base
```

这是一种：

```text
Tool Factory + Closure + Dependency Injection
```

也就是：

```text
当前请求 ctx
   ↓ 注入
Tool Factory
   ↓
只属于当前请求的 Tool instance
```

为什么需要这样？

因为 Tool 除了 `query`，还必须访问“本轮请求私有状态”：

```text
user_id
session_id
知识库调用次数
RAG trace
HITL resume state
SSE output queue
event loop
计时信息
```

这些东西都不能安全地放在全局变量里。

---

# 6. Request-scoped state 为什么重要

假设错误地写成全局状态：

```python
knowledge_tool_used = False
rag_trace = None
```

当用户 A 和用户 B 并发请求时：

```text
A: knowledge_tool_used = True

B: 读取 knowledge_tool_used
   -> 可能错误认为自己已经调用过 Tool
```

或者：

```text
A 的 rag_trace
被 B 的请求覆盖
```

这就是典型的并发状态污染。

SuperMew 使用：

```python
ctx = ChatRequestContext(...)
```

让状态生命周期绑定当前 request：

```text
Request A -> Context A -> Agent A -> Tool A
Request B -> Context B -> Agent B -> Tool B
```

互不共享。

面试回答可以说：

> `make_search_knowledge_base(ctx)` 本质是 request-scoped dependency injection。它用闭包把当前请求上下文绑定到 Tool 实例中，使 Tool 能安全访问调用预算、RAG trace、HITL 和流式事件，同时避免并发请求通过全局变量互相污染。

---

# 7. `ChatRequestContext`：一次 Agent 请求的运行时黑板

类定义：

```python
@dataclass
class ChatRequestContext:
    user_id: str
    session_id: str
    output_queue: Optional[asyncio.Queue] = None
    loop: Optional[asyncio.AbstractEventLoop] = None
```

以及内部状态：

```python
_lock
_active
_rag_trace
_knowledge_tool_slots_used
_started_at
_last_step_at
```

可以按四类理解。

## 7.1 Identity

```text
user_id
session_id
```

表示这个 request 属于谁、属于哪个 conversation。

## 7.2 Tool budget

```text
_knowledge_tool_slots_used
```

控制本轮知识库 Tool 调用次数。

## 7.3 Observability

```text
_rag_trace
_started_at
_last_step_at
```

保存 RAG trace 与节点耗时。

## 7.4 Streaming bridge

```text
output_queue
loop
```

把同步执行的 RAG 节点产生的事件安全送回 asyncio 流式请求。

因此可以把 `ChatRequestContext` 理解为：

```text
request-owned control plane
```

它不是业务知识本身，而是控制一次 Agent 执行所需的运行时状态。

---

# 8. 同步和流式请求为什么有两个 Factory

源码提供：

```python
ChatRequestContext.for_sync(...)
ChatRequestContext.for_stream(...)
```

同步模式：

```python
return cls(user_id=user_id, session_id=session_id)
```

不需要 `output_queue`。

流式模式：

```python
return cls(
    user_id=user_id,
    session_id=session_id,
    output_queue=output_queue,
    loop=asyncio.get_running_loop(),
)
```

为什么？

因为普通同步调用只关心最终结果：

```text
request
   ↓
execute
   ↓
response
```

流式调用则还希望看到中间状态：

```text
正在分析复杂度
正在检索
Auto-merging
正在 rerank
证据评分
...
```

所以只有 streaming request 才需要：

```text
RAG node
  ↓
emit_rag_step
  ↓
asyncio.Queue
  ↓
SSE
  ↓
Frontend
```

---

# 9. `emit_rag_step()`：同步 RAG 与异步 SSE 之间的桥

一个 RAG node 可以调用：

```python
ctx.emit_rag_step(icon, label, detail)
```

它会计算：

```text
elapsed_ms
stage_elapsed_ms
```

然后：

```python
loop.call_soon_threadsafe(
    queue.put_nowait,
    {"type": "rag_step", "step": step},
)
```

这里的关键是：

```python
call_soon_threadsafe
```

因为 RAG workflow 的部分代码可能运行在线程池或同步调用栈中，而 SSE 是 asyncio event loop。

不能在另一个线程直接随意操作 asyncio Queue。

所以这里做了线程安全的 event-loop bridge。

面试可答：

> 流式 observability 没有侵入 RAG 的返回值，而是通过 request context 中的 event loop 和 queue 旁路发送。同步节点调用 `emit_rag_step`，再通过 `call_soon_threadsafe` 把事件投递回主 asyncio loop，最终由 SSE consumer 推到前端。

---

# 10. 一轮最多一次知识库调用：软约束 + 硬约束

这是 SuperMew 很好的生产 Agent 设计点。

## 10.1 第一层：Prompt 软约束

`SYSTEM_PROMPT` 明确告诉模型：

```text
At most one knowledge tool call per turn.
```

并且：

```text
Once you call search_knowledge_base and receive its result,
you MUST immediately produce the Final Answer.
```

这是行为指导。

但是 Prompt 不能作为安全边界。

模型可能：

```text
误解指令
违反指令
由于模型升级导致行为变化
出现 Agent loop
```

## 10.2 第二层：代码硬约束

真正的 enforcement 在：

```python
def acquire_knowledge_tool_slot(self) -> bool:
    with self._lock:
        if self._knowledge_tool_slots_used >= 1:
            return False
        self._knowledge_tool_slots_used += 1
        return True
```

知识库 Tool 一进来先：

```python
if not ctx.acquire_knowledge_tool_slot():
    return "TOOL_CALL_LIMIT_REACHED: ..."
```

于是即使模型违反 Prompt：

```text
Agent
 ↓ 第一次 knowledge call
成功
 ↓
Agent 又想调用
 ↓ 第二次
acquire slot = false
 ↓
硬拒绝
```

这可以抽象为：

```text
Prompt = policy guidance
Code = policy enforcement
```

对于以下边界都应该遵循类似原则：

```text
权限
次数
费用预算
数据范围
危险操作
幂等性
超时
```

不能只写在 prompt。

---

# 11. 为什么内部 RAG 已经会自纠错，外层 Agent 不应该重复搜索

如果知识库只是：

```text
query -> vector search
```

那么允许 Outer Agent 再搜一次可能还有意义。

但 SuperMew 的 RAG 内部已经有：

```text
retrieve
   ↓
grade evidence
   ↓
rewrite if needed
   ↓
retrieve again
```

复杂问题还会：

```text
classify
   ↓
decompose
   ↓
parallel sub-agent retrieve
   ↓
synthesis
```

所以如果 Outer Agent 还能自由重复调用整个 RAG：

```text
Outer Agent
  ↓
RAG 内部 retrieve + rewrite
  ↓
Outer Agent 再调用
  ↓
RAG 又 retrieve + rewrite
```

会造成：

```text
成本不可控
延迟放大
trace 难理解
重复证据
不确定循环
```

因此设计为：

> 检索纠错属于 RAG 子系统，不属于 Outer Agent。

这是边界分层。

---

# 12. `knowledge.py`：Tool 不负责生成最终答案

Tool 内部执行：

```python
rag_result = run_rag_graph(query, ctx)
```

然后拿到：

```text
docs
rag_trace
hitl_resume_state
```

这里没有直接调用主模型生成最终答案。

它的任务主要是：

```text
1. enforce tool budget
2. execute RAG workflow
3. store trace
4. translate RAG state to tool protocol
5. format retrieved evidence
```

然后把 evidence 返回 Outer Agent。

因此职责是：

```text
Tool -> retrieve evidence
Agent -> generate user-facing answer
```

而不是：

```text
Tool -> retrieve + answer + UI formatting + persistence + everything
```

这是单一职责原则。

---

# 13. Tool 返回值其实是一种协议

知识库 Tool 并不总返回文档。

可能返回：

```text
Retrieved Chunks: ...
```

也可能：

```text
NEEDS_CLARIFICATION: ...
```

或者：

```text
NEEDS_SCOPE_SELECTION: ...
```

或者：

```text
NO_KNOWLEDGE: ...
```

这本质是 Outer Agent 和 RAG 子系统之间的 protocol。

可以抽象为：

```text
RAGResult
  |- ANSWERABLE(evidence)
  |- NEEDS_CLARIFICATION(question)
  |- NEEDS_SCOPE_SELECTION(options)
  `- NO_KNOWLEDGE
```

项目当前通过字符串前缀编码。

这是简单可用的方案，但如果继续工程化，可以升级为结构化 Tool output，例如：

```json
{
  "status": "needs_clarification",
  "prompt": "...",
  "docs": []
}
```

这样比自由字符串更稳定。

面试时可以进一步评价：

> 当前字符串协议实现成本低，但长期更适合使用结构化 schema，使 Agent 能通过明确枚举解析状态，减少字符串脆弱性。

---

# 14. 为什么 `rag_trace` 不直接塞进 Tool observation

Tool 最终返回给模型的是 evidence 文本：

```text
[1] source A...
[2] source B...
```

但 RAG trace 里可能有：

```text
candidate_k
retrieval_mode
rewrite_method
rerank_score
complexity
sub_questions
latency
HITL internal state
```

这些数据主要用于：

```text
前端展示
调试
日志
评估
持久化
恢复流程
```

它们不是模型回答用户问题必需的 factual context。

如果全部塞进 Tool observation：

```text
增加 token
污染 context
暴露内部实现
增加模型误用 trace 的风险
```

所以项目做了分流：

```text
RAG docs
 -> Tool return
 -> LLM

RAG trace
 -> ctx.store_rag_trace()
 -> service.py
 -> storage / frontend
```

这叫 data plane 和 control/observability plane 分离。

---

# 15. `store_rag_trace()` 与 `take_rag_trace()`

Tool 完成后：

```python
ctx.store_rag_trace(rag_trace, hitl_resume_state)
```

外层 service 在 Agent 完成后：

```python
stored_trace = ctx.take_rag_trace()
```

`take` 的实现会：

```python
context = self._rag_trace
self._rag_trace = None
return context
```

这是一个消费式读取。

为什么不是一直保留？

因为 Context 生命周期就是当前 request。

消费完之后清掉可以避免：

```text
重复读取
旧 trace 被错误复用
状态语义不清
```

另外还提供：

```python
peek_rag_trace()
```

表示只查看，不消费。

这两个 API 的语义区别和消息队列里的：

```text
peek vs consume
```

很相似。

---

# 16. `service.py`：真正的一轮请求生命周期

同步路径里：

```python
ctx = ChatRequestContext.for_sync(
    user_id=user_id,
    session_id=session_id,
)
ctx.reset_knowledge_tool_budget()
```

然后：

```python
request_agent = create_agent_for_request(ctx)
```

构造 context messages：

```python
context_messages = _build_context_messages(...)
```

执行：

```python
result = request_agent.invoke(
    {"messages": context_messages},
    config={"recursion_limit": 8},
)
```

这里又有一个额外保护：

```text
recursion_limit = 8
```

它不是知识库一轮一次的替代品，而是 Agent graph 整体最大递归/执行保护。

因此存在两级 budget：

```text
Agent overall recursion budget
+
knowledge tool specific budget
```

这比只有一个总 loop limit 更精细。

---

# 17. Agent 完成之后发生什么

Agent 返回最终答案后：

```python
stored_trace = ctx.take_rag_trace()
```

再标准化：

```python
rag_trace = normalize_rag_trace(...)
```

如果 trace 表示：

```text
needs_clarification
needs_scope_selection
```

service 不会直接把模型生成的普通答案保存，而是构造：

```text
pending_hitl
```

为下一轮用户输入准备恢复状态。

否则会继续：

```text
生成/更新 persistent note
保存 AI message
把 rag_trace 作为 extra_message_data 持久化
```

最后：

```python
finally:
    ctx.close()
```

`close()` 会：

```text
_active = False
output_queue = None
loop = None
```

这意味着 request-scoped runtime 到这里正式结束。

生命周期可以记成：

```text
create
  ↓
reset budget
  ↓
bind to Agent tools
  ↓
execute
  ↓
consume trace
  ↓
persist result
  ↓
close
```

---

# 18. 为什么 `close()` 很重要

流式请求里 Context 持有：

```text
asyncio.Queue
EventLoop reference
```

如果请求已经结束但这些引用还长期保留，可能造成：

```text
对象不能释放
错误向旧连接继续 emit
资源生命周期混乱
```

因此通过：

```python
_active = False
```

任何之后的 `emit_rag_step()` 会直接 return。

这是典型 lifecycle guard。

---

# 19. Memory 和 RAG 的边界

`service.py` 使用：

```text
最近 N 条原始消息
+
persistent_note
```

其中：

```python
CONTEXT_WINDOW_MESSAGES = 6
```

当历史变长时，用 `fast_model` 压缩为 persistent note。

所以：

```text
Conversation Memory
解决：这个用户之前说过什么？

RAG
解决：外部知识库中有哪些事实？
```

虽然最终都会进入模型 context，但来源和生命周期不同。

面试中不要说：

> RAG 就是一种长期记忆。

更准确是：

> RAG 和 Agent memory 都属于 context augmentation，但 RAG 面向外部知识检索，memory 面向会话或任务状态持续化。

---

# 20. 为什么 system prompt 仍然很重要

有了代码硬约束，不代表 Prompt 没用。

Prompt 负责的是模型决策语义，例如：

```text
什么时候使用 search_knowledge_base
拿到 NO_KNOWLEDGE 后应该怎么回答
拿到 NEEDS_CLARIFICATION 后不能编造答案
最终引用必须用 [1] [2]
HyDE 不能当事实证据
```

这些很难全部用 if/else 写死。

所以可以区分：

```text
Behavior policy -> prompt
Hard invariant -> code
```

例如：

```text
“文档问题优先查知识库” -> Prompt
“一轮最多一次” -> Code
“只能访问本用户数据” -> Backend authorization
“Tool 参数必须满足 schema” -> Validation
```

这是生产 Agent 的重要设计原则。

---

# 21. 为什么 Tool schema 只有 `query`

`search_knowledge_base` 对模型只暴露：

```text
query: str
```

没有暴露：

```text
top_k
candidate_k
chunk_level
rerank threshold
rewrite method
rerank model
```

这是正确的封装方向。

因为这些是 RAG 内部策略参数，而不是业务用户意图参数。

如果全部暴露给 Agent：

```text
Tool schema 更复杂
参数 hallucination 更多
线上行为更不稳定
内部实现难替换
```

一个好的 Tool contract 通常满足：

```text
model controls intent
backend controls execution policy
```

例如模型只表达：

```text
“我要查什么”
```

而不是决定：

```text
“Milvus ef=64、top_k=24、rerank threshold=0.38”
```

---

# 22. 从这三个文件提炼生产 Agent Tool 设计原则

## 原则 1：Tool schema 尽量小

只暴露模型真正需要控制的业务语义参数。

## 原则 2：Tool 使用 request-scoped dependency injection

不要使用可被并发污染的全局运行时状态。

## 原则 3：Prompt 负责行为指导，代码负责硬边界

尤其是权限、预算和危险操作。

## 原则 4：复杂 Tool 内部继续封装 workflow

Outer Agent 不需要知道 retrieval internals。

## 原则 5：Tool result 使用明确状态协议

不要让模型猜“这段自然语言到底表示成功还是失败”。

## 原则 6：Evidence 与 observability metadata 分离

模型只消费回答需要的证据，trace 走旁路。

## 原则 7：所有 request-owned resource 都要有明确生命周期

```text
create -> use -> consume -> close
```

---

# 23. 面试高频追问

## Q1：为什么 `make_search_knowledge_base(ctx)` 要用闭包？

推荐回答：

> 因为知识库 Tool 需要访问本轮请求私有状态，比如 user/session、调用预算、RAG trace、HITL 和 SSE queue。通过 Tool factory 把 `ChatRequestContext` 注入闭包，相当于 request-scoped dependency injection，可以避免并发请求使用全局变量造成状态污染。

## Q2：既然 Prompt 已经要求一轮只调用一次，为什么还要 `acquire_knowledge_tool_slot()`？

> Prompt 只是软约束，模型可能违反。Tool 调用次数属于成本和稳定性边界，必须由代码强制执行。SuperMew 因此采用 Prompt policy + backend enforcement 双层保护。

## Q3：为什么 RAG trace 不直接返回给 Agent？

> trace 主要服务可观测、评测、调试和 HITL 恢复，不属于回答事实。全部放进模型上下文会增加 token、污染上下文并暴露内部实现。因此 evidence 作为 Tool observation，trace 通过 request context 旁路传给 service 和前端。

## Q4：`recursion_limit=8` 和一轮一次知识库调用有什么区别？

> recursion limit 控制整个 Agent graph 的最大执行深度，是全局保险；knowledge tool slot 是针对某个高成本 Tool 的细粒度预算，两者作用层级不同。

## Q5：为什么不把 `top_k` 暴露给 Agent？

> `top_k` 是检索策略参数，不是用户业务意图。让 Agent 控制会增加参数幻觉和行为漂移，也破坏 RAG 子系统封装。更合理的是 Agent 只传 query，后端根据配置和评测决定 retrieval policy。

## Q6：Memory 和 RAG 有什么区别？

> Memory 维护会话和任务过去状态；RAG 从外部知识源检索事实。两者都扩充 context，但数据来源、生命周期和目标不同。

---

# 24. 本课必须能手画的架构图

```text
                  Chat Service
                       |
                       | create request context
                       v
              ChatRequestContext
                /              \
               / bind           \ trace/events
              v                  v
      LangChain Outer Agent   Service / SSE
              |
       model decides tool
              |
              v
  search_knowledge_base(query)
              |
       acquire tool slot
              |
              v
        run_rag_graph()
              |
         docs + trace
          /        \
         /          \
        v            v
Tool observation   ctx.store_rag_trace
        |            |
        v            v
     LLM answer   persistence/UI
```

只要这张图能脱离源码讲清楚，说明你真正理解了 SuperMew 外层 Agent。

---

# 25. 本课验收

不看源码，尝试回答：

1. `create_agent_for_request()` 为什么需要传 `ctx`？
2. Tool factory 和普通 `@tool` 全局函数有什么区别？
3. Prompt 和代码分别约束 Agent 的哪些行为？
4. RAG docs 与 rag_trace 分别通过什么路径传递？
5. 为什么一轮只有一个知识库 Tool slot，但 RAG 内部仍然可以二次检索？
6. `for_sync` 与 `for_stream` 的核心差异是什么？
7. `call_soon_threadsafe` 在这里解决了什么问题？
8. `take_rag_trace` 为什么是消费式读取？
9. `recursion_limit` 与 Tool budget 有什么区别？
10. 为什么 Tool 只暴露 query 而不暴露 retrieval 参数？

如果这 10 题能稳定回答，本课就通过。

下一课进入真正的 RAG 离线链路：

```text
Document Upload
   -> loader
   -> sanitize
   -> L1/L2/L3 hierarchical chunking
   -> parent chunk storage
   -> embedding
   -> Milvus dense + BM25 sparse indexing
```

对应：

```text
docs/course/03-rag-ingestion-and-hierarchical-chunking.md
```
