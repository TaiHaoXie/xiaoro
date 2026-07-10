"""
图片向量存储服务

集成 CLIP 图片嵌入和 Milvus 向量数据库
实现以图搜图功能
"""

from typing import List, Dict, Any, Optional, Union
from pathlib import Path
import logging
import io

from app.services.image_embedding import get_image_embedding_service, ImageEmbeddingService
from app.database.milvus import get_milvus_manager, MilvusManager
from app.database.postgres import execute_query

logger = logging.getLogger(__name__)


class ImageVectorService:
    """
    图片向量存储服务

    功能：
    1. 存储图片向量到 Milvus
    2. 向量相似度搜索
    3. 图片索引管理
    """

    # Collection 名称
    COLLECTION_IMAGES = "product_image_vectors"

    def __init__(self):
        """初始化服务"""
        self.embedding_service = get_image_embedding_service()
        self.milvus_manager = get_milvus_manager()

        # 确保Collection存在
        self._ensure_collection_exists()

        logger.info("✅ 图片向量服务初始化成功")

    def _ensure_collection_exists(self):
        """确保Milvus Collection存在"""
        try:
            # CLIP ViT-B-32 产生 512 维向量
            dimension = self.embedding_service.dimension

            if not self.milvus_manager.collection_exists(self.COLLECTION_IMAGES):
                self.milvus_manager.create_collection(
                    collection_name=self.COLLECTION_IMAGES,
                    dimension=dimension,
                    id_type="int",  # pymilvus 2.4+ 使用 "int" 而非 "int64"
                    vector_field_name="vector",
                    auto_id=True
                )
                logger.info(f"✅ 创建图片向量Collection: {self.COLLECTION_IMAGES} (维度: {dimension})")
            else:
                logger.info(f"ℹ️  Collection已存在: {self.COLLECTION_IMAGES}")

        except Exception as e:
            logger.error(f"❌ Collection初始化失败: {e}")
            raise e

    async def index_product_image(
        self,
        product_id: int,
        image_data: bytes,
        metadata: Optional[Dict] = None
    ) -> bool:
        """
        为商品图片建立索引

        Args:
            product_id: 商品ID
            image_data: 图片二进制数据
            metadata: 额外元数据

        Returns:
            是否成功
        """
        try:
            # 1. 获取商品信息
            product = await execute_query(
                "SELECT * FROM products WHERE id = $1",
                product_id,
                fetch="one"
            )

            if not product:
                logger.error(f"商品不存在: {product_id}")
                return False

            # 2. 图片向量化
            vector = await self.embedding_service.encode_image(image_data)

            # 3. 构建元数据
            entity = {
                "product_id": product_id,
                "name": product.get("name", ""),
                "brand": product.get("brand", ""),
                "category": product.get("category", ""),
                "price": float(product.get("price", 0))
            }

            if metadata:
                entity.update(metadata)

            # 4. 插入 Milvus
            self.milvus_manager.insert_vectors(
                collection_name=self.COLLECTION_IMAGES,
                vectors=[vector],
                metadata=[entity]
            )

            logger.info(f"✅ 商品图片已索引: {product['name']} (ID: {product_id})")
            return True

        except Exception as e:
            logger.error(f"❌ 图片索引失败: {e}")
            return False

    async def batch_index_products(
        self,
        product_images: List[Dict[str, Any]],
        overwrite: bool = False
    ) -> Dict[str, int]:
        """
        批量索引商品图片

        Args:
            product_images: 商品图片列表
                [{"product_id": 1, "image_data": bytes}, ...]
            overwrite: 是否覆盖已有向量

        Returns:
            统计结果
        """
        results = {
            "total": len(product_images),
            "success": 0,
            "failed": 0,
            "skipped": 0
        }

        for item in product_images:
            try:
                product_id = item["product_id"]
                image_data = item["image_data"]

                # 检查是否已索引
                if not overwrite:
                    is_indexed = await self.is_product_indexed(product_id)
                    if is_indexed:
                        results["skipped"] += 1
                        continue

                success = await self.index_product_image(
                    product_id=product_id,
                    image_data=image_data,
                    metadata=item.get("metadata")
                )

                if success:
                    results["success"] += 1
                else:
                    results["failed"] += 1

            except Exception as e:
                logger.error(f"批量索引失败 {item.get('product_id')}: {e}")
                results["failed"] += 1

        logger.info(f"✅ 批量索引完成: {results}")
        return results

    async def search_similar_images(
        self,
        query_image: bytes,
        top_k: int = 10,
        min_score: float = 0.5,
        filters: Optional[Dict] = None
    ) -> List[Dict[str, Any]]:
        """
        搜索相似图片

        Args:
            query_image: 查询图片（二进制）
            top_k: 返回数量
            min_score: 最低相似度分数
            filters: 过滤条件 {"category": "护肤品", "brand": "兰蔻"}

        Returns:
            相似商品列表
        """
        try:
            # 1. 查询图片向量化
            query_vector = await self.embedding_service.encode_image(query_image)

            # 2. 构建过滤表达式
            filter_expr = None
            if filters:
                conditions = []
                for key, value in filters.items():
                    if isinstance(value, str):
                        conditions.append(f'{key} == "{value}"')
                    else:
                        conditions.append(f'{key} == {value}')
                if conditions:
                    filter_expr = " and ".join(conditions)

            # 3. Milvus 向量搜索
            search_results = self.milvus_manager.search_vectors(
                collection_name=self.COLLECTION_IMAGES,
                query_vector=query_vector,
                limit=top_k * 2,  # 多召回一些，后续过滤
                output_fields=["product_id", "name", "brand", "category", "price"],
                filter_expression=filter_expr
            )

            # 4. 转换结果
            # 注意：Milvus metric_type=COSINE 时，distance直接返回cosine相似度（[-1, 1]，1=完全相同）
            results = []
            for result in search_results:
                distance = result.get("distance", 0.0)
                similarity = max(0.0, float(distance))

                if similarity < min_score:
                    continue

                entity = result.get("entity", {})

                results.append({
                    "product_id": entity.get("product_id"),
                    "name": entity.get("name", ""),
                    "brand": entity.get("brand", ""),
                    "category": entity.get("category", ""),
                    "price": entity.get("price", 0),
                    "similarity": round(similarity * 100, 2),
                    "distance": round(distance, 4)
                })

            # 5. 补充商品详情
            if results:
                product_ids = [r["product_id"] for r in results if r["product_id"]]
                if product_ids:
                    products = await execute_query(
                        "SELECT * FROM products WHERE id = ANY($1)",
                        product_ids,
                        fetch="all"
                    )

                    product_map = {p["id"]: p for p in products}

                    # 合并详情
                    for result in results:
                        pid = result["product_id"]
                        if pid and pid in product_map:
                            product = product_map[pid]
                            result.update({
                                "id": product["id"],
                                "description": product.get("description", ""),
                                "image_url": product.get("image_url", f"/images/{pid}.jpg"),
                                "original_price": product.get("original_price")
                            })

            # 按相似度排序
            results.sort(key=lambda x: x["similarity"], reverse=True)

            return results[:top_k]

        except Exception as e:
            logger.error(f"❌ 图片搜索失败: {e}")
            return []

    async def search_by_text(
        self,
        text_query: str,
        top_k: int = 10,
        min_score: float = 0.5,
        filters: Optional[Dict] = None
    ) -> List[Dict[str, Any]]:
        """
        用文本描述搜索相似图片

        Args:
            text_query: 文本描述
            top_k: 返回数量
            min_score: 最低相似度
            filters: 过滤条件

        Returns:
            匹配的商品列表
        """
        try:
            # 1. 文本向量化
            text_vector = await self.embedding_service.encode_text(text_query)

            # 2. Milvus 搜索
            search_results = self.milvus_manager.search_vectors(
                collection_name=self.COLLECTION_IMAGES,
                query_vector=text_vector,
                limit=top_k * 2,
                output_fields=["product_id", "name", "brand", "category", "price"],
                filter_expression=self._build_filter(filters) if filters else None
            )

            # 3. 转换结果（COSINE metric：distance即cosine相似度）
            results = []
            for result in search_results:
                distance = result.get("distance", 0.0)
                similarity = max(0.0, float(distance))

                if similarity < min_score:
                    continue

                entity = result.get("entity", {})
                results.append({
                    "product_id": entity.get("product_id"),
                    "name": entity.get("name", ""),
                    "brand": entity.get("brand", ""),
                    "category": entity.get("category", ""),
                    "price": entity.get("price", 0),
                    "similarity": round(similarity * 100, 2)
                })

            # 4. 补充详情
            if results:
                product_ids = [r["product_id"] for r in results if r["product_id"]]
                if product_ids:
                    products = await execute_query(
                        "SELECT * FROM products WHERE id = ANY($1)",
                        product_ids,
                        fetch="all"
                    )

                    product_map = {p["id"]: p for p in products}

                    for result in results:
                        pid = result["product_id"]
                        if pid and pid in product_map:
                            product = product_map[pid]
                            result.update({
                                "id": product["id"],
                                "description": product.get("description", ""),
                                "image_url": product.get("image_url", f"/images/{pid}.jpg")
                            })

            return results[:top_k]

        except Exception as e:
            logger.error(f"❌ 文本搜图失败: {e}")
            return []

    async def compute_image_text_similarity(
        self,
        image_data: bytes,
        text: str
    ) -> float:
        """
        计算图片和文本的相似度

        Args:
            image_data: 图片数据
            text: 文本描述

        Returns:
            相似度分数 (0-1)
        """
        try:
            return await self.embedding_service.compute_image_text_similarity(
                image=image_data,
                text=text
            )
        except Exception as e:
            logger.error(f"❌ 相似度计算失败: {e}")
            return 0.0

    async def is_product_indexed(self, product_id: int) -> bool:
        """
        检查商品是否已建立图片索引

        Args:
            product_id: 商品ID

        Returns:
            是否已索引
        """
        try:
            # 使用 Milvus 查询
            results = self.milvus_manager.search_vectors(
                collection_name=self.COLLECTION_IMAGES,
                query_vector=[0.0] * self.embedding_service.dimension,  # 哨兵向量
                limit=1,
                output_fields=["product_id"],
                filter_expression=f"product_id == {product_id}"
            )

            return len(results) > 0

        except Exception as e:
            logger.warning(f"索引检查失败: {e}")
            return False

    async def delete_product_index(self, product_id: int) -> bool:
        """
        删除商品的图片索引

        Args:
            product_id: 商品ID

        Returns:
            是否成功
        """
        try:
            # 先查询获取向量ID
            results = self.milvus_manager.search_vectors(
                collection_name=self.COLLECTION_IMAGES,
                query_vector=[0.0] * self.embedding_service.dimension,
                limit=100,
                output_fields=["product_id"],
                filter_expression=f"product_id == {product_id}"
            )

            if results:
                # 删除找到的向量
                ids_to_delete = [r.get("id") for r in results if r.get("id")]
                if ids_to_delete:
                    self.milvus_manager.delete_vectors(
                        collection_name=self.COLLECTION_IMAGES,
                        ids=ids_to_delete
                    )
                    logger.info(f"✅ 已删除商品图片索引: {product_id}")
                    return True

            return False

        except Exception as e:
            logger.error(f"❌ 删除索引失败: {e}")
            return False

    def _build_filter(self, filters: Dict) -> Optional[str]:
        """构建 Milvus 过滤表达式"""
        if not filters:
            return None

        conditions = []
        for key, value in filters.items():
            if isinstance(value, str):
                conditions.append(f'{key} == "{value}"')
            else:
                conditions.append(f'{key} == {value}')

        return " and ".join(conditions) if conditions else None

    async def get_collection_stats(self) -> Dict[str, Any]:
        """
        获取 Collection 统计信息

        Returns:
            统计数据
        """
        try:
            # 简单统计
            stats = {
                "collection_name": self.COLLECTION_IMAGES,
                "dimension": self.embedding_service.dimension,
                "exists": self.milvus_manager.collection_exists(self.COLLECTION_IMAGES)
            }

            # TODO: 获取实际向量数量（需要 Milvus stats API）

            return stats

        except Exception as e:
            logger.error(f"❌ 获取统计信息失败: {e}")
            return {}


# ==================== 全局实例 ====================

_image_vector_service: Optional[ImageVectorService] = None


def get_image_vector_service() -> ImageVectorService:
    """
    获取图片向量服务单例

    Returns:
        ImageVectorService实例
    """
    global _image_vector_service
    if _image_vector_service is None:
        _image_vector_service = ImageVectorService()
    return _image_vector_service


# ==================== 使用示例 ====================

"""
from app.services.image_vector import get_image_vector_service

service = get_image_vector_service()

# 索引单张图片
with open("product.jpg", "rb") as f:
    await service.index_product_image(
        product_id=1,
        image_data=f.read()
    )

# 搜索相似图片
with open("query.jpg", "rb") as f:
    results = await service.search_similar_images(
        query_image=f.read(),
        top_k=5,
        min_score=0.6
    )

for r in results:
    print(f"{r['name']} - 相似度: {r['similarity']}%")

# 文本搜图
results = await service.search_by_text(
    text_query="红色口红",
    top_k=5
)
"""
