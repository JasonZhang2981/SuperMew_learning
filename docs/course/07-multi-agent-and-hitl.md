# 07. 复杂问题：并行子 Agent、Synthesis 与 HITL

## 1. 本课目标

理解 SuperMew 为什么对复杂问题不只做一次检索，而是：

```text
complexity classify
-> decompose
-> parallel sub-agents
-> evidence synthesis
```

同时理解 HITL 为什么是 RAG 的“信息补全机制”，而不是一个 UI 功能。

---

## 2. 什么问题算复杂问题

项目把以下类型视为更可能需要复杂检索：

```text
比较 / 对比
分析原因
总结多个方面
讨论优缺点
提出方案 / 步骤
需要多个信息源综合
```

例如：

```text
“比较 SuperMew 的 Agent 层和 RAG 层的职责，并分析这种架构相对纯 ReAct 的优势。”
```

它至少包含：

1. Agent 层职责；
2. RAG 层职责；
3. 二者关系；
4. 与纯 ReAct 对比。

单 query top-k 很可能覆盖不全。

---

## 3. Query Decomposition 的目的

把复杂问题拆成 2-4 个互不重叠、可独立检索的子问题。

例如：

```text
Q: 比较 Dense、BM25、Rerank 在 SuperMew 中的作用和关系
```

可拆成：

```text
Q1: Dense retrieval 在项目中如何实现、解决什么问题？
Q2: BM25 sparse retrieval 如何实现、与 Dense 如何融合？
Q3: Rerank 在 hybrid recall 后做什么？
Q4: 三者形成的完整检索 pipeline 是什么？
```

目标是 **提高复杂问题的 coverage**。

---

## 4. 为什么子问题要求“互不重叠”

如果 planner 生成：

```text
Q1: Dense 和 BM25 有什么区别？
Q2: BM25 和 Dense 分别有什么作用？
Q3: 为什么要同时用 Dense 和 BM25？
```

三个 query 高度重复。

后果：

- 多次召回同一 chunks；
- token / retrieval cost 浪费；
- synthesis 结果重复；
- 某些真正需要的维度反而没覆盖。

所以好的 decomposition 不是“多生成几个 query”，而是 **问题空间分解**。

---

## 5. `prepare_sub_questions` 为什么单独作为节点

它看起来只是把 planner 生成的 sub_questions 发出 trace。

但从 workflow 设计角度，它是一个明确的 fan-out 前置节点：

```text
planning result
-> normalized sub questions
-> fan-out
```

单独节点的价值：

- 可观测；
- 可插入 validation；
- 以后可以做去重或依赖排序；
- Graph 结构更清晰。

---

## 6. LangGraph `Send` 的 fan-out

每个子问题构造自己的初始 state，然后发到同一个 `rag_sub_agent`。

逻辑：

```text
main state
  |
  +-> sub state 1 -> rag_sub_agent
  +-> sub state 2 -> rag_sub_agent
  +-> sub state 3 -> rag_sub_agent
```

子结果通过：

```text
sub_results: Annotated[List[dict], operator.add]
```

做 reducer 合并。

这就是典型 Map-Reduce / fan-out-fan-in Agent pattern。

---

## 7. 为什么子 Agent 能并行

每个子问题检索基本独立：

```text
embedding(query_i)
hybrid retrieve(query_i)
evidence grade(query_i)
```

不存在：

```text
Q2 必须等 Q1 答案才能开始
```

所以适合并行。

理论 latency 从：

```text
T1 + T2 + T3
```

接近：

```text
max(T1, T2, T3) + orchestration overhead
```

实际还受模型 API / Milvus concurrency / connection limit 影响。

---

## 8. 子 Agent 为什么能力被限制

当前子 Agent 只执行：

```text
retrieve -> grade
```

而没有：

```text
rewrite -> retrieve -> grade -> HITL ...
```

这是一种 **bounded sub-agent**。

为什么很重要？

Multi-Agent 的常见问题不是“能力不够”，而是：

```text
能力复制后，成本也复制
```

如果 4 个子 Agent 都可以重复 rewrite：

- LLM calls 爆炸；
- latency 不可控；
- trace 复杂；
- 失败分支指数式增加。

因此生产系统往往让子 Agent 做窄任务。

---

## 9. 子 Agent 中 partial evidence 为什么可以保留

对主问题而言，一个子问题只获得部分证据，不一定意味着完全无用。

例如：

```text
Q1 sufficient
Q2 partial
Q3 sufficient
```

总 synthesis 仍可以利用 Q2 的部分 evidence，同时最终状态标记 partial。

如果把 partial 全丢掉，复杂问题的 coverage 可能反而下降。

---

## 10. Synthesis 的核心步骤

当前 synthesis 主要做 evidence 层合并：

```text
1. 收集 answerable / partial 子结果
2. 合并所有 docs
3. chunk_id 去重
4. 保留更高 rank score
5. 重新编号 rrf_rank
6. 生成统一 context
7. 合并 sub_traces
8. 计算总 retrieval_status
```

注意：它不是让另一个 LLM 先写“子答案”。

因此不会引入中间 generation hallucination。

---

## 11. 为什么先合 docs 再由外层 Agent 回答

另一种 multi-agent 架构是：

```text
sub-agent 1 -> sub-answer
sub-agent 2 -> sub-answer
sub-agent 3 -> sub-answer
-> final LLM summarize
```

SuperMew 更接近：

```text
sub-agent 1 -> evidence
sub-agent 2 -> evidence
sub-agent 3 -> evidence
-> merge evidence
-> final Agent answer
```

优势：

- 事实来源仍是原始 chunks；
- 减少层层摘要的信息损失；
- 引用更容易映射到真实文档；
- 降低“hallucination of hallucination”。

---

## 12. Synthesis 为什么要去重

不同子问题经常召回同一 chunk。

例如：

```text
Q1 -> chunk A, B
Q2 -> chunk B, C
Q3 -> chunk A, D
```

不去重会变成：

```text
A, B, B, C, A, D
```

这不仅浪费 context，而且重复 evidence 可能让 LLM 误以为某条事实有多个独立来源。

所以按 `chunk_id` 去重很重要。

---

## 13. HITL 在什么时候触发

Evidence grader 可以判断：

```text
ambiguity = missing_slot
```

或：

```text
ambiguity = multiple_candidates
```

对应 route：

```text
clarify
scope_select
```

例如：

```text
“这个模块的配置是多少？”
```

知识库里有多个模块。

这不是“检索能力不足”，而是 user query 本身缺必要信息。

---

## 14. 为什么不能用 LLM 自己猜缺失槽位

假设用户说：

```text
“帮我查一下张经理的方案。”
```

知识库有：

```text
张伟
张强
张敏
```

如果 Agent 自己选一个，就是把 ambiguity 伪装成 certainty。

正确做法：

```text
scope_select -> 请用户选择
```

HITL 的价值就是：

> 当不确定性来自用户意图缺失，而不是模型推理时，把控制权交还用户。

---

## 15. HITL 的两类状态

### Clarify

缺关键字段。

例如：

```text
“你指的是哪个版本？”
```

### Scope Select

多个候选都合理。

例如：

```text
“知识库中有 A/B/C 三个产品线，你想问哪一个？”
```

两者本质差异：

```text
clarify = missing information
scope_select = ambiguous alternatives
```

---

## 16. 为什么 HITL 需要 Resume State

第一轮 RAG 已经做了：

```text
原问题理解
复杂度判断
一次检索
证据评分
```

如果用户补充后从头执行整个主图：

- 重复成本；
- 可能重新分类产生不同路径；
- 丢失上一轮决策上下文。

所以项目保存 resume state，例如：

```text
question
route
retrieval_status
rewrite_count
complexity
sub_questions
```

下一轮用户补充后继续。

---

## 17. Resume 后为什么做 targeted retrieval

用户补充会与原问题合并形成 refined query。

例如：

第一轮：

```text
Q: “这个模型的参数是多少？”
```

HITL：

```text
“你指哪个模型？”
```

用户：

```text
“rerank 模型”
```

refined query：

```text
rerank 模型：这个模型的参数是多少？
```

然后直接做 targeted retrieval，而不是重新走复杂度 decomposition。

因为 ambiguity 已经被用户解决。

---

## 18. 为什么 HITL 是 Agent 系统的重要能力

单轮 QA 容易假设：

```text
用户 query 总是完整的
```

真实业务并不是这样。

用户经常：

- 指代不清；
- 少说条件；
- 忘记版本；
- 没有指定数据范围；
- 有多个同名对象。

因此成熟 Agent 不能只做：

```text
“尽力猜”
```

还要知道何时：

```text
暂停 -> 向用户获取信息 -> 恢复任务
```

---

## 19. Multi-Agent RAG 的风险

### 19.1 子问题重叠

会造成重复召回。

### 19.2 子问题遗漏

Planner 没拆到关键维度，最终 coverage 仍不足。

### 19.3 并发成本

并行降低 wall-clock latency，但不会降低总计算量。

### 19.4 Context 爆炸

多个子问题各 top-k，合起来可能很大。

### 19.5 Evidence conflict

不同文档可能相互矛盾。

当前项目主要做 dedupe，还可以扩展：

```text
conflict detection
source priority
temporal filtering
```

---

## 20. 如何评测 Query Decomposition

至少测：

```text
coverage
sub-question redundancy
retrieval recall
final answer completeness
latency
number of tool/model calls
```

可以额外定义：

```text
unique_relevant_chunks / total_retrieved_chunks
```

观察 decomposition 是否真的带来新 evidence，而不只是重复。

---

## 21. 面试题

### Q1：为什么复杂问题要拆子问题？

> 单 query 的 top-k 容量有限，复杂问题通常包含多个独立检索维度。Query decomposition 可以把 coverage 问题转化成多个更聚焦的 retrieval task，再在 evidence 层合并。

### Q2：为什么子 Agent 不做完整自纠错？

> 因为 fan-out 会放大成本和延迟。生产 Multi-Agent 需要能力预算，子 Agent 应尽量窄，只完成独立可验证任务。

### Q3：为什么不让子 Agent 先各自生成答案？

> 项目选择先合并原始 evidence，再统一生成最终答案，可以减少多层生成造成的信息损失和幻觉，并保持引用与真实 chunk 对齐。

### Q4：HITL 解决什么问题？

> 主要解决 query 缺槽位和多个候选解释造成的不确定性。此时继续让模型检索或猜测没有意义，应让用户补齐信息，再从保存的 workflow state 继续执行。

### Q5：Resume 为什么比从头重跑更好？

> 它保留之前的 workflow 决策和预算状态，例如 rewrite_count，避免重复计算和路径漂移，也更符合长任务 Agent 的 checkpoint/resume 模型。

---

## 22. 本课验收

你应该能完整解释：

```text
复杂问题
-> planner 拆 2-4 个子问题
-> Send 并行调用受限 sub-agent
-> retrieve + grade
-> evidence dedupe / synthesis
-> final Agent answer
```

以及：

```text
如果 evidence grader 发现 query 缺信息
-> pause
-> HITL
-> 保存 resume state
-> 用户补充
-> targeted retrieval
-> continue
```
