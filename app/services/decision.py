"""
决策辅助服务模块

提供商品对比、购买建议、优缺点分析等功能
帮助用户做出购买决策
"""

from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
import logging

from app.services.llm import get_llm_service
from app.database.postgres import execute_query
from app.services.prompts import get_product_fact_override

logger = logging.getLogger(__name__)


class ComparisonService:
    """
    商品对比服务

    对比多个商品的各方面指标
    """

    def __init__(self):
        """初始化对比服务"""
        self.llm_service = get_llm_service()
        logger.info("✅ 决策辅助服务初始化成功")

    async def compare_products(
        self,
        product_ids: List[int]
    ) -> Dict[str, Any]:
        """
        对比多个商品

        Args:
            product_ids: 商品ID列表

        Returns:
            对比结果
        """
        if len(product_ids) < 2:
            raise ValueError("至少需要2个商品进行对比")

        if len(product_ids) > 5:
            raise ValueError("最多支持5个商品对比")

        # 获取商品信息
        products = await execute_query(
            "SELECT * FROM products WHERE id = ANY($1)",
            product_ids,
            fetch="all"
        )

        if len(products) != len(product_ids):
            found_ids = [p["id"] for p in products]
            missing = set(product_ids) - set(found_ids)
            raise ValueError(f"商品不存在: {missing}")

        # 构建对比信息
        comparison = {
            "products": products,
            "dimensions": self._extract_dimensions(products),
            "summary": await self._generate_comparison_summary(products)
        }

        return comparison

    def _extract_dimensions(self, products: List[Dict]) -> Dict[str, List]:
        """
        提取对比维度

        Args:
            products: 商品列表

        Returns:
            各维度的值
        """
        dimensions = {
            "品牌": [p["brand"] for p in products],
            "类别": [p["category"] for p in products],
            "价格": [self._format_price(p) for p in products],
            "性价比": self._calculate_value_score(products),
            "推荐指数": self._calculate_recommendation_score(products)
        }

        return dimensions

    def _format_price(self, product: Dict) -> str:
        """优先使用人工核验过的多规格价格口径。"""
        override = get_product_fact_override(product)
        if override.get("price_label"):
            return f"{override['price_label']}（{override['price_source']}）"
        return f"¥{float(product['price']):.0f}"

    def _calculate_value_score(self, products: List[Dict]) -> List[float]:
        """
        计算性价比分数

        基于价格和类别计算简单的性价比分数
        """
        scores = []
        prices = [float(p["price"]) for p in products]
        min_price = min(prices)
        max_price = max(prices)

        for p in products:
            price = float(p["price"])
            if max_price == min_price:
                score = 75
            else:
                # 价格越低分数越高
                score = 100 - ((price - min_price) / (max_price - min_price)) * 50
            scores.append(round(score, 1))

        return scores

    def _calculate_recommendation_score(self, products: List[Dict]) -> List[str]:
        """
        计算推荐指数

        基于品牌、价格等因素给出推荐等级
        """
        scores = []
        for p in products:
            price = float(p["price"])
            brand = p["brand"]

            # 简单规则
            if price >= 5000:
                score = "⭐⭐⭐⭐⭐ 高端旗舰"
            elif price >= 3000:
                score = "⭐⭐⭐⭐ 性价比之选"
            else:
                score = "⭐⭐⭐ 入门推荐"

            scores.append(score)

        return scores

    async def _generate_comparison_summary(self, products: List[Dict]) -> str:
        """
        生成对比总结

        使用LLM生成自然的对比描述
        """
        try:
            product_info = "\n".join([
                f"- {p['name']} ({p['brand']}): {self._format_price(p)}, {p.get('description', '')[:50]}"
                for p in products
            ])

            prompt = f"""请对比以下商品，给出购买建议：

{product_info}

请从以下角度分析：
1. 价格定位
2. 目标用户
3. 优缺点分析
4. 购买建议

请用简洁明了的语言回答。涉及不同容量、规格或色号的价格时，必须保留上方给出的完整价格口径和来源，不要改写成单一价格。"""

            response = await self.llm_service.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=500
            )

            return response

        except Exception as e:
            logger.error(f"LLM对比总结失败: {e}")
            return "商品对比分析暂时不可用，请参考以上参数自行判断。"


class RecommendationService:
    """
    购买建议服务

    根据用户需求推荐最合适的商品
    """

    def __init__(self):
        """初始化推荐服务"""
        self.llm_service = get_llm_service()
        logger.info("✅ 推荐服务初始化成功")

    async def get_recommendation(
        self,
        requirements: Dict[str, Any],
        category: Optional[str] = None,
        budget: Optional[float] = None,
        top_k: int = 3
    ) -> Dict[str, Any]:
        """
        根据需求获取购买建议

        Args:
            requirements: 用户需求
                - use_case: 使用场景 (游戏/办公/拍照等)
                - preferences: 偏好 (品牌/系统等)
                - priorities: 优先级 (性能/价格/外观等)
            category: 商品类别
            budget: 预算
            top_k: 推荐数量

        Returns:
            推荐结果
        """
        # 构建查询
        sql = "SELECT * FROM products WHERE 1=1"
        params = []

        if category:
            sql += " AND category = $1"
            params.append(category)

        if budget:
            sql += f" AND price <= ${len(params) + 1}"
            params.append(budget)

        sql += f" ORDER BY price ASC LIMIT {top_k * 3}"  # 多取一些用于筛选

        candidates = await execute_query(sql, *params, fetch="all")

        if not candidates:
            return {
                "recommendations": [],
                "advice": "没有找到符合要求的商品，建议调整预算或需求。"
            }

        # 根据需求筛选和排序
        scored = []
        for product in candidates:
            score = self._calculate_match_score(product, requirements)
            scored.append({**product, "match_score": score})

        scored.sort(key=lambda x: x["match_score"], reverse=True)

        recommendations = scored[:top_k]

        # 生成建议
        advice = await self._generate_advice(recommendations, requirements)

        return {
            "recommendations": recommendations,
            "advice": advice,
            "total_candidates": len(candidates)
        }

    def _calculate_match_score(self, product: Dict, requirements: Dict) -> float:
        """
        计算商品与需求的匹配分数

        Args:
            product: 商品信息
            requirements: 用户需求

        Returns:
            匹配分数 (0-100)
        """
        score = 50  # 基础分
        price = float(product["price"])

        # 预算匹配
        if "budget" in requirements:
            budget = requirements["budget"]
            if price <= budget:
                score += 20
                if price <= budget * 0.8:
                    score += 10  # 留有余地

        # 品牌偏好
        if "preferences" in requirements:
            prefs = requirements["preferences"]
            if isinstance(prefs, dict) and "brand" in prefs:
                if product["brand"] == prefs["brand"]:
                    score += 15

        # 使用场景
        if "use_case" in requirements:
            use_case = requirements["use_case"]
            if use_case == "游戏" and price >= 4000:
                score += 10
            elif use_case == "办公" and price <= 3000:
                score += 10
            elif use_case == "拍照" and "Pro" in product["name"]:
                score += 10

        return min(score, 100)

    async def _generate_advice(
        self,
        recommendations: List[Dict],
        requirements: Dict
    ) -> str:
        """生成购买建议"""
        try:
            req_text = ", ".join([f"{k}={v}" for k, v in requirements.items()])
            products_text = "\n".join([
                f"- {r['name']}: ¥{r['price']} (匹配度: {r['match_score']}%)"
                for r in recommendations[:3]
            ])

            prompt = f"""用户需求：{req_text}

推荐商品：
{products_text}

请给出购买建议，包括：
1. 最推荐的商品及理由
2. 各商品的适用场景
3. 最终购买建议

请简洁明了地回答。"""

            response = await self.llm_service.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=400
            )

            return response

        except Exception as e:
            logger.error(f"生成建议失败: {e}")
            return "根据您的需求，建议优先考虑匹配度最高的商品。"


class ProsConsService:
    """
    优缺点分析服务

    分析商品的优缺点
    """

    def __init__(self):
        """初始化优缺点服务"""
        self.llm_service = get_llm_service()

    async def analyze_pros_cons(
        self,
        product_id: int
    ) -> Dict[str, Any]:
        """
        分析商品的优缺点

        Args:
            product_id: 商品ID

        Returns:
            优缺点分析
        """
        # 获取商品信息
        product = await execute_query(
            "SELECT * FROM products WHERE id = $1",
            product_id,
            fetch="one"
        )

        if not product:
            raise ValueError(f"商品不存在: {product_id}")

        # 生成分析
        analysis = await self._generate_pros_cons(product)

        return {
            "product_id": product_id,
            "product_name": product["name"],
            "pros": analysis["pros"],
            "cons": analysis["cons"],
            "verdict": analysis["verdict"],
            "suitable_for": analysis["suitable_for"]
        }

    async def _generate_pros_cons(self, product: Dict) -> Dict[str, Any]:
        """使用LLM生成优缺点分析"""
        try:
            prompt = f"""请分析以下商品的优缺点：

商品名称：{product['name']}
品牌：{product['brand']}
价格：¥{product['price']}
描述：{product.get('description', '暂无描述')}

请从以下角度分析：
1. 优点（至少3点）
2. 缺点（至少2点）
3. 综合评价（一句话总结）
4. 适合人群

请以JSON格式返回：
{{
  "pros": ["优点1", "优点2", ...],
  "cons": ["缺点1", "缺点2", ...],
  "verdict": "综合评价",
  "suitable_for": "适合人群"
}}"""

            response = await self.llm_service.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=500
            )

            # 解析JSON
            import json
            import re

            # 提取JSON
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                result = json.loads(json_match.group())
                return result
            else:
                # 解析失败，返回默认值
                return {
                    "pros": ["性价比不错", "品牌可靠"],
                    "cons": ["具体信息不足"],
                    "verdict": "需要更多信息来判断",
                    "suitable_for": "一般用户"
                }

        except Exception as e:
            logger.error(f"优缺点分析失败: {e}")
            return {
                "pros": ["品牌知名", "质量有保障"],
                "cons": ["分析功能暂时不可用"],
                "verdict": "建议咨询客服了解更多",
                "suitable_for": "普通消费者"
            }


# ==================== 全局实例 ====================

_comparison_service: Optional[ComparisonService] = None
_recommendation_service: Optional[RecommendationService] = None
_pros_cons_service: Optional[ProsConsService] = None


def get_comparison_service() -> ComparisonService:
    global _comparison_service
    if _comparison_service is None:
        _comparison_service = ComparisonService()
    return _comparison_service


def get_recommendation_service() -> RecommendationService:
    global _recommendation_service
    if _recommendation_service is None:
        _recommendation_service = RecommendationService()
    return _recommendation_service


def get_pros_cons_service() -> ProsConsService:
    global _pros_cons_service
    if _pros_cons_service is None:
        _pros_cons_service = ProsConsService()
    return _pros_cons_service


# ==================== 使用示例 ====================

"""
from app.services.decision import get_comparison_service, get_recommendation_service

# 商品对比
comparison_svc = get_comparison_service()
result = await comparison_svc.compare_products([1, 2, 3])
print(result["summary"])

# 购买建议
recommendation_svc = get_recommendation_service()
result = await recommendation_svc.get_recommendation(
    requirements={"use_case": "游戏", "budget": 5000},
    category="手机"
)
print(result["advice"])
"""
