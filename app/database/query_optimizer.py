"""
数据库查询优化工具

提供批量查询、缓存等优化功能
避免N+1查询问题
"""

from typing import List, Dict, Any, Optional, Union
from functools import lru_cache
import logging

from app.database.postgres import execute_query

logger = logging.getLogger(__name__)


class QueryOptimizer:
    """
    查询优化器

    提供批量查询方法，避免N+1问题
    """

    @staticmethod
    async def batch_get_products_by_ids(
        product_ids: List[int],
        fields: str = "*"
    ) -> List[Dict[str, Any]]:
        """
        批量获取商品（避免N+1）

        Args:
            product_ids: 商品ID列表
            fields: 返回字段

        Returns:
            商品列表
        """
        if not product_ids:
            return []

        # 去重
        unique_ids = list(set(product_ids))

        # 使用 IN 子句批量查询
        placeholders = ",".join([f"${i+1}" for i in range(len(unique_ids))])

        results = await execute_query(
            f"SELECT {fields} FROM products WHERE id IN ({placeholders})",
            *unique_ids,
            fetch="all"
        )

        return results

    @staticmethod
    async def batch_get_products_by_names(
        product_names: List[str],
        fuzzy_match: bool = True,
        limit_per_name: int = 3
    ) -> List[Dict[str, Any]]:
        """
        批量根据名称获取商品（避免N+1）

        Args:
            product_names: 商品名称列表
            fuzzy_match: 是否模糊匹配
            limit_per_name: 每个名称最多返回结果数

        Returns:
            商品列表
        """
        if not product_names:
            return []

        # 去重
        unique_names = list(set(product_names))

        if fuzzy_match:
            # 使用单个查询配合 OR 条件
            like_conditions = []
            args = []

            for name in unique_names[:10]:  # 最多10个名称，防止查询过长
                like_conditions.append(f"name LIKE ${len(args) + 1}")
                args.append(f"%{name}%")

            if like_conditions:
                query = " OR ".join(like_conditions)
                results = await execute_query(
                    f"SELECT DISTINCT * FROM products WHERE {query} ORDER BY id DESC LIMIT 50",
                    *args,
                    fetch="all"
                )
                return results
        else:
            # 精确匹配，使用 IN 子句
            placeholders = ",".join([f"${i+1}" for i in range(len(unique_names))])

            results = await execute_query(
                f"SELECT * FROM products WHERE name IN ({placeholders})",
                *unique_names,
                fetch="all"
            )

            return results

    @staticmethod
    async def batch_get_conversations(
        session_ids: List[str]
    ) -> Dict[str, Dict[str, Any]]:
        """
        批量获取会话（避免N+1）

        Args:
            session_ids: 会话ID列表

        Returns:
            会话字典 {session_id: conversation_data}
        """
        if not session_ids:
            return {}

        unique_ids = list(set(session_ids))
        placeholders = ",".join([f"${i+1}" for i in range(len(unique_ids))])

        results = await execute_query(
            f"SELECT * FROM conversations WHERE session_id IN ({placeholders})",
            *unique_ids,
            fetch="all"
        )

        return {r["session_id"]: r for r in results}

    @staticmethod
    async def get_products_with_filters(
        category: Optional[str] = None,
        brand: Optional[str] = None,
        min_price: Optional[float] = None,
        max_price: Optional[float] = None,
        search_text: Optional[str] = None,
        limit: int = 20,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """
        带筛选条件的商品查询（单次查询）

        Args:
            category: 类别筛选
            brand: 品牌筛选
            min_price: 最低价格
            max_price: 最高价格
            search_text: 搜索文本
            limit: 返回数量
            offset: 偏移量

        Returns:
            商品列表
        """
        conditions = []
        args = []
        arg_count = 0

        if category:
            arg_count += 1
            conditions.append(f"category = ${arg_count}")
            args.append(category)

        if brand:
            arg_count += 1
            conditions.append(f"brand = ${arg_count}")
            args.append(brand)

        if min_price is not None:
            arg_count += 1
            conditions.append(f"price >= ${arg_count}")
            args.append(min_price)

        if max_price is not None:
            arg_count += 1
            conditions.append(f"price <= ${arg_count}")
            args.append(max_price)

        if search_text:
            arg_count += 1
            conditions.append(f"(name LIKE ${arg_count} OR description LIKE ${arg_count + 1})")
            args.extend([f"%{search_text}%", f"%{search_text}%"])
            arg_count += 1

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        results = await execute_query(
            f"SELECT * FROM products {where_clause} ORDER BY id DESC LIMIT ${arg_count + 1} OFFSET ${arg_count + 2}",
            *args,
            limit,
            offset,
            fetch="all"
        )

        return results

    @staticmethod
    async def count_products_with_filters(
        category: Optional[str] = None,
        brand: Optional[str] = None,
        min_price: Optional[float] = None,
        max_price: Optional[float] = None,
        search_text: Optional[str] = None
    ) -> int:
        """
        统计符合条件的商品数量

        Args:
            同 get_products_with_filters

        Returns:
            商品数量
        """
        conditions = []
        args = []
        arg_count = 0

        if category:
            arg_count += 1
            conditions.append(f"category = ${arg_count}")
            args.append(category)

        if brand:
            arg_count += 1
            conditions.append(f"brand = ${arg_count}")
            args.append(brand)

        if min_price is not None:
            arg_count += 1
            conditions.append(f"price >= ${arg_count}")
            args.append(min_price)

        if max_price is not None:
            arg_count += 1
            conditions.append(f"price <= ${arg_count}")
            args.append(max_price)

        if search_text:
            arg_count += 1
            conditions.append(f"(name LIKE ${arg_count} OR description LIKE ${arg_count + 1})")
            args.extend([f"%{search_text}%", f"%{search_text}%"])
            arg_count += 1

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        count = await execute_query(
            f"SELECT COUNT(*) FROM products {where_clause}",
            *args,
            fetch="val"
        )

        return count


# ==================== 全局实例 ====================

_query_optimizer: Optional[QueryOptimizer] = None


def get_query_optimizer() -> QueryOptimizer:
    """获取查询优化器单例"""
    global _query_optimizer
    if _query_optimizer is None:
        _query_optimizer = QueryOptimizer()
    return _query_optimizer


# ==================== 使用示例 ====================

"""
# 批量获取商品
optimizer = get_query_optimizer()

# 根据ID批量获取（避免循环查询）
products = await optimizer.batch_get_products_by_ids([1, 2, 3, 4, 5])

# 根据名称批量获取
products = await optimizer.batch_get_products_by_names(["iPhone", "华为", "小米"])

# 带筛选条件查询
products = await optimizer.get_products_with_filters(
    category="手机",
    min_price=1000,
    max_price=5000,
    min_rating=4.0,
    limit=10
)

# 统计数量
count = await optimizer.count_products_with_filters(
    category="手机",
    min_price=1000
)
"""
