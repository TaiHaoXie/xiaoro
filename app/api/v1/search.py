"""
搜索API模块

处理商品搜索、类别/品牌筛选
支持分页查询
"""

from fastapi import APIRouter, Query, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
import logging

from app.database.postgres import execute_query
from app.utils.pagination import PaginationParams, PaginatedResponse, get_pagination_params

logger = logging.getLogger(__name__)

# 创建路由器
router = APIRouter(prefix="/search", tags=["搜索"])


# ==================== 数据模型 ====================

class SearchRequest(BaseModel):
    """搜索请求"""
    query: str = Field(..., description="搜索关键词")
    category: Optional[str] = Field(None, description="商品类别")
    brand: Optional[str] = Field(None, description="品牌")
    min_price: Optional[float] = Field(None, ge=0, description="最低价格")
    max_price: Optional[float] = Field(None, ge=0, description="最高价格")


class ProductCard(BaseModel):
    """商品卡片"""
    id: int
    name: str
    category: str
    brand: str
    price: float
    original_price: Optional[float] = None
    image_url: Optional[str] = None
    rating: Optional[float] = None
    review_count: int = 0


class CategoryInfo(BaseModel):
    """类别信息"""
    id: int
    name: str
    count: int


class BrandInfo(BaseModel):
    """品牌信息"""
    id: int
    name: str
    product_count: int


# ==================== 搜索接口 ====================

@router.post("/products", response_model=PaginatedResponse[ProductCard])
async def search_products(
    request: SearchRequest,
    pagination: PaginationParams = Depends(get_pagination_params)
):
    """
    商品搜索（支持分页）

    支持关键词搜索 + 过滤条件

    请求示例：
    ```json
    {
        "query": "蓝牙耳机",
        "category": "耳机",
        "max_price": 500
    }
    ```

    查询参数：?page=1&page_size=20
    """
    try:
        # 构建查询条件
        conditions = []
        args = []
        arg_count = 0

        # 关键词搜索
        if request.query:
            arg_count += 1
            conditions.append(f"(name LIKE ${arg_count} OR description LIKE ${arg_count + 1})")
            args.extend([f"%{request.query}%", f"%{request.query}%"])
            arg_count += 1

        # 类别筛选
        if request.category:
            arg_count += 1
            conditions.append(f"category = ${arg_count}")
            args.append(request.category)

        # 品牌筛选
        if request.brand:
            arg_count += 1
            conditions.append(f"brand = ${arg_count}")
            args.append(request.brand)

        # 价格范围
        if request.min_price is not None:
            arg_count += 1
            conditions.append(f"price >= ${arg_count}")
            args.append(request.min_price)

        if request.max_price is not None:
            arg_count += 1
            conditions.append(f"price <= ${arg_count}")
            args.append(request.max_price)

        # 构建WHERE子句
        where_clause = ""
        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)

        # 获取总数
        count_query = f"SELECT COUNT(*) FROM products {where_clause}"
        total = await execute_query(count_query, *args, fetch="val")

        # 获取分页数据（移除不存在的 rating/sales_count 列依赖）
        data_query = f"""
            SELECT id, name, category, brand, price, original_price, image_url, review_count
            FROM products {where_clause}
            ORDER BY id DESC
            LIMIT ${arg_count + 1} OFFSET ${arg_count + 2}
        """
        args.extend([pagination.page_size, pagination.offset])

        products = await execute_query(data_query, *args, fetch="all")

        return PaginatedResponse.create(
            items=products,
            total=total,
            params=pagination
        )

    except Exception as e:
        logger.error(f"搜索错误: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/products/{product_id}")
async def get_product(product_id: int):
    """
    获取商品详情

    Args:
        product_id: 商品ID
    """
    try:
        product = await execute_query(
            """SELECT * FROM products WHERE id = $1""",
            product_id,
            fetch="one"
        )

        if not product:
            raise HTTPException(status_code=404, detail="商品不存在")

        return product

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取商品详情错误: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/categories", response_model=PaginatedResponse[CategoryInfo])
async def list_categories(pagination: PaginationParams = Depends(get_pagination_params)):
    """
    列出所有商品类别（支持分页）
    """
    try:
        # 获取总数和分页数据
        async def get_items(offset, limit):
            return await execute_query(
                """SELECT category as id, category as name, COUNT(*) as count
                   FROM products
                   GROUP BY category
                   ORDER BY count DESC
                   LIMIT $1 OFFSET $2""",
                limit, offset,
                fetch="all"
            )

        async def get_total():
            result = await execute_query(
                """SELECT COUNT(DISTINCT category) FROM products""",
                fetch="val"
            )
            return result

        from app.utils.pagination import async_paginate
        return await async_paginate(get_items, get_total, pagination)

    except Exception as e:
        logger.error(f"获取类别列表错误: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/brands", response_model=PaginatedResponse[BrandInfo])
async def list_brands(pagination: PaginationParams = Depends(get_pagination_params)):
    """
    列出所有品牌（支持分页）
    """
    try:
        # 获取总数和分页数据
        async def get_items(offset, limit):
            return await execute_query(
                """SELECT brand as id, brand as name, COUNT(*) as product_count
                   FROM products
                   WHERE brand IS NOT NULL
                   GROUP BY brand
                   ORDER BY product_count DESC
                   LIMIT $1 OFFSET $2""",
                limit, offset,
                fetch="all"
            )

        async def get_total():
            result = await execute_query(
                """SELECT COUNT(DISTINCT brand) FROM products WHERE brand IS NOT NULL""",
                fetch="val"
            )
            return result

        from app.utils.pagination import async_paginate
        return await async_paginate(get_items, get_total, pagination)

    except Exception as e:
        logger.error(f"获取品牌列表错误: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 使用示例 ====================

"""
# 分页查询示例
GET /api/v1/search/products?page=2&page_size=10

# 带搜索条件
POST /api/v1/search/products?page=1&page_size=20
{
    "query": "蓝牙耳机",
    "category": "耳机",
    "max_price": 500
}

# 响应格式
{
    "items": [...],
    "total": 150,
    "page": 1,
    "page_size": 20,
    "total_pages": 8,
    "has_next": true,
    "has_prev": false
}
"""
