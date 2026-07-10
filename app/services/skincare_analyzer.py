"""
护肤品类专业分析服务

针对护肤产品的专业分析：
- 成分分析
- 肤质匹配
- 功效评估
- 安全性检查
"""

from typing import Dict, List, Any, Optional
import logging

logger = logging.getLogger(__name__)


# 肤质类型定义
SKIN_TYPES = {
    "dry": {
        "name": "干性肌肤",
        "characteristics": ["紧绷", "脱皮", "细纹", "缺乏光泽"],
        "needs": ["深层保湿", "修护屏障", "滋润"],
        "avoid": ["酒精", "水杨酸", "强碱性"]
    },
    "oily": {
        "name": "油性肌肤",
        "characteristics": ["T区出油", "毛孔粗大", "痘痘", "油光"],
        "needs": ["控油", "收敛毛孔", "祛痘"],
        "avoid": ["矿物油", "厚重质地"]
    },
    "combination": {
        "name": "混合性肌肤",
        "characteristics": ["T区油", "两颊干", "毛孔不均"],
        "needs": ["水油平衡", "分区护理"],
        "avoid": ["过于滋润", "过于控油"]
    },
    "sensitive": {
        "name": "敏感性肌肤",
        "characteristics": ["易泛红", "刺痛", "过敏", "薄"],
        "needs": ["温和", "修护", "舒缓"],
        "avoid": ["酒精", "香精", "防腐剂", "强功效成分"]
    },
    "normal": {
        "name": "中性肌肤",
        "characteristics": ["水油平衡", "毛孔细", "有光泽"],
        "needs": ["维持", "预防", "基础护理"],
        "avoid": ["过度清洁", "频繁去角质"]
    }
}

# 护肤成分数据库
INGREDIENTS_DB = {
    # 保湿成分
    "hyaluronic_acid": {
        "name": "玻尿酸",
        "effect": "深层保湿",
        "suitable_for": ["dry", "oily", "combination", "sensitive", "normal"],
        "concerns": ["干燥", "细纹", "缺乏弹性"]
    },
    "ceramide": {
        "name": "神经酰胺",
        "effect": "修护屏障",
        "suitable_for": ["dry", "sensitive", "combination"],
        "concerns": ["敏感", "干燥", "屏障受损"]
    },
    "squalane": {
        "name": "角鲨烷",
        "effect": "滋润修护",
        "suitable_for": ["dry", "sensitive", "normal"],
        "concerns": ["干燥", "粗糙", "缺乏光泽"]
    },

    # 抗老成分
    "retinol": {
        "name": "A醇",
        "effect": "抗老、改善细纹",
        "suitable_for": ["dry", "normal", "combination"],
        "concerns": ["细纹", "松弛", "暗沉"],
        "caution": "建立耐受，孕妇慎用"
    },
    "bifida": {
        "name": "二裂酵母",
        "effect": "修护抗老",
        "suitable_for": ["dry", "oily", "combination", "sensitive", "normal"],
        "concerns": ["初老", "暗沉", "细纹"]
    },
    "peptide": {
        "name": "多肽",
        "effect": "抗老紧致",
        "suitable_for": ["dry", "normal", "combination"],
        "concerns": ["细纹", "松弛", "弹性下降"]
    },

    # 美白成分
    "niacinamide": {
        "name": "烟酰胺",
        "effect": "美白提亮、控油",
        "suitable_for": ["oily", "combination", "normal"],
        "concerns": ["暗沉", "色斑", "出油"],
        "caution": "高浓度可能刺激"
    },
    "vitamin_c": {
        "name": "维生素C",
        "effect": "美白抗氧化",
        "suitable_for": ["dry", "normal", "combination"],
        "concerns": ["暗沉", "色斑", "抗氧化"],
        "caution": "不稳定，需避光保存"
    },

    # 祛痘成分
    "salicylic_acid": {
        "name": "水杨酸",
        "effect": "去角质、祛痘",
        "suitable_for": ["oily", "combination"],
        "concerns": ["痘痘", "闭口", "毛孔粗大"],
        "caution": "孕妇慎用，避免过度使用"
    },

    # 舒缓成分
    "bisabolol": {
        "name": "红没药醇",
        "effect": "舒缓抗敏",
        "suitable_for": ["sensitive", "dry", "normal"],
        "concerns": ["泛红", "刺痛", "敏感"]
    },
    "centella": {
        "name": "积雪草",
        "effect": "舒缓修护",
        "suitable_for": ["sensitive", "oily", "combination"],
        "concerns": ["泛红", "痘痘", "敏感"]
    },

    # 防晒成分
    "zinc_oxide": {
        "name": "氧化锌",
        "effect": "物理防晒",
        "suitable_for": ["sensitive", "dry", "normal"],
        "concerns": ["防晒", "敏感"],
        "caution": "可能泛白"
    }
}


class SkincareAnalyzer:
    """护肤专业分析器"""

    def __init__(self):
        """初始化分析器"""
        pass

    def analyze_product(self, product: Dict[str, Any]) -> Dict[str, Any]:
        """
        分析护肤产品

        Args:
            product: 商品信息（包含skincare_info）

        Returns:
            分析结果
        """
        import json

        skincare_info = product.get("skincare_info", {})
        # 如果是字符串，解析为字典
        if isinstance(skincare_info, str):
            skincare_info = json.loads(skincare_info)

        specs = product.get("specs", {})
        if isinstance(specs, str):
            specs = json.loads(specs)

        analysis = {
            "product_name": product["name"],
            "category": product["category"],
            "overall_rating": self._calculate_overall_rating(skincare_info),
            "ingredient_analysis": self._analyze_ingredients(skincare_info),
            "skin_compatibility": self._analyze_skin_compatibility(skincare_info),
            "safety_level": self._assess_safety(skincare_info, specs),
            "recommendation_score": 0
        }

        # 计算推荐分数
        analysis["recommendation_score"] = self._calculate_recommendation_score(analysis)

        return analysis

    def match_skin_concerns(
        self,
        product: Dict[str, Any],
        skin_type: str,
        concerns: List[str]
    ) -> Dict[str, Any]:
        """
        肤质与需求匹配分析

        Args:
            product: 商品信息
            skin_type: 肤质类型 (dry/oily/combination/sensitive/normal)
            concerns: 护肤需求列表

        Returns:
            匹配结果
        """
        import json

        skincare_info = product.get("skincare_info", {})
        if isinstance(skincare_info, str):
            skincare_info = json.loads(skincare_info)
        product_skin_types = skincare_info.get("skin_types", [])
        product_concerns = skincare_info.get("concerns", [])

        # 肤质类型映射（英文到中文）
        skin_type_map = {
            "dry": "干性",
            "oily": "油性",
            "combination": "混合性",
            "sensitive": "敏感性",
            "normal": "中性"
        }

        # 肤质匹配度
        skin_match = 0
        chinese_skin_type = skin_type_map.get(skin_type, skin_type)

        if chinese_skin_type in product_skin_types:
            skin_match = 100
        elif skin_type == "combination" and ("干性" in product_skin_types or "油性" in product_skin_types):
            skin_match = 70
        else:
            skin_match = 30

        # 需求匹配度
        concern_matches = len(set(concerns) & set(product_concerns))
        concern_match = (concern_matches / len(concerns) * 100) if concerns else 0

        # 成分匹配
        ingredient_bonus = self._analyze_ingredient_match(skin_type, concerns, skincare_info)

        # 综合评分
        overall_score = (skin_match * 0.4 + concern_match * 0.4 + ingredient_bonus * 0.2)

        return {
            "skin_type_match": skin_match,
            "concern_match": concern_match,
            "ingredient_bonus": ingredient_bonus,
            "overall_match": round(overall_score, 1),
            "recommendation": self._generate_recommendation(overall_score, skin_match, concern_match),
            "reasons": self._generate_match_reasons(skin_match, concern_match, ingredient_bonus, skincare_info)
        }

    def compare_ingredients(
        self,
        products: List[Dict[str, Any]],
        target_concerns: List[str]
    ) -> Dict[str, Any]:
        """
        成分对比分析

        Args:
            products: 商品列表
            target_concerns: 目标护肤需求

        Returns:
            对比结果
        """
        comparison = {
            "products": [],
            "ingredient_comparison": [],
            "best_for": {}
        }

        for product in products:
            skincare_info = product.get("skincare_info", {})
            key_ingredients = skincare_info.get("key_ingredients", [])

            product_analysis = {
                "name": product["name"],
                "price": product["price"],
                "key_ingredients": key_ingredients,
                "effectiveness_score": self._calculate_effectiveness(key_ingredients, target_concerns)
            }
            comparison["products"].append(product_analysis)

        # 排序
        comparison["products"].sort(key=lambda x: x["effectiveness_score"], reverse=True)

        # 找出最适合的产品
        if comparison["products"]:
            best = comparison["products"][0]
            comparison["best_for"] = {
                "product": best["name"],
                "reason": f"含有 {len(best['key_ingredients'])} 种有效成分，针对性最强"
            }

        return comparison

    def _calculate_overall_rating(self, skincare_info: Dict) -> float:
        """计算综合评分"""
        professional = skincare_info.get("professional_rating", 0)
        user = skincare_info.get("user_rating", 0)
        return round((professional * 0.6 + user * 0.4), 1)

    def _analyze_ingredients(self, skincare_info: Dict) -> Dict[str, Any]:
        """分析成分"""
        key_ingredients = skincare_info.get("key_ingredients", [])

        analysis = {
            "count": len(key_ingredients),
            "key_ingredients": key_ingredients,
            "highlight": self._get_highlight_ingredient(key_ingredients)
        }

        return analysis

    def _analyze_skin_compatibility(self, skincare_info: Dict) -> Dict[str, Any]:
        """分析肤质兼容性"""
        skin_types = skincare_info.get("skin_types", [])

        return {
            "suitable_for": skin_types,
            "suitability_level": "广泛" if len(skin_types) >= 4 else "特定"
        }

    def _assess_safety(self, skincare_info: Dict, specs: Dict) -> Dict[str, Any]:
        """评估安全性"""
        # 基础安全性评估
        concerns = []
        level = "安全"

        # 检查是否含有刺激性成分
        # 这里可以扩展更多检查规则

        return {
            "level": level,
            "concerns": concerns,
            "suitable_for_sensitive": "敏感肌" in skincare_info.get("skin_types", [])
        }

    def _calculate_recommendation_score(self, analysis: Dict) -> float:
        """计算推荐分数"""
        score = analysis["overall_rating"] * 20  # 评分转分数
        return round(min(score, 100), 1)

    def _analyze_ingredient_match(
        self,
        skin_type: str,
        concerns: List[str],
        skincare_info: Dict
    ) -> float:
        """分析成分匹配度"""
        key_ingredients = skincare_info.get("key_ingredients", [])
        match_score = 0

        for ingredient in key_ingredients:
            name = ingredient.get("name", "")
            effect = ingredient.get("effect", "")

            # 检查是否适合该肤质
            for ing_key, ing_data in INGREDIENTS_DB.items():
                if ing_data["name"] in name:
                    if skin_type in ing_data["suitable_for"]:
                        match_score += 20
                    # 检查是否匹配需求
                    matched_concerns = set(concerns) & set(ing_data.get("concerns", []))
                    match_score += len(matched_concerns) * 10

        return min(match_score, 100)

    def _generate_recommendation(self, overall: float, skin: float, concern: float) -> str:
        """生成推荐建议"""
        if overall >= 80:
            return "强烈推荐"
        elif overall >= 60:
            return "推荐"
        elif overall >= 40:
            return "可以考虑"
        else:
            return "不太适合"

    def _generate_match_reasons(
        self,
        skin_match: float,
        concern_match: float,
        ingredient_bonus: float,
        skincare_info: Dict
    ) -> List[str]:
        """生成匹配理由"""
        reasons = []

        if skin_match >= 80:
            reasons.append(f"✅ 适合你的肤质")
        elif skin_match <= 30:
            reasons.append(f"⚠️ 可能不太适合你的肤质")

        if concern_match >= 70:
            reasons.append(f"✅ 针对你的护肤需求")
        elif concern_match == 0:
            reasons.append(f"ℹ️ 未针对你的特定需求")

        if ingredient_bonus >= 50:
            reasons.append(f"✅ 含有有效成分")

        # 添加产品特定理由
        key_ingredients = skincare_info.get("key_ingredients", [])
        if key_ingredients:
            top_ingredient = key_ingredients[0]
            reasons.append(f"含 {top_ingredient['name']}（{top_ingredient['effect']}）")

        return reasons

    def _calculate_effectiveness(
        self,
        ingredients: List[Dict],
        concerns: List[str]
    ) -> float:
        """计算成分有效性"""
        score = 0

        for ingredient in ingredients:
            name = ingredient.get("name", "")
            effect = ingredient.get("effect", "")

            # 检查成分是否匹配需求
            for concern in concerns:
                if concern in effect or any(keyword in effect for keyword in
                    ["抗老", "保湿", "美白", "祛痘", "修护", "舒缓"]):
                    score += 20

        return min(score, 100)

    def _get_highlight_ingredient(self, ingredients: List[Dict]) -> Optional[Dict]:
        """获取核心成分"""
        if not ingredients:
            return None

        # 返回第一个成分作为核心成分
        return ingredients[0]


# 全局实例
_analyzer: Optional[SkincareAnalyzer] = None


def get_skincare_analyzer() -> SkincareAnalyzer:
    """获取护肤分析器单例"""
    global _analyzer
    if _analyzer is None:
        _analyzer = SkincareAnalyzer()
    return _analyzer
