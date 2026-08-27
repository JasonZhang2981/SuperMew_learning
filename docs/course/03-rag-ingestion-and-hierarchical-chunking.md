# 03. RAG 入库：解析、三级分块、Embedding、父块存储

## 1. 本课目标

理解 SuperMew 的知识库不是“上传文件后直接做 embedding”，而是先构造一个 **L1/L2/L3 层级 chunk tree**，再分别把“可召回叶子块”和“可回溯父块”存起来。

---

## 2. 入库主链路

核心源码：

```text
backend/api/routes/documents.py
backend/indexing/document_loader.py
backend/indexing/embedding.py
backend/indexing/milvus_writer.py
backend/indexing/parent_chunk_store.py
backend/jobs/upload_jobs.py
```

整体流程：

```text
上传文件
  ↓
清理旧版本
  ↓
文档解析
  ↓
文本净化
  ↓
三级分块 L1/L2/L3
  ↓
父级块写入 ParentChunkStore
  ↓
所有 chunk 计算 dense embedding
  ↓
写入 Milvus
  ↓
Milvus 自动维护 BM25 sparse representation
```

上传任务进度也被拆成：

```text
upload
cleanup
parse
parent_store
vector_store
```

这说明项目已经把 indexing 看成独立的数据工程 pipeline。

---

## 3. 文档支持哪些类型

`DocumentLoader` 会根据文件类型选择不同 loader：

```text
PDF   -> PyPDFLoader
Word  -> Docx2txtLoader
Excel -> UnstructuredExcelLoader
HTML  -> 自定义 HTML processor
```

这体现一个重要原则：

> RAG 的第一步不是向量检索，而是可靠地把异构文档转换成统一文本单元。

真实项目中，解析质量往往直接决定 RAG 上限。

---

## 4. 为什么先做文本清洗

SuperMew 在 split 前会进行 Unicode / 控制字符净化，例如：

- Unicode NFC normalization；
- 去零宽字符；
- 去 BOM；
- 去不可打印控制字符；
- 去私有区乱码；
- 剔除孤立 surrogate。

这不是“洁癖”，而是实际工程问题。

脏字符可能导致：

```text
embedding API 请求失败
数据库 utf-8 写入失败
BM25 analyzer 分词异常
前端渲染异常
chunk hash / id 不稳定
```

面试可以说：

> 数据清洗是 indexing pipeline 的一部分，而不是 LLM 前处理的附属步骤。

---

## 5. 三级分块是整个 RAG 的关键

默认构造三级：

```text
L1：大块，约 2400+ 字符
L2：中块，约 1600+ 字符
L3：小块，约 800 字符
```

实际代码会结合初始化 `chunk_size` 和最小值计算，且每层都有 overlap。

逻辑结构：

```text
L1 root chunk
├── L2 chunk A
│   ├── L3 chunk A1
│   ├── L3 chunk A2
│   └── ...
└── L2 chunk B
    ├── L3 chunk B1
    └── ...
```

每个 chunk 都保存：

```text
chunk_id
parent_chunk_id
root_chunk_id
chunk_level
chunk_idx
filename
page_number
text
```

---

## 6. 为什么不用一种 chunk size

这是 RAG 最经典的冲突：

### 小块优点

- query 与局部事实更容易匹配；
- embedding 语义更聚焦；
- BM25 关键词不会被大量无关词稀释。

### 小块缺点

- 上下文不完整；
- 可能只召回一句结论，没有定义/前因/限制条件。

### 大块优点

- 上下文完整；
- 更适合最终生成。

### 大块缺点

- embedding 语义混杂；
- 稀疏检索可能被噪声稀释；
- token 成本更高。

SuperMew 的答案是：

> **small-to-big retrieval**：用 L3 负责精准召回，用父块负责恢复上下文。

这是后面 Auto-merging 的基础。

---

## 7. chunk ID 为什么显式编码 level

大致形式：

```text
filename::p{page}::l{level}::{index}
```

这样有几个好处：

- 可直接定位来源文件和页；
- 容易排查重复 chunk；
- 父子关系稳定；
- 删除文档时可按 filename 处理；
- trace 中容易展示。

生产中 chunk id 应该具有：

```text
稳定性
唯一性
可追踪性
```

---

## 8. 为什么同时保存 `parent_chunk_id` 和 `root_chunk_id`

假设：

```text
L1-A
└── L2-A1
    └── L3-A1-1
```

L3：

```text
parent_chunk_id = L2-A1
root_chunk_id   = L1-A
```

这让系统既可以：

```text
L3 -> L2
```

也能知道最终属于哪个 L1 root。

当前 Auto-merging 是逐层合并：

```text
L3 -> L2 -> L1
```

显式层级关系比运行时重新推断更可靠。

---

## 9. Milvus 中保存什么

每个 chunk 会写入：

```text
dense_embedding
text
filename
file_type
file_path
page_number
chunk_idx
chunk_id
parent_chunk_id
root_chunk_id
chunk_level
```

Milvus schema 同时声明：

```text
dense_embedding: FLOAT_VECTOR
sparse_embedding: SPARSE_FLOAT_VECTOR
```

注意：业务代码写入时主要传 dense embedding + text；BM25 sparse representation 由 Milvus 的 BM25 Function 根据 text 生成。

这非常值得面试讲清楚：

> Sparse 并不是额外调用一个 embedding 模型生成，而是利用 Milvus 内置 BM25 Function 从文本构造 sparse vector。

---

## 10. Dense 索引为什么用 HNSW + IP

当前设计：

```text
index_type = HNSW
metric_type = IP
```

HNSW 是 ANN 索引，核心目的：

```text
用近似最近邻换取低延迟
```

IP 是 inner product，相似度是否等价于 cosine 取决于 embedding 是否归一化。

面试中不要直接说：

> IP 就是 cosine。

更准确：

> 如果 embedding 经过 L2 normalization，inner product 与 cosine similarity 排序等价；否则不是完全同一指标。

---

## 11. BM25 sparse index 为什么有价值

Dense embedding 擅长语义，例如：

```text
“登录失败” ≈ “无法认证”
```

BM25 擅长精确词：

```text
API 名
类名
错误码
型号
人名
版本号
产品名
```

例如用户问：

```text
ERR_CONN_POOL_102 是什么？
```

Dense 可能不擅长特殊 token；BM25 会直接命中。

因此工程知识库通常需要 hybrid retrieval。

---

## 12. ParentChunkStore 为什么单独存在

在线检索首先只搜索某一层，例如 L3。

但 Auto-merging 需要根据：

```text
parent_chunk_id
```

把父块内容取回来。

父块可以独立保存到关系数据库，而不是每次都去向量库做向量搜索。

这体现：

```text
Vector DB = 相似搜索
Relational store = 结构关系 / 精确读取
```

不要把所有数据职责都强行塞给向量数据库。

---

## 13. 为什么 indexing 要支持“清理旧版本”

如果同名文件重复上传，而不清理旧 chunk，会出现：

```text
旧版和新版同时被召回
重复 evidence
版本冲突
BM25 document frequency 污染
```

所以更新知识库时必须考虑：

```text
upsert / versioning / delete old chunks
```

这属于生产 RAG 数据生命周期管理。

---

## 14. 面试题

### Q1：为什么采用三级分块？

> 小 chunk 更利于精准召回，大 chunk 更利于提供完整上下文。项目通过层级 parent-child chunk 同时满足两者：先从最细粒度召回，再根据同父节点命中情况向上合并。

### Q2：为什么不直接对 L1、L2、L3 全部检索？

> 多层同时召回容易产生大量重复片段，并且不同尺度的相似度分数不可直接比较。项目选择固定叶子层召回，再通过结构化 parent relation 做 deterministic merge。

### Q3：Dense + BM25 的互补是什么？

> Dense 提供语义召回，BM25 提供词法精确匹配，尤其对实体、专有名词、错误码和版本号有效。两路召回后再进行 rank fusion。

### Q4：为什么父块放关系存储？

> 父块回溯是按 chunk_id 精确读取，不需要 ANN；结构关系和文本读取更适合关系/键值存储，向量库主要负责相似搜索。

---

## 15. 本课验收

你应该能画出：

```text
Document
  ↓ parse + sanitize
L1
└── L2
    └── L3
        ↓ embedding
      Milvus
```

并解释一句：

> SuperMew 的 indexing 目标不是简单切块，而是提前构造后续 small-to-big retrieval 所需的层级结构。
