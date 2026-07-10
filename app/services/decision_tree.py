"""
决策过程可视化服务

追踪和展示AI的决策过程，让用户了解推荐逻辑
"""

from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field
from datetime import datetime
import json
import logging

logger = logging.getLogger(__name__)


@dataclass
class DecisionStep:
    """决策步骤"""
    step_id: str
    step_type: str  # intent, retrieval, analysis, ranking, final
    title: str
    description: str
    data: Dict[str, Any] = field(default_factory=dict)
    score: Optional[float] = None
    reason: Optional[str] = None


@dataclass
class DecisionTree:
    """决策树"""
    session_id: str
    user_query: str
    start_time: datetime = field(default_factory=datetime.now)
    steps: List[DecisionStep] = field(default_factory=list)
    final_recommendation: Optional[Dict[str, Any]] = None
    end_time: Optional[datetime] = None

    def add_step(self, step_type: str, title: str, description: str,
                 data: Dict[str, Any] = None, score: float = None, reason: str = None):
        """添加决策步骤"""
        step = DecisionStep(
            step_id=f"step_{len(self.steps) + 1}",
            step_type=step_type,
            title=title,
            description=description,
            data=data or {},
            score=score,
            reason=reason
        )
        self.steps.append(step)
        logger.debug(f"添加决策步骤: {title}")

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "session_id": self.session_id,
            "user_query": self.user_query,
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "duration_ms": (self.end_time - self.start_time).total_seconds() * 1000 if self.end_time else None,
            "steps": [
                {
                    "step_id": s.step_id,
                    "step_type": s.step_type,
                    "title": s.title,
                    "description": s.description,
                    "data": s.data,
                    "score": s.score,
                    "reason": s.reason
                }
                for s in self.steps
            ],
            "final_recommendation": self.final_recommendation
        }


class DecisionVisualizer:
    """
    决策过程可视化服务

    功能：
    1. 追踪决策过程的每一步
    2. 生成可读的决策说明
    3. 构建决策树结构
    """

    def __init__(self):
        """初始化服务"""
        self._active_trees: Dict[str, DecisionTree] = {}

    def create_tree(self, session_id: str, user_query: str) -> DecisionTree:
        """创建新的决策树"""
        tree = DecisionTree(session_id=session_id, user_query=user_query)
        self._active_trees[session_id] = tree
        return tree

    def get_tree(self, session_id: str) -> Optional[DecisionTree]:
        """获取决策树"""
        return self._active_trees.get(session_id)

    def complete_tree(self, session_id: str, final_recommendation: Dict[str, Any]):
        """完成决策树"""
        tree = self._active_trees.get(session_id)
        if tree:
            tree.end_time = datetime.now()
            tree.final_recommendation = final_recommendation
            logger.info(f"决策树完成: {session_id}, 步骤数: {len(tree.steps)}")

    async def analyze_and_build_tree(
        self,
        session_id: str,
        user_query: str,
        intent_result: Dict[str, Any],
        retrieved_products: List[Dict[str, Any]],
        final_recommendation: Dict[str, Any]
    ) -> DecisionTree:
        """
        分析并构建决策树

        Args:
            session_id: 会话ID
            user_query: 用户查询
            intent_result: 意图识别结果
            retrieved_products: 检索到的商品
            final_recommendation: 最终推荐

        Returns:
            决策树
        """
        tree = self.create_tree(session_id, user_query)

        # 步骤1: 意图理解
        tree.add_step(
            step_type="intent",
            title="🔍 理解你的需求",
            description="分析你的购物意图和偏好",
            data={
                "detected_intent": intent_result.get("intent", "unknown"),
                "category": intent_result.get("category"),
                "budget": intent_result.get("budget"),
                "preferences": intent_result.get("preferences", [])
            },
            score=intent_result.get("confidence", 0.8),
            reason=self._explain_intent(intent_result)
        )

        # 步骤2: 商品检索
        tree.add_step(
            step_type="retrieval",
            title=f"📦 搜索商品 (找到{len(retrieved_products)}款)",
            description="从商品库中筛选匹配的产品",
            data={
                "total_candidates": len(retrieved_products),
                "categories": list(set(p.get("category") for p in retrieved_products)),
                "price_range": [
                    min(p.get("price", 0) for p in retrieved_products),
                    max(p.get("price", 0) for p in retrieved_products)
                ]
            },
            reason=f"根据你的需求，筛选出{len(retrieved_products)}款相关商品"
        )

        # 步骤3: 匹配度分析
        scored_products = []
        for product in retrieved_products[:5]:  # 只分析前5个
            score = self._calculate_match_score(intent_result, product)
            scored_products.append({
                "product": product,
                "score": score,
                "reason": self._explain_match_score(score)
            })

        tree.add_step(
            step_type="analysis",
            title="📊 匹配度分析",
            description="分析每款商品与你需求的匹配程度",
            data={
                "top_matches": [
                    {
                        "name": p["product"]["name"],
                        "score": p["score"],
                        "price": p["product"]["price"]
                    }
                    for p in sorted(scored_products, key=lambda x: x["score"], reverse=True)[:3]
                ]
            },
            reason="综合价格、功能、品牌等因素评估匹配度"
        )

        # 步骤4: 排序推荐
        sorted_products = sorted(scored_products, key=lambda x: x["score"], reverse=True)
        tree.add_step(
            step_type="ranking",
            title="🏆 智能排序",
            description="根据匹配度和性价比排序",
            data={
                "ranking_criteria": ["匹配度", "性价比", "品牌口碑"],
                "top_3": [
                    {
                        "rank": i + 1,
                        "name": p["product"]["name"],
                        "score": p["score"]
                    }
                    for i, p in enumerate(sorted_products[:3])
                ]
            },
            reason="优先推荐匹配度高且性价比好的产品"
        )

        # 步骤5: 最终推荐
        if final_recommendation:
            tree.add_step(
                step_type="final",
                title="✨ 为你推荐",
                description="根据以上分析，为你推荐最合适的商品",
                data={
                    "recommended": final_recommendation.get("name", "未知"),
                    "price": final_recommendation.get("price"),
                    "key_reasons": final_recommendation.get("reasons", [])
                },
                score=sorted_products[0]["score"] if sorted_products else 0,
                reason=self._generate_final_reason(final_recommendation, intent_result)
            )

        self.complete_tree(session_id, final_recommendation)
        return tree

    def _calculate_match_score(self, intent: Dict[str, Any], product: Dict[str, Any]) -> float:
        """计算匹配度分数"""
        score = 0.5  # 基础分

        # 价格匹配
        budget = intent.get("budget")
        if budget and product.get("price"):
            price = product.get("price")
            if price <= budget:
                score += 0.2
            elif price <= budget * 1.2:
                score += 0.1

        # 品牌匹配
        preferences = intent.get("preferences", [])
        preferred_brands = []
        if isinstance(preferences, dict):
            preferred_brands = preferences.get("brands", [])
        elif isinstance(preferences, list):
            # 从列表中提取品牌
            preferred_brands = [p for p in preferences if isinstance(p, str)]

        if preferred_brands and product.get("brand") in preferred_brands:
            score += 0.15

        # 品类匹配
        category = intent.get("category")
        if category and category.lower() in str(product.get("category", "")).lower():
            score += 0.15

        return min(score, 1.0)

    def _explain_intent(self, intent: Dict[str, Any]) -> str:
        """解释意图识别结果"""
        intent_type = intent.get("intent", "unknown")
        explanations = {
            "product_search": "你正在寻找特定商品",
            "comparison": "你想对比不同商品的差异",
            "recommendation": "你需要个性化的购买建议",
            "price_inquiry": "你想了解商品价格信息"
        }
        return explanations.get(intent_type, "正在理解你的需求...")

    def _explain_match_score(self, score: float) -> str:
        """解释匹配度分数"""
        if score >= 0.85:
            return "高度匹配"
        elif score >= 0.7:
            return "比较匹配"
        elif score >= 0.5:
            return "一般匹配"
        else:
            return "匹配度较低"

    def _generate_final_reason(self, product: Dict[str, Any], intent: Dict[str, Any]) -> str:
        """生成最终推荐理由"""
        reasons = []

        # 价格理由
        budget = intent.get("budget")
        price = product.get("price", 0)
        if budget and price:
            if price <= budget:
                reasons.append(f"价格¥{price}在你的预算内")
            else:
                reasons.append(f"虽然价格¥{price}略超预算，但性价比高")

        # 功能理由
        tags = product.get("tags", [])
        if "旗舰" in tags:
            reasons.append("旗舰配置，性能强劲")
        if "拍照" in tags:
            reasons.append("拍照表现出色")
        if "高性价比" in tags:
            reasons.append("性价比优秀")

        return "、".join(reasons) if reasons else "综合表现优秀"

    def format_tree_for_display(self, tree: DecisionTree) -> str:
        """格式化决策树用于显示"""
        lines = [f"## 📋 决策过程分析\n"]

        for step in tree.steps:
            lines.append(f"### {step.title}")
            lines.append(f"*{step.description}*")

            if step.reason:
                lines.append(f"**分析**: {step.reason}")

            if step.data:
                # 只显示关键数据
                if step.step_type == "intent":
                    if step.data.get("detected_intent"):
                        lines.append(f"- 意图: `{step.data['detected_intent']}`")
                    if step.data.get("budget"):
                        lines.append(f"- 预算: ¥{step.data['budget']}")
                elif step.step_type == "analysis":
                    for match in step.data.get("top_matches", []):
                        lines.append(f"- {match['name']}: 匹配度 **{match['score']*100:.0f}%**")

            lines.append("")

        return "\n".join(lines)


# ==================== 全局实例 ====================

_visualizer: Optional[DecisionVisualizer] = None


def get_decision_visualizer() -> DecisionVisualizer:
    """获取决策可视化服务单例"""
    global _visualizer
    if _visualizer is None:
        _visualizer = DecisionVisualizer()
    return _visualizer
