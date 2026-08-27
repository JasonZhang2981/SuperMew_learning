# 06. 检索纠错：Step-back、HyDE 与 Rewrite Budget

## 1. 本课目标

理解 SuperMew 在“初次检索有相关信号，但证据不足”时为什么不直接放弃，而是只允许一次受控 Query Rewrite，并在 Step-back 与 HyDE 中二选一。

---

## 2. Rewrite 不是默认步骤

主路径不是：

```text
query -> rewrite -> retrieve
```

而是：

```text
query
-> retrieve
-> grade evidence
-> 只有需要时才 rewrite
```

这是一个重要设计。

原因：

- 原 query 可能本来就很好；
- 每次 rewrite 都增加模型调用成本；
- 改写可能偏离用户原意；
- 先看真实 retrieval failure，再决定修复方式，更有针对性。

因此这里属于 **retrieval-conditioned query transformation**。

---

## 3. Rewrite 何时触发

典型条件：

```text
有 docs
相关性不为 none
但 answerability 不足
且 rewrite_count < 1
```

换句话说：

```text
“不是完全搜不到，而是搜得不够好”
```

如果完全没有相关内容，盲目 rewrite 往往只会扩大 hallucination 风险。

---

## 4. Step-back 的核心思想

Step-back Prompting 的思路是：

> 原问题太具体时，先退到一个更高层、更一般化的问题，再利用一般知识表述帮助检索。

例如原问题：

```text
SuperMew 的 `LEAF_RETRIEVE_LEVEL=3` 为什么适合 Auto-merging？
```

可能改成：

```text
层级 RAG 中为什么通常从细粒度叶子块开始召回？
```

原问题带了：

- 项目名；
- 配置名；
- 具体 level 数字。

这些细节可能限制检索空间。

Step-back 提取背后的通用概念后，更容易召回讲机制的文档。

---

## 5. Step-back 适合什么失败模式

项目 prompt 中的选择原则可以概括成：

```text
问题过于具体
实体/型号/时间/条件很多
需要上升到概念或机制
```

例如：

```text
“v2.3.1 中 A 模块 5 秒超时是什么原因？”
```

知识库可能只有：

```text
“A 模块的连接超时机制”
```

Step-back 能消除过细约束。

---

## 6. HyDE 的核心思想

HyDE：Hypothetical Document Embeddings。

基本思路：

1. LLM 根据 query 生成一段“假设答案式文档”；
2. 用这段假设文档帮助形成更丰富的检索 query；
3. 去知识库检索真实文档；
4. 最终答案只使用真实 retrieved chunks。

例如用户只问：

```text
“为什么混合检索更稳？”
```

问题很抽象。

HyDE 可能生成一个带相关术语的假设文本：

```text
Dense retrieval 提供 semantic recall，BM25 提供 lexical exact-match，RRF 用于 rank fusion……
```

这段文本包含知识库中更可能出现的术语，因此 embedding / BM25 更容易命中。

---

## 7. HyDE 为什么适合“模糊、概念性问题”

用户语言和文档语言可能存在 vocabulary gap。

用户：

```text
“怎么让搜索更准？”
```

文档：

```text
hybrid retrieval
cross-encoder reranking
reciprocal rank fusion
```

原 query 太泛。

HyDE 相当于先让 LLM 猜一份“目标文档可能长什么样”，从而补充检索术语。

---

## 8. Step-back 与 HyDE 的差异

| 对比项 | Step-back | HyDE |
|---|---|---|
| 主要问题 | query 太具体 | query 太模糊/术语不足 |
| 转换方向 | 向上抽象 | 向可能答案语义展开 |
| 生成内容 | 更一般的问题 | 假设答案文档 |
| 主要风险 | 抽象过度 | 引入模型猜测 |
| 是否是事实证据 | 否 | 否 |

最关键一句：

> 两者都只改变 retrieval query，不改变 evidence truth source。

---

## 9. 为什么只能选择一种 Rewrite

代码要求本轮：

```text
method = step_back OR hyde
```

而不是两个都跑再融合。

这样做的收益：

### 成本可控

一次 rewrite model + 一次 rewritten retrieval。

### Trace 清楚

你能明确知道：

```text
这一次 recovery 是 Step-back 还是 HyDE 起作用
```

### 避免候选爆炸

如果：

```text
original query
step-back query
hyde query
```

全部召回再融合，检索逻辑会迅速复杂化。

### 便于评测

可以分别统计：

```text
Step-back recovery rate
HyDE recovery rate
```

---

## 10. Rewrite Plan 为什么使用结构化输出

不是让模型返回自由文本：

```text
“我觉得应该用 HyDE，因为……”
```

而是结构化：

```text
method
step_back_question
hyde_document
```

并且有互斥校验。

例如：

```text
method=step_back
=> step_back_question 必须有值
=> hyde_document 必须为空
```

这体现 Agent 工程原则：

> 让 LLM 参与决策时，尽量把输出收敛到明确 schema，并在业务代码中校验 invariant。

---

## 11. 为什么 rewritten query 还保留 original query

项目不是完全替换原问题，而是类似：

```text
original query
+
step-back question / hypothetical document
```

好处：

- 保留原始实体和约束；
- 同时加入抽象概念或扩展术语；
- 减少 rewrite drift。

如果完全只使用 Step-back question，可能把原始条件丢掉。

---

## 12. Rewrite Drift 是什么

例如原问题：

```text
“SuperMew 的 RRF 参数 k 是多少？”
```

如果 Step-back 成：

```text
“RRF 如何工作？”
```

可能召回大量原理内容，却失去项目具体参数。

所以 query rewrite 的目标不是“改得更漂亮”，而是：

```text
提高 retrieval recall，同时尽量保留 user intent
```

---

## 13. 为什么 rewrite budget = 1

这是 SuperMew 的重要设计。

主路径最多：

```text
initial retrieval
+
one rewritten retrieval
```

### 原因 1：防止无限自纠错

如果 grading 每次都说 partial：

```text
rewrite -> partial -> rewrite -> partial -> ...
```

会变成 loop。

### 原因 2：边际收益递减

第一次 rewrite 往往能修复 query mismatch；第二、三次更可能开始语义漂移。

### 原因 3：延迟预算

RAG 每轮可能包括 rerank + grader，重复非常昂贵。

### 原因 4：失败后应该换策略

一次 rewrite 仍不足时，更合理的下一步可能是：

```text
clarify user
or
no_knowledge
```

而不是继续让模型猜 query。

---

## 14. Rewrite 后为什么重新 Grade

不能假设：

```text
“既然 rewrite 了，第二次结果一定更好”
```

所以流程：

```text
rewrite
-> retrieve_rewritten
-> grade_documents
```

第二次 evidence 仍要经过同一标准。

这保证 decision consistency。

---

## 15. Rewrite 后仍 partial 怎么办

预算已经耗尽时：

- 如果仍有部分证据，可能进入 clarify；
- 如果没有可回答证据，进入 no_knowledge；
- 不允许继续 rewrite。

这就是**有限自纠错**，而不是无限 autonomous search。

---

## 16. 为什么不直接做 Multi-Query Retrieval

另一种常见方案：

```text
原问题 -> 生成 3-5 个改写 query -> 并行召回 -> RRF
```

SuperMew 当前没有在 simple query recovery 上这么做。

可能原因：

- 成本更高；
- 候选重复更多；
- 与复杂问题 decomposition 机制重叠；
- trace 与归因更复杂。

但它是一个很好的扩展实验，后面的 hands-on lab 会安排。

---

## 17. 怎么评测 Query Rewrite 是否有效

不要只看最终回答。

至少分别测：

### Retrieval 指标

```text
Recall@K
MRR
nDCG
Hit Rate
```

比较：

```text
original query vs rewritten query
```

### Recovery 指标

```text
初次 grade != sufficient
经过 rewrite 后 grade == sufficient
```

可以定义：

```text
rewrite_recovery_rate
```

### Drift 指标

检查 rewrite 是否召回了与原问题约束冲突的文档。

### 成本/延迟

```text
rewrite trigger rate
extra latency
extra token cost
```

---

## 18. 面试题

### Q1：Step-back 和 HyDE 有什么区别？

> Step-back 用于问题过于具体、约束过细的场景，通过抽象到更高层概念扩大召回；HyDE 用于问题较模糊或缺少知识库术语的场景，先生成假设答案语义，再用于检索。二者都只能作为 query transformation，不能作为事实证据。

### Q2：为什么不每次都先 HyDE？

> 会增加成本，而且原始 query 本身可能已经足够准确；HyDE 还可能引入错误概念。SuperMew 先做真实检索和证据评分，只在检索不足时触发 recovery。

### Q3：为什么 rewrite 只允许一次？

> 为了限制循环、成本和 semantic drift。一次纠错失败后，应转向澄清或 abstain，而不是继续让模型反复生成新的 query。

### Q4：如何判断 rewrite 是真的有价值？

> 做成对 retrieval evaluation：比较原 query 和 rewrite 后 Recall@K / nDCG，同时统计 evidence 从 partial/weak 恢复到 sufficient 的比例，并结合额外 latency 和 token 成本。

---

## 19. 本课验收

你要能脱口而出：

```text
初次检索不足
-> planner 在 Step-back / HyDE 中二选一
-> 只执行一次 rewritten retrieval
-> 再次 evidence grading
-> 成功则 answer，失败则 clarify/no_knowledge
```

以及最关键一句：

> Query rewrite 是检索策略，不是知识来源。
