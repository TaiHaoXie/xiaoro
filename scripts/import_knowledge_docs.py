"""
护肤美妆知识文档批量导入脚本

把 data/knowledge_docs/ 下的 Markdown 文档切块后写入知识库，
复用线上文档上传接口同款逻辑（TextChunker + KnowledgeBaseService + knowledge_documents 登记），
确保答辩演示时 RAG 检索可以直接命中。

用法：
    # 全量导入（默认会先清掉之前由本脚本导入的同源文档，避免重复）
    .venv/bin/python scripts/import_knowledge_docs.py

    # 不清理、直接追加
    .venv/bin/python scripts/import_knowledge_docs.py --no-clean

    # 指定目录
    .venv/bin/python scripts/import_knowledge_docs.py --docs-dir data/knowledge_docs
"""

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from uuid import uuid4

# 保证可以 import app.*
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.api.v1.documents import TextChunker  # noqa: E402
from app.database.postgres import (  # noqa: E402
    init_postgres_pool,
    close_postgres_pool,
    execute_query,
)
from app.services.knowledge_base import get_knowledge_service  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("import_knowledge_docs")

IMPORT_SOURCE = "knowledge_docs_seed"
KNOWLEDGE_TYPE = "user_guide"
CATEGORY = "skincare_guide"


def _build_document_id(filename: str) -> str:
    sanitized = "".join(
        ch for ch in Path(filename).stem if ch.isalnum() or ch in ("_", "-")
    )[:24] or "document"
    return f"doc_{sanitized}_{uuid4().hex[:10]}"


async def clean_previous_import() -> None:
    """删除上一次由本脚本导入的文档及其知识块，避免重复入库。"""
    docs = await execute_query(
        "SELECT id FROM knowledge_documents WHERE category = $1",
        CATEGORY,
        fetch="all",
    )
    doc_ids = [row["id"] for row in (docs or [])]

    # 知识块通过 metadata.import_source 标记定位
    await execute_query(
        "DELETE FROM knowledge_base WHERE type = $1 AND metadata->>'import_source' = $2",
        KNOWLEDGE_TYPE,
        IMPORT_SOURCE,
        fetch="none",
    )
    if doc_ids:
        await execute_query(
            "DELETE FROM knowledge_documents WHERE id = ANY($1::varchar[])",
            doc_ids,
            fetch="none",
        )
    logger.info("已清理历史导入：文档 %d 篇、对应知识块已删除", len(doc_ids))


async def import_one(path: Path, chunker: TextChunker) -> int:
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        logger.warning("跳过空文档：%s", path.name)
        return 0

    chunks = chunker.chunk_text(text)
    if not chunks:
        logger.warning("未切出任何块：%s", path.name)
        return 0

    document_id = _build_document_id(path.name)
    knowledge_service = get_knowledge_service()
    title_stem = path.stem

    for chunk in chunks:
        chunk_metadata = {
            "document_id": document_id,
            "source_filename": path.name,
            "chunk_index": chunk["index"],
            "total_chunks": len(chunks),
            "file_type": "md",
            "import_source": IMPORT_SOURCE,
        }
        await knowledge_service.add_knowledge(
            title=f"{title_stem} · 第 {chunk['index'] + 1} 段",
            content=chunk["content"],
            knowledge_type=KNOWLEDGE_TYPE,
            metadata=chunk_metadata,
            product_id=None,
        )

    preview = text.replace("\n", " ")[:220]
    await execute_query(
        """
        INSERT INTO knowledge_documents
            (id, filename, title, file_type, category, product_id, size, chunks_count, content_preview, metadata, created_at, updated_at)
        VALUES
            ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, NOW(), NOW())
        """,
        document_id,
        path.name,
        title_stem,
        "md",
        CATEGORY,
        None,
        len(text.encode("utf-8")),
        len(chunks),
        preview,
        json.dumps({"import_source": IMPORT_SOURCE}),
        fetch="none",
    )

    logger.info("✅ 导入 %s -> %d 块", path.name, len(chunks))
    return len(chunks)


async def main(docs_dir: str, clean: bool) -> None:
    docs_path = (PROJECT_ROOT / docs_dir).resolve()
    files = sorted(docs_path.glob("*.md"))
    if not files:
        logger.error("目录下没有 .md 文档：%s", docs_path)
        return

    await init_postgres_pool()
    try:
        if clean:
            await clean_previous_import()

        chunker = TextChunker(chunk_size=500, overlap=50)
        total_docs = 0
        total_chunks = 0
        for path in files:
            n = await import_one(path, chunker)
            if n > 0:
                total_docs += 1
                total_chunks += n

        logger.info("=" * 48)
        logger.info("导入完成：%d 篇文档，共 %d 个知识块", total_docs, total_chunks)

        # 入库后校验
        doc_count = await execute_query(
            "SELECT COUNT(*) AS c FROM knowledge_documents WHERE category = $1",
            CATEGORY,
            fetch="one",
        )
        kb_count = await execute_query(
            "SELECT COUNT(*) AS c FROM knowledge_base WHERE metadata->>'import_source' = $1",
            IMPORT_SOURCE,
            fetch="one",
        )
        logger.info(
            "数据库校验：knowledge_documents=%s 篇，knowledge_base=%s 块",
            doc_count["c"],
            kb_count["c"],
        )
    finally:
        await close_postgres_pool()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="批量导入护肤美妆知识文档到知识库")
    parser.add_argument("--docs-dir", default="data/knowledge_docs", help="文档目录（相对项目根）")
    parser.add_argument("--no-clean", dest="clean", action="store_false", help="不清理历史导入，直接追加")
    parser.set_defaults(clean=True)
    args = parser.parse_args()

    asyncio.run(main(args.docs_dir, args.clean))
