# SuperMew Agent / RAG 源码学习课程

> 目标：不是“看懂 README”，而是从资深 Agent 工程师视角，把 SuperMew 的设计拆成可复用的工程能力，最终能够独立修改源码，并在面试中解释设计取舍。

## 0. 项目一句话定位

SuperMew 是一个完整的 **Agent + Agentic RAG** 应用：外层由 LangChain Agent 负责判断是否调用工具，知识库工具内部再进入 LangGraph 驱动的 RAG workflow；文档侧使用三级父子分块，检索侧使用 Milvus Dense + BM25 混合召回、RRF、Auto-merging 和可选 Rerank，并加入证据评分、查询改写、复杂问题拆解和 HITL。

## 1. 学习顺序

请严格按下面顺序学习，不建议一开始就钻 `pipeline.py`。

1. [01 - 项目核心骨架与一次请求的完整调用链](01-project-skeleton-and-request-flow.md)
2. [02 - 外层 Agent：模型、Tool Calling、上下文与边界](02-agent-runtime-and-tool-calling.md)
3. [03 - RAG 入库：解析、三级分块、Embedding、父块存储](03-rag-ingestion-and-hierarchical-chunking.md)
4. [04 - RAG 检索：Dense + BM25、RRF、Auto-merging、Rerank](04-hybrid-retrieval-and-ranking.md)
5. [05 - Agentic RAG：LangGraph 状态机与证据路由](05-agentic-rag-langgraph-pipeline.md)
6. [06 - 检索纠错：Step-back、HyDE、rewrite budget](06-query-rewrite-and-recovery.md)
7. [07 - 复杂问题：并行子 Agent、Synthesis、HITL](07-multi-agent-and-hitl.md)
8. [08 - 工程化：可观测性、评测、失败降级与生产设计](08-observability-evaluation-production.md)
9. [09 - 面试题库：从源码回答 RAG / Agent 高频问题](09-interview-question-bank.md)
10. [10 - 动手实验：从“会看”到“会改”](10-hands-on-labs.md)

## 2. 阅读源码时只抓 5 条主线

### 主线 A：HTTP 请求如何进入 Agent

```text
frontend
  -> POST /chat/stream
  -> backend/api/routes/chat.py
  -> backend/chat/service.py
  -> backend/chat/runtime.py
  -> LangChain Agent
```

### 主线 B：Agent 如何调用 RAG

```text
Agent
  -> search_knowledge_base tool
  -> backend/tools/knowledge.py
  -> run_rag_graph()
  -> backend/rag/pipeline.py
```

### 主线 C：文档如何进入知识库

```text
上传文件
  -> DocumentLoader
  -> L1 / L2 / L3 chunks
  -> parent chunk store
  -> EmbeddingService
  -> MilvusWriter
  -> Milvus dense + sparse/BM25 index
```

### 主线 D：问题如何被检索

```text
query
  -> embedding
  -> Milvus hybrid_retrieve
      |- dense HNSW / inner product
      |- sparse BM25
      `- RRF
  -> Auto-merging
  -> optional rerank
  -> threshold filter
```

### 主线 E：为什么它叫 Agentic RAG

因为“检索”不是一次固定函数，而是有状态决策过程：

```text
复杂度判断
  |- simple -> retrieve -> grade
  |                    |- answer
  |                    |- rewrite -> retrieve -> grade
  |                    |- clarify / scope select / no knowledge
  |
  `- complex -> decompose -> parallel sub-agents -> synthesis
```

## 3. 学完后应达到的面试能力

你至少要能不看代码回答以下问题：

- SuperMew 中 Agent 与 RAG 的边界在哪里？
- 为什么 RAG 本身又用 LangGraph，而不是把检索全塞进一个 Tool 函数？
- Dense 与 BM25 分别解决什么问题？为什么需要 RRF？
- 为什么先检索 L3 小块，再把相邻/同父节点证据合并成 L2/L1？
- Auto-merging 和普通 Parent Document Retriever 有什么异同？
- Rerank 应该放在召回前还是召回后？为什么？
- Step-back 和 HyDE 分别适合哪类检索失败？
- 为什么 rewrite 只允许一次？
- 如何判断“检索结果相关”与“检索结果足以回答”不是一回事？
- 为什么复杂问题拆成多个子问题并行检索？它会带来什么风险？
- HITL 在这里解决的到底是“模型能力问题”还是“信息不足问题”？
- 如果 Milvus/BM25/Rerank/Embedding 某一环节失败，系统怎样降级？

## 4. 关键源码地图

```text
backend/
├── app.py
├── api/routes/
│   ├── chat.py
│   └── documents.py
├── chat/
│   ├── runtime.py          # 外层 Agent 创建
│   ├── service.py          # 对话、SSE、HITL、历史上下文
│   └── request_context.py  # 每次请求的 trace / tool 状态
├── tools/
│   └── knowledge.py        # RAG 被封装成 Agent Tool
├── rag/
│   ├── pipeline.py         # LangGraph Agentic RAG 主图
│   └── utils.py            # 检索、merge、rerank、rewrite
├── indexing/
│   ├── document_loader.py  # 文档解析 + 三级分块
│   ├── embedding.py
│   ├── milvus_client.py    # Dense/BM25/RRF
│   ├── milvus_writer.py
│   └── parent_chunk_store.py
└── schemas/
    └── chat.py
```

## 5. 建议学习方法

每一课都做四件事：

1. **先说架构**：这一层解决什么问题。
2. **再走调用链**：函数从哪里进、到哪里出。
3. **再问为什么**：为什么不使用更简单方案。
4. **最后面试化**：把源码设计压缩成 1-2 分钟口头答案。

不要背函数名。真正需要记住的是：状态、边界、数据流、失败模式、取舍。

## 6. 最终目标

完成全部课程后，应当能够：

- 从零画出 SuperMew Agent + RAG 架构图；
- 修改一个 RAG node，而不破坏状态流；
- 修改一个检索阶段参数并解释召回/精排变化；
- 新增一个 Agent Tool；
- 新增一种 query rewrite 策略；
- 为 RAG 增加离线评测集与指标；
- 在面试中把它讲成一个“生产级 Agentic RAG 系统”，而不是一个 LangChain Demo。
