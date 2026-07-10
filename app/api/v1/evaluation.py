"""
质量评测API路由

提供评测相关的API接口
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime

from app.services.evaluation import get_evaluation_service

router = APIRouter(prefix="/evaluation", tags=["质量评测"])


# ==================== 请求/响应模型 ====================

class FeedbackRequest(BaseModel):
    """用户反馈请求"""
    session_id: str = Field(default="", description="会话ID")
    message_id: str = Field(default="", description="消息ID")
    user_message: str = Field(default="", description="用户消息")
    ai_response: str = Field(default="", description="AI回复")
    feedback_type: str = Field(..., description="反馈类型: thumbs_up/thumbs_down/report")
    rating: Optional[int] = Field(None, ge=1, le=5, description="评分1-5")
    feedback_text: Optional[str] = Field(None, description="反馈文本")
    response_time_ms: Optional[int] = Field(None, description="响应时间(毫秒)")
    retrieval_sources: Optional[List[str]] = Field(None, description="检索来源")


class FeedbackResponse(BaseModel):
    """反馈响应"""
    success: bool
    evaluation_id: Optional[int] = None
    message: str = ""


class StatsResponse(BaseModel):
    """统计响应"""
    success: bool
    period_days: int = 0
    total_evaluations: int = 0
    total_ratings: int = 0
    avg_rating: float = 0.0
    thumbs_up: int = 0
    thumbs_down: int = 0
    satisfaction_rate: float = 0.0
    avg_response_time_ms: float = 0.0


# ==================== API接口 ====================

@router.post("/feedback", response_model=FeedbackResponse)
async def submit_feedback(request: FeedbackRequest):
    """
    提交用户反馈

    用户可以对AI回复进行评价，用于质量追踪和改进
    """
    service = get_evaluation_service()

    result = await service.record_feedback(
        session_id=request.session_id,
        message_id=request.message_id,
        user_message=request.user_message,
        ai_response=request.ai_response,
        feedback_type=request.feedback_type,
        rating=request.rating,
        feedback_text=request.feedback_text,
        response_time_ms=request.response_time_ms,
        retrieval_sources=request.retrieval_sources
    )

    if result.get("success"):
        return FeedbackResponse(
            success=True,
            evaluation_id=result.get("evaluation_id"),
            message="感谢您的反馈！"
        )
    else:
        raise HTTPException(status_code=500, detail=result.get("error"))


@router.get("/stats")
async def get_stats(days: int = 7):
    """
    获取统计数据

    返回指定天数内的评测统计信息
    """
    service = get_evaluation_service()

    result = await service.get_daily_stats(days=days)

    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("error"))

    summary = result.get("summary", {})

    return {
        "success": True,
        "period_days": days,
        "total_evaluations": summary.get("total_evaluations", 0),
        "total_ratings": summary.get("total_ratings", 0),
        "avg_rating": round(summary.get("avg_rating", 0), 2),
        "thumbs_up": summary.get("thumbs_up", 0),
        "thumbs_down": summary.get("thumbs_down", 0),
        "satisfaction_rate": summary.get("satisfaction_rate", 0),
        "avg_response_time_ms": round(summary.get("avg_response_time_ms", 0), 2),
        "daily_stats": result.get("daily_stats", [])
    }


@router.get("/report")
async def get_quality_report():
    """
    获取质量报告

    返回综合质量评估和改进建议
    """
    service = get_evaluation_service()

    result = await service.get_quality_report()

    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("error"))

    return result


@router.get("/bad-samples")
async def get_bad_samples(limit: int = 10):
    """
    获取负面样本

    返回需要人工审查的负面反馈样本
    """
    service = get_evaluation_service()

    result = await service.get_bad_samples(limit=limit)

    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("error"))

    return result


@router.post("/review/{evaluation_id}")
async def mark_reviewed(evaluation_id: int, is_bad_sample: bool = True):
    """
    标记样本已审查

    将负面样本标记为已审查
    """
    service = get_evaluation_service()

    result = await service.mark_reviewed(evaluation_id, is_bad_sample)

    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("error"))

    return {"success": True, "message": "标记成功"}
