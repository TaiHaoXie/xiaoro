"""
图片处理Celery任务

批量处理图片索引、向量化等耗时操作
"""

from celery import Task
from typing import Dict, Any, List
import logging
import asyncio
from datetime import datetime

logger = logging.getLogger(__name__)


# ==================== 基础任务类 ====================

class ImageTask(Task):
    """图片处理任务基类"""

    def __init__(self):
        super().__init__()
        self._image_vector_service = None
        self._storage_service = None

    @property
    def image_vector_service(self):
        """懒加载图片向量服务"""
        if self._image_vector_service is None:
            from app.services.image_vector import get_image_vector_service
            self._image_vector_service = get_image_vector_service()
        return self._image_vector_service

    @property
    def storage_service(self):
        """懒加载存储服务"""
        if self._storage_service is None:
            from app.services.storage import get_minio_service
            self._storage_service = get_minio_service()
        return self._storage_service


# ==================== 批量图片索引任务 ====================

def batch_index_product_images(
    product_ids: List[int],
    overwrite: bool = False
) -> Dict[str, Any]:
    """
    批量为商品建立图片向量索引

    Args:
        product_ids: 商品ID列表
        overwrite: 是否覆盖已有索引

    Returns:
        索引结果统计
    """
    from app.database.postgres import execute_query

    results = {
        "total": len(product_ids),
        "success": 0,
        "failed": 0,
        "skipped": 0,
        "details": []
    }

    logger.info(f"开始批量索引商品图片: {len(product_ids)}个商品")

    for product_id in product_ids:
        try:
            # 1. 检查商品是否存在
            product = asyncio.run(execute_query(
                "SELECT * FROM products WHERE id = $1",
                product_id,
                fetch="one"
            ))

            if not product:
                results["failed"] += 1
                results["details"].append({
                    "product_id": product_id,
                    "status": "failed",
                    "error": "商品不存在"
                })
                continue

            # 2. 检查是否已有索引
            if not overwrite:
                is_indexed = asyncio.run(
                    get_image_vector_service().is_product_indexed(product_id)
                )
                if is_indexed:
                    results["skipped"] += 1
                    results["details"].append({
                        "product_id": product_id,
                        "status": "skipped",
                        "message": "已有索引，跳过"
                    })
                    continue

            # 3. 下载/获取图片
            image_data = None

            # 方式1: 从MinIO/存储下载
            try:
                image_data = asyncio.run(
                    get_storage_service().download_product_image(product_id)
                )
            except Exception as e:
                logger.debug(f"从存储下载失败: {e}")

            # 方式2: 如果没有存储图片，使用image_url字段
            if not image_data and product.get("image_url"):
                import httpx
                try:
                    # 处理相对URL
                    url = product["image_url"]
                    if url.startswith("/"):
                        # 开发环境使用本地路径
                        # 尝试从本地文件系统读取
                        from pathlib import Path
                        local_path = Path(__file__).parent.parent.parent / "static" / url.lstrip("/")
                        if local_path.exists():
                            image_data = local_path.read_bytes()
                    else:
                        # 下载远程图片
                        async def download_remote():
                            async with httpx.AsyncClient(timeout=10.0) as client:
                                response = await client.get(url)
                                return response.content
                        image_data = asyncio.run(download_remote())
                except Exception as e:
                    logger.warning(f"下载图片失败 {product_id}: {e}")

            # 方式3: 使用占位图片（开发测试用）
            if not image_data:
                logger.warning(f"商品 {product_id} 无图片，使用占位图")
                # 创建一个简单的占位图
                from PIL import Image
                import io
                img = Image.new('RGB', (224, 224), color=(212, 188, 200))  # Morandi色
                output = io.BytesIO()
                img.save(output, format='JPEG')
                image_data = output.getvalue()

            # 4. 建立向量索引
            from app.services.image_vector import get_image_vector_service
            from app.services.storage import get_storageService

            image_vector_service = get_image_vector_service()
            success = asyncio.run(image_vector_service.index_product_image(
                product_id=product_id,
                image_data=image_data
            ))

            if success:
                results["success"] += 1
                results["details"].append({
                    "product_id": product_id,
                    "status": "success",
                    "message": "索引成功"
                })
            else:
                results["failed"] += 1
                results["details"].append({
                    "product_id": product_id,
                    "status": "failed",
                    "error": "索引失败"
                })

        except Exception as e:
            logger.error(f"索引失败 {product_id}: {e}")
            results["failed"] += 1
            results["details"].append({
                "product_id": product_id,
                "status": "failed",
                "error": str(e)
            })

    logger.info(f"批量索引完成: 成功{results['success']}, 跳过{results['skipped']}, 失败{results['failed']}")

    return results


# ==================== 全量索引任务 ====================

def index_all_products(
    batch_size: int = 50,
    overwrite: bool = False
) -> Dict[str, Any]:
    """
    为所有商品建立图片索引

    Args:
        batch_size: 每批处理数量
        overwrite: 是否覆盖已有索引

    Returns:
        索引结果统计
    """
    from app.database.postgres import execute_query

    logger.info("开始全量商品图片索引")

    # 获取所有商品ID
    products = asyncio.run(execute_query(
        "SELECT id FROM products ORDER BY id",
        fetch="all"
    ))

    product_ids = [p["id"] for p in products]

    # 分批处理
    all_results = {
        "total": len(product_ids),
        "success": 0,
        "failed": 0,
        "skipped": 0,
        "batches": len(product_ids) // batch_size + 1
    }

    for i in range(0, len(product_ids), batch_size):
        batch = product_ids[i:i+batch_size]
        logger.info(f"处理批次 {i//batch_size + 1}/{all_results['batches']}")

        batch_result = batch_index_product_images(batch, overwrite)

        all_results["success"] += batch_result["success"]
        all_results["failed"] += batch_result["failed"]
        all_results["skipped"] += batch_result["skipped"]

    logger.info(f"全量索引完成: 成功{all_results['success']}, 跳过{all_results['skipped']}, 失败{all_results['failed']}")

    return all_results


# ==================== 辅助函数 ====================

def get_image_vector_service():
    """获取图片向量服务"""
    from app.services.image_vector import get_image_vector_service
    return get_image_vector_service()


def get_storage_service():
    """获取存储服务"""
    from app.services.storage import get_minio_service
    return get_minio_service()
