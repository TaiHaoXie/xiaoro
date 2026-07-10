"""
PostgreSQL 数据库连接模块

提供异步PostgreSQL连接池管理
"""

import asyncpg
from typing import Optional, List, Dict, Any
from contextlib import asynccontextmanager
import logging

from app.config import settings

logger = logging.getLogger(__name__)


# 全局连接池
_postgres_pool: Optional[asyncpg.Pool] = None


async def init_postgres_pool():
    """
    初始化PostgreSQL连接池

    在应用启动时调用
    """
    global _postgres_pool

    if _postgres_pool is not None and not _postgres_pool._closed:
        logger.info("PostgreSQL连接池已存在，跳过重复初始化")
        return _postgres_pool

    logger.info("初始化PostgreSQL连接池...")

    _postgres_pool = await asyncpg.create_pool(
        host=settings.POSTGRES_HOST,
        port=settings.POSTGRES_PORT,
        user=settings.POSTGRES_USER,
        password=settings.POSTGRES_PASSWORD,
        database=settings.POSTGRES_DB,
        min_size=5,      # 最小连接数
        max_size=20,     # 最大连接数
        command_timeout=60,  # 命令超时（秒）
    )

    logger.info("✅ PostgreSQL连接池初始化成功")
    return _postgres_pool


async def close_postgres_pool():
    """
    关闭PostgreSQL连接池

    在应用关闭时调用
    """
    global _postgres_pool

    if _postgres_pool:
        await _postgres_pool.close()
        _postgres_pool = None
        logger.info("✅ PostgreSQL连接池已关闭")


def get_postgres_pool() -> asyncpg.Pool:
    """
    获取PostgreSQL连接池

    Returns:
        asyncpg.Pool: 连接池对象

    Raises:
        RuntimeError: 如果连接池未初始化
    """
    if _postgres_pool is None:
        raise RuntimeError("PostgreSQL连接池未初始化，请先调用 init_postgres_pool()")
    return _postgres_pool


@asynccontextmanager
async def get_postgres_connection():
    """
    获取PostgreSQL连接的上下文管理器

    使用方式：
        async with get_postgres_connection() as conn:
            result = await conn.fetch("SELECT * FROM products")

    Yields:
        asyncpg.Connection: 数据库连接对象
    """
    pool = get_postgres_pool()
    async with pool.acquire() as connection:
        yield connection


# ==================== 数据库操作辅助函数 ====================

async def execute_query(
    query: str,
    *args,
    fetch: str = "all"  # all, one, val, none
) -> Any:
    """
    执行SQL查询的辅助函数

    Args:
        query: SQL查询语句
        *args: 查询参数
        fetch: 返回类型
            - "all": 返回所有行（列表）
            - "one": 返回第一行
            - "val": 返回第一个值
            - "none": 不返回（用于INSERT/UPDATE/DELETE）

    Returns:
        根据fetch参数返回不同类型的数据

    Examples:
        # 查询所有商品
        products = await execute_query("SELECT * FROM products")

        # 查询单个商品
        product = await execute_query("SELECT * FROM products WHERE id = $1", 1, fetch="one")

        # 查询单个值
        count = await execute_query("SELECT COUNT(*) FROM products", fetch="val")

        # 执行插入
        await execute_query("INSERT INTO products (name) VALUES ($1)", "商品名", fetch="none")
    """
    async with get_postgres_connection() as conn:
        if fetch == "all":
            result = await conn.fetch(query, *args)
            return [dict(row) for row in result] if result else []

        elif fetch == "one":
            result = await conn.fetchrow(query, *args)
            return dict(result) if result else None

        elif fetch == "val":
            return await conn.fetchval(query, *args)

        elif fetch == "none":
            await conn.execute(query, *args)
            return None

        else:
            raise ValueError(f"不支持的fetch类型: {fetch}")


async def execute_transaction(queries: List[tuple]) -> List[Any]:
    """
    执行事务（多个SQL语句，要么全成功，要么全失败）

    Args:
        queries: SQL语句列表，每个元素是 (query, args, fetch_type) 元组

    Returns:
        每个查询的返回结果列表

    Example:
        results = await execute_transaction([
            ("INSERT INTO products (name) VALUES ($1) RETURNING id", ["商品A"], "one"),
            ("UPDATE inventory SET count = count - 1 WHERE product_id = $1", [1], "none"),
        ])
    """
    async with get_postgres_connection() as conn:
        async with conn.transaction():
            results = []
            for query_tuple in queries:
                if len(query_tuple) == 2:
                    query, args = query_tuple
                    fetch_type = "all"
                else:
                    query, args, fetch_type = query_tuple

                if fetch_type == "all":
                    result = await conn.fetch(query, *args)
                    results.append([dict(row) for row in result])
                elif fetch_type == "one":
                    result = await conn.fetchrow(query, *args)
                    results.append(dict(result) if result else None)
                elif fetch_type == "val":
                    results.append(await conn.fetchval(query, *args))
                elif fetch_type == "none":
                    await conn.execute(query, *args)
                    results.append(None)

            return results


# ==================== 表初始化SQL ====================

CREATE_USERS_TABLE_SQL = """
-- 用户表
CREATE TABLE IF NOT EXISTS users (
    id BIGSERIAL PRIMARY KEY,
    username VARCHAR(50) UNIQUE NOT NULL,
    email VARCHAR(255) UNIQUE,
    hashed_password VARCHAR(255) NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW(),
    last_login TIMESTAMP
);

-- 创建索引
CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
"""

CREATE_TABLES_SQL = """
-- 商品表
CREATE TABLE IF NOT EXISTS products (
    id BIGSERIAL PRIMARY KEY,
    name VARCHAR(500) NOT NULL,
    category VARCHAR(100),
    brand VARCHAR(100),
    price DECIMAL(10, 2),
    original_price DECIMAL(10, 2),
    description TEXT,
    specifications JSONB,
    image_url TEXT,
    detail_url TEXT,
    platform VARCHAR(50),
    stock INTEGER DEFAULT 0,
    sales_count INTEGER DEFAULT 0,
    rating DECIMAL(3, 2),
    review_count INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- 创建索引
CREATE INDEX IF NOT EXISTS idx_products_category ON products(category);
CREATE INDEX IF NOT EXISTS idx_products_brand ON products(brand);
CREATE INDEX IF NOT EXISTS idx_products_price ON products(price);
CREATE INDEX IF NOT EXISTS idx_products_category_price ON products(category, price);

-- 用户会话表
CREATE TABLE IF NOT EXISTS conversations (
    id BIGSERIAL PRIMARY KEY,
    session_id VARCHAR(100) UNIQUE NOT NULL,
    user_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
    messages JSONB,
    intent VARCHAR(100),
    context JSONB,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- 推荐记录表（用于质量评测闭环）
CREATE TABLE IF NOT EXISTS recommendations (
    id BIGSERIAL PRIMARY KEY,
    conversation_id BIGINT REFERENCES conversations(id),
    product_ids BIGINT[],
    reason TEXT,
    user_feedback VARCHAR(50),
    clicked_products BIGINT[],
    purchased_products BIGINT[],
    created_at TIMESTAMP DEFAULT NOW()
);

-- 知识库表（FAQ、避坑指南等）
CREATE TABLE IF NOT EXISTS knowledge_base (
    id BIGSERIAL PRIMARY KEY,
    title VARCHAR(500) NOT NULL,
    content TEXT NOT NULL,
    type VARCHAR(50) NOT NULL,  -- faq, product_desc, user_guide, review, comparison
    product_id BIGINT REFERENCES products(id) ON DELETE SET NULL,
    metadata JSONB,
    created_at TIMESTAMP DEFAULT NOW()
);

-- 上传文档登记表（用于专属知识库构建与展示）
CREATE TABLE IF NOT EXISTS knowledge_documents (
    id VARCHAR(120) PRIMARY KEY,
    filename VARCHAR(255) NOT NULL,
    title VARCHAR(255),
    file_type VARCHAR(20),
    category VARCHAR(100),
    product_id VARCHAR(100),
    size BIGINT DEFAULT 0,
    chunks_count INTEGER DEFAULT 0,
    content_preview TEXT,
    metadata JSONB,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_knowledge_documents_category ON knowledge_documents(category);
CREATE INDEX IF NOT EXISTS idx_knowledge_documents_created_at ON knowledge_documents(created_at DESC);
"""


async def init_database_tables():
    """
    初始化数据库表结构

    在首次运行时调用，创建必要的表
    """
    logger.info("初始化数据库表...")

    async with get_postgres_connection() as conn:
        # 执行建表SQL
        await conn.execute(CREATE_USERS_TABLE_SQL)
        await conn.execute(CREATE_TABLES_SQL)

    logger.info("✅ 数据库表初始化完成")


# ==================== 连接池监控 ====================

def get_pool_stats() -> Dict[str, Any]:
    """
    获取连接池状态统计

    Returns:
        连接池状态信息
    """
    if _postgres_pool is None:
        return {
            "status": "not_initialized",
            "size": 0,
            "maxsize": 0,
            "available": 0,
            "minsize": 0
        }

    return {
        "status": "active",
        "size": _postgres_pool.size,
        "maxsize": _postgres_pool.maxsize,
        "minsize": _postgres_pool.minsize,
        "available": _postgres_pool._queue.qsize(),  # 可用连接数
        "max_wait": "default",  # 最大等待时间
        "timeout": _postgres_pool._timeout,
        "command_timeout": _postgres_pool._opts.command_timeout
    }


def get_pool_health() -> Dict[str, Any]:
    """
    获取连接池健康状态

    Returns:
        健康状态信息
    """
    stats = get_pool_stats()

    if stats["status"] == "not_initialized":
        return {
            "healthy": False,
            "reason": "连接池未初始化"
        }

    # 计算使用率
    usage_rate = (stats["size"] - stats["available"]) / stats["maxsize"] if stats["maxsize"] > 0 else 0

    # 健康判断
    healthy = True
    warnings = []

    if usage_rate > 0.8:
        warnings.append(f"连接池使用率过高: {usage_rate*100:.1f}%")

    if stats["available"] == 0:
        warnings.append("无可用连接")

    return {
        "healthy": healthy and len(warnings) == 0,
        "usage_rate": round(usage_rate * 100, 2),
        "warnings": warnings,
        "stats": stats
    }


# ==================== 使用示例 ====================

"""
# 在main.py的lifespan中初始化
from app.database.postgres import init_postgres_pool, close_postgres_pool

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时
    await init_postgres_pool()
    await init_database_tables()  # 首次运行需要
    yield
    # 关闭时
    await close_postgres_pool()


# 在其他模块中使用
from app.database.postgres import execute_query

# 查询所有商品
products = await execute_query("SELECT * FROM products LIMIT 10")

# 插入商品
await execute_query(
    "INSERT INTO products (name, price) VALUES ($1, $2)",
    "蓝牙耳机", 299.00,
    fetch="none"
)

# 查询单个商品
product = await execute_query(
    "SELECT * FROM products WHERE id = $1",
    1,
    fetch="one"
)
"""
