"""
知识库服务模块

管理电商领域知识库
支持文档的增删改查和向量化检索
"""

from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
import logging
import json
import re

from app.services.embedding import get_embedding_service
from app.database.postgres import execute_query
from app.config import settings

logger = logging.getLogger(__name__)


class KnowledgeBaseService:
    """
    知识库服务

    管理产品说明、使用指南、FAQ等知识
    """

    # 知识类型
    TYPE_PRODUCT_DESC = "product_desc"      # 商品描述
    TYPE_USER_GUIDE = "user_guide"          # 使用指南
    TYPE_FAQ = "faq"                        # 常见问题
    TYPE_REVIEW = "review"                  # 用户评价
    TYPE_COMPARISON = "comparison"          # 商品对比

    def __init__(self):
        """初始化知识库服务"""
        self.embedding_service = get_embedding_service()
        logger.info("✅ 知识库服务初始化成功")

    def _extract_terms(self, text: str) -> List[str]:
        """提取轻量检索词，增强本地 embedding 模式下的知识召回稳定性。"""
        normalized = (text or "").lower()
        ascii_terms = re.findall(r"[a-z0-9]+", normalized)
        cjk_chars = re.findall(r"[\u4e00-\u9fff]", normalized)
        cjk_bigrams = [
            "".join(cjk_chars[i:i + 2])
            for i in range(len(cjk_chars) - 1)
        ]
        return ascii_terms + cjk_chars + cjk_bigrams

    async def add_knowledge(
        self,
        title: str,
        content: str,
        knowledge_type: str,
        metadata: Optional[Dict] = None,
        product_id: Optional[int] = None
    ) -> int:
        """
        添加知识条目

        Args:
            title: 标题
            content: 内容
            knowledge_type: 知识类型
            metadata: 额外元数据
            product_id: 关联商品ID

        Returns:
            知识条目ID
        """
        try:
            # 1. 保存到数据库
            knowledge_id = await execute_query(
                """INSERT INTO knowledge_base
                   (title, content, type, product_id, metadata, created_at)
                   VALUES ($1, $2, $3, $4, $5, NOW())
                   RETURNING id""",
                title,
                content,
                knowledge_type,
                product_id,
                json.dumps(metadata) if metadata else None,
                fetch="one"
            )

            knowledge_id = knowledge_id["id"]
            logger.info(f"✅ 知识条目已保存: {title} (ID: {knowledge_id})")

            # 2. 向量化并存储到 Milvus（可选）
            try:
                vector = await self.embedding_service.encode(content)
                # TODO: 存储到 Milvus
                logger.debug(f"向量已生成: {len(vector)}维")
            except Exception as e:
                logger.warning(f"向量化失败: {e}")

            return knowledge_id

        except Exception as e:
            logger.error(f"❌ 添加知识失败: {e}")
            raise e

    async def search_knowledge(
        self,
        query: str,
        knowledge_type: Optional[str] = None,
        product_id: Optional[int] = None,
        top_k: int = 5,
        min_score: float = 0.18
    ) -> List[Dict[str, Any]]:
        """
        搜索知识库

        Args:
            query: 查询文本
            knowledge_type: 知识类型过滤
            product_id: 商品ID过滤
            top_k: 返回数量
            min_score: 最低相关度

        Returns:
            匹配的知识条目列表
        """
        try:
            # 1. 查询向量编码
            query_vector = await self.embedding_service.encode(query)

            # 2. 构建SQL查询
            sql = "SELECT * FROM knowledge_base WHERE 1=1"
            params = []

            if knowledge_type:
                sql += " AND type = $1"
                params.append(knowledge_type)

            if product_id:
                sql += f" AND product_id = ${len(params) + 1}"
                params.append(product_id)

            sql += " ORDER BY created_at DESC LIMIT 100"

            # 3. 执行查询
            knowledge_items = await execute_query(sql, *params, fetch="all")

            if not knowledge_items:
                return []

            # 4. 计算相似度
            query_terms = set(self._extract_terms(query))
            results = []
            for item in knowledge_items:
                # 简单计算：标题和内容的匹配度
                item_text = f"{item['title']} {item['content']}"
                item_vector = await self.embedding_service.encode(item_text)
                item_terms = set(self._extract_terms(item_text))

                # 余弦相似度
                similarity = sum(a * b for a, b in zip(query_vector, item_vector))
                lexical_overlap = len(query_terms & item_terms)
                lexical_score = min(0.55, lexical_overlap * 0.08)
                combined_score = similarity + lexical_score

                if combined_score >= min_score:
                    results.append({
                        "id": item["id"],
                        "title": item["title"],
                        "content": item["content"],
                        "type": item["type"],
                        "product_id": item["product_id"],
                        "similarity": round(combined_score * 100, 2),
                        "embedding_similarity": round(similarity * 100, 2),
                        "lexical_overlap": lexical_overlap,
                        "metadata": json.loads(item["metadata"]) if item["metadata"] else {}
                    })

            # 5. 排序并返回
            results.sort(key=lambda x: x["similarity"], reverse=True)

            return results[:top_k]

        except Exception as e:
            logger.error(f"❌ 知识搜索失败: {e}")
            return []

    async def get_product_knowledge(
        self,
        product_id: int,
        knowledge_type: Optional[str] = None
    ) -> List[Dict]:
        """
        获取商品相关知识

        Args:
            product_id: 商品ID
            knowledge_type: 知识类型（可选）

        Returns:
            知识条目列表
        """
        sql = "SELECT * FROM knowledge_base WHERE product_id = $1"
        params = [product_id]

        if knowledge_type:
            sql += " AND type = $2"
            params.append(knowledge_type)

        sql += " ORDER BY created_at DESC"

        results = await execute_query(sql, *params, fetch="all")

        return [
            {
                "id": r["id"],
                "title": r["title"],
                "content": r["content"],
                "type": r["type"],
                "metadata": json.loads(r["metadata"]) if r["metadata"] else {}
            }
            for r in results
        ]

    async def get_faq(
        self,
        category: Optional[str] = None
    ) -> List[Dict]:
        """
        获取常见问题

        Args:
            category: 问题分类

        Returns:
            FAQ列表
        """
        sql = "SELECT * FROM knowledge_base WHERE type = 'faq'"
        params = []

        if category:
            sql += " AND metadata->>'category' = $1"
            params.append(category)

        sql += " ORDER BY created_at DESC"

        results = await execute_query(sql, *params, fetch="all")

        return [
            {
                "id": r["id"],
                "question": r["title"],
                "answer": r["content"],
                "metadata": json.loads(r["metadata"]) if r["metadata"] else {}
            }
            for r in results
        ]

    async def delete_knowledge(self, knowledge_id: int) -> bool:
        """
        删除知识条目

        Args:
            knowledge_id: 知识ID

        Returns:
            是否成功
        """
        try:
            await execute_query(
                "DELETE FROM knowledge_base WHERE id = $1",
                knowledge_id,
                fetch="none"
            )
            logger.info(f"✅ 知识条目已删除: {knowledge_id}")
            return True
        except Exception as e:
            logger.error(f"❌ 删除知识失败: {e}")
            return False


# ==================== 全局实例 ====================

_knowledge_service: Optional[KnowledgeBaseService] = None


def get_knowledge_service() -> KnowledgeBaseService:
    """
    获取知识库服务单例

    Returns:
        KnowledgeBaseService实例
    """
    global _knowledge_service
    if _knowledge_service is None:
        _knowledge_service = KnowledgeBaseService()
    return _knowledge_service


# ==================== 使用示例 ====================

"""
from app.services.knowledge_base import get_knowledge_service

service = get_knowledge_service()

# 添加知识
await service.add_knowledge(
    title="iPhone 15 Pro 使用技巧",
    content="iPhone 15 Pro 支持钛金属边框...",
    knowledge_type="user_guide",
    product_id=1
)

# 搜索知识
results = await service.search_knowledge(
    query="iPhone 15 怎么使用灵动岛",
    top_k=3
)

# 获取FAQ
faqs = await service.get_faq(category="支付")
"""
