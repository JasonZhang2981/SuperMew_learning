# 04. RAG 检索：Dense + BM25、RRF、Auto-merging、Rerank

## 1. 本课目标

把 SuperMew 在线检索链完整拆开：

```text
query
  -> dense embedding
  -> Dense + BM25 hybrid recall
  -> RRF fusion
  -> Auto-merging
  -> optional rerank
  -> score threshold
  -> final top-k
```

这条链是整个项目最值得面试讲的 RAG 工程部分。

---

## 2. 检索入口

核心函数位于：

```text
backend/rag/utils.py
retrieve_documents(query, top_k)
```

它不是直接 `vector_store.similarity_search()`，而是显式管理：

- candidate pool 大小；
- leaf chunk level；
- hybrid / dense fallback；
- Auto-merging；
- rerank；
- minimum score；
- trace metadata。

这比常见 Demo 更接近生产检索 pipeline。

---

## 3. 为什么 `top_k` 前还要有 `candidate_k`

项目把最终 top-k 和初始召回候选数区分开。

默认思路：

```text
candidate_k = top_k × multiplier
```

例如：

```text
top_k = 8
multiplier = 3
candidate_k = 24
```

原因：如果第一阶段只召回 8 个，再 rerank 8 个，reranker 没有真正的选择空间。

典型两阶段检索：

```text
高召回 Recall：取较大候选池
      ↓
高精度 Rank：压缩到最终 top-k
```

面试术语：**retrieve many, rank few**。

---

## 4. 只检索叶子层

检索 filter 会限制：

```text
chunk_level == LEAF_RETRIEVE_LEVEL
```

默认是 L3。

原因是：

- L3 语义更集中；
- 不会让同一内容的大块和小块同时竞争；
- 后面可以通过 parent relation 恢复大上下文。

这是一种很典型的 hierarchical retrieval。

---

## 5. Dense 召回

Query 首先通过 embedding service 得到 dense vector。

然后 Milvus 使用 HNSW ANN 搜索：

```text
query vector
  -> dense_embedding field
  -> IP similarity
```

Dense 的优势：

- 同义改写；
- 自然语言表达变化；
- 跨词面的语义相关。

缺点：

- 专有名词可能不稳定；
- 数字、版本号、错误码等可能被弱化；
- embedding model domain mismatch 会影响效果。

---

## 6. BM25 sparse 召回

同一个 query 也进入 sparse BM25 检索。

Milvus schema 已定义 BM25 Function，因此可以直接把 query text 作为 sparse search 输入。

BM25 的优势：

- exact term；
- rare keyword；
- product / module / class / error code；
- 可解释词法匹配。

缺点：

- 不理解同义词；
- 用户用不同表达时可能 miss。

因此两者天然互补。

---

## 7. 两路召回为什么用 RRF

SuperMew 使用 `RRFRanker` 做融合。

RRF：Reciprocal Rank Fusion。

核心思想不是比较原始 score，而是比较 rank。

经典形式：

```text
RRF(d) = Σ 1 / (k + rank_i(d))
```

假设一个文档：

```text
Dense rank = 2
BM25 rank  = 5
```

那么它会在两个 ranking list 中都获得贡献。

### 为什么不用直接加分数

因为 Dense similarity 和 BM25 score 的数值尺度不同：

```text
Dense: 0.72
BM25 : 13.4
```

直接相加没有统计意义。

RRF 使用名次，可以避免做复杂 score calibration。

这是高频面试点。

---

## 8. hybrid search 失败时为什么退化成 dense

`retrieve_documents` 有降级路径：

```text
hybrid_retrieve
    ↓ exception
 dense_retrieve
```

如果 dense 也失败，则返回空结果并记录失败 meta。

这说明在线 RAG 不应默认所有基础设施永远正常。

### 为什么不是反过来只 BM25 fallback？

当前代码选择 dense fallback，是项目策略，而不是理论唯一解。

生产系统可以设计：

```text
hybrid
  -> dense-only
  -> lexical-only
  -> cached result
```

具体取决于组件可用性和业务需求。

---

## 9. Auto-merging 是怎么做的

初始 candidate 是 L3。

系统会统计多个 chunk 是否属于同一个 parent。

如果同一个 parent 下命中的 child 数量达到阈值，例如 2：

```text
L2-A
├── L3-A1  <-- hit
├── L3-A2  <-- hit
└── L3-A3
```

则把多个 L3 替换为父节点 L2-A。

然后系统再做一次：

```text
L2 -> L1
```

所以完整过程：

```text
L3 candidates
  -> merge siblings to L2
  -> merge L2 siblings to L1
```

---

## 10. Auto-merging 解决什么问题

如果 query 同时命中同一章节中的多个细粒度片段，说明：

```text
用户问题可能需要这一整段上下文
```

与其把多个重叠 L3 都交给 LLM：

```text
chunk A1
chunk A2
chunk A3
```

不如恢复成：

```text
parent A
```

收益：

- 减少重复；
- 恢复上下文连续性；
- 引用更自然；
- 降低多个 overlapping chunk 占用的 token。

---

## 11. 为什么 merge 在 rerank 前

SuperMew pipeline：

```text
recall -> merge -> rerank
```

而不是：

```text
recall -> rerank -> merge
```

这样做的逻辑：

1. 召回到多个 sibling 后先恢复完整父块；
2. reranker 对“最终可能提供给 LLM 的语义单元”进行评分；
3. 避免先把 child 过滤掉后，无法满足 merge threshold。

这是一个可以讨论的设计取舍。

缺点也存在：

- 父块更长，rerank 输入成本更高；
- 父块可能包含无关上下文。

因此 auto-merge threshold 是重要超参数。

---

## 12. merge 后的 score 如何处理

父节点替换多个 children 后，需要给父节点一个排序依据。

项目会把 child 的有效 rank score 聚合到 parent，倾向保留更高分。

这是一种 heuristic。

更复杂方案可以是：

```text
max(child score)
mean(top child scores)
coverage-aware score
parent rerank score
```

当前项目最终还有 reranker，所以 merge score 更多用于 fallback /排序基础。

---

## 13. Rerank 的位置与作用

项目支持外部 rerank endpoint。

输入：

```text
query
candidate documents
```

输出：

```text
relevance_score
```

典型 Cross-Encoder reranker 相比 embedding 双塔更精确，因为它联合编码：

```text
(query, document)
```

而不是分别编码后点积。

代价：更慢、更贵。

所以只能对候选池使用，不能扫描整个 corpus。

---

## 14. 为什么 Rerank 是 optional

Rerank 依赖：

```text
RERANK_MODEL
RERANK_API_KEY
RERANK_BINDING_HOST
```

如果没配置，pipeline 仍能工作。

这体现 production 组件应该允许 capability degradation。

同时如果 rerank HTTP 失败，代码会 fallback 到原 rank score，而不是直接让整个 RAG 报错。

---

## 15. Rerank threshold

最终还会做：

```text
score >= RERANK_MIN_SCORE
```

这一步不是为了排序，而是为了 **reject low-confidence retrieval**。

区别：

```text
Top-K：相对排序
Threshold：绝对质量门槛
```

如果所有文档都很差，top-k 仍然会返回“最不差的 8 个”。

这可能导致 hallucination。

Threshold 能允许：

```text
最终 0 个文档
```

然后交给 evidence grader / no_knowledge 逻辑。

---

## 16. 检索 trace 为什么记录这么多字段

项目记录：

```text
retrieval_mode
candidate_k
recall_count
post_merge_candidate_count
auto_merge_applied
rerank_applied
rerank_error
post_rerank_count
post_threshold_count
retrieval_empty
...
```

这非常重要。

如果用户说“RAG 答错了”，你不能只看最终回答。

要判断错误来自：

```text
query understanding?
embedding?
recall?
RRF?
merge?
rerank?
threshold?
evidence grading?
generation?
```

这就是 RAG observability。

---

## 17. 一条完整在线检索示例

用户：

```text
SuperMew 的知识库为什么要做三级分块？
```

### Step 1: Query embedding

得到 dense vector。

### Step 2: Hybrid recall

Dense 可能召回：

```text
hierarchical chunks
parent child retrieval
```

BM25 可能召回：

```text
chunk_level
parent_chunk_id
```

### Step 3: RRF

把两套排名融合。

### Step 4: Auto-merging

若多个 L3 来自同一 L2，则恢复 L2。

### Step 5: Rerank

对 query-document pair 精排。

### Step 6: Threshold

过滤低质量候选。

### Step 7: Top-k docs

送入 evidence grading。

---

## 18. 面试题

### Q1：为什么 Hybrid Retrieval 比只用向量更好？

> Dense 和 lexical retrieval 的错误模式不同。Dense 擅长语义，BM25 擅长精确 term；混合召回可以提高 recall，然后通过 RRF 解决不同 score space 难融合的问题。

### Q2：RRF 为什么适合融合 Dense 和 BM25？

> 因为二者原始分数不在同一尺度，RRF 基于排名而不是原始分数做融合，不需要 score calibration，且对不同 retriever 比较鲁棒。

### Q3：Reranker 为什么不直接取代 Retriever？

> Cross-Encoder reranker 精度高但推理复杂度高，不适合对全库所有文档计算，所以必须先高效 recall 缩小候选池，再精排。

### Q4：为什么 Auto-merging 不是简单把相邻 chunk 拼接？

> 它依赖预先建立的 parent-child hierarchy，只有多个同父 child 被召回达到阈值时才替换成语义完整的父块，结构比按位置盲目拼接更稳定。

### Q5：Top-k 有什么问题？

> Top-k 永远返回相对最优结果，即使所有候选都不相关。因此需要 threshold、evidence grading 或 abstention 机制避免把低质量上下文送给 LLM。

---

## 19. 本课验收

不看代码讲出：

```text
L3 hybrid recall
-> Dense + BM25
-> RRF
-> Auto-merging
-> Rerank
-> threshold
-> top-k
```

并且能解释每一步“前一步解决不了什么问题，所以才需要下一步”。
