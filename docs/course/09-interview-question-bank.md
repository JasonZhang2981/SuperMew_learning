# 09. 面试题库：从 SuperMew 源码回答 Agent / RAG 高频问题

> 用法：不要死背答案。先按“问题 → 项目设计 → 为什么 → 取舍 → 可优化点”组织口述。

---

## 1. 30 秒项目介绍

> SuperMew 是一个完整的 Agentic RAG 应用。外层用 LangChain Agent 负责工具选择，知识库被封装成 `search_knowledge_base` Tool；Tool 内部进入 LangGraph RAG workflow。离线侧对 PDF/Word/Excel/HTML 做清洗和三级父子分块，在线侧使用 Milvus Dense + BM25 Hybrid Search，通过 RRF 融合，再做 Auto-merging、可选 Rerank 和阈值过滤。检索后还会做 Evidence Grading，根据证据状态选择直接回答、Step-back/HyDE 改写、HITL 澄清或 no-knowledge；复杂问题会拆成多个子问题并行检索后再做 evidence synthesis。

---

## 2. 2 分钟项目介绍

建议结构：

```text
业务定位
-> 外层 Agent
-> RAG 入库
-> 在线检索
-> Agentic routing
-> 工程化
```

示例回答：

> 这个项目我会把它拆成两层。第一层是通用 Agent，主要负责判断用户请求是否需要调用知识库等工具；第二层是独立的 Agentic RAG workflow，负责真正的知识检索策略。知识入库时不是单层固定切块，而是构造 L1/L2/L3 的层级 chunk tree，L3 用于精确召回，L2/L1 用于后续恢复上下文。在线检索会同时走 Dense HNSW 和 BM25 sparse search，用 RRF 做 rank fusion，然后根据多个同父 leaf chunk 的命中情况做 Auto-merging，再对候选做可选 Cross-Encoder rerank 和分数阈值过滤。之后 LangGraph 会让一个 Evidence Grader 判断 relevance、answerability 和 ambiguity，如果只是 query 表述导致证据不足，会在 Step-back 和 HyDE 中选择一种做一次 query rewrite；如果缺关键条件则走 HITL；复杂问题则拆成 2-4 个子问题，并行做受限的 retrieve+grade，最后把 evidence 去重合并。工程上还记录完整 rag_trace，并对工具调用次数、rewrite 次数和子问题数做预算限制。

---

# A. Agent 架构

## Q1：这个项目为什么既用了 LangChain Agent，又用了 LangGraph？

**答题核心：不同层级。**

> LangChain Agent 解决的是开放式工具选择，例如当前用户问题是否需要知识库。LangGraph 解决的是知识库内部受控、多步骤、有循环和并行的检索 workflow。前者需要一定模型自治，后者需要确定状态机和可观测性，所以两者并不重复。

追问：为什么不全都放一个 Graph？

> 可以，但会把通用对话、天气工具和 RAG 内部检索策略强耦合。独立 Tool contract 更利于测试、替换和复用。

---

## Q2：Agent 和 Workflow 的区别？

> Agent 的下一步主要由模型根据状态动态决定；Workflow 的节点和可达路径由工程师预定义。生产系统通常混合使用：开放决策交给 Agent，必须稳定、受控、可追踪的执行流程交给 Workflow。

结合项目：

```text
外层工具选择 = Agent
内层 RAG = controlled workflow
```

---

## Q3：为什么限制知识库 Tool 一轮最多调用一次？

> 因为 RAG Tool 内部已经有 evidence grading 和一次 rewrite 自纠错，如果外层再反复调用会形成双层循环，增加成本和不可预测延迟。系统通过 request-scoped slot 做硬限制，而不是只依赖 prompt。

---

## Q4：Prompt 里已经要求不重复调用，为什么代码还要限制？

> Prompt 是软约束。调用次数、权限、预算、数据访问范围等关键边界必须由程序验证，不能把安全性寄托在模型服从指令上。

---

## Q5：Tool schema 为什么只传 query，不把 top_k / rerank / chunk_level 暴露给模型？

> 这些属于检索系统策略，不是通用 Agent 的业务决策。参数越多，模型构造错误的风险越高；保持 Tool schema 小，还能让 RAG 内部替换实现而不破坏外部 contract。

---

# B. Chunking / Indexing

## Q6：为什么要三级分块？

> 这是为了同时优化 retrieval precision 和 context completeness。小块 embedding 更聚焦、容易召回，但上下文不足；大块上下文完整但检索噪声更大。项目从 L3 小块召回，再通过 parent hierarchy 恢复 L2/L1。

---

## Q7：为什么不直接同时检索 L1/L2/L3？

> 多尺度同时召回会产生大量重复，而且不同粒度的相似度分数并不天然可比。固定叶子层召回，再通过显式 parent relation 做结构恢复，更容易控制和评测。

---

## Q8：Chunk overlap 有什么作用？

> 防止关键信息被切分边界截断。例如定义在 chunk 尾部、解释在下一个 chunk 开头，没有 overlap 会让任何一个单独 chunk 都缺失完整语义。代价是增加重复和索引体积。

---

## Q9：为什么要清洗 Unicode / 零宽字符？

> 文档解析产生的不可见字符可能导致 embedding、数据库写入、BM25 analyzer、前端展示和 chunk identity 异常。生产 indexing pipeline 应该先做规范化和清洗。

---

## Q10：为什么 Parent Chunk 不一定要存向量数据库？

> Parent lookup 是按 chunk_id 的结构化精确读取，不需要 ANN。向量库负责相似搜索，关系/键值存储更适合 parent-child relation 和精确文本读取。

---

# C. Hybrid Retrieval

## Q11：为什么 Dense + BM25？

> Dense 擅长语义同义表达，BM25 擅长 exact term、实体、错误码、型号和版本。两种 retriever 的错误模式互补，混合召回通常比单路更稳。

---

## Q12：为什么需要 RRF？

> Dense similarity 和 BM25 score 不在同一数值尺度，直接加权相加需要校准。RRF 只依赖各检索器的 ranking position，因此可以稳定融合不同 score space。

公式：

```text
RRF(d) = Σ 1/(k + rank_i(d))
```

---

## Q13：为什么不让 Cross-Encoder Reranker 直接检索全库？

> Cross-Encoder 对每个 query-document pair 联合推理，精度高但成本高，不适合全量扫描。Retriever 先用 ANN/BM25 高效缩小候选，Reranker 再做精排。

---

## Q14：candidate_k 和 top_k 为什么不同？

> candidate_k 用于第一阶段保证 recall，top_k 是最终上下文数量。如果一开始只召回 top_k，Reranker 没有足够候选进行重新排序。

---

## Q15：Top-k 为什么不够，还要 threshold？

> Top-k 是相对排序，即使知识库没有正确答案也会返回“最相关”的若干文档。Threshold 可以让所有候选都不合格时返回空结果，从而触发 no-knowledge，而不是强迫模型回答。

---

# D. Auto-merging

## Q16：Auto-merging 是什么？

> 系统先召回细粒度 L3 chunk。如果同一父节点下多个 child 同时命中并达到阈值，就用完整 parent chunk 替换这些 children，然后可以继续从 L2 合并到 L1。

---

## Q17：Auto-merging 和直接拼相邻 chunk 有什么区别？

> 直接拼邻居是基于位置，可能拼到无关段落；Auto-merging 基于预构建的 parent-child semantic hierarchy，并且只有 sibling hit 达到阈值才向上恢复上下文。

---

## Q18：为什么先 merge 再 rerank？

> 这样 reranker 评估的是最终可能交给 LLM 的上下文单元，也避免 child 在 rerank 阶段被过滤后无法达到 merge threshold。代价是父块更长，rerank 成本会上升。

---

# E. Evidence Grading

## Q19：为什么检索之后还要 LLM Grader？

> Retriever/Reranker 只能判断相关程度，很难判断当前 evidence 是否完整回答了用户问题、问题是否缺槽位。Grader 把 relevance、answerability 和 ambiguity 分开建模，用于决定 answer/rewrite/HITL/no-knowledge。

---

## Q20：相关性和可回答性有什么区别？

> 相关性表示主题是否相近，可回答性表示证据是否足以支持问题要求。例如文档说明系统使用 Milvus，但用户问“为什么不用 Elasticsearch”，主题高度相关，但证据不足以解释原因。

---

## Q21：为什么 LLM 给出 route 后还需要代码规则 `_resolve_route`？

> LLM 可以做语义判断，但合法状态和预算必须由确定性逻辑保证，例如无 docs 时不能 answer、缺槽位必须 clarify、rewrite 次数不能无限增加。

---

## Q22：no_knowledge 和模型说“不知道”有什么区别？

> no_knowledge 是 RAG workflow 基于 retrieval/evidence 状态做出的结构化决策；模型说“不知道”只是生成结果。前者可以被观测、统计、做产品策略和评测。

---

# F. Query Rewrite

## Q23：Step-back 是什么？

> 当原 query 过于具体、带很多实体和约束时，把问题上升到更一般的概念或机制层级，帮助召回讲原理的文档。

---

## Q24：HyDE 是什么？

> 先由 LLM 生成一段假设答案式文档，让 query 获得更接近目标文档的术语和语义，再用它检索真实知识库。假设文档只能用于检索，不能作为最终事实证据。

---

## Q25：Step-back 和 HyDE 怎么选？

> 过度具体、约束过细时倾向 Step-back；问题很模糊、缺少知识库术语时倾向 HyDE。项目用结构化 planner 二选一，而不是两个都跑。

---

## Q26：为什么只允许 rewrite 一次？

> 为了控制循环、延迟和 semantic drift。一次改写仍无法恢复证据时，更合理的是让用户澄清或直接 abstain，而不是继续让模型反复猜 query。

---

# G. Complex Query / Multi-Agent

## Q27：为什么复杂问题要拆子问题？

> 复杂问题通常包含多个独立 evidence dimension，单个 query 的 top-k 容量有限。拆成多个聚焦 query 可以提高 coverage，再在 evidence 层合并。

---

## Q28：为什么子 Agent 只 retrieve + grade？

> Multi-Agent fan-out 会放大每个步骤的成本，因此子 Agent 要 bounded。主 Agent 负责复杂 planning，子 Agent 只执行窄而可验证的 retrieval task，避免指数级 workflow。

---

## Q29：为什么不让每个子 Agent 先生成答案？

> 先生成子答案再二次总结会产生信息压缩和多层 hallucination。项目保留原始 retrieved chunks，统一合并 evidence，再让最终 Agent 生成一次答案，更利于 grounding 和 citation。

---

## Q30：并行一定更省成本吗？

> 不。并行主要降低 wall-clock latency，总计算量通常不变甚至更高。还要考虑 API 并发限制、Milvus 连接、模型吞吐和最终 context size。

---

# H. HITL

## Q31：什么时候应该 HITL？

> 当不确定性来自用户输入缺失，而不是模型能力不足时。例如缺版本、缺角色、多个同名实体。继续检索或让模型猜都不能解决信息缺口，此时应该询问用户。

---

## Q32：clarify 和 scope_select 区别？

> clarify 是缺少必要条件；scope_select 是存在多个合理候选方向，需要用户选择一个。

---

## Q33：为什么要保存 resume state？

> 用户补充是原任务的继续，不是新任务。保存原 question、route、rewrite budget、complexity 等状态，可以从 breakpoint 继续，避免从头重跑和状态漂移。

---

# I. Observability / Evaluation

## Q34：RAG 回答错误怎么定位？

推荐回答顺序：

```text
1. gold evidence 在不在库里？
2. 是否进入 recall candidate？
3. 是否被 RRF/rerank 排掉？
4. 是否被 auto-merge/threshold 处理错误？
5. grader route 是否正确？
6. final answer 是否忠于 evidence？
```

---

## Q35：怎么评估 Hybrid Search？

> 用固定 Golden Queries 对比 Dense-only、BM25-only、Hybrid-RRF 的 Recall@K、MRR、nDCG，并按 query 类型分桶，例如 lexical-heavy 和 semantic paraphrase。

---

## Q36：怎么评估 Rerank？

> 固定第一阶段 candidate pool，比较 rerank 前后 MRR/nDCG/Precision@K，同时观察额外延迟和最终 answer groundedness。

---

## Q37：怎么评估 Query Rewrite？

> 统计 rewrite trigger rate、rewrite recovery rate、drift rate，并比较 rewrite 前后的 Recall@K / answerability，同时量化额外 latency 和 token cost。

---

## Q38：Agentic RAG 还需要哪些普通 RAG 没有的指标？

```text
route accuracy
clarification success rate
resume success rate
sub-question coverage
sub-question redundancy
rewrite recovery
no-knowledge precision
```

---

# J. 设计优化题

## Q39：如果让你优化 SuperMew 的 RAG，你会先做什么？

建议不要回答“换更大的模型”。

优先级示例：

1. 建立分层 Golden Dataset 和 retrieval trace dashboard；
2. 做 Dense/BM25/RRF/Rerank/Auto-merge ablation；
3. 增加 metadata filter；
4. 针对冲突文档加入 source/version priority；
5. 动态 candidate_k/top_k；
6. 评估 Multi-Query 是否比当前单次 rewrite 更有效。

---

## Q40：如果知识库非常大，当前架构有哪些瓶颈？

可能包括：

- HNSW 内存和构建成本；
- BM25 sparse index 规模；
- parent store 数据量；
- large candidate rerank cost；
- 多租户 metadata filtering；
- 热点 query cache；
- shard / partition strategy。

---

## Q41：如果用户只允许访问自己部门的文档，应该在哪里做权限过滤？

> 权限应该在 retrieval 数据访问层做硬过滤，把 ACL/tenant/department 转成 Milvus filter 或预过滤条件，而不是检索全库后再让 LLM“不要引用无权内容”。

---

## Q42：如何防止 Prompt Injection 文档操控 Agent？

> 把 retrieved document 明确当不可信数据，不当系统指令；高权限 Tool 需要独立鉴权和参数验证；检索结果不能覆盖 system policy；必要时对文档中的指令模式做检测，最终工具权限由服务端决定。

---

# K. 反向追问：面试官可能继续深挖

你应该准备以下追问：

```text
RRF 的 k 怎么选？
HNSW 的 M / efConstruction / ef 有什么影响？
IP 和 cosine 什么关系？
BM25 的 TF/IDF 核心思想？
Cross-Encoder 为什么慢？
Auto-merge threshold 怎么调？
chunk size 怎么做实验？
为什么 rerank 在 merge 后？
Evidence Grader 自己会不会 hallucinate？
如何校准 grader confidence？
如果 planner 拆错子问题怎么办？
复杂问题 4 个子 Agent 如何控制 context 爆炸？
HITL 状态怎么持久化到多实例服务？
Redis/DB 在生产里怎么承载 checkpoint？
```

---

# L. 面试回答模板

遇到任何 SuperMew 设计题，可以用 5 段式：

```text
1. 先说项目当前怎么做
2. 解释它解决的核心问题
3. 解释为什么不是更简单方案
4. 说明当前方案的代价/风险
5. 给出可验证的优化或评测方法
```

示例：

> 项目当前采用 Dense + BM25 双路召回，并通过 RRF 融合。这样是为了同时覆盖语义匹配和关键词精确匹配，尤其对错误码、版本和专有名词更稳。之所以不用直接加权原始分数，是因为 Dense similarity 和 BM25 score 不在一个尺度，RRF 通过 rank 做融合更省校准成本。它的代价是 rank-only 会丢失一部分绝对置信度，所以后面还需要 rerank 和 threshold。我会用不同 query 类型的 Recall@K 和 nDCG 做分桶 ablation 来验证 Hybrid 的实际增益。

这种回答比只说“混合检索效果更好”强很多。
