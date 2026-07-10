"""
LLM 成本监控模块

追踪 LLM API 调用成本，设置预算警告
支持多模型成本计算
"""

from typing import Dict, Optional, List
from datetime import datetime, timedelta
from dataclasses import dataclass, field
import logging
import json
from enum import Enum

logger = logging.getLogger(__name__)


# ==================== 模型定价配置 ====================

class LLMProvider(str, Enum):
    """LLM提供商"""
    DOUBAO = "doubao"
    KIMI = "kimi"
    SILICONFLOW = "siliconflow"
    ZHIPU = "zhipu"


@dataclass
class ModelPricing:
    """模型定价配置（元/1K tokens）"""
    input_price: float  # 输入价格
    output_price: float  # 输出价格


# 模型定价表（根据实际API定价更新）
MODEL_PRICING: Dict[str, ModelPricing] = {
    # 豆包（字节）
    "doubao-pro-4k": ModelPricing(input_price=0.00012, output_price=0.00012),
    "doubao-pro-32k": ModelPricing(input_price=0.00024, output_price=0.00024),
    "doubao-lite-4k": ModelPricing(input_price=0.00004, output_price=0.00004),

    # Kimi（月之暗面）
    "moonshot-v1-8k": ModelPricing(input_price=0.012, output_price=0.012),
    "moonshot-v1-32k": ModelPricing(input_price=0.024, output_price=0.024),
    "moonshot-v1-128k": ModelPricing(input_price=0.06, output_price=0.06),

    # 智谱
    "glm-4": ModelPricing(input_price=0.01, output_price=0.01),
    "glm-4-0520": ModelPricing(input_price=0.015, output_price=0.015),

    # SiliconFlow / Qwen
    "Qwen/Qwen2.5-7B-Instruct": ModelPricing(input_price=0.00035, output_price=0.00035),
    "deepseek-ai/DeepSeek-V3.2": ModelPricing(input_price=0.002, output_price=0.008),
}


# ==================== 使用记录 ====================

@dataclass
class UsageRecord:
    """单次使用记录"""
    timestamp: datetime
    model: str
    provider: LLMProvider
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cost: float
    cached: bool = False
    session_id: Optional[str] = None


# ==================== 成本监控器 ====================

class CostMonitor:
    """
    LLM 成本监控器

    功能：
    1. 记录每次 LLM 调用的成本
    2. 统计时段内的总成本
    3. 预算警告
    4. 成本报表生成
    """

    def __init__(
        self,
        daily_budget: float = 100.0,  # 每日预算（元）
        warning_threshold: float = 0.8  # 警告阈值（80%）
    ):
        """
        初始化成本监控器

        Args:
            daily_budget: 每日预算上限
            warning_threshold: 警告阈值（百分比）
        """
        self.daily_budget = daily_budget
        self.warning_threshold = warning_threshold
        self.records: List[UsageRecord] = []

        # 警告状态（防止重复警告）
        self._warning_sent = False

        logger.info(f"💰 LLM成本监控器已初始化")
        logger.info(f"   每日预算: ¥{daily_budget}")
        logger.info(f"   警告阈值: {warning_threshold * 100}%")

    def record_usage(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        provider: LLMProvider = LLMProvider.DOUBAO,
        cached: bool = False,
        session_id: Optional[str] = None
    ) -> float:
        """
        记录一次LLM调用

        Args:
            model: 模型名称
            input_tokens: 输入token数
            output_tokens: 输出token数
            provider: 提供商
            cached: 是否来自缓存
            session_id: 会话ID

        Returns:
            本次调用成本（元）
        """
        # 获取定价
        pricing = MODEL_PRICING.get(model)
        if not pricing:
            logger.warning(f"未知模型定价: {model}，使用默认价格")
            pricing = ModelPricing(input_price=0.01, output_price=0.01)

        # 计算成本
        input_cost = (input_tokens / 1000) * pricing.input_price
        output_cost = (output_tokens / 1000) * pricing.output_price
        total_cost = input_cost + output_cost

        # 如果来自缓存，成本为0
        if cached:
            total_cost = 0

        # 创建记录
        record = UsageRecord(
            timestamp=datetime.now(),
            model=model,
            provider=provider,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
            cost=total_cost,
            cached=cached,
            session_id=session_id
        )

        self.records.append(record)

        # 检查预算
        self._check_budget()

        logger.debug(
            f"LLM调用记录: {model} - "
            f"{input_tokens + output_tokens} tokens - ¥{total_cost:.6f}"
        )

        return total_cost

    def _check_budget(self):
        """检查预算并发出警告"""
        daily_cost = self.get_daily_cost()

        if not self._warning_sent and daily_cost >= self.daily_budget * self.warning_threshold:
            self._warning_sent = True
            logger.warning(
                f"⚠️  LLM成本警告: 已使用 {daily_cost:.2f} 元 "
                f"({daily_cost / self.daily_budget * 100:.1f}% 的预算)"
            )

        # 重置每日警告
        if self._warning_sent and daily_cost < self.daily_budget * self.warning_threshold:
            self._warning_sent = False

    def get_daily_cost(self) -> float:
        """
        获取今日成本

        Returns:
            今日总成本（元）
        """
        today = datetime.now().date()
        return sum(
            r.cost for r in self.records
            if r.timestamp.date() == today
        )

    def get_period_cost(
        self,
        hours: Optional[int] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None
    ) -> float:
        """
        获取时段成本

        Args:
            hours: 最近N小时
            start_time: 开始时间
            end_time: 结束时间

        Returns:
            时段总成本
        """
        if hours:
            cutoff = datetime.now() - timedelta(hours=hours)
            relevant_records = [r for r in self.records if r.timestamp >= cutoff]
        elif start_time and end_time:
            relevant_records = [
                r for r in self.records
                if start_time <= r.timestamp <= end_time
            ]
        else:
            relevant_records = self.records

        return sum(r.cost for r in relevant_records)

    def get_summary(self) -> Dict:
        """
        获取成本统计摘要

        Returns:
            统计信息字典
        """
        if not self.records:
            return {
                "total_cost": 0,
                "total_calls": 0,
                "total_tokens": 0,
                "cached_calls": 0,
                "daily_cost": 0,
                "daily_budget": self.daily_budget,
                "budget_usage": 0
            }

        total_cost = sum(r.cost for r in self.records)
        total_tokens = sum(r.total_tokens for r in self.records)
        cached_calls = sum(1 for r in self.records if r.cached)
        daily_cost = self.get_daily_cost()

        # 按模型统计
        model_stats: Dict[str, Dict] = {}
        for r in self.records:
            if r.model not in model_stats:
                model_stats[r.model] = {
                    "calls": 0,
                    "tokens": 0,
                    "cost": 0
                }
            model_stats[r.model]["calls"] += 1
            model_stats[r.model]["tokens"] += r.total_tokens
            model_stats[r.model]["cost"] += r.cost

        return {
            "total_cost": round(total_cost, 4),
            "total_calls": len(self.records),
            "total_tokens": total_tokens,
            "cached_calls": cached_calls,
            "cache_hit_rate": round(cached_calls / len(self.records) * 100, 2) if self.records else 0,
            "daily_cost": round(daily_cost, 4),
            "daily_budget": self.daily_budget,
            "budget_usage": round(daily_cost / self.daily_budget * 100, 2),
            "model_breakdown": model_stats
        }

    def export_report(self, filepath: Optional[str] = None) -> str:
        """
        导出成本报告

        Args:
            filepath: 输出文件路径

        Returns:
            JSON报告字符串
        """
        report = {
            "generated_at": datetime.now().isoformat(),
            "summary": self.get_summary(),
            "records": [
                {
                    "timestamp": r.timestamp.isoformat(),
                    "model": r.model,
                    "provider": r.provider.value,
                    "tokens": r.total_tokens,
                    "cost": r.cost,
                    "cached": r.cached
                }
                for r in self.records[-100:]  # 最近100条
            ]
        }

        json_str = json.dumps(report, ensure_ascii=False, indent=2)

        if filepath:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(json_str)
            logger.info(f"📄 成本报告已导出: {filepath}")

        return json_str

    def clear_old_records(self, days: int = 7):
        """
        清理旧记录

        Args:
            days: 保留最近N天的记录
        """
        cutoff = datetime.now() - timedelta(days=days)
        old_count = len(self.records)
        self.records = [r for r in self.records if r.timestamp >= cutoff]
        removed = old_count - len(self.records)

        if removed > 0:
            logger.info(f"🗑️  已清理 {removed} 条旧记录（保留最近{days}天）")


# ==================== 全局实例 ====================

_cost_monitor: Optional[CostMonitor] = None


def get_cost_monitor() -> CostMonitor:
    """
    获取成本监控器单例

    Returns:
        CostMonitor实例
    """
    global _cost_monitor
    if _cost_monitor is None:
        _cost_monitor = CostMonitor()
    return _cost_monitor


# ==================== 装饰器 ====================

def track_llm_cost(
    provider: LLMProvider = LLMProvider.DOUBAO,
    session_id: Optional[str] = None
):
    """
    LLM成本追踪装饰器

    Args:
        provider: LLM提供商
        session_id: 会话ID

    Example:
        @track_llm_cost(provider=LLMProvider.KIMI)
        async def chat_with_llm(messages):
            ...
    """
    def decorator(func):
        async def async_wrapper(*args, **kwargs):
            # 调用原函数
            result = await func(*args, **kwargs)

            # 尝试从响应中提取token信息
            # （假设返回值包含 usage 信息）
            try:
                if hasattr(result, "usage"):
                    monitor = get_cost_monitor()
                    monitor.record_usage(
                        model=getattr(result, "model", "unknown"),
                        input_tokens=result.usage.prompt_tokens,
                        output_tokens=result.usage.completion_tokens,
                        provider=provider,
                        cached=getattr(result, "cached", False),
                        session_id=session_id
                    )
            except Exception as e:
                logger.debug(f"成本记录跳过: {e}")

            return result

        return async_wrapper
    return decorator


# ==================== API端点示例 ====================

"""
# 在 FastAPI 中使用
from fastapi import APIRouter
from app.services.cost_monitor import get_cost_monitor

router = APIRouter(prefix="/admin", tags=["管理"])

@router.get("/cost/summary")
async def get_cost_summary():
    monitor = get_cost_monitor()
    return monitor.get_summary()

@router.get("/cost/export")
async def export_cost_report():
    monitor = get_cost_monitor()
    return {
        "report": json.loads(monitor.export_report())
    }

@router.post("/cost/budget")
async def set_daily_budget(budget: float):
    monitor = get_cost_monitor()
    monitor.daily_budget = budget
    return {"message": f"每日预算已设置为 ¥{budget}"}
"""
