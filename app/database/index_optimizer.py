"""
数据库索引优化模块

提供索引创建、分析和优化建议
"""

from typing import List, Dict, Any, Optional
import logging

from app.database.postgres import execute_query

logger = logging.getLogger(__name__)


# ==================== 索引定义 ====================

INDEX_DEFINITIONS = {
    # 商品表索引
    "products": [
        {
            "name": "idx_products_category_price",
            "sql": "CREATE INDEX IF NOT EXISTS idx_products_category_price ON products(category, price DESC);",
            "purpose": "类别+价格组合查询优化"
        },
        {
            "name": "idx_products_brand_category",
            "sql": "CREATE INDEX IF NOT EXISTS idx_products_brand_category ON products(brand, category);",
            "purpose": "品牌+类别组合查询优化"
        },
        {
            "name": "idx_products_sales_rating",
            "sql": "CREATE INDEX IF NOT EXISTS idx_products_sales_rating ON products(sales_count DESC, rating DESC);",
            "purpose": "销量和评分排序优化"
        },
        {
            "name": "idx_products_name_gin",
            "sql": "CREATE INDEX IF NOT EXISTS idx_products_name_gin ON products USING gin(to_tsvector('simple', name));",
            "purpose": "商品名称全文搜索优化"
        },
        {
            "name": "idx_products_name_trgm",
            "sql": "CREATE INDEX IF NOT EXISTS idx_products_name_trgm ON products USING gin(name gin_trgm_ops);",
            "purpose": "商品名称模糊搜索优化（需要pg_trgm扩展）"
        },
    ],
    # 会话表索引
    "conversations": [
        {
            "name": "idx_conversations_user_id",
            "sql": "CREATE INDEX IF NOT EXISTS idx_conversations_user_id ON conversations(user_id);",
            "purpose": "用户会话查询优化"
        },
        {
            "name": "idx_conversations_created_at",
            "sql": "CREATE INDEX IF NOT EXISTS idx_conversations_created_at ON conversations(created_at DESC);",
            "purpose": "按时间查询会话优化"
        },
        {
            "name": "idx_conversations_session_id",
            "sql": "CREATE INDEX IF NOT EXISTS idx_conversations_session_id ON conversations(session_id);",
            "purpose": "会话ID查询优化"
        },
    ],
    # 推荐表索引
    "recommendations": [
        {
            "name": "idx_recommendations_conversation_id",
            "sql": "CREATE INDEX IF NOT EXISTS idx_recommendations_conversation_id ON recommendations(conversation_id);",
            "purpose": "会话推荐查询优化"
        },
        {
            "name": "idx_recommendations_product_ids",
            "sql": "CREATE INDEX IF NOT EXISTS idx_recommendations_product_ids ON recommendations USING GIN(product_ids);",
            "purpose": "推荐商品ID数组查询优化"
        },
        {
            "name": "idx_recommendations_created_at",
            "sql": "CREATE INDEX IF NOT EXISTS idx_recommendations_created_at ON recommendations(created_at DESC);",
            "purpose": "按时间查询推荐优化"
        },
    ],
    # 知识库表索引
    "knowledge_base": [
        {
            "name": "idx_knowledge_type",
            "sql": "CREATE INDEX IF NOT EXISTS idx_knowledge_type ON knowledge_base(type);",
            "purpose": "按类型查询知识优化"
        },
        {
            "name": "idx_knowledge_product_id",
            "sql": "CREATE INDEX IF NOT EXISTS idx_knowledge_product_id ON knowledge_base(product_id);",
            "purpose": "商品相关知识查询优化"
        },
        {
            "name": "idx_knowledge_title_gin",
            "sql": "CREATE INDEX IF NOT EXISTS idx_knowledge_title_gin ON knowledge_base USING gin(to_tsvector('simple', title));",
            "purpose": "知识标题全文搜索优化"
        },
        {
            "name": "idx_knowledge_content_gin",
            "sql": "CREATE INDEX IF NOT EXISTS idx_knowledge_content_gin ON knowledge_base USING gin(to_tsvector('simple', content));",
            "purpose": "知识内容全文搜索优化"
        },
    ],
    # 用户表索引
    "users": [
        {
            "name": "idx_users_username",
            "sql": "CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);",
            "purpose": "用户名查询优化"
        },
        {
            "name": "idx_users_email",
            "sql": "CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);",
            "purpose": "邮箱查询优化"
        },
        {
            "name": "idx_users_active",
            "sql": "CREATE INDEX IF NOT EXISTS idx_users_active ON users(is_active) WHERE is_active = TRUE;",
            "purpose": "活跃用户查询优化（部分索引）"
        },
    ],
}


class IndexOptimizer:
    """
    索引优化器

    创建、分析和管理数据库索引
    """

    async def create_all_indexes(self) -> Dict[str, List[str]]:
        """
        创建所有优化索引

        Returns:
            创建结果 {table: [index_names]}
        """
        results = {}

        for table, indexes in INDEX_DEFINITIONS.items():
            created = []
            for index_def in indexes:
                try:
                    await execute_query(index_def["sql"], fetch="none")
                    created.append(index_def["name"])
                    logger.info(f"✅ 创建索引: {index_def['name']}")
                except Exception as e:
                    logger.warning(f"⚠️  索引创建失败 {index_def['name']}: {e}")

            results[table] = created

        return results

    async def analyze_index_usage(self) -> Dict[str, Any]:
        """
        分析索引使用情况

        Returns:
            索引使用统计
        """
        # 查询所有表的索引使用情况
        query = """
            SELECT
                schemaname,
                tablename,
                indexname,
                idx_scan as index_scans,
                idx_tup_read as tuples_read,
                idx_tup_fetch as tuples_fetched
            FROM pg_stat_user_indexes
            ORDER BY idx_scan ASC
        """

        results = await execute_query(query, fetch="all")

        # 分析未使用的索引
        unused_indexes = [r for r in results if r["index_scans"] == 0]
        low_usage_indexes = [r for r in results if 0 < r["index_scans"] < 100]

        return {
            "total_indexes": len(results),
            "unused_indexes": [
                {"name": r["indexname"], "table": r["tablename"]}
                for r in unused_indexes
            ],
            "low_usage_indexes": [
                {"name": r["indexname"], "table": r["tablename"], "scans": r["index_scans"]}
                for r in low_usage_indexes
            ],
            "most_used_indexes": sorted(
                [{"name": r["indexname"], "scans": r["index_scans"]} for r in results],
                key=lambda x: x["scans"],
                reverse=True
            )[:10]
        }

    async def get_table_size_info(self) -> List[Dict[str, Any]]:
        """
        获取表和索引大小信息

        Returns:
            表大小统计
        """
        query = """
            SELECT
                schemaname,
                tablename,
                pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) AS total_size,
                pg_size_pretty(pg_relation_size(schemaname||'.'||tablename)) AS table_size,
                pg_size_pretty(pg_indexes_size(schemaname||'.'||tablename)) AS indexes_size
            FROM pg_tables
            WHERE schemaname = 'public'
            ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC
        """

        return await execute_query(query, fetch="all")

    async def enable_required_extensions(self) -> bool:
        """
        启用必需的PostgreSQL扩展

        Returns:
            是否成功
        """
        extensions = ["pg_trgm"]  # 三角扩展，用于模糊搜索

        for ext in extensions:
            try:
                await execute_query(f"CREATE EXTENSION IF NOT EXISTS {ext}", fetch="none")
                logger.info(f"✅ 启用扩展: {ext}")
            except Exception as e:
                logger.warning(f"⚠️  扩展启用失败 {ext}: {e}")

        return True

    async def vacuum_analyze_tables(self, tables: Optional[List[str]] = None) -> Dict[str, str]:
        """
        清理和分析表（VACUUM ANALYZE）

        Args:
            tables: 要处理的表列表，None表示处理所有表

        Returns:
            处理结果
        """
        if tables is None:
            tables = ["products", "conversations", "recommendations", "knowledge_base", "users"]

        results = {}

        for table in tables:
            try:
                await execute_query(f"VACUUM ANALYZE {table}", fetch="none")
                results[table] = "success"
                logger.info(f"✅ VACUUM ANALYZE: {table}")
            except Exception as e:
                results[table] = f"failed: {e}"
                logger.error(f"❌ VACUUM ANALYZE 失败 {table}: {e}")

        return results


# ==================== 全局实例 ====================

_index_optimizer: Optional[IndexOptimizer] = None


def get_index_optimizer() -> IndexOptimizer:
    """获取索引优化器单例"""
    global _index_optimizer
    if _index_optimizer is None:
        _index_optimizer = IndexOptimizer()
    return _index_optimizer


# ==================== 使用示例 ====================

"""
# 在应用启动时优化索引
from app.database.index_optimizer import get_index_optimizer

optimizer = get_index_optimizer()

# 1. 启用必需扩展
await optimizer.enable_required_extensions()

# 2. 创建所有优化索引
results = await optimizer.create_all_indexes()

# 3. 分析索引使用情况
usage = await optimizer.analyze_index_usage()
print(f"未使用的索引: {usage['unused_indexes']}")

# 4. 获取表大小信息
sizes = await optimizer.get_table_size_info()

# 5. 定期清理分析（可在定时任务中调用）
await optimizer.vacuum_analyze_tables()
"""
