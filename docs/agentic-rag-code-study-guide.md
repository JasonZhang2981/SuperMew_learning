# Agentic RAG Code Study Guide

这份文档是给代码基础还不强的学习者使用的代码解构指导书。它不会只告诉你“项目有什么”，而是带你沿着一次真实请求的执行路径，一步一步看懂这个 Agentic RAG 项目为什么这样写、每个模块负责什么、关键函数之间怎么连接。

已有的 [learning-roadmap.md](learning-roadmap.md) 更像学习路线图：告诉你先学什么、后学什么。本文件更像源码讲义：围绕真实代码解释“这一行代码背后的逻辑”。

## 0. 先建立读代码的方法

读这个项目时不要从最复杂的 RAG 算法开始。小白最容易卡住的原因是：还没弄清“请求从哪里来、返回到哪里去”，就直接钻进检索、重写、rerank、LangGraph。

建议你把项目分成五条主线：

```text
主线 A：聊天请求
前端输入 -> /chat/stream -> chat service -> Agent -> SSE 返回前端

主线 B：Agent 工具调用
Agent 判断要不要用工具 -> search_knowledge_base -> RAG 图

主线 C：RAG 检索
问题分类 -> 初次检索 -> 相关性评分 -> 查询重写 -> 扩展检索 -> 合成上下文

主线 D：文档入库
上传文件 -> 解析文本 -> 三级分块 -> 父块入 PostgreSQL -> 叶子块入 Milvus

主线 E：前端展示
接收 SSE -> 更新消息 -> 展示思考步骤 -> 展示引用来源
```

每次读一个文件时，只问四个问题：

1. 这个文件属于哪条主线？
2. 它接收什么输入？
3. 它输出什么给下一个模块？
4. 它失败时会怎么处理？

你不需要第一次就看懂所有细节。先看调用链，再看关键函数，最后再看边界处理。

## 1. 一次聊天请求的全链路地图

先记住这条主链路：

```text
frontend/src/stores/chat.ts
  -> POST /chat/stream
  -> backend/api/routes/chat.py
  -> backend/chat/service.py
  -> backend/chat/runtime.py
  -> backend/tools/knowledge.py
  -> backend/rag/pipeline.py
  -> backend/rag/utils.py
  -> Milvus / PostgreSQL / Redis / LLM
  -> backend/chat/service.py
  -> SSE data: {...}
  -> frontend/src/stores/chat.ts
  -> Chat 组件展示
```

把它翻译成白话：

1. 用户在前端输入问题。
2. 前端通过 `fetch('/chat/stream')` 请求后端。
3. FastAPI 路由把请求交给聊天服务。
4. 聊天服务准备历史上下文、持久化笔记、SSE 队列。
5. LangChain Agent 开始思考，必要时调用知识库工具。
6. 知识库工具启动 LangGraph RAG 流程。
7. RAG 从 Milvus 检索叶子分块，必要时从 PostgreSQL 合并父级分块。
8. Agent 根据工具返回的 chunks 生成最终回答。
9. 后端把回答 token、RAG step、trace 按 SSE 事件推给前端。
10. 前端一边显示“思考中”，一边接收内容和引用信息。

这一条链路是整份代码的骨架。后面每一章都在解释骨架上的某一段。

## 2. 前端如何发起流式聊天

重点文件：

- [frontend/src/stores/chat.ts](../frontend/src/stores/chat.ts)
- [frontend/src/types/chat.ts](../frontend/src/types/chat.ts)
- [frontend/src/components/Chat/ChatArea.vue](../frontend/src/components/Chat/ChatArea.vue)
- [frontend/src/components/Chat/ThinkingTrace.vue](../frontend/src/components/Chat/ThinkingTrace.vue)
- [frontend/src/components/Chat/References.vue](../frontend/src/components/Chat/References.vue)

### 2.1 先看 `handleSend`

前端聊天的入口在 `frontend/src/stores/chat.ts` 的 `handleSend()`。

它做了几件事：

```text
检查登录状态
  -> 取出输入框文本
  -> 先把用户消息插入 messages
  -> 创建一个空的 AI 消息
  -> 创建 AbortController
  -> fetch('/chat/stream')
  -> 读取 response.body 的流式数据
  -> 按 SSE 事件类型更新消息
```

你可以把 `messages` 理解成前端页面的“消息数组”。页面不是直接等后端一次性返回完整答案，而是先插入一条空 AI 消息：

```text
{
  text: '',
  isUser: false,
  isThinking: true,
  ragTrace: null,
  ragSteps: [],
  _groupedSteps: [],
}
```

这条消息一开始是空的。后端每推来一点内容，前端就往 `text` 里追加一点，所以你看到的是打字机效果。

### 2.2 为什么要用 `AbortController`

`AbortController` 的作用是让用户可以中断回答。

前端创建：

```text
this.abortController = new AbortController()
```

请求时传入：

```text
signal: this.abortController.signal
```

点击停止时调用：

```text
this.abortController.abort()
```

这会让浏览器主动断开流式请求。前端捕获 `AbortError` 后，把当前 AI 消息改成“已终止回答”。

### 2.3 前端如何解析 SSE

SSE 的格式大致是：

```text
data: {"type":"content","content":"你好"}

data: {"type":"rag_step","step":{...}}

data: [DONE]
```

前端不是一次读完整响应，而是：

```text
reader.read()
  -> TextDecoder 解码
  -> 累积到 buffer
  -> 找到 '\n\n'
  -> 取出一个完整事件
  -> JSON.parse
```

你要特别关注这段逻辑：

```text
while ((eventEndIndex = buffer.indexOf('\n\n')) !== -1) {
  const eventStr = buffer.slice(0, eventEndIndex)
  buffer = buffer.slice(eventEndIndex + 2)
  ...
}
```

它解决的是“网络分片”问题。浏览器收到的数据块不一定刚好等于一个完整 JSON 事件，所以必须用 `buffer` 缓存半截内容。

### 2.4 前端认识哪些事件类型

`chat.ts` 里主要处理五类事件：

```text
content        追加 AI 正文
trace          保存最终 rag_trace
rag_step       追加一个实时 RAG 步骤
session_title  更新会话标题
error          展示错误
```

其中 `rag_step` 是项目里比较有学习价值的设计。RAG 检索不是等全部结束后才展示，而是在检索、评分、重写过程中实时推给前端。

### 2.5 本章检查题

读完本章后，你应该能回答：

1. 用户点击发送后，前端为什么要先插入一条空 AI 消息？
2. `buffer` 为什么不能省略？
3. `content`、`rag_step`、`trace` 三种事件分别更新消息里的什么字段？
4. 停止回答时，前端断开的是哪一个请求？

## 3. FastAPI 如何接住 `/chat/stream`

重点文件：

- [backend/api/routes/chat.py](../backend/api/routes/chat.py)
- [backend/schemas/chat.py](../backend/schemas/chat.py)
- [backend/infra/auth.py](../backend/infra/auth.py)

### 3.1 路由函数在哪里

`backend/api/routes/chat.py` 里有两个聊天接口：

```text
POST /chat         非流式，一次性返回
POST /chat/stream  流式，SSE 返回
```

Agentic RAG 项目重点看 `/chat/stream`：

```text
@router.post("/chat/stream")
async def chat_stream_endpoint(request: ChatRequest, current_user: User = Depends(get_current_user)):
    ...
```

这个函数的参数里有两个重点：

```text
request: ChatRequest
current_user: User = Depends(get_current_user)
```

`ChatRequest` 是前端传来的 JSON，里面有用户问题和会话 ID。`Depends(get_current_user)` 表示这个接口必须登录，后端会从 Bearer Token 里解析当前用户。

### 3.2 为什么返回 `StreamingResponse`

普通接口一般这样返回：

```text
return {"response": "..."}
```

但流式接口返回：

```text
return StreamingResponse(event_generator(), media_type="text/event-stream")
```

`StreamingResponse` 的核心意义是：后端不用等整个 Agent 完成，可以一边执行一边 `yield` 数据。

这就是为什么用户能实时看到：

```text
正在检索知识库...
正在评估文档相关性...
正在重写查询...
答案正文逐字出现...
```

### 3.3 `event_generator` 做什么

`event_generator` 是一个异步生成器。它调用：

```text
chat_with_agent_stream(request.message, current_user.username, session_id)
```

然后把里面产生的每个 chunk 原样 `yield` 出去。

白话理解：

```text
FastAPI 路由自己不懂 Agent
它只负责：
  - 校验用户
  - 拿到 message/session_id
  - 调用聊天服务
  - 把聊天服务产生的 SSE 字符串转发给浏览器
```

### 3.4 本章检查题

1. `/chat/stream` 为什么必须依赖 `get_current_user`？
2. `StreamingResponse` 和普通 JSON 返回有什么区别？
3. 路由层有没有直接调用 RAG？
4. 路由层负责业务逻辑，还是负责把请求转给 service？

## 4. `chat/service.py` 如何组织上下文、记忆和 SSE

重点文件：

- [backend/chat/service.py](../backend/chat/service.py)
- [backend/chat/storage.py](../backend/chat/storage.py)
- [backend/chat/streaming.py](../backend/chat/streaming.py)
- [backend/chat/rag_context.py](../backend/chat/rag_context.py)

`backend/chat/service.py` 是聊天主链路里最重要的文件之一。你可以把它理解成“对话总调度器”。

### 4.1 入口函数

它有两个主要入口：

```text
chat_with_agent(...)          非流式聊天
chat_with_agent_stream(...)   流式聊天
```

学习时优先看 `chat_with_agent_stream`，因为前端主要用它。

### 4.2 第一步：加载历史消息

函数一开始做：

```text
messages, metadata = storage.load_with_meta(user_id, session_id)
```

这里的 `storage` 是 `ConversationStorage()`，负责从 PostgreSQL 和 Redis 里读写会话。

`messages` 是历史对话消息，`metadata` 里包含会话标题、持久化笔记等信息。

### 4.3 第二步：准备持久化笔记

代码里有一个概念叫 `persistent_note`，可以理解成“会话摘要记忆”。

为什么需要它？

模型上下文窗口有限。如果每次都把所有历史消息塞进去，会越来越长。项目采取的做法是：

```text
最近几轮原始消息 + 一份压缩后的持久化笔记
```

对应函数是：

```text
_build_context_messages(messages, persistent_note, user_text)
```

它把上下文整理成：

```text
SystemMessage(持久化笔记，可选)
最近 6 条历史消息
HumanMessage(当前问题)
```

这就是 Agent 真正看到的输入。

### 4.4 第三步：重置 RAG 状态

每一轮开始前会调用：

```text
get_last_rag_context(clear=True)
reset_knowledge_tool_calls()
```

这两个动作非常关键：

```text
get_last_rag_context(clear=True)
  清掉上一轮遗留的 rag_trace，避免把上一次检索结果保存到这一次消息里

reset_knowledge_tool_calls()
  重置知识库工具调用次数，保证这一轮最多调用一次 search_knowledge_base
```

这是 Agent 项目里常见的“每轮状态隔离”问题。如果不清理，旧状态可能污染新回答。

### 4.5 第四步：建立 RAG step 队列

流式函数里创建了：

```text
output_queue = asyncio.Queue()
```

然后定义了一个 `_RagStepProxy`：

```text
class _RagStepProxy:
    def put_nowait(self, step):
        output_queue.put_nowait({"type": "rag_step", "step": step})
```

再调用：

```text
set_rag_step_queue(_RagStepProxy())
```

这一步把 RAG pipeline 和 SSE 输出连接起来。后面 RAG 里调用 `emit_rag_step(...)` 时，消息会被放进这个队列，最后被 `/chat/stream` 推给前端。

### 4.6 第五步：后台跑 Agent

`chat_with_agent_stream` 内部定义了 `_agent_worker()`，然后：

```text
agent_task = asyncio.create_task(_agent_worker())
```

这表示 Agent 在后台异步执行，主函数继续从 `output_queue` 里取事件并 `yield` 给前端。

这个结构很重要：

```text
Agent worker 负责生产事件
主 while 循环负责消费事件并发送给浏览器
```

如果没有队列，Agent 生成正文和 RAG step 就很难统一流式推送。

### 4.7 第六步：过滤工具调用 chunk

在 `_agent_worker()` 里，代码只把真正的文本内容推给前端：

```text
if getattr(msg, "tool_call_chunks", None):
    continue
```

工具调用过程中的内部 chunk 不直接显示给用户。用户看到的是项目自己整理过的 `rag_step` 和最终回答，而不是模型原始工具调用协议。

### 4.8 第七步：保存最终回答和 trace

Agent 完成后，服务层会：

```text
rag_context = get_last_rag_context(clear=True)
rag_trace = rag_context.get("rag_trace") if rag_context else None
yield trace 事件
yield [DONE]
更新 persistent_note
保存 AIMessage 和 rag_trace
```

所以 `rag_trace` 有两个用途：

```text
实时回答结束时推给前端展示
保存到数据库，之后加载历史会话时还能看到
```

### 4.9 本章检查题

1. `persistent_note` 和最近 6 条历史消息分别解决什么问题？
2. 为什么每轮开始要清空上一次的 RAG context？
3. `_agent_worker` 和外层 `while True` 是生产者/消费者关系吗？
4. `rag_step` 和 `rag_trace` 有什么区别？

## 5. Agent 在 `runtime.py` 里如何创建

重点文件：

- [backend/chat/runtime.py](../backend/chat/runtime.py)
- [backend/tools/knowledge.py](../backend/tools/knowledge.py)
- [backend/tools/weather.py](../backend/tools/weather.py)

### 5.1 `runtime.py` 是 Agent 工厂

`backend/chat/runtime.py` 里最核心的是：

```text
def create_agent_instance():
    model = init_chat_model(...)
    fast_model = init_chat_model(...)
    agent = create_agent(...)
    return agent, model, fast_model
```

模块底部执行：

```text
agent, model, fast_model = create_agent_instance()
```

这表示项目启动后就创建一个模块级 Agent 实例。其他文件直接从这里 import：

```text
from backend.chat.runtime import agent, fast_model
```

### 5.2 `model` 和 `fast_model` 的区别

代码创建了两个模型对象：

```text
model       主 Agent 回答用
fast_model 轻量任务用，比如生成会话标题、更新持久化笔记、问题分类
```

白话理解：

```text
主回答：更重，更关注质量
辅助任务：更快，更便宜
```

这是 Agent 应用常见设计：不是所有任务都需要用最强模型。

### 5.3 System Prompt 控制 Agent 行为

`SYSTEM_PROMPT` 里有几个非常重要的约束：

```text
Use search_knowledge_base when users ask document/knowledge questions.
Do not call the same tool repeatedly in one turn.
At most one knowledge tool call per turn.
After receiving search_knowledge_base result, you MUST NOT call any tool again.
If retrieved context is insufficient, answer honestly.
When answering based on retrieved chunks, cite source chunks inline.
```

这段 prompt 不是普通描述，而是在约束 Agent 的行为边界。

为什么要限制一轮最多调用一次知识库工具？

因为 Agent 如果没有约束，可能出现：

```text
检索 -> 不满意 -> 再检索 -> 又不满意 -> 继续检索
```

这会导致成本增加、延迟变长，甚至触发递归限制。项目同时在 prompt 和工具代码里做了双重限制。

### 5.4 `create_agent` 绑定工具

关键代码：

```text
agent = create_agent(
    model=model,
    tools=[get_current_weather, search_knowledge_base],
    system_prompt=SYSTEM_PROMPT,
)
```

这行代码把模型和工具绑定起来。绑定后，模型不只是“聊天模型”，而是可以选择调用工具的 Agent。

工具列表里现在有两个：

```text
get_current_weather     天气工具示例
search_knowledge_base   知识库检索工具，Agentic RAG 的核心
```

### 5.5 本章检查题

1. `agent` 是每次请求新建，还是模块加载时创建？
2. `fast_model` 在聊天主回答里直接生成答案吗？
3. System Prompt 为什么要限制工具调用次数？
4. Agent 什么时候会调用 `search_knowledge_base`？

## 6. 知识库工具如何连接 Agent 和 RAG

重点文件：

- [backend/tools/knowledge.py](../backend/tools/knowledge.py)
- [backend/rag/pipeline.py](../backend/rag/pipeline.py)
- [backend/chat/rag_context.py](../backend/chat/rag_context.py)

### 6.1 `@tool` 是什么

`knowledge.py` 里最重要的是：

```text
@tool("search_knowledge_base")
def search_knowledge_base(query: str) -> str:
    ...
```

`@tool` 把普通 Python 函数包装成 LangChain Agent 可调用的工具。

对 Agent 来说，这个工具长这样：

```text
工具名：search_knowledge_base
输入：query 字符串
输出：一段文本，包含 Retrieved Chunks
```

### 6.2 工具调用次数限制

文件顶部有：

```text
_KNOWLEDGE_TOOL_CALLS_THIS_TURN = 0
```

每次调用工具时先执行：

```text
if not _try_acquire_knowledge_tool_call():
    return "TOOL_CALL_LIMIT_REACHED..."
```

这和 `runtime.py` 里的 System Prompt 是配套的：

```text
Prompt 软约束：告诉模型不要重复调用
代码硬约束：模型真重复调用时直接拒绝
```

这是一种很实用的 Agent 防护方式。不要只相信 prompt，关键边界要用代码兜底。

### 6.3 工具如何启动 RAG

工具内部调用：

```text
rag_result = run_rag_graph(query)
```

这就是 Agent 和 RAG 的连接点。

白话调用链：

```text
Agent 决定要查知识库
  -> 调用 search_knowledge_base(query)
  -> search_knowledge_base 调用 run_rag_graph(query)
  -> LangGraph RAG 返回 docs + rag_trace
  -> 工具把 docs 格式化成文本
  -> Agent 阅读工具结果并生成最终回答
```

注意：RAG pipeline 本身不直接回答用户。它主要返回“检索上下文”。最终自然语言回答仍然由 Agent 生成。

### 6.4 为什么要 `record_rag_context`

工具拿到 `rag_trace` 后调用：

```text
record_rag_context(rag_trace)
```

原因是工具函数只能把字符串返回给 Agent，但服务层还需要结构化的 `rag_trace` 保存到数据库并推给前端。

所以项目用了一个中间暂存：

```text
工具内部：record_rag_context(rag_trace)
服务层结束后：get_last_rag_context(clear=True)
```

这是一种跨层传递 trace 的办法。

### 6.5 工具返回给 Agent 的格式

工具最终返回：

```text
Retrieved Chunks:
[1] filename (Page x):
文本...

---

[2] filename (Page y):
文本...
```

System Prompt 要求 Agent 用 `[1]`、`[2]` 这种编号引用来源。这个编号就是在工具返回字符串里构造的。

### 6.6 本章检查题

1. `search_knowledge_base` 返回的是结构化 dict 还是字符串？
2. `rag_trace` 为什么不能只放在工具返回字符串里？
3. Prompt 限制和代码限制分别解决什么问题？
4. RAG pipeline 是直接生成最终答案，还是给 Agent 提供上下文？

## 7. LangGraph RAG 主图如何工作

重点文件：

- [backend/rag/pipeline.py](../backend/rag/pipeline.py)
- [backend/rag/utils.py](../backend/rag/utils.py)
- [backend/chat/streaming.py](../backend/chat/streaming.py)

`backend/rag/pipeline.py` 是项目最核心、也最容易让小白迷路的文件。读它时不要从每个函数细节开始，要先看图结构。

### 7.1 RAGState 是图的共享状态

文件里定义了：

```text
class RAGState(TypedDict):
    question: str
    query: str
    context: str
    docs: List[dict]
    route: Optional[str]
    expansion_type: Optional[str]
    expanded_query: Optional[str]
    ...
```

LangGraph 的每个节点都接收一个 `state`，返回一个“局部更新 dict”。这些更新会合并回状态。

白话理解：

```text
RAGState 就是一张流程表
每个节点读表里的信息
做完自己的事后，把新信息写回表里
```

比如 `retrieve_initial` 会写入：

```text
query
docs
context
rag_trace
```

`grade_documents_node` 会写入：

```text
route
rag_trace.grade_score
rag_trace.rewrite_needed
```

### 7.2 主图从复杂度分类开始

`build_rag_graph()` 里设置入口：

```text
graph.set_entry_point("classify_complexity")
```

所以 RAG 第一件事不是检索，而是判断问题简单还是复杂。

```text
classify_complexity
  -> simple  -> retrieve_initial
  -> complex -> decompose_question
```

简单问题走标准 RAG。复杂问题会先分解成子问题。

### 7.3 简单问题的标准 RAG 流程

简单问题走这条链：

```text
retrieve_initial
  -> 如果没有 docs：rewrite_question
  -> 如果有 docs：grade_documents

grade_documents
  -> 相关：结束，交给 Agent 回答
  -> 不相关：rewrite_question

rewrite_question
  -> retrieve_expanded
  -> 结束，交给 Agent 回答
```

这就是 Corrective RAG 的基本思想：

```text
先检索
检查检索结果是否靠谱
不靠谱就重写问题再检索一次
```

### 7.4 `retrieve_initial` 做什么

`retrieve_initial(state)` 从用户原问题开始：

```text
query = state["question"]
retrieved = retrieve_documents(query, top_k=5)
results = retrieved.get("docs", [])
context = _format_docs(results)
```

它还会不断调用：

```text
emit_rag_step(...)
```

这些 step 会实时推到前端，比如：

```text
正在检索知识库
三级分块检索
Auto-merging 合并
检索完成
```

所以 `retrieve_initial` 不只是检索，还负责构造第一版 `rag_trace`。

### 7.5 `grade_documents_node` 做什么

这个节点用一个评分模型判断：

```text
检索到的 context 和用户问题是否相关？
```

结构化输出模型是：

```text
class GradeDocuments(BaseModel):
    binary_score: str
```

它只要 `yes` 或 `no`。

如果是 `yes`：

```text
route = "generate_answer"
```

如果是 `no`：

```text
route = "rewrite_question"
```

注意：这里的 `generate_answer` 不是 RAG 图里的一个生成节点，而是表示“RAG 可以结束了，返回上下文给 Agent 生成回答”。

### 7.6 `rewrite_question_node` 做什么

当初次检索为空或评分不通过时，会进入查询重写。

项目支持三种策略：

```text
step_back  退步问题：把具体问题抽象成更通用的问题
hyde       假设性文档：先生成一段像答案资料的文本，再用它检索
complex    组合策略：step_back + hyde
```

路由模型会选择策略：

```text
router.with_structured_output(RewriteStrategy).invoke(...)
```

如果没有初始结果，代码会强制使用 `step_back`。这很合理，因为没有任何命中文档时，先把问题抽象化通常能扩大召回范围。

### 7.7 `retrieve_expanded` 做什么

扩展检索会根据策略走不同分支：

```text
hyde 或 complex:
  用 hypothetical_doc 检索

step_back 或 complex:
  用 expanded_query 检索
```

如果是 `complex`，可能有两路结果，需要：

```text
dedupe_documents(results)
```

去重后再构造最终上下文。

### 7.8 复杂问题如何分解成子 Agent

复杂问题不是直接检索，而是：

```text
classify_complexity
  -> decompose_question
  -> _fanout_sub_questions
  -> 多个 rag_sub_agent 并行执行
  -> synthesis
```

`decompose_question` 会生成 2-4 个子问题。

`_fanout_sub_questions` 使用 LangGraph 的 `Send`：

```text
Send("rag_sub_agent", {...})
```

每个子问题都会进入一个完整的 RAG 子图：

```text
retrieve_initial -> grade_documents -> rewrite_question -> retrieve_expanded
```

最后 `synthesis` 把所有子问题检索到的 docs 合并、去重、排序。

这就是 README 里提到的“并行 Sub-Agent 图流程”。这里的子 Agent 不是另一个聊天 Agent，而是每个子问题都有一条独立 RAG 子流程。

### 7.9 本章检查题

1. `RAGState` 为什么要包含 `docs`、`context`、`rag_trace`？
2. 简单问题和复杂问题从哪个节点开始分流？
3. `grade_documents_node` 输出的 `generate_answer` 是不是直接生成回答？
4. 子 Agent 是负责聊天，还是负责子问题检索？

## 8. 混合检索、Rerank、Auto-merging 的代码逻辑

重点文件：

- [backend/rag/utils.py](../backend/rag/utils.py)
- [backend/indexing/milvus_client.py](../backend/indexing/milvus_client.py)
- [backend/indexing/embedding.py](../backend/indexing/embedding.py)
- [backend/indexing/parent_chunk_store.py](../backend/indexing/parent_chunk_store.py)

### 8.1 `retrieve_documents` 是检索入口

RAG pipeline 不直接操作 Milvus，而是调用：

```text
retrieve_documents(query, top_k=5)
```

它在 `backend/rag/utils.py` 里。

整体流程：

```text
resolve_candidate_k
  -> 生成 query 的 dense embedding
  -> Milvus hybrid_retrieve
  -> _finalize_retrieval
  -> 返回 docs + meta
```

如果 hybrid 检索失败，会降级：

```text
hybrid_retrieve 失败
  -> dense_retrieve
  -> 如果 dense 也失败，返回空 docs 和 failed meta
```

这就是双向降级的一部分：核心检索失败时，不让整个聊天请求直接崩掉。

### 8.2 候选池 candidate_k

用户最终只需要 top 5，但检索阶段一般会多取一些候选：

```text
candidate_k = top_k * RETRIEVAL_CANDIDATE_MULTIPLIER
```

原因是后面还要做：

```text
Auto-merging
Rerank
阈值过滤
```

如果一开始只取 5 条，后面过滤后可能剩很少。多取候选可以给后处理留空间。

### 8.3 为什么只检索叶子块

检索时有：

```text
filter_expr = f"chunk_level == {LEAF_RETRIEVE_LEVEL}"
```

默认 `LEAF_RETRIEVE_LEVEL = 3`，也就是先检索 L3 叶子块。

原因：

```text
L3 块更短、更聚焦，适合向量召回
L1/L2 块更长、更完整，适合回答时提供上下文
```

所以项目采取：

```text
先用 L3 精准召回
如果同一个父块下多个 L3 都命中，再自动合并到 L2 或 L1
```

### 8.4 Auto-merging 如何实现

入口在：

```text
_auto_merge_candidates(docs)
```

核心函数：

```text
_merge_to_parent_level(docs, threshold=AUTO_MERGE_THRESHOLD)
```

逻辑是：

```text
按 parent_chunk_id 分组
  -> 如果同一个 parent 下命中的子块数量 >= threshold
  -> 从 ParentChunkStore 取父块正文
  -> 用父块替换多个子块
```

它会做两轮：

```text
L3 -> L2
L2 -> L1
```

这就是三级分块 + Auto-merging 的核心。

白话例子：

```text
用户问一个比较大的概念
Milvus 命中了同一节里的 3 个小片段
说明这一整节都可能相关
于是系统拿父级分块作为更完整的上下文给 Agent
```

### 8.5 Rerank 如何实现

Rerank 入口：

```text
_rerank_documents(query, docs, top_k)
```

如果环境变量里配置了：

```text
RERANK_MODEL
RERANK_API_KEY
RERANK_BINDING_HOST
```

就会请求 rerank 服务：

```text
POST /v1/rerank
```

Rerank 的作用：

```text
Milvus 负责召回候选
Rerank 负责重新排序，挑出更相关的 top_k
```

如果 rerank 失败，代码不会中断，而是：

```text
return _sort_by_rank_score(docs_with_rank)[:top_k]
```

也就是回退到原始召回分。

### 8.6 `meta` 为什么很重要

`retrieve_documents` 返回的不只是 docs，还有 meta。

meta 里记录：

```text
retrieval_mode
candidate_k
recall_count
auto_merge_applied
auto_merge_replaced_chunks
rerank_enabled
rerank_applied
rerank_error
post_rerank_count
post_threshold_count
```

这些字段最后会进入 `rag_trace`，前端可以展示，也方便你调试：

```text
为什么没召回？
是 hybrid 失败了吗？
是 rerank 过滤太严了吗？
Auto-merging 有没有发生？
```

### 8.7 本章检查题

1. 为什么检索阶段要取 `candidate_k`，而不是直接取 `top_k`？
2. Auto-merging 什么时候会把 L3 换成 L2/L1？
3. Rerank 服务失败时，聊天是否会直接失败？
4. `meta` 对前端展示和调试有什么价值？

## 9. 文档上传、三级分块、Milvus 入库链路

重点文件：

- [backend/api/routes/documents.py](../backend/api/routes/documents.py)
- [backend/indexing/document_loader.py](../backend/indexing/document_loader.py)
- [backend/indexing/milvus_writer.py](../backend/indexing/milvus_writer.py)
- [backend/indexing/parent_chunk_store.py](../backend/indexing/parent_chunk_store.py)
- [backend/api/resources.py](../backend/api/resources.py)

### 9.1 为什么先学入库链路

RAG 检索能查到东西的前提是：文档已经被正确解析、分块、写入向量库。

如果你只看检索，不看入库，会不知道这些字段从哪里来：

```text
chunk_id
parent_chunk_id
root_chunk_id
chunk_level
page_number
filename
text
```

### 9.2 上传接口主流程

异步上传接口在：

```text
POST /documents/upload/async
```

它做两件事：

```text
先保存上传文件
再把真正处理任务丢给后台 _process_upload_job
```

`_process_upload_job` 是入库链路主流程：

```text
保存文件完成
  -> 清理同名旧文档
  -> loader.load_document
  -> 拆出 parent_docs 和 leaf_docs
  -> parent_chunk_store.upsert_documents(parent_docs)
  -> milvus_writer.write_documents(leaf_docs)
  -> 更新 job 状态
```

### 9.3 `DocumentLoader` 如何三级分块

`backend/indexing/document_loader.py` 里定义了三个 splitter：

```text
_splitter_level_1  大块
_splitter_level_2  中块
_splitter_level_3  小块/叶子块
```

核心函数是：

```text
_split_page_to_three_levels(text, base_doc, page_global_chunk_idx)
```

它的嵌套结构是：

```text
一页原文
  -> 切成多个 L1
      -> 每个 L1 切成多个 L2
          -> 每个 L2 切成多个 L3
```

每个 chunk 都有：

```text
chunk_id          自己的 ID
parent_chunk_id   父块 ID
root_chunk_id     所属 L1 根块 ID
chunk_level       1 / 2 / 3
chunk_idx         在文档里的顺序
```

### 9.4 父块和叶子块为什么分开存

上传任务里有：

```text
parent_docs = chunk_level in (1, 2)
leaf_docs = chunk_level == 3
```

然后：

```text
parent_docs -> PostgreSQL ParentChunk
leaf_docs   -> Milvus
```

原因：

```text
Milvus 主要负责向量召回，需要存最适合检索的小块
PostgreSQL 负责保存父级正文，需要支持根据 parent_chunk_id 精确取回完整上下文
```

这就是 Leaf-only 向量化存储。

### 9.5 `MilvusWriter` 如何写入向量库

`backend/indexing/milvus_writer.py` 的入口是：

```text
write_documents(documents, batch_size=50, progress_callback=None)
```

它按 batch 处理：

```text
取一批 leaf_docs
  -> texts = [doc["text"] for doc in batch]
  -> dense_embeddings = embedding_service.get_embeddings(texts)
  -> 组装 insert_data
  -> client.insert(...)
  -> progress_callback(processed, total)
```

注意：代码只显式写入 dense embedding 和 text。BM25 sparse 部分是 Milvus 2.5+ 服务端通过 schema function 从 `text` 字段生成的，所以这里不需要本地手写 sparse vector。

### 9.6 文本清洗为什么重要

`sanitize_text` 会处理：

```text
Unicode 规范化
零宽字符
控制字符
私有区乱码
孤立代理项
```

小白可能会觉得这不是核心，但它对生产系统很重要。文档来自 PDF、Word、Excel、HTML，里面经常有不可见字符。如果不清洗，可能导致：

```text
数据库写入失败
embedding 输入异常
前端展示乱码
检索文本污染
```

### 9.7 本章检查题

1. L1、L2、L3 三层 chunk 的关系是什么？
2. 为什么只把 L3 写入 Milvus？
3. `parent_chunk_id` 在检索阶段有什么用？
4. 文档上传为什么要先清理同名旧文档？

## 10. RAG trace 如何实时推给前端并展示

重点文件：

- [backend/chat/streaming.py](../backend/chat/streaming.py)
- [backend/chat/service.py](../backend/chat/service.py)
- [backend/rag/pipeline.py](../backend/rag/pipeline.py)
- [frontend/src/stores/chat.ts](../frontend/src/stores/chat.ts)
- [frontend/src/components/Chat/ThinkingTrace.vue](../frontend/src/components/Chat/ThinkingTrace.vue)
- [frontend/src/components/Chat/RetrievalTraceDetails.vue](../frontend/src/components/Chat/RetrievalTraceDetails.vue)

### 10.1 `rag_step` 和 `rag_trace` 的区别

先分清两个概念：

```text
rag_step
  实时步骤事件
  例如：正在检索、正在评分、正在重写
  用于“思考中”的动态展示

rag_trace
  完整结构化结果
  例如：召回 chunks、rerank 分数、合并信息、子问题 traces
  用于回答结束后的详细展开和历史保存
```

### 10.2 `emit_rag_step` 如何跨线程推送

`backend/chat/streaming.py` 里有：

```text
set_rag_step_queue(queue)
emit_rag_step(icon, label, detail="")
```

`set_rag_step_queue` 保存当前事件循环和队列。

`emit_rag_step` 里用：

```text
_RAG_STEP_LOOP.call_soon_threadsafe(...)
```

原因是 RAG pipeline 可能在不同执行上下文中运行，直接操作 asyncio 队列不一定安全。`call_soon_threadsafe` 可以把事件安全地投递回主事件循环。

### 10.3 子 Agent 分组如何展示

复杂问题会拆成多个子问题。为了让前端知道某个 step 属于哪个子问题，后端用了线程本地变量：

```text
_sub_agent_context = threading.local()
```

子 Agent 执行前：

```text
set_sub_agent_group(question)
```

执行后：

```text
clear_sub_agent_group()
```

`emit_rag_step` 会把 group 加到 step 里：

```text
step["group"] = group
```

前端收到后用 `appendRagStepToGroups` 分组展示。

### 10.4 前端如何分组

`frontend/src/stores/chat.ts` 里：

```text
appendRagStepToGroups(prev, step)
```

如果 step 有 `group`：

```text
找到同名 group
  -> 追加到该组
找不到
  -> 新建一个折叠组
```

如果没有 `group`：

```text
归入普通主流程步骤
```

这样复杂问题的多个子问题不会混成一团。

### 10.5 本章检查题

1. 为什么实时展示用 `rag_step`，历史详情用 `rag_trace`？
2. `call_soon_threadsafe` 解决什么问题？
3. 子问题并行检索时，前端如何知道 step 属于哪个子问题？
4. `rag_trace` 是什么时候发给前端的？

## 11. 数据库模型和会话持久化怎么支撑 Agent

重点文件：

- [backend/db/models.py](../backend/db/models.py)
- [backend/chat/storage.py](../backend/chat/storage.py)
- [backend/infra/database.py](../backend/infra/database.py)
- [backend/infra/cache.py](../backend/infra/cache.py)

### 11.1 先看四个核心表

`backend/db/models.py` 里最重要的是：

```text
User
ChatSession
ChatMessage
ParentChunk
```

关系是：

```text
User
  -> ChatSession
      -> ChatMessage

ParentChunk 独立服务 RAG 父块存储
```

### 11.2 `User`

`User` 保存：

```text
username
password_hash
role
created_at
```

`role` 用于权限控制。普通用户可以聊天和管理自己的会话，admin 才能上传、删除、查看文档。

### 11.3 `ChatSession`

`ChatSession` 保存一个会话：

```text
user_id
session_id
metadata_json
updated_at
created_at
```

`metadata_json` 很重要，它可以保存非消息正文的信息，例如：

```text
title
persistent_note
```

### 11.4 `ChatMessage`

每条消息保存：

```text
message_type
content
timestamp
rag_trace
```

`rag_trace` 只会出现在 AI 消息上。这样用户打开历史会话时，不仅能看到回答，还能看到当时检索到了哪些来源。

### 11.5 `ParentChunk`

`ParentChunk` 保存 L1/L2 父级分块：

```text
chunk_id
text
filename
page_number
parent_chunk_id
root_chunk_id
chunk_level
chunk_idx
```

它服务于 Auto-merging。Milvus 命中 L3 后，如果要合并到父块，就通过 `chunk_id` 到 PostgreSQL 查父块正文。

### 11.6 本章检查题

1. 为什么 `ChatMessage` 要保存 `rag_trace`？
2. `metadata_json` 适合保存什么信息？
3. `ParentChunk` 为什么不直接放在 Milvus 里？
4. 普通聊天历史和 RAG 文档父块分别由哪些表支撑？

## 12. 推荐学习顺序和练习任务

如果你是小白，建议按下面顺序学习，不要跳：

### 第 1 天：只跑通和画链路

目标：

```text
能启动项目
能注册登录
能发一条消息
能打开浏览器开发者工具看到 /chat/stream
```

阅读：

```text
README.md
docs/learning-roadmap.md
frontend/src/stores/chat.ts 的 handleSend
backend/api/routes/chat.py
```

练习：

1. 在纸上画出一次 `/chat/stream` 的请求链路。
2. 找出前端传给后端的 JSON 字段。
3. 找出后端返回的 SSE 事件类型。

### 第 2 天：读懂聊天服务和 Agent

阅读：

```text
backend/chat/service.py
backend/chat/runtime.py
backend/tools/knowledge.py
```

练习：

1. 解释 `persistent_note` 是怎么进入 Agent 上下文的。
2. 解释为什么知识库工具一轮最多调用一次。
3. 修改 System Prompt 的一句话，观察 Agent 行为是否变化。

### 第 3 天：读懂 RAG 主图

阅读：

```text
backend/rag/pipeline.py
```

练习：

1. 画出 simple 问题的 RAG 流程。
2. 画出 complex 问题的 RAG 流程。
3. 找出每个节点会更新 `RAGState` 的哪些字段。

### 第 4 天：读懂检索实现

阅读：

```text
backend/rag/utils.py
backend/indexing/milvus_client.py
backend/indexing/parent_chunk_store.py
```

练习：

1. 把 `RETRIEVAL_CANDIDATE_MULTIPLIER` 改小或改大，观察 trace 字段变化。
2. 解释 hybrid 失败后如何 fallback 到 dense。
3. 解释 Auto-merging 的触发条件。

### 第 5 天：读懂文档入库

阅读：

```text
backend/api/routes/documents.py
backend/indexing/document_loader.py
backend/indexing/milvus_writer.py
```

练习：

1. 上传一份小文档，观察 upload job 的阶段变化。
2. 打印或断点查看 L1/L2/L3 chunk 的数量。
3. 解释为什么父块和叶子块分开存储。

### 第 6 天：读懂 trace 展示

阅读：

```text
backend/chat/streaming.py
frontend/src/stores/chat.ts
frontend/src/components/Chat/ThinkingTrace.vue
frontend/src/components/Chat/RetrievalTraceDetails.vue
frontend/src/components/Chat/References.vue
```

练习：

1. 新增一个 `emit_rag_step`，让前端显示新的检索阶段。
2. 在前端 trace 面板里展示一个已有 meta 字段。
3. 区分实时 step 和最终 trace。

### 第 7 天：做一个小功能

推荐从下面三选一：

```text
任务 A：新增一个简单 Agent 工具
任务 B：在 RAG trace 里多展示一个检索字段
任务 C：给文档上传增加更清晰的错误提示
```

做功能时按这个顺序：

```text
先找到入口
  -> 再找到数据结构
  -> 再找到展示位置
  -> 小改动
  -> 跑通
  -> 写下你改了哪些链路
```

## 13. 最后给小白的读码建议

不要试图一次看懂所有库：

```text
LangChain
LangGraph
FastAPI
Milvus
SQLAlchemy
Vue
Pinia
SSE
```

这些库都很大。你在这个项目里的学习目标不是成为每个库的专家，而是看懂它们在这个项目中承担的角色：

```text
FastAPI     接收请求，返回 SSE
LangChain   创建可调用工具的 Agent
LangGraph   编排 RAG 多节点流程
Milvus      做向量和 BM25 混合召回
PostgreSQL  存用户、会话、父级分块
Redis       缓存热点会话和父块
Vue/Pinia   管理前端状态和流式展示
```

每次读代码都从“输入和输出”开始。只要你能说清楚一个函数接收什么、返回什么、下一步被谁使用，你就已经在真正理解这个项目了。

