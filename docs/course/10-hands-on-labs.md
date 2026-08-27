# 10. 动手实验：从“会看源码”到“能独立改 Agentic RAG”

## 1. 目标

前 9 课解决“理解”。这一课解决“掌握”。

你最终要做到：

```text
能改一个节点
能改一个 retrieval policy
能新增一个 Tool
能设计评测
能解释修改后的 trade-off
```

每个实验都要求：

1. 修改前先写假设；
2. 只改一个变量；
3. 保存 trace；
4. 用固定 query 对比；
5. 写结论。

---

# Lab 1：手动画出一次完整 RAG Trace

## 任务

选择一个简单知识问题，运行一次请求，记录：

```text
question
complexity
candidate_k
retrieval_mode
recall_count
auto_merge_applied
rerank_applied
post_threshold_count
evidence_relevance
evidence_answerability
route
```

然后画：

```text
query
-> retrieve
-> merge
-> rerank
-> grade
-> answer
```

## 验收

你能指出每个字段来自哪个源码文件。

---

# Lab 2：对比 Dense-only 与 Hybrid Retrieval

## 修改点

在实验分支中，让 `retrieve_documents` 暂时只走 `dense_retrieve`。

## 测试集

至少准备 20 个 query，分两类：

### Semantic queries

例如同义改写、自然语言描述。

### Lexical queries

例如：

```text
函数名
错误码
版本号
配置项
```

## 指标

```text
Hit@5
Recall@8
MRR
```

## 预期

Dense-only 在语义 query 不一定差，但 lexical-heavy query 可能退化。

## 面试产出

你要能说：

> 我不是理论上认为 Hybrid 更好，而是按 query type 做了分桶验证。

---

# Lab 3：修改 RRF 参数

## 任务

研究 `RRFRanker(k=...)`。

尝试：

```text
k = 20
k = 60
k = 100
```

## 思考

RRF 中 k 越大：

- 不同 rank 之间分数差异被压缩；
- top rank 的优势相对变弱。

k 越小：

- 排名前部文档贡献更大。

## 验收

用固定 query 看融合 ranking 是否变化。

---

# Lab 4：Auto-merging Threshold Ablation

## 当前参数

```text
AUTO_MERGE_THRESHOLD
```

尝试：

```text
1 / 2 / 3
```

## 观察

记录：

```text
auto_merge_replaced_chunks
post_merge_candidate_count
最终 context token 数
answer correctness
```

## 思考

### threshold 太低

容易过度向上合并，引入无关大段上下文。

### threshold 太高

很少 merge，失去恢复上下文效果。

---

# Lab 5：修改 Chunk Size

## 任务

构造两个索引版本：

### Small

```text
L3 ~ 400-500 chars
```

### Current

```text
L3 ~ 800 chars
```

## 比较

```text
index chunk count
embedding cost
Recall@K
Auto-merge frequency
context precision
```

## 关键问题

不要问：

> “最佳 chunk size 是多少？”

应该问：

> “在我的文档分布、embedding model 和 query 类型下，哪种粒度的 recall/context trade-off 更好？”

---

# Lab 6：Rerank Ablation

## 对照

```text
A: Hybrid + RRF
B: Hybrid + RRF + Rerank
```

## 固定候选池

为了公平比较，保存第一阶段同一批 candidate。

## 指标

```text
MRR
nDCG@8
Precision@5
latency
```

## 进阶

按 query 类型分桶：

```text
事实查询
长问题
多约束问题
精确实体问题
```

---

# Lab 7：Threshold 与 No-Knowledge

## 任务

准备 10 个知识库明确没有答案的问题。

调整：

```text
RERANK_MIN_SCORE
```

## 指标

```text
false answer rate
no_knowledge precision
no_knowledge recall
```

## 目标

理解：

```text
更保守 != 一定更好
```

阈值太高会拒绝本来可回答的问题。

---

# Lab 8：验证 Evidence Grader

## 构造 4 类 case

### Case A

```text
strong + sufficient
```

### Case B

```text
strong + partial
```

### Case C

```text
missing_slot
```

### Case D

```text
no relevant docs
```

## 任务

观察 grader 是否正确输出：

```text
answer / rewrite / clarify / no_knowledge
```

## 进阶

人工标注 50 条，做 route confusion matrix。

---

# Lab 9：Step-back vs HyDE

## 数据集

准备两类初始检索失败 query。

### 过于具体

```text
带版本、型号、实体、配置名
```

### 过于抽象

```text
缺少知识库领域术语
```

## 记录

```text
planner method
original Recall@K
rewrite Recall@K
final answerability
```

## 指标

```text
rewrite recovery rate
```

---

# Lab 10：增加 Multi-Query Rewrite

## 目标

自己实现一个扩展：

```text
original query
-> 生成 3 个 paraphrases
-> 各自 retrieval
-> RRF / dedupe
-> grade
```

## 要求

不要直接替换现有策略。

新增实验路径，例如：

```text
rewrite_method = multi_query
```

## 对比

```text
Step-back
HyDE
Multi-Query
```

指标：

```text
Recall@K
unique relevant evidence
latency
cost
```

---

# Lab 11：复杂问题 Decomposition

## 问题示例

```text
“比较项目中 Dense、BM25、Rerank、Auto-merging 的职责、顺序和相互关系。”
```

## 记录

Planner 生成的 2-4 个子问题。

人工检查：

```text
是否覆盖所有维度？
是否重复？
是否可独立检索？
```

## 指标

```text
sub-question coverage
redundancy
unique evidence gain
```

---

# Lab 12：强制 Simple vs Complex 对照

对同一个复杂问题：

```text
A: 强制 simple 单 query retrieval
B: 正常 complex decomposition
```

比较：

```text
retrieved unique relevant chunks
answer completeness
latency
context size
```

这能直接证明 Multi-Agent decomposition 是否值得。

---

# Lab 13：HITL Resume

## 构造问题

知识库有两个相同/类似对象，例如：

```text
“告诉我 Alpha 模块的配置。”
```

让 `Alpha` 存在多个版本或多个候选。

## 验证

第一轮：

```text
route = clarify / scope_select
```

第二轮用户补充后：

```text
resume_state 被读取
targeted retrieval 执行
不是全图从头跑
```

## 重点

检查：

```text
rewrite_count 是否保留
original question 是否保留
```

---

# Lab 14：新增一个 Agent Tool

## 示例

新增：

```text
get_document_metadata(filename)
```

要求：

- Tool schema 小；
- 不允许模型直接传数据库 SQL；
- 服务端做 filename validation；
- 返回结构化结果；
- 加入 system prompt 调用边界。

## 目标

理解 Agent Tool 的“能力封装 + 安全边界”。

---

# Lab 15：为 Tool 增加调用预算

给新增 Tool 做类似：

```text
acquire_xxx_tool_slot()
```

验证模型重复调用时，代码层能拒绝。

这能让你真正理解：

```text
Prompt guardrail vs code guardrail
```

---

# Lab 16：Metadata Filter

## 目标

给检索增加 metadata 条件，例如：

```text
filename
file_type
version
```

## 思路

从 query 中抽取结构化 filter：

```text
{"file_type": "PDF"}
```

然后拼入 Milvus filter expression。

## 注意

模型生成的 filter 必须服务端校验允许字段和值域。

---

# Lab 17：Retrieval Cache

## 目标

给：

```text
query -> dense embedding
```

增加缓存。

也可以缓存：

```text
normalized query -> retrieval result
```

## 评估

```text
cache hit rate
P50/P95 latency
stale result risk
```

---

# Lab 18：故障注入

手动模拟：

```text
Rerank timeout
Milvus hybrid failure
Embedding failure
Grader failure
```

记录系统行为。

目标是回答：

> 如果线上某个外部模型 API 挂了，系统是“部分降级”还是“整个请求崩溃”？

---

# Lab 19：建立最小 Golden Dataset

创建：

```text
evals/golden.jsonl
```

建议字段：

```json
{
  "question": "...",
  "expected_route": "answer",
  "relevant_chunk_ids": ["..."],
  "answer_key_points": ["..."],
  "query_type": "lexical"
}
```

至少 50 条，覆盖：

```text
simple
semantic
lexical
complex
ambiguous
unanswerable
```

---

# Lab 20：写一个离线 Evaluation Runner

输出每次实验：

```text
Recall@K
MRR
nDCG
route_accuracy
no_knowledge_precision
rewrite_recovery_rate
average_latency
```

把结果保存成 CSV 或 JSON。

最终目标：以后任何 RAG 改动都能跑 regression test。

---

# Lab 21：你自己的“面试版本”架构图

最后不要直接拿项目图背。

自己画一版：

```text
                        ┌──────────────┐
User -> Chat Service -> │ Outer Agent  │
                        └──────┬───────┘
                               │ Tool
                               v
                      ┌──────────────────┐
                      │ Agentic RAG Graph│
                      └────────┬─────────┘
                               │
              ┌────────────────┼────────────────┐
              v                v                v
        Query Planning    Hybrid Retrieval   Evidence Grade
                             │
                       Dense + BM25
                             │
                            RRF
                             │
                        Auto Merge
                             │
                           Rerank
```

并能在 3 分钟内讲清。

---

# 最终毕业标准

你可以认为自己真正掌握了这个项目，当你能够独立回答并动手验证以下问题：

1. 为什么是 L3 recall + parent merge？
2. 为什么 Dense + BM25 后用 RRF？
3. 为什么 merge 在 rerank 前？
4. 为什么相关不代表可回答？
5. 为什么 rewrite 只能一次？
6. 为什么复杂问题要 fan-out？
7. 为什么子 Agent 要 bounded？
8. 为什么 HITL 要 checkpoint/resume？
9. 如何定位一次错误回答属于 retrieval 还是 generation？
10. 如何用实验而不是案例证明某个 RAG 模块有效？

完成这些，你在面试里就不再是“用过 LangChain/RAG”，而是能够以工程师视角讨论 **Agentic RAG 的结构、状态、检索、评测和生产约束**。
