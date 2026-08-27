# 05. Agentic RAG：LangGraph 状态机与证据路由

## 1. 本课目标

理解 `backend/rag/pipeline.py` 为什么是项目 RAG 的核心，以及它如何把一次检索升级成“有状态、有路由、有自纠错”的 Agentic RAG。

---

## 2. 先看 RAGState

核心思想：每一个节点不靠隐式变量传递信息，而是读写统一状态。

重要字段可分成几组。

### Query / evidence

```text
question
query
context
docs
```

### 决策状态

```text
route
retrieval_status
```

### 证据评分

```text
evidence_relevance
evidence_answerability
evidence_ambiguity
evidence_confidence
```

### Rewrite

```text
rewrite_count
rewrite_method
rewritten_query
step_back_question
hyde_document
```

### Complex query

```text
complexity
complexity_reason
sub_questions
sub_results
```

### HITL

```text
missing_slots
hitl_prompt
hitl_options
```

### Observability

```text
rag_trace
rag_step_group
```

面试时可概括：

> LangGraph 的价值不只是“画图”，而是把 Agentic RAG 的中间状态显式化，从而让分支、循环、并行和 HITL 都可以被可靠管理。

---

## 3. 主图长什么样

简化：

```text
classify_complexity
      |
      +-- simple --------------------------+
      |                                    |
      |                              retrieve_initial
      |                                    |
      |                             grade_documents
      |                             /      |      \
      |                        answer   rewrite   HITL/no
      |                                   |
      |                           rewrite_question
      |                                   |
      |                          retrieve_rewritten
      |                                   |
      |                           grade_documents
      |
      +-- complex
             |
      prepare_sub_questions
             |
       parallel Send
      /      |      \
 sub-agent sub-agent sub-agent
      \      |      /
          synthesis
```

这就是项目的 Agentic RAG 主骨架。

---

## 4. 为什么一开始先做复杂度分类

简单问题例如：

```text
“X 的定义是什么？”
```

不需要拆成 3 个子问题。

复杂问题例如：

```text
“比较 A 和 B 的架构差异，并分析各自适用场景。”
```

单 query 很可能只召回某一个角度。

因此先路由：

```text
simple -> standard RAG
complex -> query decomposition + parallel retrieval
```

这属于 retrieval planning。

---

## 5. 为什么有简单问题 fast path

代码不会所有问题都先调用模型分类。

如果本地规则足够确定，例如：

- 问题很短；
- 有明显“是什么/多少/哪个”等单事实 marker；
- 没有多个维度；
- 没有比较/分析/为什么等复杂 marker；

就直接判 simple。

好处：

```text
降低 latency
降低 LLM cost
减少不必要随机性
```

这体现一个重要 Agent 原则：

> 能用可靠规则解决的问题，不要全部交给 LLM。

---

## 6. `retrieve_initial` 做什么

节点本身不直接实现 Milvus 细节，而调用：

```text
retrieve_documents(query)
```

然后把返回 docs 格式化为 context，并把检索 meta 放入 `rag_trace`。

职责分层：

```text
pipeline.py = orchestration / decision
utils.py    = retrieval capability
```

这也是良好的工程边界。

---

## 7. Evidence Grading 是为什么

很多 RAG 系统犯一个错误：

```text
retrieve top-k -> 默认它们能回答 -> 直接生成
```

SuperMew 中间插入 evidence grader。

它判断至少三件事：

### relevance

```text
none / weak / strong
```

问：这些片段和问题主题相关吗？

### answerability

```text
none / partial / sufficient
```

问：这些片段足够支撑答案吗？

### ambiguity

```text
none / missing_slot / multiple_candidates
```

问：问题本身是不是缺条件或有多个候选解释？

这三个维度不要混淆。

---

## 8. “相关”为什么不等于“可回答”

例如问题：

```text
“项目里为什么选择 Milvus 而不是 Elasticsearch？”
```

检索可能命中：

```text
项目使用 Milvus，支持 Dense + BM25。
```

这和主题强相关。

但文档没有说明“为什么不用 Elasticsearch”。

所以：

```text
relevance = strong
answerability = partial / none
```

如果只做 relevance grading，模型很容易开始脑补设计原因。

这是项目中非常成熟的一点。

---

## 9. route 有哪些

Evidence grader 输出：

```text
answer
rewrite
clarify
scope_select
no_knowledge
```

它们分别表示：

### answer

证据已经足够。

### rewrite

有相关信号，但当前 query 表达不适合检索，可以尝试一次 query transformation。

### clarify

缺关键信息，比如版本、模块、角色、文件类型等。

### scope_select

存在多个合理候选方向，需要用户选择。

### no_knowledge

没有可靠相关证据。

这比简单的：

```text
relevant / not relevant
```

强得多。

---

## 10. `_resolve_route` 为什么还要再做一次规则收敛

虽然 LLM grader 已输出 route，但代码不会完全相信它。

例如：

```text
如果没有 docs -> 强制 no_knowledge
如果 ambiguity=missing_slot -> 强制 clarify
如果 multiple_candidates -> scope_select
answer 只有 strong + sufficient 才接受
rewrite 超预算后不能继续 rewrite
```

这体现：

> LLM 提供语义判断，业务代码负责合法状态机约束。

这是生产 Agent 非常核心的设计。

---

## 11. 为什么不让 grader 自己完全决定下一步

如果完全按照 LLM route：

```text
LLM: rewrite
-> rewrite
-> LLM: rewrite
-> rewrite
-> ...
```

就可能形成循环。

所以需要外部 invariant：

```text
rewrite_count < 1
```

同理：

```text
没有 docs 时不能 answer
```

这些是 deterministic guardrail。

---

## 12. 为什么 RAG 输出不仅是 docs

最终 `rag_result` 还包含：

```text
route
retrieval_status
rag_trace
hitl_prompt
...
```

原因是下游 Chat Service / Agent 需要知道：

```text
“这批 docs 是可靠答案证据？”
还是
“应该暂停并问用户？”
```

因此一个成熟 RAG API 应该返回：

```text
evidence + status + diagnostics
```

而不是只返回 `List[Document]`。

---

## 13. 子 Agent 为什么简化成 retrieve -> grade

复杂问题分解后的每个子问题，只执行：

```text
retrieve_initial
-> grade_documents
```

没有再做完整 rewrite loop。

这是有意的预算控制。

原因：

如果 4 个子问题都允许 rewrite：

```text
最多 4 × 2 次 retrieval
+ grading
+ rewrite model
```

成本和延迟迅速膨胀。

所以子 Agent 采用更窄策略：

```text
partial evidence 可以保留给 synthesis
完全不相关则丢弃
```

这体现了 multi-agent 系统中的 budget management。

---

## 14. LangGraph `Send` 解决什么问题

复杂问题有多个 sub_questions。

通过 `Send`：

```text
sub_question_1 -> rag_sub_agent
sub_question_2 -> rag_sub_agent
sub_question_3 -> rag_sub_agent
```

每个子 Agent 独立 state，然后结果通过 reducer 合并到 `sub_results`。

这比 for-loop 串行调用有两个优势：

- 结构上支持 fan-out / fan-in；
- LangGraph 能显式表示并行任务。

---

## 15. Synthesis 不是 LLM 总结，而是 evidence 合并

当前 synthesis 核心做：

```text
收集 answerable / partial sub-results
-> 合并 docs
-> 按 chunk_id 去重
-> 重新编号
-> 合并 sub traces
```

最终自然语言答案仍由外层 Agent 生成。

所以这里的 synthesis 更准确叫：

```text
retrieval result synthesis
```

而不是 answer synthesis。

---

## 16. 为什么复杂问题的总状态可能是 partial

如果多个子问题里：

```text
A sufficient
B sufficient
C partial
```

最终 evidence 并不能宣称所有方面都完整。

因此 synthesis 会保留 partial 信号。

这是一种比“有 docs 就 answerable”更细的状态表达。

---

## 17. `rag_trace` 是图之外的第二条主线

每个节点都不仅修改业务状态，还记录 trace。

例如：

```text
retrieval_stage
retrieved_chunks
rewrite_method
complexity
sub_questions
sub_traces
route
confidence
```

因此 Debug 时可以重建：

```text
为什么走到了这个答案？
```

不需要依赖模型 chain-of-thought。

正确的生产做法是记录 **可观察决策和输入输出**，不是记录隐私敏感的隐藏推理。

---

## 18. Agentic RAG 和普通 RAG 的本质区别

普通 RAG：

```text
retrieve -> generate
```

Agentic RAG：

```text
plan
-> retrieve
-> inspect evidence
-> choose next action
-> possibly transform query / ask human / decompose
-> retrieve again
-> synthesize
-> generate
```

关键不是“用了 Agent 框架”，而是：

> **检索过程本身具有基于状态和证据的动态决策能力。**

---

## 19. 面试题

### Q1：为什么 Evidence Grader 要区分 relevance 和 answerability？

> 主题相关不代表证据足够支撑用户问题。分开建模能减少模型基于“相关但不充分”的材料脑补答案。

### Q2：LLM 已经输出 route，为什么代码还要 `_resolve_route`？

> LLM 负责语义判断，但合法状态、预算和安全边界必须由 deterministic logic 收敛，例如无文档不能 answer、rewrite 次数必须受限。

### Q3：Agentic RAG 中最重要的“Agentic”体现在哪里？

> 在检索后会根据 evidence state 动态决定 answer、rewrite、clarify、scope selection 或 no knowledge；复杂问题还会拆成并行子任务。也就是说 retrieval policy 不是固定直线。

### Q4：为什么不让每个子 Agent 也完整自纠错？

> Multi-agent fan-out 会放大成本和延迟，因此对子 Agent 限制能力和预算，只保留必要的 retrieve + grade，把 partial evidence 交给总 synthesis，是一种工程折中。

---

## 20. 本课验收

能够白板画出：

```text
classify
  ├─ simple: retrieve -> grade -> answer/rewrite/HITL/no
  └─ complex: decompose -> parallel retrieve+grade -> synthesis
```

并解释：

> Graph 中每一条边不是“模型想到哪里走哪里”，而是 LLM semantic judgment + deterministic routing rule 共同决定。
