# SuperMew Learning Portal

这个仓库保留了 SuperMew 原始源码，同时增加了一套面向 **Agent / Agentic RAG 源码学习与面试准备** 的课程文档。

## 推荐入口

从这里开始：

- [完整课程目录](docs/course/00-course-index.md)

## 课程章节

1. [项目核心骨架与一次请求完整调用链](docs/course/01-project-skeleton-and-request-flow.md)
2. [外层 Agent：Tool Calling、上下文与边界](docs/course/02-agent-runtime-and-tool-calling.md)
3. [RAG 入库：解析、三级分块、Embedding、父块存储](docs/course/03-rag-ingestion-and-hierarchical-chunking.md)
4. [RAG 检索：Dense + BM25、RRF、Auto-merging、Rerank](docs/course/04-hybrid-retrieval-and-ranking.md)
5. [Agentic RAG：LangGraph 状态机与证据路由](docs/course/05-agentic-rag-langgraph-pipeline.md)
6. [检索纠错：Step-back、HyDE 与 Rewrite Budget](docs/course/06-query-rewrite-and-recovery.md)
7. [复杂问题：并行子 Agent、Synthesis 与 HITL](docs/course/07-multi-agent-and-hitl.md)
8. [工程化：可观测性、评测、失败降级与生产设计](docs/course/08-observability-evaluation-production.md)
9. [面试题库：Agent / RAG 高频问题](docs/course/09-interview-question-bank.md)
10. [动手实验：从会看源码到能独立修改](docs/course/10-hands-on-labs.md)

## 核心源码阅读顺序

```text
backend/chat/runtime.py
  ↓
backend/tools/knowledge.py
  ↓
backend/rag/pipeline.py
  ↓
backend/rag/utils.py
  ↓
backend/indexing/milvus_client.py
  ↓
backend/indexing/document_loader.py
```

不要一开始从 `pipeline.py` 第一行读到最后一行。先建立“外层 Agent → RAG Tool → 内层 LangGraph → Retrieval Capability → Indexing”的层级，再逐节点深入。

## 学习完成标准

最终你应该可以独立解释并修改：

```text
Outer Agent
Tool Boundary
Hierarchical Chunking
Dense + BM25 Hybrid Retrieval
RRF
Auto-merging
Rerank
Evidence Grading
Step-back / HyDE
Complex Query Decomposition
Parallel Sub-Agent
HITL Resume
RAG Trace / Evaluation
```

同时能用 2-3 分钟把项目讲成一个完整的生产级 Agentic RAG 系统，而不是只说“用了 LangChain + Milvus”。
