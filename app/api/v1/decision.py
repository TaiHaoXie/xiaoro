"""
决策辅助API模块

提供商品对比、购买建议、优缺点分析等接口
"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime
import logging

from app.services.decision import (
    get_comparison_service,
    get_recommendation_service,
    get_pros_cons_service
)

logger = logging.getLogger(__name__)

# 创建路由器
router = APIRouter(prefix="/decision", tags=["决策辅助"])


# ==================== 数据模型 ====================

class CompareRequest(BaseModel):
    """商品对比请求"""
    product_ids: List[int] = Field(..., description="商品ID列表", min_items=2, max_items=5)


class RecommendationRequest(BaseModel):
    """购买建议请求"""
    requirements: Dict[str, Any] = Field(default_factory=dict, description="用户需求")
    category: Optional[str] = Field(None, description="商品类别")
    budget: Optional[float] = Field(None, description="预算")
    top_k: int = Field(3, description="推荐数量", ge=1, le=10)


class QuickDecisionRequest(BaseModel):
    """快速决策请求"""
    product_ids: List[int] = Field(..., description="待选商品ID列表", min_items=2, max_items=10)
    budget: Optional[float] = Field(None, description="预算")
    priority: str = Field("value", description="优先级: value/performance/balance")


# ==================== 商品对比接口 ====================

@router.post("/compare")
async def compare_products(request: CompareRequest):
    """
    商品对比分析

    对比多个商品的各项指标，给出购买建议

    请求示例：
    ```json
    {
        "product_ids": [1, 2, 3]
    }
    ```
    """
    try:
        service = get_comparison_service()
        result = await service.compare_products(request.product_ids)

        return {
            "success": True,
            "products": result["products"],
            "dimensions": result["dimensions"],
            "summary": result["summary"]
        }

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"商品对比错误: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/compare")
async def compare_products_query(
    ids: str = Query(..., description="商品ID，逗号分隔，如: 1,2,3")
):
    """
    商品对比（GET方式）

    使用查询参数进行对比

    示例：/decision/compare?ids=1,2,3
    """
    try:
        product_ids = [int(id.strip()) for id in ids.split(",")]
        service = get_comparison_service()
        result = await service.compare_products(product_ids)

        return {
            "success": True,
            "products": result["products"],
            "dimensions": result["dimensions"],
            "summary": result["summary"]
        }

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"商品对比错误: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 购买建议接口 ====================

@router.post("/recommend")
async def get_recommendation(request: RecommendationRequest):
    """
    智能购买建议

    根据用户需求推荐最合适的商品

    请求示例：
    ```json
    {
        "requirements": {
            "use_case": "游戏",
            "preferences": {"brand": "苹果"}
        },
        "category": "手机",
        "budget": 5000,
        "top_k": 3
    }
    ```
    """
    try:
        service = get_recommendation_service()

        # 添加预算到需求
        requirements = request.requirements.copy()
        if request.budget:
            requirements["budget"] = request.budget

        result = await service.get_recommendation(
            requirements=requirements,
            category=request.category,
            budget=request.budget,
            top_k=request.top_k
        )

        return {
            "success": True,
            "recommendations": result["recommendations"],
            "advice": result["advice"],
            "total_candidates": result["total_candidates"]
        }

    except Exception as e:
        logger.error(f"购买建议错误: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 优缺点分析接口 ====================

@router.get("/analyze/{product_id}")
async def analyze_product(product_id: int):
    """
    商品优缺点分析

    分析指定商品的优缺点，给出购买建议

    Args:
        product_id: 商品ID
    """
    try:
        service = get_pros_cons_service()
        analysis = await service.analyze_pros_cons(product_id)

        return {
            "success": True,
            **analysis
        }

    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"优缺点分析错误: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 场景化推荐接口 ====================

@router.get("/scenarios")
async def list_scenarios():
    """
    列出可用的推荐场景

    返回预设的使用场景和需求模板
    """
    scenarios = [
        {
            "id": "gaming",
            "name": "游戏娱乐",
            "description": "高性能游戏设备推荐",
            "keywords": ["游戏", "电竞", "高性能"],
            "requirements": {
                "use_case": "游戏",
                "priorities": ["性能", "散热", "屏幕"]
            }
        },
        {
            "id": "office",
            "name": "办公学习",
            "description": "高效办公设备推荐",
            "keywords": ["办公", "学习", "商务"],
            "requirements": {
                "use_case": "办公",
                "priorities": ["便携", "续航", "性价比"]
            }
        },
        {
            "id": "photography",
            "name": "摄影创作",
            "description": "拍照摄影设备推荐",
            "keywords": ["拍照", "摄影", "创作"],
            "requirements": {
                "use_case": "拍照",
                "priorities": ["相机", "影像", "色彩"]
            }
        },
        {
            "id": "budget",
            "name": "高性价比",
            "description": "预算有限的高性价比选择",
            "keywords": ["性价比", "预算", "实惠"],
            "requirements": {
                "priorities": ["价格", "性价比"]
            }
        },
        {
            "id": "premium",
            "name": "高端旗舰",
            "description": "不计成本的顶级体验",
            "keywords": ["旗舰", "高端", "顶级"],
            "requirements": {
                "priorities": ["性能", "体验", "品质"]
            }
        }
    ]

    return {
        "scenarios": scenarios
    }


@router.post("/scenarios/{scenario_id}")
async def recommend_by_scenario(
    scenario_id: str,
    category: Optional[str] = Query(None, description="商品类别"),
    budget: Optional[float] = Query(None, description="预算")
):
    """
    按场景推荐商品

    根据预设场景智能推荐

    Args:
        scenario_id: 场景ID
        category: 商品类别
        budget: 预算
    """
    try:
        # 获取场景配置
        scenarios_result = await list_scenarios()
        scenarios = scenarios_result["scenarios"]
        scenario = next((s for s in scenarios if s["id"] == scenario_id), None)

        if not scenario:
            raise HTTPException(status_code=404, detail=f"场景不存在: {scenario_id}")

        service = get_recommendation_service()

        requirements = scenario["requirements"].copy()
        if budget:
            requirements["budget"] = budget

        result = await service.get_recommendation(
            requirements=requirements,
            category=category,
            budget=budget,
            top_k=3
        )

        return {
            "success": True,
            "scenario": scenario["name"],
            "recommendations": result["recommendations"],
            "advice": result["advice"]
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"场景推荐错误: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 快速决策接口 ====================

@router.post("/quick-decision")
async def quick_decision(request: QuickDecisionRequest):
    """
    快速决策助手

    在多个商品中快速做出选择

    请求示例：
    ```json
    {
        "product_ids": [1, 2, 3],
        "budget": 5000,
        "priority": "value"
    }
    ```
    """
    try:
        product_ids = request.product_ids
        budget = request.budget
        priority = request.priority
        # 获取商品信息
        from app.database.postgres import execute_query

        products = await execute_query(
            "SELECT * FROM products WHERE id = ANY($1)",
            product_ids,
            fetch="all"
        )

        if not products:
            raise HTTPException(status_code=404, detail="没有找到匹配的商品")

        # 根据优先级排序
        if priority == "value":
            # 性价比优先
            products.sort(key=lambda p: float(p["price"]))
        elif priority == "performance":
            # 性能优先（价格越高越好）
            products.sort(key=lambda p: float(p["price"]), reverse=True)
        else:
            # 平衡
            products.sort(key=lambda p: float(p["price"]))

        # 预算过滤
        if budget:
            products = [p for p in products if float(p["price"]) <= budget]

        if not products:
            return {
                "success": True,
                "winner": None,
                "message": f"没有商品符合预算 ¥{budget}",
                "recommendations": []
            }

        # 选择最佳
        winner = products[0]

        # 生成建议
        advice = f"根据您的{priority == 'value' and '性价比' or priority == 'performance' and '性能' or '综合'}优先原则，"
        advice += f"推荐选择 {winner['name']}。"
        if budget and float(winner["price"]) <= budget * 0.8:
            advice += f" 价格 ¥{winner['price']} 在预算范围内，还能节省 ¥{budget - float(winner['price'])}。"
        elif budget:
            advice += f" 价格 ¥{winner['price']} 占用了预算的 {float(winner['price'])/budget*100:.0f}%。"

        return {
            "success": True,
            "winner": {
                "id": winner["id"],
                "name": winner["name"],
                "brand": winner["brand"],
                "price": float(winner["price"]),
                "reason": f"{'最便宜' if priority == 'value' else '最高端' if priority == 'performance' else '最均衡'}的选择"
            },
            "message": advice,
            "recommendations": products[:3]
        }

    except Exception as e:
        logger.error(f"快速决策错误: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 测试接口 ====================

@router.get("/test/sample-comparison")
async def sample_comparison():
    """
    示例商品对比

    返回预设商品的对比结果
    """
    try:
        service = get_comparison_service()

        # 对比手机产品
        result = await service.compare_products([1, 2, 3])

        return {
            "success": True,
            "products": result["products"],
            "dimensions": result["dimensions"],
            "summary": result["summary"]
        }

    except Exception as e:
        logger.error(f"示例对比错误: {e}")
        raise HTTPException(status_code=500, detail=str(e))
