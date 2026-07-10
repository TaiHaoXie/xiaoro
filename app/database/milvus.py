"""
Milvus 向量数据库连接模块

提供Milvus客户端管理和向量操作
"""

from pymilvus import MilvusClient, connections, Collection
from pymilvus.milvus_client.index import IndexParams
from typing import List, Dict, Any, Optional
import logging

from app.config import settings

logger = logging.getLogger(__name__)


# ==================== Milvus客户端管理 ====================

class MilvusManager:
    """
    Milvus管理器

    封装Milvus的常用操作，支持连接重试
    """

    def __init__(self, max_retries: int = 3):
        """初始化Milvus客户端"""
        self.max_retries = max_retries
        self._client = None
        self._connect()

    def _connect(self):
        """建立Milvus连接（支持重试）"""
        for attempt in range(self.max_retries):
            try:
                self._client = MilvusClient(
                    uri=settings.milvus_uri,
                    token=settings.MILVUS_TOKEN
                )
                # 测试连接
                self._client.list_collections()
                logger.info(f"✅ Milvus客户端已连接: {settings.MILVUS_HOST}:{settings.MILVUS_PORT}")
                return
            except Exception as e:
                if attempt < self.max_retries - 1:
                    logger.warning(f"Milvus连接失败(尝试{attempt+1}/{self.max_retries}): {e}")
                    import time
                    time.sleep(2 ** attempt)  # 指数退避
                else:
                    logger.error(f"❌ Milvus连接失败: {e}")
                    raise

    @property
    def client(self):
        """获取客户端（支持重连）"""
        if self._client is None:
            self._connect()
        return self._client

    # ==================== Collection管理 ====================

    def list_collections(self) -> List[str]:
        """
        列出所有Collection

        Returns:
            Collection名称列表
        """
        return self.client.list_collections()

    def collection_exists(self, collection_name: str) -> bool:
        """
        检查Collection是否存在

        Args:
            collection_name: Collection名称

        Returns:
            是否存在
        """
        return self.client.has_collection(collection_name)

    def create_collection(
        self,
        collection_name: str,
        dimension: int,
        id_type: str = "int",  # pymilvus 2.4+ 使用 "int" 或 "varchar"
        vector_field_name: str = "vector",
        auto_id: bool = True
    ) -> bool:
        """
        创建Collection

        Args:
            collection_name: Collection名称
            dimension: 向量维度
            id_type: ID类型（"int" 或 "varchar"）
            vector_field_name: 向量字段名称
            auto_id: 是否自动生成ID

        Returns:
            是否创建成功
        """
        if self.collection_exists(collection_name):
            logger.warning(f"Collection已存在: {collection_name}")
            return False

        self.client.create_collection(
            collection_name=collection_name,
            dimension=dimension,
            id_type=id_type,
            vector_field_name=vector_field_name,
            auto_id=auto_id
        )

        # 创建索引（HNSW算法，高性能）。高层 create_collection 可能已建 AUTOINDEX，做幂等保护
        try:
            index_params = IndexParams()
            index_params.add_index(
                field_name=vector_field_name,
                index_type="HNSW",
                metric_type="COSINE",
                params={
                    "M": 16,
                    "efConstruction": 256
                }
            )
            self.client.create_index(
                collection_name=collection_name,
                index_params=index_params
            )
        except Exception as e:
            # 如已存在 AUTOINDEX / 其他索引则跳过
            existing = self.client.list_indexes(collection_name)
            logger.info(f"ℹ️  索引创建跳过（已有索引={existing}）: {type(e).__name__}")

        logger.info(f"✅ Collection创建成功: {collection_name}")
        return True

    def drop_collection(self, collection_name: str) -> bool:
        """
        删除Collection（慎用！）

        Args:
            collection_name: Collection名称

        Returns:
            是否删除成功
        """
        if not self.collection_exists(collection_name):
            logger.warning(f"Collection不存在: {collection_name}")
            return False

        self.client.drop_collection(collection_name)
        logger.info(f"✅ Collection已删除: {collection_name}")
        return True

    # ==================== 向量插入 ====================

    def insert_vectors(
        self,
        collection_name: str,
        vectors: List[List[float]],
        metadata: Optional[List[Dict]] = None
    ) -> List[int]:
        """
        插入向量数据

        Args:
            collection_name: Collection名称
            vectors: 向量列表
            metadata: 元数据列表（每个向量对应一个字典）

        Returns:
            插入的ID列表

        Example:
            manager = MilvusManager()
            ids = manager.insert_vectors(
                collection_name="product_vectors",
                vectors=[[0.1, 0.2, ...], [0.3, 0.4, ...]],
                metadata=[
                    {"product_id": 1, "category": "耳机"},
                    {"product_id": 2, "category": "音箱"}
                ]
            )
        """
        # 准备数据
        data = []
        for i, vector in enumerate(vectors):
            row = {"vector": vector}
            if metadata and i < len(metadata):
                row.update(metadata[i])
            data.append(row)

        # 插入数据
        result = self.client.insert(collection_name, data)

        # 刷新数据（确保可搜索）—— 部分pymilvus版本MilvusClient无flush，做兼容
        try:
            self.client.flush(collection_name)
        except (AttributeError, Exception):
            try:
                from pymilvus import Collection, connections
                if connections.has_connection("default"):
                    Collection(collection_name).flush()
            except Exception:
                pass

        return result["ids"]

    # ==================== 向量搜索 ====================

    def search_vectors(
        self,
        collection_name: str,
        query_vector: List[float],
        limit: int = 10,
        output_fields: Optional[List[str]] = None,
        filter_expression: Optional[str] = None
    ) -> List[Dict]:
        """
        向量搜索（相似度检索）

        Args:
            collection_name: Collection名称
            query_vector: 查询向量
            limit: 返回结果数量
            output_fields: 需要返回的字段列表
            filter_expression: 过滤表达式（如 "price < 500"）

        Returns:
            搜索结果列表，每个元素包含：
            - id: 向量ID
            - distance: 相似度距离（越小越相似）
            - entity: 完整实体数据

        Example:
            results = manager.search_vectors(
                collection_name="product_vectors",
                query_vector=[0.1, 0.2, ...],
                limit=5,
                output_fields=["product_id", "category"]
            )
        """
        results = self.client.search(
            collection_name=collection_name,
            data=[query_vector],  # 支持批量查询
            limit=limit,
            output_fields=output_fields or [],
            filter=filter_expression
        )

        # results[0] 是第一个查询的结果
        return results[0] if results else []

    # ==================== 向量删除 ====================

    def delete_vectors(
        self,
        collection_name: str,
        ids: List[int]
    ) -> bool:
        """
        删除向量

        Args:
            collection_name: Collection名称
            ids: 要删除的ID列表

        Returns:
            是否删除成功
        """
        self.client.delete(collection_name, ids)
        logger.info(f"✅ 已删除 {len(ids)} 个向量")
        return True


# ==================== 全局实例 ====================

_milvus_manager: Optional[MilvusManager] = None


def get_milvus_manager() -> MilvusManager:
    """
    获取Milvus管理器单例

    Returns:
        MilvusManager实例
    """
    global _milvus_manager
    if _milvus_manager is None:
        _milvus_manager = MilvusManager()
    return _milvus_manager


# ==================== 初始化Collections ====================

def init_collections():
    """
    初始化所有需要的Collections

    在应用启动时调用
    """
    manager = get_milvus_manager()

    # 定义需要创建的Collections
    collections = [
        {
            "name": "product_description_vectors",
            "dimension": 1024,  # 根据embedding模型调整
            "description": "商品描述向量"
        },
        {
            "name": "product_image_vectors",
            "dimension": 512,  # CLIP默认512维
            "description": "商品图片向量"
        },
        {
            "name": "review_vectors",
            "dimension": 1024,
            "description": "用户评价向量"
        },
        {
            "name": "knowledge_vectors",
            "dimension": 1024,
            "description": "知识库向量"
        }
    ]

    for collection_config in collections:
        name = collection_config["name"]
        dim = collection_config["dimension"]

        if not manager.collection_exists(name):
            manager.create_collection(name, dimension=dim)
            logger.info(f"✅ Collection创建成功: {name}")
        else:
            logger.info(f"ℹ️  Collection已存在: {name}")


# ==================== 使用示例 ====================

"""
# 在main.py的lifespan中初始化
from app.database.milvus import init_collections

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时
    init_collections()
    yield


# 在其他模块中使用
from app.database.milvus import get_milvus_manager

manager = get_milvus_manager()

# 搜索相似商品
results = manager.search_vectors(
    collection_name="product_description_vectors",
    query_vector=[0.1, 0.2, ...],  # 用户查询的embedding
    limit=10,
    output_fields=["product_id", "category", "brand"]
)

for result in results:
    print(f"Product ID: {result['entity']['product_id']}")
    print(f"Similarity: {result['distance']}")
"""
