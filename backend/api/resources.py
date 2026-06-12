import os
from pathlib import Path

from backend.indexing import (
    DocumentLoader,
    MilvusWriter,
    ParentChunkStore,
    embedding_service,
)
from backend.indexing.milvus_client import get_milvus_store

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR.parent / "data"
UPLOAD_DIR = DATA_DIR / "documents"

loader = DocumentLoader()
parent_chunk_store = ParentChunkStore()
milvus_manager = get_milvus_store()
milvus_writer = MilvusWriter(embedding_service=embedding_service, milvus_manager=milvus_manager)


def remove_bm25_stats_for_filename(filename: str) -> None:
    """删除 Milvus 中该文件对应 chunk 前，先从持久化 BM25 统计中扣减。"""
    rows = milvus_manager.query_all(
        filter_expr=f'filename == "{filename}"',
        output_fields=["text"],
    )
    texts = [r.get("text") or "" for r in rows]
    embedding_service.increment_remove_documents(texts)


def delete_document_transactionally(filename: str, job_manager=None, job_id=None) -> int:
    """
    一致性且事务性地删除文档的所有关联数据。
    包含以下步骤：
    1. 初始化 Milvus 集合并在必要时更新 Job 状态。
    2. 从 Milvus 中分页查询出所有的 L3 叶子 chunk 文本（只读阶段）。
    3. 同步扣减 BM25 稀疏词表的全局 doc_freq 统计。
    4. 执行 Milvus 中的向量删除（不归路点）。
       - 补偿机制：若 Milvus 向量删除失败，回滚 BM25 stats 的扣减（加回）。
    5. 删除 PostgreSQL 中的 L1/L2 父级分块以及对应的 Redis 缓存。
       - 注意：若 Milvus 删除成功但 Postgres/Redis 清理失败，我们保留 BM25 扣减（因为 Milvus 向量已彻底物理删除），
         并抛出异常，以便进行下一次重试。下一次重试会查询到 0 条 Milvus 数据，从而跳过 BM25/Milvus 步骤，直接清理 PostgreSQL。
    """
    if job_manager and job_id:
        job_manager.update_step(job_id, "prepare", 50, "running", "正在初始化 Milvus 集合")
    
    milvus_manager.init_collection()
    delete_expr = f'filename == "{filename}"'
    
    if job_manager and job_id:
        job_manager.complete_step(job_id, "prepare", "准备完成")

    # 1. 预先查询所有的叶子分块文本
    if job_manager and job_id:
        job_manager.update_step(job_id, "bm25", 10, "running", "正在从 Milvus 读取分块文本数据")
    
    try:
        rows = milvus_manager.query_all(
            filter_expr=delete_expr,
            output_fields=["text"],
        )
        texts = [r.get("text") or "" for r in rows if r.get("text")]
    except Exception as e:
        raise RuntimeError(f"从 Milvus 查询文档分块失败: {str(e)}") from e

    # 2. 扣减 BM25 统计
    bm25_deducted = False
    if texts:
        if job_manager and job_id:
            job_manager.update_step(job_id, "bm25", 50, "running", f"正在同步扣减 BM25 词频统计 (分块数: {len(texts)})")
        try:
            embedding_service.increment_remove_documents(texts)
            bm25_deducted = True
        except Exception as e:
            raise RuntimeError(f"扣减 BM25 统计失败: {str(e)}") from e

    if job_manager and job_id:
        job_manager.complete_step(job_id, "bm25", f"BM25 词频统计同步扣减完成 (分块数: {len(texts)})")

    # 3. 删除 Milvus 向量
    if job_manager and job_id:
        job_manager.update_step(job_id, "milvus", 20, "running", "正在物理删除 Milvus 中的向量分块")
    
    chunks_deleted = 0
    try:
        result = milvus_manager.delete(delete_expr)
        chunks_deleted = result.get("delete_count", 0) if isinstance(result, dict) else 0
    except Exception as e:
        # 补偿回滚：若 Milvus 物理删除失败，回退/还原 BM25 扣减统计
        if bm25_deducted and texts:
            try:
                embedding_service.increment_add_documents(texts)
            except Exception as rollback_err:
                # 记录严重的补偿失败
                pass
        raise RuntimeError(f"删除 Milvus 向量失败: {str(e)}") from e

    if job_manager and job_id:
        job_manager.complete_step(job_id, "milvus", f"向量数据清理完成，共删除 {chunks_deleted} 条记录")

    # 4. 删除 Postgres 中的 ParentChunk 和 Redis 缓存
    if job_manager and job_id:
        job_manager.update_step(job_id, "parent_store", 20, "running", "正在清理 PostgreSQL 数据库和 Redis 中的父级分块")
    
    try:
        parent_chunk_store.delete_by_filename(filename)
    except Exception as e:
        # 抛出异常供重试：因为 Milvus 向量已经删除，重试时 query_all 为空，不会再次扣减 BM25，能够干净地推进并重试成功。
        raise RuntimeError(f"清理 PostgreSQL 父级分块及缓存失败: {str(e)}") from e

    if job_manager and job_id:
        job_manager.complete_step(job_id, "parent_store", "父级分块及 Redis 缓存已清空")

    return chunks_deleted


def is_supported_document(filename: str) -> bool:
    file_lower = filename.lower()
    return (
        file_lower.endswith(".pdf")
        or file_lower.endswith((".docx", ".doc"))
        or file_lower.endswith((".xlsx", ".xls"))
        or file_lower.endswith((".html", ".htm"))
    )


async def save_upload_file(file, file_path: Path) -> None:
    with open(file_path, "wb") as f:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            f.write(chunk)


def ensure_upload_dir() -> None:
    os.makedirs(UPLOAD_DIR, exist_ok=True)
