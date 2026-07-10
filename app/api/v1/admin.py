"""
管理API模块

提供系统管理、监控相关接口
"""

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from typing import Optional, List
import logging

from app.services.cost_monitor import get_cost_monitor
from app.database.index_optimizer import get_index_optimizer
from app.database.query_optimizer import get_query_optimizer
from app.database.postgres import get_pool_stats, get_pool_health

logger = logging.getLogger(__name__)

# 创建路由器
router = APIRouter(prefix="/admin", tags=["管理"])


# ==================== 数据模型 ====================

class BudgetRequest(BaseModel):
    """预算设置请求"""
    daily_budget: float = Field(..., description="每日预算上限（元）", gt=0)


# ==================== 成本监控接口 ====================

@router.get("/cost/summary")
async def get_cost_summary():
    """
    获取成本统计摘要

    返回：
    - 总成本、调用次数
    - 今日成本、预算使用率
    - 缓存命中率
    - 按模型分组的统计
    """
    monitor = get_cost_monitor()
    return monitor.get_summary()


@router.get("/cost/daily")
async def get_daily_cost():
    """
    获取今日成本详情

    返回：
    - 今日总成本
    - 今日预算使用情况
    """
    monitor = get_cost_monitor()
    return {
        "daily_cost": monitor.get_daily_cost(),
        "daily_budget": monitor.daily_budget,
        "budget_remaining": monitor.daily_budget - monitor.get_daily_cost(),
        "budget_usage_percent": round(
            monitor.get_daily_cost() / monitor.daily_budget * 100, 2
        )
    }


@router.post("/cost/budget")
async def set_daily_budget(request: BudgetRequest):
    """
    设置每日预算上限

    Args:
        request: 包含 daily_budget 的请求体

    Returns:
        设置结果确认
    """
    monitor = get_cost_monitor()
    old_budget = monitor.daily_budget
    monitor.daily_budget = request.daily_budget

    logger.info(f"💰 每日预算已更新: ¥{old_budget} → ¥{request.daily_budget}")

    return {
        "message": "每日预算已更新",
        "old_budget": old_budget,
        "new_budget": request.daily_budget
    }


@router.get("/cost/export")
async def export_cost_report():
    """
    导出成本报告

    返回：
    - JSON格式的详细成本报告
    - 包含最近100条调用记录
    """
    monitor = get_cost_monitor()

    import json
    return {
        "report": json.loads(monitor.export_report())
    }


@router.post("/cost/clear")
async def clear_old_records(days: int = 7):
    """
    清理旧的成本记录

    Args:
        days: 保留最近N天的记录（默认7天）

    Returns:
        清理结果
    """
    monitor = get_cost_monitor()
    old_count = len(monitor.records)

    monitor.clear_old_records(days=days)

    new_count = len(monitor.records)
    removed = old_count - new_count

    return {
        "message": f"已清理 {removed} 条旧记录",
        "kept_days": days,
        "remaining_records": new_count
    }


# ==================== 健康检查 ====================

@router.get("/health")
async def admin_health():
    """
    管理端健康检查

    返回系统状态概览
    """
    monitor = get_cost_monitor()

    return {
        "status": "healthy",
        "cost_monitor": {
            "enabled": True,
            "daily_budget": monitor.daily_budget,
            "daily_cost": monitor.get_daily_cost()
        },
        "services": {
            "llm": "active",
            "milvus": "active",
            "redis": "active",
            "postgres": "active"
        }
    }


# ==================== 数据库索引管理 ====================

@router.post("/database/indexes/create")
async def create_indexes():
    """
    创建所有优化索引

    创建数据库中缺失的优化索引，提升查询性能
    """
    optimizer = get_index_optimizer()

    # 先启用必需扩展
    await optimizer.enable_required_extensions()

    # 创建索引
    results = await optimizer.create_all_indexes()

    return {
        "message": "索引创建完成",
        "results": results
    }


@router.get("/database/indexes/usage")
async def get_index_usage():
    """
    获取索引使用情况

    分析哪些索引被使用，哪些未被使用
    """
    optimizer = get_index_optimizer()
    usage = await optimizer.analyze_index_usage()

    return usage


@router.get("/database/tables/sizes")
async def get_table_sizes():
    """
    获取表大小信息

    返回各表及其索引占用的存储空间
    """
    optimizer = get_index_optimizer()
    sizes = await optimizer.get_table_size_info()

    return {
        "tables": sizes
    }


@router.post("/database/vacuum")
async def vacuum_analyze(tables: Optional[List[str]] = None):
    """
    清理和分析表

    执行VACUUM ANALYZE，优化表性能

    Args:
        tables: 要处理的表列表，不指定则处理所有表
    """
    optimizer = get_index_optimizer()
    results = await optimizer.vacuum_analyze_tables(tables)

    return {
        "message": "VACUUM ANALYZE 完成",
        "results": results
    }


# ==================== 连接池监控 ====================

@router.get("/database/pool")
async def get_pool_stats_api():
    """
    获取数据库连接池状态

    返回：
    - 连接池大小、最大/最小连接数
    - 可用连接数、超时设置
    """
    return get_pool_stats()


@router.get("/database/pool/health")
async def get_pool_health_api():
    """
    获取连接池健康状态

    返回：
    - 健康状态、使用率
    - 警告信息（如有）
    """
    return get_pool_health()


# ==================== 健康检查 ====================

@router.get("/health")
async def admin_health():
    """
    管理端健康检查

    返回系统状态概览
    """
    monitor = get_cost_monitor()
    pool_health = get_pool_health()

    return {
        "status": "healthy" if pool_health.get("healthy", True) else "degraded",
        "cost_monitor": {
            "enabled": True,
            "daily_budget": monitor.daily_budget,
            "daily_cost": monitor.get_daily_cost()
        },
        "database": {
            "pool": get_pool_stats(),
            "health": pool_health
        },
        "services": {
            "llm": "active",
            "milvus": "active",
            "redis": "active",
            "postgres": "active"
        }
    }


# ==================== 使用示例 ====================

"""
# 在 main.py 中注册路由
from app.api.v1 import admin

app.include_router(admin.router, prefix="/api/v1")


# 调用示例
GET /api/v1/admin/cost/summary
{
  "total_cost": 12.3456,
  "total_calls": 234,
  "daily_cost": 5.6789,
  "budget_usage": 5.68,
  "cache_hit_rate": 35.2
}

POST /api/v1/admin/cost/budget
{
  "daily_budget": 200.0
}
"""
