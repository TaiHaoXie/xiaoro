"""
意图识别服务模块

结合规则引擎和LLM，识别用户对话意图
支持：商品搜索、商品对比、购买咨询、投诉建议等

集成新的意图分类系统（基于用户场景）
"""

from typing import Dict, List, Optional, Any, Tuple
from pydantic import BaseModel, Field
import logging
import re
import json

from app.services.llm import get_llm_service
from app.config import settings

# 导入新的意图分类系统
from app.prompts.intent_prompts import (
    INTENT_CLASSIFIER_SYSTEM,
    build_system_prompt,
    get_scenario_prompt,
    detect_after_sales_intent,
    AFTER_SALES_KEYWORDS,
    INTENT_PRIORITIES
)

logger = logging.getLogger(__name__)


# ==================== 意图类型定义 ====================

class IntentType:
    """意图类型常量"""
    # 商品相关
    PRODUCT_SEARCH = "product_search"      # 商品搜索
    PRODUCT_DETAIL = "product_detail"      # 商品详情
    PRODUCT_COMPARE = "product_compare"    # 商品对比
    PRODUCT_RECOMMEND = "product_recommend"  # 商品推荐

    # 购买相关
    PRICE_INQUIRY = "price_inquiry"        # 价格询问
    PURCHASE_ADVICE = "purchase_advice"    # 购买建议
    ORDER_QUERY = "order_query"            # 订单查询

    # 售后相关
    COMPLAINT = "complaint"                # 投诉
    RETURN_REFUND = "return_refund"        # 退换货
    AFTER_SALES = "after_sales"            # 售后咨询

    # 其他
    GREETING = "greeting"                  # 问候
    CHITCHAT = "chitchat"                  # 闲聊
    UNKNOWN = "unknown"                    # 未知


# ==================== 新意图分类系统（基于用户场景）====================

class ScenarioIntent:
    """
    场景化意图类型（与用户提示词系统对应）
    """
    # 高优先级
    CLEAR_PURCHASE = "明确购买"      # 已知目标商品，准备下单
    COMPARISON_DECISION = "比价决策"  # 在2-3个选项中犹豫
    NEED_EXPLORATION = "需求探索"    # 有模糊需求，不确定买什么

    # 中优先级
    KNOWLEDGE_QUERY = "知识咨询"     # 了解产品知识
    PROMOTION_QUERY = "优惠活动"     # 询问折扣、优惠券
    STOCK_QUERY = "库存咨询"        # 询问是否有货

    # 低优先级
    NEW_PRODUCT_QUERY = "新品咨询"   # 了解新品
    GREETING = "闲聊寒暄"           # 打招呼

    # 转接
    AFTER_SALES = "售后服务"        # 退换货、物流、投诉


# 意图映射（旧系统 -> 新系统）
INTENT_MAPPING = {
    IntentType.PRODUCT_SEARCH: ScenarioIntent.NEED_EXPLORATION,
    IntentType.PRODUCT_DETAIL: ScenarioIntent.CLEAR_PURCHASE,
    IntentType.PRODUCT_COMPARE: ScenarioIntent.COMPARISON_DECISION,
    IntentType.PRODUCT_RECOMMEND: ScenarioIntent.NEED_EXPLORATION,
    IntentType.PRICE_INQUIRY: ScenarioIntent.CLEAR_PURCHASE,
    IntentType.PURCHASE_ADVICE: ScenarioIntent.COMPARISON_DECISION,
    IntentType.ORDER_QUERY: ScenarioIntent.AFTER_SALES,
    IntentType.COMPLAINT: ScenarioIntent.AFTER_SALES,
    IntentType.RETURN_REFUND: ScenarioIntent.AFTER_SALES,
    IntentType.AFTER_SALES: ScenarioIntent.AFTER_SALES,
    IntentType.GREETING: ScenarioIntent.GREETING,
    IntentType.CHITCHAT: ScenarioIntent.GREETING,
    IntentType.UNKNOWN: ScenarioIntent.NEED_EXPLORATION,
}

# 反向映射（新系统 -> 旧系统）
SCENARIO_TO_INTENT_MAPPING = {
    ScenarioIntent.CLEAR_PURCHASE: IntentType.PRICE_INQUIRY,
    ScenarioIntent.COMPARISON_DECISION: IntentType.PRODUCT_COMPARE,
    ScenarioIntent.NEED_EXPLORATION: IntentType.PRODUCT_SEARCH,
    ScenarioIntent.KNOWLEDGE_QUERY: IntentType.PRODUCT_DETAIL,
    ScenarioIntent.PROMOTION_QUERY: IntentType.PRODUCT_SEARCH,
    ScenarioIntent.STOCK_QUERY: IntentType.PRODUCT_DETAIL,
    ScenarioIntent.NEW_PRODUCT_QUERY: IntentType.PRODUCT_SEARCH,
    ScenarioIntent.GREETING: IntentType.GREETING,
    ScenarioIntent.AFTER_SALES: IntentType.AFTER_SALES,
}


# ==================== 意图数据模型 ====================

class IntentResult(BaseModel):
    """意图识别结果"""
    intent: str = Field(description="意图类型（旧系统）")
    scenario_intent: str = Field(default="", description="场景意图类型（新系统）")
    confidence: float = Field(description="置信度（0-1）", ge=0, le=1)
    entities: Dict[str, Any] = Field(default_factory=dict, description="提取的实体信息")
    raw_query: str = Field(description="原始查询")
    priority: str = Field(default="中", description="优先级：高/中/低/转接")
    scenario_prompt: str = Field(default="", description="场景化提示词")

    class Config:
        """Pydantic配置"""
        json_schema_extra = {
            "example": {
                "intent": "product_search",
                "scenario_intent": "需求探索",
                "confidence": 0.95,
                "entities": {"category": "手机", "budget": 5000},
                "raw_query": "我想买个5000左右的手机",
                "priority": "高",
                "scenario_prompt": "## 推荐回复策略..."
            }
        }


# ==================== 规则引擎 ====================

class RuleEngine:
    """
    规则引擎

    基于关键词和模式匹配进行快速意图识别
    """

    # 意图关键词映射
    INTENT_KEYWORDS = {
        IntentType.PRODUCT_SEARCH: [
            "搜索", "找", "查找", "想要", "想买", "有没有", "推荐", "看看",
            "什么", "哪些", "怎么样", "如何"
        ],
        IntentType.PRODUCT_DETAIL: [
            "详情", "介绍", "参数", "配置", "规格", "多大", "多重",
            "什么牌子", "哪个品牌", "详细信息"
        ],
        IntentType.PRODUCT_COMPARE: [
            "对比", "比较", "哪个好", "区别", "差异", "vs", "还是",
            "选哪个", "怎么样", "推荐哪个", "怎么选", "二选一"
        ],
        IntentType.PRODUCT_RECOMMEND: [
            "推荐", "建议", "哪个好", "适合", "最好", "热门", "畅销",
            "销量", "排行", "口碑"
        ],
        IntentType.PRICE_INQUIRY: [
            "多少钱", "价格", "贵不贵", "便宜", "优惠", "折扣", "多少钱",
            "价位", "价格多少", "售价"
        ],
        IntentType.PURCHASE_ADVICE: [
            "值得买吗", "建议买吗", "怎么样", "好不好", "靠谱吗",
            "买哪个", "选哪个", "推荐买", "购买建议"
        ],
        IntentType.ORDER_QUERY: [
            "订单", "发货", "物流", "配送", "快递", "到哪了",
            "我的订单", "查订单"
        ],
        IntentType.COMPLAINT: [
            "投诉", "问题", "差劲", "垃圾", "太差", "不负责",
            "解决", "处理"
        ],
        IntentType.RETURN_REFUND: [
            "退货", "退款", "退换", "换货", "不想买了", "取消订单"
        ],
        IntentType.AFTER_SALES: [
            "售后", "保修", "维修", "客服", "联系", "电话"
        ],
        IntentType.GREETING: [
            "你好", "您好", "哈喽", "hi", "hello", "在吗", "在不在"
        ],
    }

    @classmethod
    def match_intent(cls, query: str) -> Tuple[str, float]:
        """
        基于关键词匹配意图

        Args:
            query: 用户查询

        Returns:
            (意图类型, 置信度)
        """
        query_lower = query.lower()

        # 统计各意图的命中次数
        intent_scores = {}
        for intent, keywords in cls.INTENT_KEYWORDS.items():
            score = sum(1 for kw in keywords if kw in query_lower)
            if score > 0:
                intent_scores[intent] = score

        if not intent_scores:
            return IntentType.UNKNOWN, 0.0

        # 返回得分最高的意图
        best_intent = max(intent_scores, key=intent_scores.get)
        # 置信度基于命中关键词数量，最高0.8
        confidence = min(0.8, intent_scores[best_intent] * 0.2)

        return best_intent, confidence

    @classmethod
    def extract_entities(cls, query: str, intent: str) -> Dict[str, Any]:
        """
        提取实体信息

        Args:
            query: 用户查询
            intent: 意图类型

        Returns:
            实体字典
        """
        entities = {}

        # 提取价格范围
        price_pattern = r'(\d+)元?[-~到至](\d+)元?'
        price_match = re.search(price_pattern, query)
        if price_match:
            entities["price_range"] = {
                "min": int(price_match.group(1)),
                "max": int(price_match.group(2))
            }

        # 提取预算
        budget_pattern = r'(\d+)元?(?:以内|以下|左右)'
        budget_match = re.search(budget_pattern, query)
        if budget_match:
            entities["budget"] = int(budget_match.group(1))

        compare_match = re.search(
            r'(?:.*?[，,:：]\s*)?([\u4e00-\u9fa5A-Za-z0-9\-\+]+?)\s*(?:和|还是|vs|VS)\s*([\u4e00-\u9fa5A-Za-z0-9\-\+]+?)(?:怎么选|哪个好|哪个更好|区别|对比|比较|值得买吗)?(?:[？?。！!]*)$',
            query.strip()
        )
        if compare_match:
            entities["products"] = [compare_match.group(1).strip(), compare_match.group(2).strip()]

        # 提取品牌（简单匹配，实际应该用品牌库）
        brands = [
            "苹果", "华为", "小米", "三星", "oppo", "vivo", "耐克", "阿迪达斯",
            "兰蔻", "雅诗兰黛", "SK-II", "科颜氏", "悦木之源", "欧莱雅", "资生堂",
            "理肤泉", "薇诺娜", "玉泽", "修丽可", "海蓝之谜", "贝德玛", "安热沙",
            "阿玛尼", "NARS", "植村秀", "YSL", "CPB", "芭比波朗", "MAC", "魅可",
            "花西子", "橘朵", "完美日记", "毛戈平", "迪奥"
        ]
        matched_brands = [brand for brand in brands if brand.lower() in query.lower()]
        if matched_brands:
            entities["brand"] = matched_brands[0]
            if len(matched_brands) > 1:
                entities["brands"] = matched_brands[:3]

        # 提取商品类别（简单匹配）
        category_aliases = {
            "护肤-精华水": ["神仙水", "精华水"],
            "护肤-精华": ["精华", "精华液", "精华露", "肌底液"],
            "护肤-面霜": ["面霜", "乳霜", "修护霜"],
            "护肤-爽肤水": ["爽肤水", "化妆水", "柔肤水", "菌菇水"],
            "护肤-防晒": ["防晒", "隔离"],
            "护肤-卸妆": ["卸妆", "洁肤液"],
            "护肤-洁面": ["洁面", "洗面奶"],
            "护肤-面膜": ["面膜"],
            "美妆-妆前": ["妆前", "妆前乳", "隔离乳", "妆前霜"],
            "美妆-粉底": ["粉底", "粉底液", "持妆粉底", "奶油肌粉底"],
            "美妆-气垫": ["气垫", "气垫粉底"],
            "美妆-散粉": ["散粉", "蜜粉", "粉饼", "定妆粉", "大白饼"],
            "美妆-遮瑕": ["遮瑕", "遮瑕液", "遮瑕盘"],
            "美妆-口红": ["口红", "唇釉", "唇泥", "小金条", "红管"],
            "美妆-腮红": ["腮红", "胭脂", "修容盘", "高光盘"],
            "手机": ["手机"],
            "电脑": ["电脑", "笔记本"],
            "耳机": ["耳机"],
            "鞋子": ["鞋子", "球鞋", "运动鞋"],
            "衣服": ["衣服", "外套", "裙子"],
            "包包": ["包包", "手袋"]
        }
        for category, aliases in category_aliases.items():
            if any(alias in query for alias in aliases):
                entities["category"] = category
                break

        # 提取肤质与问题
        skin_type_aliases = {
            "干性": ["干皮", "干性", "干敏肌", "干燥肌"],
            "油性": ["油皮", "油性", "大油皮"],
            "混合性": ["混油", "混干", "混合肌", "混合性"],
            "敏感性": ["敏感肌", "泛红肌", "脆弱肌", "干敏肌"]
        }
        skin_types = [skin_type for skin_type, aliases in skin_type_aliases.items() if any(alias in query for alias in aliases)]
        if skin_types:
            entities["skin_types"] = skin_types

        concern_aliases = {
            "抗初老": ["抗初老", "抗老", "抗衰", "细纹"],
            "保湿修护": ["保湿", "补水", "修护", "屏障"],
            "舒缓泛红": ["泛红", "敏感", "舒缓"],
            "祛痘控油": ["痘痘", "闭口", "控油", "出油"],
            "提亮": ["暗沉", "提亮", "美白"],
            "持妆控油": ["持妆", "控油", "不脱妆", "不斑驳"],
            "遮瑕修饰": ["遮瑕", "遮黑眼圈", "遮痘印", "修饰毛孔"],
            "妆感自然": ["自然", "裸妆", "服帖", "不假面"],
            "提气色": ["气色", "显白", "不挑皮", "日常通勤"]
        }
        concerns = [name for name, aliases in concern_aliases.items() if any(alias in query for alias in aliases)]
        if concerns:
            entities["skin_concerns"] = concerns

        # 提取明星单品/别名，优先绑定到更明确的商品
        product_aliases = {
            "小棕瓶": {"product_name": "雅诗兰黛小棕瓶精华", "brand": "雅诗兰黛", "category": "护肤-精华"},
            "小黑瓶": {"product_name": "兰蔻小黑瓶精华", "brand": "兰蔻", "category": "护肤-精华"},
            "神仙水": {"product_name": "SK-II神仙水", "brand": "SK-II", "category": "护肤-精华水"},
            "菌菇水": {"product_name": "悦木之源菌菇水", "brand": "悦木之源", "category": "护肤-爽肤水"},
            "粉水": {"product_name": "贝德玛粉水", "brand": "贝德玛", "category": "护肤-卸妆"},
            "DW": {"product_name": "雅诗兰黛DW持妆粉底液", "brand": "雅诗兰黛", "category": "美妆-粉底"},
            "权力粉底液": {"product_name": "阿玛尼权力粉底液", "brand": "阿玛尼", "category": "美妆-粉底"},
            "大白饼": {"product_name": "NARS裸光蜜粉饼", "brand": "NARS", "category": "美妆-散粉"},
            "小金条": {"product_name": "YSL小金条细管口红", "brand": "YSL", "category": "美妆-口红"},
        }
        matched_products = []
        for alias, info in product_aliases.items():
            if alias in query:
                matched_products.append(info["product_name"])
                if "product_alias" not in entities:
                    entities["product_alias"] = alias
                    entities["product_name"] = info["product_name"]
                    entities["brand"] = info["brand"]
                    entities.setdefault("category", info["category"])

        if matched_products and "products" not in entities:
            entities["products"] = matched_products[:3]
        elif (
            "products" not in entities
            and len(matched_brands) > 1
            and any(keyword in query for keyword in ["和", "还是", "vs", "怎么选", "哪个好"])
        ):
            entities["products"] = matched_brands[:3]

        return entities


# ==================== LLM意图识别 ====================

class LLMIntentClassifier:
    """
    基于LLM的意图分类器

    用于处理复杂、模糊的意图
    """

    # 意图分类提示词
    INTENT_CLASSIFICATION_PROMPT = """你是一个电商对话系统的意图分类器。

请分析用户输入，判断其意图类型，并提取关键实体。

**意图类型**：
1. product_search - 商品搜索（如：我想买手机、找运动鞋）
2. product_detail - 商品详情（如：iPhone 15的参数是什么）
3. product_compare - 商品对比（如：华为和小米哪个好）
4. product_recommend - 商品推荐（如：推荐一款耳机）
5. price_inquiry - 价格询问（如：这个多少钱）
6. purchase_advice - 购买建议（如：值得买吗）
7. order_query - 订单查询（如：我的订单到哪了）
8. complaint - 投诉（如：你们服务太差了）
9. return_refund - 退换货（如：我要退货）
10. after_sales - 售后咨询（如：怎么联系客服）
11. greeting - 问候（如：你好）
12. chitchat - 闲聊（如：今天天气真好）
13. unknown - 未知意图

**输出格式（JSON）**：
```json
{{
  "intent": "意图类型",
  "confidence": 0.95,
  "reasoning": "判断理由",
  "entities": {{
    "product": "商品名称",
    "brand": "品牌",
    "category": "类别",
    "price_range": {{"min": 100, "max": 500}}
  }}
}}
```

用户输入：{query}

请严格按照JSON格式输出："""

    def __init__(self):
        """初始化LLM分类器"""
        self.llm_service = get_llm_service()

    def _parse_json_response(self, response: str) -> Dict[str, Any]:
        """Parse fenced or plain JSON returned by the classifier LLM."""
        if not response:
            raise ValueError("empty intent response")

        json_match = re.search(r'```(?:json)?\s*(.*?)\s*```', response, re.DOTALL)
        if json_match:
            json_str = json_match.group(1).strip()
        else:
            object_match = re.search(r'\{.*\}', response, re.DOTALL)
            json_str = object_match.group(0).strip() if object_match else response.strip()

        return json.loads(json_str)

    async def classify(self, query: str) -> Optional[IntentResult]:
        """
        使用LLM进行意图分类

        Args:
            query: 用户查询

        Returns:
            IntentResult对象
        """
        try:
            prompt = self.INTENT_CLASSIFICATION_PROMPT.format(query=query)

            response = await self.llm_service.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,  # 低温度以获得稳定输出
                max_tokens=500
            )

            result = self._parse_json_response(response)

            return IntentResult(
                intent=result.get("intent", IntentType.UNKNOWN),
                confidence=result.get("confidence", 0.5),
                entities=result.get("entities", {}),
                raw_query=query
            )

        except Exception as e:
            logger.warning(f"LLM意图分类不可用，已回退到规则结果: {e}")
            return None


# ==================== 主意图识别服务 ====================

class IntentService:
    """
    意图识别服务

    结合规则引擎和LLM，实现快速准确的意图识别
    集成新的场景化意图分类系统
    """

    def __init__(self):
        """初始化意图识别服务"""
        self.rule_engine = RuleEngine()
        self.llm_classifier = LLMIntentClassifier()

        # 导入新的意图分类器
        from app.prompts.test_intent_classifier import IntentClassifier as ScenarioIntentClassifier
        self.scenario_classifier = ScenarioIntentClassifier()

        logger.info("✅ 意图识别服务初始化成功（含场景化意图）")

    def _get_scenario_intent(self, query: str) -> Dict[str, Any]:
        """
        使用新的场景化意图分类器

        Args:
            query: 用户查询

        Returns:
            场景意图结果
        """
        try:
            # 使用规则分类器（快速）
            result = self.scenario_classifier.classify_by_rules(query)

            # 获取优先级
            priority = INTENT_PRIORITIES.get(result["intent"], "中")

            # 提取场景变量（不包括user_input，单独传递）
            scenario_vars = self._extract_scenario_vars(query, result["intent"])

            # 生成场景提示词
            scenario_prompt = get_scenario_prompt(
                result["intent"],
                user_input=query,
                **scenario_vars
            )

            return {
                "scenario_intent": result["intent"],
                "priority": priority,
                "scenario_prompt": scenario_prompt,
                "method": result.get("method", "rule_based")
            }
        except Exception as e:
            logger.error(f"场景意图分类失败: {e}")
            return {
                "scenario_intent": ScenarioIntent.NEED_EXPLORATION,
                "priority": "中",
                "scenario_prompt": "",
                "method": "fallback"
            }

    def _extract_scenario_vars(self, query: str, intent: str) -> Dict[str, Any]:
        """
        提取场景变量用于生成提示词

        Args:
            query: 用户查询
            intent: 意图类型

        Returns:
            场景变量字典
        """
        scenario_vars = {}

        # 根据意图类型提取不同变量
        if intent == ScenarioIntent.COMPARISON_DECISION:
            # 提取对比的产品
            # 匹配 "A和B" 模式
            products = re.findall(r'(\w+(?:\s+\w+)?)\s*[和和vs]\s*(\w+(?:\s+\w+)?)', query)
            if products:
                scenario_vars["product_a"] = products[0][0]
                scenario_vars["product_b"] = products[0][1]

        elif intent == ScenarioIntent.NEED_EXPLORATION:
            # 提取预算和类别
            budget_match = re.search(r'(\d+)元?(?:以内|以下|左右)', query)
            if budget_match:
                scenario_vars["price_range"] = f"{budget_match.group(1)}元左右"

            # 提取类别
            for category in ["手机", "耳机", "笔记本", "手表", "平板", "护肤品", "精华", "面膜", "粉底", "气垫", "口红", "散粉", "妆前"]:
                if category in query:
                    scenario_vars["category"] = category
                    break

        return scenario_vars

    async def recognize(
        self,
        query: str,
        use_llm: bool = False,
        confidence_threshold: float = 0.6
    ) -> IntentResult:
        """
        识别用户意图

        Args:
            query: 用户查询
            use_llm: 是否强制使用LLM
            confidence_threshold: 置信度阈值，低于此值使用LLM

        Returns:
            IntentResult对象（包含场景意图）
        """
        logger.info(f"🔍 识别意图: {query}")

        # 步骤1：使用旧系统识别意图（保持兼容）
        intent, confidence = self.rule_engine.match_intent(query)
        entities = self.rule_engine.extract_entities(query, intent)

        # 步骤2：使用新系统识别场景意图
        scenario_result = self._get_scenario_intent(query)

        # 步骤3：按配置决定是否调用外部LLM分类器，默认使用规则+场景分类保持演示稳定
        should_use_llm = (
            use_llm or (
                settings.INTENT_LLM_ENABLED
                and confidence < settings.INTENT_LLM_CONFIDENCE_THRESHOLD
            )
        )

        if should_use_llm:
            logger.info(f"规则引擎置信度较低({confidence})，使用LLM增强...")
            llm_result = await self.llm_classifier.classify(query)

            if llm_result:
                # 融合结果：LLM优先
                intent = llm_result.intent
                confidence = llm_result.confidence
                entities = {**entities, **llm_result.entities}
        elif confidence < confidence_threshold:
            logger.debug(
                "规则意图置信度较低但外部LLM分类未开启，使用场景分类兜底: "
                f"{scenario_result['scenario_intent']}"
            )

        # 构建结果
        result = IntentResult(
            intent=intent,
            scenario_intent=scenario_result["scenario_intent"],
            confidence=confidence,
            entities=entities,
            raw_query=query,
            priority=scenario_result["priority"],
            scenario_prompt=scenario_result["scenario_prompt"]
        )

        logger.info(
            f"✅ 意图识别结果: {result.intent} / {result.scenario_intent} "
            f"(置信度: {result.confidence:.2f}, 优先级: {result.priority})"
        )

        return result

    async def batch_recognize(
        self,
        queries: List[str]
    ) -> List[IntentResult]:
        """
        批量识别意图

        Args:
            queries: 查询列表

        Returns:
            IntentResult列表
        """
        results = []
        for query in queries:
            result = await self.recognize(query)
            results.append(result)
        return results


# ==================== 全局实例 ====================

_intent_service: IntentService = None


def get_intent_service() -> IntentService:
    """
    获取意图识别服务单例

    Returns:
        IntentService实例
    """
    global _intent_service
    if _intent_service is None:
        _intent_service = IntentService()
    return _intent_service


# ==================== 使用示例 ====================

"""
from app.services.intent import get_intent_service

service = get_intent_service()

# 单个查询
result = await service.recognize("我想买一双耐克运动鞋，预算500元")
print(f"意图: {result.intent}")
print(f"置信度: {result.confidence}")
print(f"实体: {result.entities}")
# 输出：
# 意图: product_search
# 置信度: 0.8
# 实体: {"brand": "耐克", "category": "鞋子", "budget": 500}

# 强制使用LLM
result = await service.recognize("这天气真不错，适合运动吗？", use_llm=True)

# 批量识别
queries = [
    "你好",
    "我想买手机",
    "华为和小米哪个好",
    "我要退货"
]
results = await service.batch_recognize(queries)
for r in results:
    print(f"{r.raw_query} -> {r.intent}")
"""
