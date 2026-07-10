"""
文档上传API模块

处理非结构化文档上传、解析、向量化
用于构建专属知识库
"""

from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
import logging
import os
import tempfile
from pathlib import Path
import json
from uuid import uuid4

from app.database.postgres import execute_query
from app.services.knowledge_base import get_knowledge_service

logger = logging.getLogger(__name__)

# 创建路由器
router = APIRouter(prefix="/documents", tags=["文档管理"])


# ==================== 数据模型 ====================

class DocumentUploadResponse(BaseModel):
    """文档上传响应"""
    success: bool
    document_id: Optional[str] = None
    filename: str
    file_type: str
    size: int
    chunks_count: Optional[int] = None
    message: str


class BatchUploadResponse(BaseModel):
    """批量上传响应"""
    success: bool
    total: int
    uploaded: int
    failed: int
    results: List[Dict[str, Any]]


class DocumentListItem(BaseModel):
    """文档列表项"""
    document_id: str
    filename: str
    title: str
    file_type: str
    category: str
    product_id: Optional[str] = None
    size: int
    chunks_count: int
    content_preview: str = ""
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[str] = None


class DocumentStatsResponse(BaseModel):
    """文档统计响应"""
    success: bool
    total_documents: int = 0
    total_chunks: int = 0
    category_breakdown: List[Dict[str, Any]] = Field(default_factory=list)
    recent_documents: List[Dict[str, Any]] = Field(default_factory=list)


# ==================== 文档解析 ====================

class DocumentParser:
    """文档解析器"""

    @staticmethod
    def parse_pdf(file_path: str) -> str:
        """解析PDF文件"""
        try:
            import PyPDF2
            text = ""
            with open(file_path, 'rb') as f:
                reader = PyPDF2.PdfReader(f)
                for page in reader.pages:
                    text += page.extract_text() + "\n"
            return text
        except ImportError:
            # 如果PyPDF2不可用，使用简单的文本提取
            logger.warning("PyPDF2未安装，使用基础解析")
            return DocumentParser._basic_parse(file_path)
        except Exception as e:
            logger.error(f"PDF解析失败: {e}")
            raise

    @staticmethod
    def parse_txt(file_path: str) -> str:
        """解析TXT文件"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return f.read()
        except UnicodeDecodeError:
            # 尝试其他编码
            with open(file_path, 'r', encoding='gbk') as f:
                return f.read()

    @staticmethod
    def parse_word(file_path: str) -> str:
        """解析Word文件"""
        try:
            from docx import Document
            doc = Document(file_path)
            text = ""
            for paragraph in doc.paragraphs:
                text += paragraph.text + "\n"
            return text
        except ImportError:
            logger.warning("python-docx未安装")
            raise HTTPException(
                status_code=500,
                detail="Word文件解析需要安装 python-docx"
            )
        except Exception as e:
            logger.error(f"Word解析失败: {e}")
            raise

    @staticmethod
    def parse_markdown(file_path: str) -> str:
        """解析Markdown文件"""
        return DocumentParser.parse_txt(file_path)

    @staticmethod
    def _basic_parse(file_path: str) -> str:
        """基础解析（备用）"""
        try:
            with open(file_path, 'rb') as f:
                return f.read().decode('utf-8', errors='ignore')
        except:
            return ""


# ==================== 文本分块 ====================

class TextChunker:
    """文本分块器"""

    def __init__(self, chunk_size: int = 500, overlap: int = 50):
        self.chunk_size = chunk_size
        self.overlap = overlap

    def chunk_text(self, text: str) -> List[Dict[str, Any]]:
        """
        将文本分块

        Args:
            text: 输入文本

        Returns:
            文本块列表
        """
        chunks = []

        # 按段落分割
        paragraphs = text.split('\n\n')
        current_chunk = ""
        chunk_index = 0

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue

            # 如果单个段落超过chunk_size，需要分割
            if len(para) > self.chunk_size:
                # 先保存当前chunk
                if current_chunk:
                    chunks.append({
                        "index": chunk_index,
                        "content": current_chunk.strip(),
                        "size": len(current_chunk)
                    })
                    chunk_index += 1
                    current_chunk = ""

                # 分割长段落
                for i in range(0, len(para), self.chunk_size - self.overlap):
                    chunk_text = para[i:i + self.chunk_size]
                    chunks.append({
                        "index": chunk_index,
                        "content": chunk_text,
                        "size": len(chunk_text)
                    })
                    chunk_index += 1
            else:
                # 检查添加该段落后是否超过chunk_size
                if len(current_chunk) + len(para) > self.chunk_size:
                    # 保存当前chunk
                    if current_chunk:
                        chunks.append({
                            "index": chunk_index,
                            "content": current_chunk.strip(),
                            "size": len(current_chunk)
                        })
                        chunk_index += 1
                    current_chunk = para + "\n\n"
                else:
                    current_chunk += para + "\n\n"

        # 保存最后一个chunk
        if current_chunk:
            chunks.append({
                "index": chunk_index,
                "content": current_chunk.strip(),
                "size": len(current_chunk)
            })

        return chunks


def _build_document_id(filename: str) -> str:
    sanitized = "".join(ch for ch in Path(filename).stem if ch.isalnum() or ch in ("_", "-"))[:24] or "document"
    return f"doc_{sanitized}_{uuid4().hex[:10]}"


def _normalize_metadata(value: Any) -> Dict[str, Any]:
    if not value:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return {}
    return {}


async def _persist_document_and_chunks(
    *,
    filename: str,
    ext: str,
    file_size: int,
    category: Optional[str],
    product_id: Optional[str],
    metadata: Optional[str],
    text_content: str,
    chunks: List[Dict[str, Any]]
) -> DocumentUploadResponse:
    """同步入库文档与分块知识，确保答辩演示时可立刻查询。"""
    document_id = _build_document_id(filename)
    metadata_dict: Dict[str, Any] = {}
    if metadata:
        metadata_dict = json.loads(metadata)

    knowledge_service = get_knowledge_service()

    for chunk in chunks:
        chunk_metadata = {
            **metadata_dict,
            "document_id": document_id,
            "source_filename": filename,
            "chunk_index": chunk["index"],
            "total_chunks": len(chunks),
            "file_type": ext[1:],
            "import_source": "document_upload",
        }
        await knowledge_service.add_knowledge(
            title=f"{Path(filename).stem} · 第 {chunk['index'] + 1} 段",
            content=chunk["content"],
            knowledge_type=category or "document",
            metadata=chunk_metadata,
            product_id=int(product_id) if product_id and str(product_id).isdigit() else None
        )

    preview = text_content.strip().replace("\n", " ")[:220]
    await execute_query(
        """
        INSERT INTO knowledge_documents
            (id, filename, title, file_type, category, product_id, size, chunks_count, content_preview, metadata, created_at, updated_at)
        VALUES
            ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, NOW(), NOW())
        """,
        document_id,
        filename,
        Path(filename).stem,
        ext[1:],
        category or "document",
        product_id,
        file_size,
        len(chunks),
        preview,
        json.dumps(metadata_dict) if metadata_dict else None,
        fetch="none"
    )

    return DocumentUploadResponse(
        success=True,
        document_id=document_id,
        filename=filename,
        file_type=ext[1:],
        size=file_size,
        chunks_count=len(chunks),
        message=f"文档已入知识库，共导入 {len(chunks)} 个文本块"
    )


async def _process_uploaded_document(
    *,
    file: UploadFile,
    category: Optional[str],
    product_id: Optional[str],
    metadata: Optional[str]
) -> DocumentUploadResponse:
    """单文档上传主流程，便于批量复用。"""
    FILE_PARSERS = {
        "application/pdf": DocumentParser.parse_pdf,
        "text/plain": DocumentParser.parse_txt,
        "text/markdown": DocumentParser.parse_markdown,
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": DocumentParser.parse_word,
    }

    EXTENSION_PARSERS = {
        ".pdf": DocumentParser.parse_pdf,
        ".txt": DocumentParser.parse_txt,
        ".md": DocumentParser.parse_markdown,
        ".docx": DocumentParser.parse_word,
    }

    content_type = file.content_type or ""
    filename = file.filename or "untitled.txt"
    ext = Path(filename).suffix.lower()
    parser = FILE_PARSERS.get(content_type) or EXTENSION_PARSERS.get(ext)

    if not parser:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件类型: {content_type} ({ext})"
        )

    content = await file.read()
    file_size = len(content)

    MAX_SIZE = 20 * 1024 * 1024
    if file_size > MAX_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"文件过大: {file_size} 字节（最大20MB）"
        )

    with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        text_content = parser(tmp_path)
        if not text_content or len(text_content.strip()) < 10:
            raise HTTPException(status_code=400, detail="文档内容为空或解析失败")

        chunker = TextChunker(chunk_size=500, overlap=50)
        chunks = chunker.chunk_text(text_content)
        if not chunks:
            raise HTTPException(status_code=500, detail="文本分块失败")

        return await _persist_document_and_chunks(
            filename=filename,
            ext=ext,
            file_size=file_size,
            category=category,
            product_id=product_id,
            metadata=metadata,
            text_content=text_content,
            chunks=chunks
        )
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


# ==================== 文档上传接口 ====================

@router.post("/upload", response_model=DocumentUploadResponse)
async def upload_document(
    file: UploadFile = File(..., description="文档文件"),
    category: Optional[str] = Form(None, description="文档分类"),
    product_id: Optional[str] = Form(None, description="关联商品ID"),
    metadata: Optional[str] = Form(None, description="额外元数据(JSON)")
):
    """
    上传文档到知识库

    支持的格式：
    - PDF (.pdf)
    - Word (.docx)
    - 纯文本 (.txt)
    - Markdown (.md)

    流程：
    1. 文件上传和验证
    2. 内容解析
    3. 文本分块
    4. 向量化
    5. 存储到知识库
    """
    try:
        return await _process_uploaded_document(
            file=file,
            category=category,
            product_id=product_id,
            metadata=metadata
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"文档上传失败: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"文档上传失败: {str(e)}"
        )


@router.post("/upload/batch", response_model=BatchUploadResponse)
async def upload_documents_batch(
    files: List[UploadFile] = File(..., description="文档文件列表"),
    category: Optional[str] = Form(None, description="文档分类"),
    product_id: Optional[str] = Form(None, description="关联商品ID"),
    metadata: Optional[str] = Form(None, description="额外元数据(JSON)")
):
    """
    批量上传文档

    Args:
        files: 文档文件列表
        category: 文档分类

    Returns:
        批量上传结果
    """
    results = []
    uploaded = 0
    failed = 0

    for file in files:
        try:
            result = await _process_uploaded_document(
                file=file,
                category=category,
                product_id=product_id,
                metadata=metadata
            )
            results.append({
                "filename": file.filename,
                "success": True,
                "document_id": result.document_id,
                "chunks_count": result.chunks_count
            })
            uploaded += 1

        except Exception as e:
            logger.error(f"文件上传失败 {file.filename}: {e}")
            results.append({
                "filename": file.filename,
                "success": False,
                "error": str(e)
            })
            failed += 1

    return BatchUploadResponse(
        success=True,
        total=len(files),
        uploaded=uploaded,
        failed=failed,
        results=results
    )


@router.get("/list")
async def list_documents(
    category: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100)
):
    """
    获取已上传文档列表

    Args:
        category: 文档分类过滤
        limit: 返回数量

    Returns:
        文档列表
    """
    params: List[Any] = []
    sql = """
        SELECT id, filename, title, file_type, category, product_id, size, chunks_count,
               content_preview, metadata, created_at
        FROM knowledge_documents
        WHERE 1=1
    """
    if category:
        sql += f" AND category = ${len(params) + 1}"
        params.append(category)
    sql += f" ORDER BY created_at DESC LIMIT ${len(params) + 1}"
    params.append(limit)

    rows = await execute_query(sql, *params, fetch="all")

    return {
        "total": len(rows),
        "results": [
            DocumentListItem(
                document_id=row["id"],
                filename=row["filename"],
                title=row["title"] or Path(row["filename"]).stem,
                file_type=row["file_type"] or "",
                category=row["category"] or "document",
                product_id=row["product_id"],
                size=row["size"] or 0,
                chunks_count=row["chunks_count"] or 0,
                content_preview=row["content_preview"] or "",
                metadata=_normalize_metadata(row["metadata"]),
                created_at=row["created_at"].isoformat() if row.get("created_at") else None
            ).model_dump()
            for row in rows
        ]
    }


@router.get("/stats", response_model=DocumentStatsResponse)
async def get_document_stats():
    """文档知识库统计，供答辩后台展示。"""
    total_documents = await execute_query(
        "SELECT COUNT(*) FROM knowledge_documents",
        fetch="val"
    ) or 0
    total_chunks = await execute_query(
        """
        SELECT COUNT(*)
        FROM knowledge_base
        WHERE metadata->>'import_source' = 'document_upload'
        """,
        fetch="val"
    ) or 0
    category_breakdown = await execute_query(
        """
        SELECT category, COUNT(*) AS count
        FROM knowledge_documents
        GROUP BY category
        ORDER BY count DESC, category ASC
        """,
        fetch="all"
    )
    recent_documents = await execute_query(
        """
        SELECT id, filename, category, chunks_count, created_at
        FROM knowledge_documents
        ORDER BY created_at DESC
        LIMIT 5
        """,
        fetch="all"
    )

    return DocumentStatsResponse(
        success=True,
        total_documents=total_documents,
        total_chunks=total_chunks,
        category_breakdown=category_breakdown,
        recent_documents=[
            {
                **doc,
                "created_at": doc["created_at"].isoformat() if doc.get("created_at") else None
            }
            for doc in recent_documents
        ]
    )


@router.delete("/{document_id}")
async def delete_document(document_id: str):
    """
    删除文档

    Args:
        document_id: 文档ID

    Returns:
        删除结果
    """
    await execute_query(
        """
        DELETE FROM knowledge_base
        WHERE metadata->>'document_id' = $1
        """,
        document_id,
        fetch="none"
    )
    deleted = await execute_query(
        "DELETE FROM knowledge_documents WHERE id = $1 RETURNING id",
        document_id,
        fetch="one"
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="文档不存在")

    return {
        "success": True,
        "message": f"文档 {document_id} 已删除"
    }
