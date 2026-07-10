"""
上传API模块

处理图片上传等文件操作
"""

from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, List
import logging

from app.services.image_preprocessing import get_image_preprocessor

logger = logging.getLogger(__name__)

# 创建路由器
router = APIRouter(prefix="/upload", tags=["上传"])


# ==================== 上传接口 ====================

@router.post("/image")
async def upload_image(
    file: UploadFile = File(..., description="图片文件"),
    product_id: Optional[int] = Form(None, description="关联商品ID"),
    preprocess: bool = Form(True, description="是否预处理图片")
):
    """
    上传图片

    支持的格式：JPEG, PNG, WebP
    最大大小：10MB

    处理流程：
    1. 验证文件类型和大小
    2. 预处理（压缩、格式转换）
    3. 向量化（CLIP）
    4. 存储到向量库

    Args:
        file: 图片文件
        product_id: 关联的商品ID（可选）
        preprocess: 是否预处理图片

    Returns:
        上传结果
    """
    # 验证文件类型
    allowed_types = ["image/jpeg", "image/png", "image/webp"]
    if file.content_type not in allowed_types:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件类型：{file.content_type}"
        )

    # 读取文件
    content = await file.read()

    # 验证文件大小（最大10MB）
    MAX_SIZE = 10 * 1024 * 1024
    if len(content) > MAX_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"文件过大：{len(content)} 字节（最大10MB）"
        )

    # 预处理图片
    processed_result = None
    if preprocess:
        try:
            preprocessor = get_image_preprocessor()
            processed_result = await preprocessor.process(content)

            if processed_result["success"]:
                content = processed_result["data"]
                logger.info(
                    f"图片预处理完成: {processed_result['original_format']} → {processed_result['format']}, "
                    f"压缩率: {processed_result['compression_ratio']}"
                )
            else:
                logger.warning(f"图片预处理失败，使用原始图片: {processed_result.get('error')}")
        except Exception as e:
            logger.warning(f"图片预处理跳过: {e}")

    # TODO: 实现图片处理
    # 1. 图片向量化（CLIP）
    # 2. 存储到Milvus
    # 3. 保存到对象存储（MinIO）

    return {
        "success": True,
        "message": "图片上传成功",
        "data": {
            "filename": file.filename,
            "size": len(content),
            "content_type": file.content_type,
            "product_id": product_id,
            "preprocessed": processed_result is not None and processed_result.get("success"),
            "preprocess_info": processed_result if processed_result and processed_result.get("success") else None
        }
    }


@router.post("/images/batch")
async def upload_images_batch(
    files: List[UploadFile] = File(..., description="图片文件列表")
):
    """
    批量上传图片

    Args:
        files: 图片文件列表

    Returns:
        上传结果
    """
    results = []

    for file in files:
        try:
            # 处理单个文件
            content = await file.read()

            results.append({
                "filename": file.filename,
                "success": True,
                "size": len(content)
            })

        except Exception as e:
            logger.error(f"文件上传失败 {file.filename}: {e}")
            results.append({
                "filename": file.filename,
                "success": False,
                "error": str(e)
            })

    return {
        "success": True,
        "message": f"上传完成：{len([r for r in results if r['success']])}/{len(files)} 成功",
        "data": results
    }
