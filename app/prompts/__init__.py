"""
电商导购Agent提示词系统

提供：
- 意图分类提示词
- 场景化回复模板
- 售后转接话术
- 用户分层策略
"""

from app.prompts.intent_prompts import (
    INTENT_CLASSIFIER_SYSTEM,
    build_system_prompt,
    get_scenario_prompt,
    detect_after_sales_intent,
    AFTER_SALES_KEYWORDS
)

__all__ = [
    "INTENT_CLASSIFIER_SYSTEM",
    "build_system_prompt",
    "get_scenario_prompt",
    "detect_after_sales_intent",
    "AFTER_SALES_KEYWORDS"
]
