"""
分页工具模块

提供统一的分页响应格式和分页辅助函数
"""

from typing import Generic, TypeVar, List, Any, Optional
from pydantic import BaseModel, Field
from math import ceil

T = TypeVar("T")


class PaginationParams(BaseModel):
    """分页参数"""
    page: int = Field(1, ge=1, description="页码（从1开始）")
    page_size: int = Field(20, ge=1, le=100, description="每页数量")

    @property
    def offset(self) -> int:
        """计算偏移量"""
        return (self.page - 1) * self.page_size

    @property
    def limit(self) -> int:
        """获取限制数量"""
        return self.page_size


class PaginatedResponse(BaseModel, Generic[T]):
    """
    分页响应

    通用的分页响应格式
    """
    items: List[T] = Field(default_factory=list, description="数据列表")
    total: int = Field(0, description="总数量")
    page: int = Field(1, description="当前页码")
    page_size: int = Field(20, description="每页数量")
    total_pages: int = Field(0, description="总页数")
    has_next: bool = Field(False, description="是否有下一页")
    has_prev: bool = Field(False, description="是否有上一页")

    @classmethod
    def create(
        cls,
        items: List[T],
        total: int,
        params: PaginationParams
    ) -> "PaginatedResponse[T]":
        """
        创建分页响应

        Args:
            items: 数据列表
            total: 总数量
            params: 分页参数

        Returns:
            分页响应对象
        """
        total_pages = ceil(total / params.page_size) if total > 0 else 0
        has_next = params.page < total_pages
        has_prev = params.page > 1

        return cls(
            items=items,
            total=total,
            page=params.page,
            page_size=params.page_size,
            total_pages=total_pages,
            has_next=has_next,
            has_prev=has_prev
        )


def paginate(
    items: List[T],
    params: PaginationParams
) -> PaginatedResponse[T]:
    """
    对列表进行分页

    Args:
        items: 完整数据列表
        params: 分页参数

    Returns:
        分页响应

    Example:
        items = [1, 2, 3, ..., 100]
        params = PaginationParams(page=2, page_size=10)
        result = paginate(items, params)
        # result.items = [11, 12, ..., 20]
    """
    total = len(items)
    start = params.offset
    end = start + params.page_size

    paginated_items = items[start:end]

    return PaginatedResponse.create(
        items=paginated_items,
        total=total,
        params=params
    )


async def async_paginate(
    items_func: callable,  # 异步获取数据的函数
    total_func: callable,  # 异步获取总数的函数
    params: PaginationParams
) -> PaginatedResponse:
    """
    异步分页（用于数据库查询）

    Args:
        items_func: 异步获取数据的函数 (offset, limit) -> List[T]
        total_func: 异步获取总数的函数 () -> int
        params: 分页参数

    Returns:
        分页响应

    Example:
        async def get_items(offset, limit):
            return await execute_query(
                "SELECT * FROM products LIMIT $1 OFFSET $2",
                limit, offset,
                fetch="all"
            )

        async def get_total():
            return await execute_query("SELECT COUNT(*) FROM products", fetch="val")

        params = PaginationParams(page=1, page_size=20)
        result = await async_paginate(get_items, get_total, params)
    """
    items = await items_func(params.offset, params.limit)
    total = await total_func()

    return PaginatedResponse.create(
        items=items,
        total=total,
        params=params
    )


# ==================== FastAPI 依赖 ====================

from fastapi import Query

def get_pagination_params(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量")
) -> PaginationParams:
    """
    从查询参数获取分页参数

    用法:
        @router.get("/products")
        async def list_products(pagination: PaginationParams = Depends(get_pagination_params)):
            ...
    """
    return PaginationParams(page=page, page_size=page_size)


# ==================== 使用示例 ====================

"""
# 在API中使用
from fastapi import APIRouter, Depends
from app.utils.pagination import PaginationParams, PaginatedResponse, get_pagination_params, async_paginate

@router.get("/products", response_model=PaginatedResponse[Product])
async def list_products(pagination: PaginationParams = Depends(get_pagination_params)):
    # 获取数据
    async def get_items(offset, limit):
        return await execute_query(
            "SELECT * FROM products ORDER BY sales_count DESC LIMIT $1 OFFSET $2",
            limit, offset,
            fetch="all"
        )

    async def get_total():
        return await execute_query("SELECT COUNT(*) FROM products", fetch="val")

    return await async_paginate(get_items, get_total, pagination)


# 对现有列表进行分页
@router.get("/categories")
async def list_categories(pagination: PaginationParams = Depends(get_pagination_params)):
    all_categories = ["手机", "电脑", "耳机", ...]
    return paginate(all_categories, pagination)
"""
