"""
智能对话Agent服务

整合RAG、商品搜索、决策辅助等功能
提供统一的对话处理能力
支持多轮对话上下文
"""

from typing import List, Dict, Any, Optional, Tuple, AsyncGenerator
from datetime import datetime
import asyncio
import logging
import re

from app.services.llm import get_llm_service
from app.services.intent import get_intent_service, IntentResult
from app.services.rag import get_rag_retriever
from app.services.decision import get_comparison_service, get_recommendation_service
from app.services.conversation import get_conversation_service, ConversationContext
from app.services.decision_tree import get_decision_visualizer
from app.services.image_preprocessing import get_image_preprocessor
from app.services.prompts import (
    AGENT_SYSTEM_PROMPT,
    SHOPPING_RECOMMEND_PROMPT,
    CHITCHAT_PROMPT,
    build_profile_hint,
    format_product_info,
    get_product_fact_override,
)
from app.database.postgres import execute_query

logger = logging.getLogger(__name__)


class ShoppingAgent:
    """
    智能导购Agent

    核心功能：
    1. 理解用户需求（意图识别）
    2. 检索相关信息（RAG + 商品搜索）
    3. 分析决策（对比推荐）
    4. 生成回复（LLM）
    5. 记住上下文（多轮对话）
    """

    COPY_REPLACEMENTS = {
        "理想选择": "优先看",
        "不错的选择": "可作备选",
        "效果显著": "更偏功效型",
        "值得考虑": "可以纳入备选",
        "能够帮助": "偏向",
        "以其": "",
        "闻名": "为主",
        "著称": "为主",
        "受到欢迎": "偏常见",
        "受到推崇": "偏常见",
        "功能强大": "功能更集中",
        "满足需求": "贴合这次诉求",
        "适合各种肤质": "大部分肤质都能用",
        "多种肤质适用": "大部分肤质都能用",
        "超预算": "预算边缘",
    }

    SUBCAT_EXCLUSION_WORDS = [
        "眼霜", "面霜", "乳液", "眼膜", "面膜", "洁面", "洗面奶",
        "卸妆", "爽肤水", "化妆水", "防晒", "粉底", "气垫", "散粉",
        "口红", "唇釉", "遮瑕", "隔离", "眼部精华", "精华",
    ]

    COMPARE_KEYWORDS = ["对比", "比较", "哪个好", "怎么选", "还是", "vs", "二选一", "选哪个", "区别"]
    RECOMMEND_KEYWORDS = ["推荐", "适合", "想买", "值得买", "选", "求", "帮忙挑", "有没有", "介绍", "种草"]
    KNOWLEDGE_QUERY_PATTERNS = [
        "什么是", "是什么", "啥意思", "什么意思", "是啥意思",
        "注意什么", "要注意", "需要注意", "注意事项",
        "怎么用", "如何使用", "正确用法", "使用方法", "怎么涂", "怎么擦",
        "可以一起用吗", "能不能一起用", "能一起用吗", "可以搭配", "能不能搭配", "能搭配吗",
        "可以混用吗", "能不能混用", "能混用吗",
        "有用吗", "效果怎么样", "有什么功效", "有什么作用", "功效是什么", "作用是什么",
        "怎么建立耐受", "建立耐受", "需要建立耐受",
        "孕妇能用吗", "孕妇可以用吗", "孕期能用吗", "哺乳期能用吗",
        "敏感肌能用吗", "油皮能用吗", "干皮能用吗", "痘肌能用吗",
        "要避光吗", "需要避光吗", "要防晒吗", "需要防晒吗",
        "多久用一次", "可以天天用吗", "能天天用吗",
        "适合什么年龄", "几岁可以用", "多大可以用",
        "和什么区别", "有什么区别", "区别是什么",
        "成分是什么", "含酒精吗", "有酒精吗", "含香精吗", "致痘吗", "闷痘吗",
        "原理", "如何工作",
    ]
    BEAUTY_DOMAIN_KEYWORDS = [
        "护肤", "美妆", "化妆", "彩妆", "防晒", "粉底", "气垫", "口红", "唇釉", "眼影",
        "腮红", "散粉", "蜜粉", "遮瑕", "隔离", "妆前", "眉笔", "眼线", "睫毛膏", "高光",
        "修容", "卸妆", "洁面", "洗面奶", "爽肤水", "化妆水", "精华", "面霜", "乳液",
        "眼霜", "面膜", "眼膜", "精华水", "精华油", "护肤油", "润唇膏", "唇膜",
        "敏感肌", "干皮", "油皮", "混油", "混干", "中性皮", "痘痘肌", "痘肌",
        "黑头", "闭口", "毛孔", "暗沉", "泛红", "抗老", "抗初老", "美白", "淡斑",
        "祛痘", "痘印", "保湿", "补水", "控油", "修护", "屏障", "烟酰胺", "水杨酸",
        "玻尿酸", "视黄醇", "A醇", "玻色因", "维C", "神经酰胺", "氨基酸",
        "早C晚A", "早c晚a", "刷酸", "果酸", "壬二酸", "传明酸", "胜肽", "角鲨烷",
        "积雪草", "虾青素", "熊果苷", "泛醇", "B5", "维A", "VC", "维c",
        "抗氧化", "抗衰老", "抗皱", "紧致", "提亮", "去黄", "淡纹",
        "建立耐受", "不耐受", "搓泥", "闷痘", "致痘", "闷闭口", "卡粉", "浮粉", "脱妆", "斑驳",
        "护肤步骤", "护肤顺序", "护肤入门", "新手护肤", "成分", "搭配", "叠加",
        "SPF", "PA", "色号", "妆效", "定妆", "粉饼", "唇膏", "唇泥",
        "卸妆油", "卸妆膏", "卸妆水", "洗面霜", "洁颜",
    ]

    NON_BEAUTY_KEYWORDS = [
        "手机", "电脑", "笔记本", "平板", "耳机", "音响", "相机", "摄像机",
        "汽车", "电动车", "摩托车", "自行车", "轮胎", "机油",
        "电视", "冰箱", "洗衣机", "空调", "微波炉", "烤箱", "电饭煲", "吸尘器", "扫地机器人",
        "路由器", "键盘", "鼠标", "显示器", "显卡", "CPU", "主板", "硬盘", "内存",
        "游戏", "游戏机", "PS5", "Switch", "Xbox",
        "房子", "装修", "家具", "沙发", "床垫",
        "餐厅", "美食", "菜谱", "零食", "饮料", "白酒", "啤酒", "红酒",
        "电影", "电视剧", "小说", "音乐", "演唱会",
        "旅游", "酒店", "机票", "火车票", "飞机", "高铁",
        "股票", "基金", "理财", "保险", "贷款",
        "英语", "数学", "考研", "考试", "学习",
        "狗", "猫", "猫粮", "狗粮", "宠物",
    ]

    BEAUTY_SCENARIO_INTENTS = {
        "知识咨询", "需求探索", "品类咨询", "比价决策", "对比决策",
        "单品判断", "优惠活动", "库存咨询", "新品咨询",
    }

    @classmethod
    def _is_beauty_domain(cls, message: str, entities: Dict[str, Any], scenario_intent: str = "") -> bool:
        cat = entities.get("category") or ""
        if cat:
            beauty_cat_prefixes = ("护肤-", "彩妆-", "护肤", "彩妆", "防晒", "粉底", "气垫", "口红", "唇釉", "眼影",
                                   "腮红", "散粉", "蜜粉", "遮瑕", "隔离", "妆前", "眉笔", "眼线", "睫毛膏", "高光",
                                   "修容", "卸妆", "洁面", "洗面奶", "爽肤水", "化妆水", "精华", "面霜", "乳液",
                                   "眼霜", "面膜", "眼膜", "精华水", "精华油", "护肤油", "润唇膏", "唇膜", "香水",
                                   "唇膏", "唇泥", "粉饼")
            if any(cat.startswith(p) or cat == p for p in beauty_cat_prefixes):
                return True
        if entities.get("brand") or entities.get("products"):
            return True
        skin_types = entities.get("skin_types") or []
        skin_concerns = entities.get("skin_concerns") or []
        if skin_types or skin_concerns:
            return True
        has_beauty_kw = any(kw in message for kw in cls.BEAUTY_DOMAIN_KEYWORDS)
        if has_beauty_kw:
            return True
        has_non_beauty_kw = any(kw in message for kw in cls.NON_BEAUTY_KEYWORDS)
        if has_non_beauty_kw:
            return False
        if scenario_intent and scenario_intent in cls.BEAUTY_SCENARIO_INTENTS:
            return True
        return False

    @classmethod
    def _post_process_intent(cls, message: str, intent_result) -> None:
        """统一的意图后处理：用关键词对规则引擎结果做最终纠偏。"""
        detected_products = intent_result.entities.get("products", [])
        scenario = getattr(intent_result, "scenario_intent", "") or ""
        if len(detected_products) >= 2 and any(kw in message for kw in cls.COMPARE_KEYWORDS):
            intent_result.intent = "product_compare"
        elif (
            intent_result.intent == "unknown"
            and any(kw in message for kw in cls.RECOMMEND_KEYWORDS)
            and (
                intent_result.entities.get("category")
                or intent_result.entities.get("brand")
                or detected_products
            )
        ):
            intent_result.intent = "product_recommend"
        elif (
            intent_result.intent in ("product_search", "product_recommend", "purchase_advice", "product_detail", "unknown")
            and not cls._is_beauty_domain(message, intent_result.entities, scenario_intent=scenario)
        ):
            intent_result.intent = "chitchat"

        has_purchase_intent_words = any(kw in message for kw in ("推荐", "买", "种草", "求", "帮忙挑", "想买", "入手"))
        has_knowledge_pattern = any(kw in message for kw in cls.KNOWLEDGE_QUERY_PATTERNS)
        if (
            has_knowledge_pattern
            and not has_purchase_intent_words
            and not detected_products
            and not intent_result.entities.get("brand")
            and scenario not in ("售后服务", "比价决策", "对比决策")
        ):
            intent_result.scenario_intent = "知识咨询"
            intent_result.intent = "knowledge_query"
            intent_result.confidence = max(intent_result.confidence, 0.7)

    @staticmethod
    def _build_user_state_section(entities: Dict[str, Any]) -> str:
        """根据情绪/状态生成提示词片段，让LLM调整回复策略。"""
        state = entities.get("user_state", {}) if isinstance(entities, dict) else {}
        if not state:
            return ""
        hints = []
        if state.get("urgency") == "high":
            hints.append("用户比较着急，回复要简短直接，先给结论再说原因，不要铺垫太长")
        if state.get("sentiment") == "frustrated":
            hints.append("用户有点烦躁，语气要更诚恳直接，不要说套话，直接给出明确判断")
        if state.get("sentiment") == "concerned":
            hints.append("用户有顾虑，多讲注意事项和风险点，语气要让人安心")
        if state.get("decision_state") == "hesitating":
            hints.append("用户在纠结，要明确给出第一推荐，并说清楚什么情况下换备选")
        if "risk_aversion" in (state.get("concerns") or []):
            hints.append("用户求稳，优先推荐稳妥款，强调风险低、不容易翻车")
        if "budget_drift" in (state.get("concerns") or []):
            hints.append("用户在意预算，优先推荐预算内的款，超出预算的明确说清楚")
        if not hints:
            return ""
        return "\n### 🎯 回复策略\n" + "；".join(hints) + "。"

    @staticmethod
    def _canonical_product_name(name: str) -> str:
        """把口语别名映射成库内标准品名，避免对比时同系列多条混入。"""
        alias_map = {
            "小黑瓶": "兰蔻小黑瓶精华",
            "兰蔻小黑瓶": "兰蔻小黑瓶精华",
            "小棕瓶": "雅诗兰黛小棕瓶精华",
            "雅诗兰黛小棕瓶": "雅诗兰黛小棕瓶精华",
            "神仙水": "SK-II神仙水",
            "菌菇水": "悦木之源菌菇水",
            "大哥大": "理肤泉大哥大",
            "理肤泉大哥大": "理肤泉大哥大",
            "盾护": "珀莱雅盾护",
            "珀莱雅盾护": "珀莱雅盾护",
            "蓝胖子": "资生堂蓝胖子",
            "资生堂蓝胖子": "资生堂蓝胖子",
            "小金瓶": "安热沙小金瓶",
            "安热沙小金瓶": "安热沙小金瓶",
            "安耐晒": "安热沙小金瓶",
            "小白瓶": "OLAY小白瓶",
            "绿宝瓶": "赫莲娜绿宝瓶",
            "大红瓶": "SK-II大红瓶",
            "紫熨斗": "欧莱雅紫熨斗",
            "粉水": "兰蔻粉水",
            "金水": "科颜氏金盏花",
            "金盏花水": "科颜氏金盏花",
            "B5": "理肤泉B5",
            "安心霜": "理肤泉特安舒缓",
            "特安霜": "理肤泉特安舒缓",
            "净痘": "理肤泉DUO+",
            "DUO+": "理肤泉DUO+",
            "K乳": "理肤泉K乳",
            "AI乳": "理肤泉AI乳",
            "小白管": "兰蔻小白管",
            "兰蔻空气感": "兰蔻空气感防晒",
            "空气感防晒": "兰蔻空气感防晒",
            "小方瓶": "植村秀小方瓶",
            "DW粉底": "雅诗兰黛DW",
            "DW持妆": "雅诗兰黛DW",
        }
        cleaned = (name or "").strip()
        if cleaned in alias_map:
            return alias_map[cleaned]
        for alias, canonical in alias_map.items():
            if alias in cleaned:
                return canonical
        return cleaned

    @staticmethod
    async def _find_product_id_by_fuzzy_name(name: str) -> Optional[int]:
        """通过商品名模糊查找商品ID：精确匹配 → LIKE子串 → 多关键词AND匹配。"""
        canonical = ShoppingAgent._canonical_product_name(name)
        row = await execute_query(
            "SELECT id FROM products WHERE name = $1 LIMIT 1",
            canonical, fetch="one"
        )
        if row:
            return row["id"]
        row = await execute_query(
            "SELECT id FROM products WHERE name LIKE $1 "
            "ORDER BY (sales_count IS NULL), sales_count DESC, id ASC LIMIT 1",
            f"%{canonical}%", fetch="one"
        )
        if row:
            return row["id"]
        # 多关键词AND匹配：把输入拆成2字以上的中文/英文token，要求name同时包含所有token
        tokens = [t for t in re.findall(r'[\u4e00-\u9fa5A-Za-z0-9+]{2,}', canonical) if len(t) >= 2]
        # 过滤掉无区分度的通用词
        stop_tokens = {"防晒", "精华", "面霜", "乳液", "洁面", "洗面奶", "爽肤水", "眼霜", "面膜", "口红",
                       "推荐", "对比", "比较", "哪个", "怎么", "什么", "这个", "那个", "适合", "还是"}
        tokens = [t for t in tokens if t not in stop_tokens]
        if tokens and len(tokens) <= 5:
            conditions = " AND ".join([f"name LIKE ${i+1}::text" for i in range(len(tokens))])
            params = [f"%{t}%" for t in tokens]
            sql = f"SELECT id FROM products WHERE {conditions} ORDER BY (sales_count IS NULL), sales_count DESC, id ASC LIMIT 1"
            row = await execute_query(sql, *params, fetch="one")
            if row:
                return row["id"]
        return None

    @staticmethod
    def _format_db_product(product: Dict[str, Any], score: float = 98.0, reason: str = "") -> Dict[str, Any]:
        specs = product.get("specifications") or {}
        if isinstance(specs, str):
            try:
                import json as _json
                specs = _json.loads(specs)
            except Exception:
                specs = {}
        if not isinstance(specs, dict):
            specs = {}
        skincare_info = product.get("skincare_info") or {}
        if isinstance(skincare_info, str):
            try:
                import json as _json2
                skincare_info = _json2.loads(skincare_info)
            except Exception:
                skincare_info = {}
        if not isinstance(skincare_info, dict):
            skincare_info = {}

        def pick(*keys, default=""):
            for k in keys:
                v = specs.get(k)
                if v not in (None, "", [], {}):
                    return v
            for k in keys:
                v = skincare_info.get(k)
                if v not in (None, "", [], {}):
                    return v
            return default

        original_price = product.get("original_price")
        try:
            original_price = float(original_price) if original_price is not None else None
        except Exception:
            original_price = None

        price_band = pick("price_band")
        subcategory = pick("subcategory")
        volume = pick("volume")
        texture = pick("texture")
        usage_time = pick("usage_time")
        key_ingredients = pick("key_ingredients")
        concerns_raw = pick("concerns", default=[])
        if isinstance(concerns_raw, str):
            concerns_list = [c.strip() for c in concerns_raw.replace("；", ";").split(";") if c.strip()]
        elif isinstance(concerns_raw, list):
            concerns_list = [str(c).strip() for c in concerns_raw if str(c).strip()]
        else:
            concerns_list = []
        suitable_skin = pick("suitable_skin_types")
        target_users = pick("target_users")
        positioning = pick("positioning")
        shop_name = pick("shop_name")
        pitfalls = pick("pitfalls")
        source_type = pick("source_type")

        detail_url = product.get("detail_url") or ""
        is_real_detail = bool(detail_url) and any(domain in detail_url for domain in [
            "detail.tmall.com/item.htm", "detail.tmall.hk", "item.taobao.com",
            "item.jd.com", "item.m.jd.com", "npc.jd.com"
        ])

        raw_platform = product.get("platform") or ""
        platform = raw_platform
        if not platform:
            if "jd.com" in detail_url:
                platform = "京东"
            elif "tmall" in detail_url:
                platform = "天猫"
            elif "taobao" in detail_url:
                platform = "淘宝"
        elif platform.lower() in ("jd", "jingdong"):
            platform = "京东"
        elif platform.lower() in ("tmall",):
            platform = "天猫"
        elif platform.lower() in ("taobao",):
            platform = "淘宝"

        return {
            "id": product["id"],
            "name": product["name"],
            "brand": product.get("brand"),
            "category": product.get("category"),
            "subcategory": subcategory,
            "price": float(product["price"]) if product.get("price") is not None else None,
            "original_price": original_price,
            "description": product.get("description", ""),
            "detail_url": detail_url,
            "is_real_detail": is_real_detail,
            "platform": platform,
            "image_url": product.get("image_url") or "",
            "price_band": price_band,
            "shop_name": shop_name,
            "volume": volume,
            "texture": texture,
            "usage_time": usage_time,
            "key_ingredients": key_ingredients,
            "concerns": concerns_list,
            "suitable_skin_types": suitable_skin,
            "target_users": target_users,
            "positioning": positioning,
            "pitfalls": pitfalls,
            "source_type": source_type,
            "tags": product.get("tags", []),
            "stock": product.get("stock"),
            "match_score": score,
            "relevance": score,
            "rerank_reason": reason or "命中明确商品名，优先按单品意图推荐。"
        }

    @staticmethod
    def _merge_products(primary: List[Dict[str, Any]], secondary: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        merged: List[Dict[str, Any]] = []
        seen = set()
        for item in primary + secondary:
            item_id = item.get("id")
            if item_id in seen:
                continue
            seen.add(item_id)
            merged.append(item)
        return merged

    @staticmethod
    def _split_stream_text(text: str, chunk_size: int = 18) -> List[str]:
        """把本地兜底文案拆成小块，保证 LLM 过载时前端也能呈现流式吐字。"""
        if not text:
            return []
        return [text[i:i + chunk_size] for i in range(0, len(text), chunk_size)]

    @staticmethod
    def _is_single_product_judgement(message: str, exact_product_name: Optional[str]) -> bool:
        """识图/单品追问只判断当前商品，不扩展成多商品推荐。"""
        if not exact_product_name:
            return False
        judgement_keywords = [
            "这款", "这个", "能不能用", "能用吗", "可不可以用",
            "适不适合", "适合我吗", "敏感肌能不能用"
        ]
        return any(keyword in (message or "") for keyword in judgement_keywords)

    @staticmethod
    def _desired_compare_count(intent_result: IntentResult, fallback: int = 3) -> int:
        """按用户问法决定对比数量：明确两款就 2，明确三款就 3，点名商品优先按点名数量。"""
        entities = intent_result.entities or {}
        product_count = len(entities.get("products") or [])
        if product_count >= 2:
            return max(2, min(3, product_count))

        query = intent_result.raw_query or ""
        if re.search(r"(这三(?:个|款|支)?|三(?:个|款|支)|3\s*(?:个|款|支))", query):
            return 3
        if re.search(r"(这两(?:个|款|支)?|两个|两款|两支|2\s*(?:个|款|支)|二选一)", query):
            return 2

        return max(2, min(3, fallback))

    @staticmethod
    def _preferred_category(context: ConversationContext) -> Optional[str]:
        categories = context.user_profile.get("preferred_categories") or []
        return categories[0] if categories else None

    @classmethod
    def _sanitize_recommendation_copy(cls, text: str) -> str:
        """替换少量高频 AI/广告腔词，不改变商品和事实。不修改 **粗体标题行**。"""
        if not text:
            return ""
        lines = text.split("\n")
        sanitized_lines = []
        in_summary = False
        truncated = False
        for line in lines:
            if truncated:
                continue
            if line.strip().startswith("## ") and ("综合建议" in line or "总结" in line):
                in_summary = True
            if in_summary:
                alt_match = re.search(r"(备选参考|备选推荐|其他可选|另外.*款|还有.*款)", line)
                if alt_match:
                    truncated_line = line[:alt_match.start()].rstrip(" ，,。.")
                    if truncated_line:
                        sanitized_lines.append(truncated_line)
                    truncated = True
                    continue
            if line.strip().startswith("**") and line.strip().endswith("**"):
                sanitized_lines.append(line)
                continue
            if line.strip().startswith("#"):
                sanitized_lines.append(line)
                continue
            cleaned = line
            for source, target in cls.COPY_REPLACEMENTS.items():
                cleaned = cleaned.replace(source, target)
            is_param_line = bool(re.match(r"^\s*-\s*(参考价格|核心功效|核心成分|适合肤质|防晒指数|遮瑕|持妆|色号|质地|容量|规格|关键差异|价格|肤感|风险点|判断建议|核心特点|适合情况)[:：]", cleaned))
            if not is_param_line:
                cleaned = re.sub(r"适合[^，,。；\n]{0,24}敏感肌(?:肤)?(?:使用|人群)?", "敏感肌需先核成分表并局部试用", cleaned)
                cleaned = cleaned.replace("适合敏感肌", "敏感肌需先核成分表并局部试用")
                if "注意事项" not in cleaned:
                    cleaned = cleaned.replace("敏感肌友好", "敏感肌需先核成分表并局部试用")
                cleaned = re.sub(r"敏感肌肤?[^。；\n]{0,24}(?:同样适用|也适用)", "敏感肌需先核成分表并局部试用", cleaned)
            sanitized_lines.append(cleaned)
        result = "\n".join(sanitized_lines)
        result = cls._strip_prompt_leakage(result)
        result = cls._strip_ai_closing(result)
        return result

    PROMPT_LEAK_PATTERNS = [
        (r"^\s*###\s*情况[AB][：:\s].*$", "___remove___"),
        (r"^\s*###\s*(?:情况A|情况B)\s*$", "___remove___"),
        (r"^\s*【[^】]*(?:推荐|选购|单品判断|对比)[^】]*】\s*$", "___remove___"),
        (r"^\s*【[^】]*(?:推荐|选购|单品判断|对比)[^】]*】\s*", ""),
        (r"^\s*(?:第一段|第二段|第三段|最后一段)(?:[（(][^）)]*[）)])?\s*[：:]\s*", ""),
        (r"^\s*然后(?:输出标题|写|每款商品一个块)[：:]\s*", ""),
        (r"^\s*接下来每款商品一个块[：:]\s*", ""),
        (r"^\s*开头(?:2-3句|直接给结论|2句)[：:]\s*", ""),
        (r"^\s*定位标签用[：:].*$", "___remove___"),
        (r"^\s*\*\*硬性要求[：:].*$", "___remove___"),
        (r"^\s*\*\*重要\*\*[：:].*$", "___remove___"),
    ]

    @classmethod
    def _strip_prompt_leakage(cls, text: str) -> str:
        lines = text.split("\n")
        cleaned = []
        for line in lines:
            new_line = line
            skip = False
            for pattern, replacement in cls.PROMPT_LEAK_PATTERNS:
                if re.match(pattern, line):
                    if replacement == "___remove___":
                        skip = True
                        break
                    new_line = re.sub(pattern, replacement, line)
                    break
            if not skip:
                cleaned.append(new_line)
        return "\n".join(cleaned).strip()

    AI_CLOSING_PATTERNS = [
        r"希望(?:这些|以上)?(?:建议|推荐|信息|内容).{0,40}$",
        r"以上(?:就是|是).{0,30}(?:推荐|建议|介绍|分享).{0,20}$",
        r"如果(?:你|您).{0,10}(?:还有)?(?:其他)?(?:问题|疑问|需要).{0,30}$",
        r"祝(?:你|您).{0,20}$",
        r"选购愉快[！!。.]*$",
        r"有(?:任何)?(?:问题|疑问).{0,20}(?:随时|欢迎).{0,20}$",
    ]

    @classmethod
    def _strip_ai_closing(cls, text: str) -> str:
        """砍掉 LLM 爱在最后加的一句总结/客套话（不影响商品块）。"""
        paragraphs = re.split(r"\n{2,}", text.strip())
        while len(paragraphs) > 1:
            last = paragraphs[-1].strip()
            if not last:
                paragraphs.pop()
                continue
            is_closing = False
            for pat in cls.AI_CLOSING_PATTERNS:
                if re.search(pat, last):
                    is_closing = True
                    break
            if not is_closing and len(last) < 40 and not last.startswith("**") and not last.startswith("-") and not last.startswith("##"):
                if re.search(r"(帮到你|祝你|希望|以上|选购|随时)", last):
                    is_closing = True
            if is_closing:
                paragraphs.pop()
            else:
                break
        return "\n\n".join(paragraphs)

    @staticmethod
    def _as_price(product: Dict[str, Any]) -> Optional[float]:
        try:
            if product.get("price") is None:
                return None
            return float(product["price"])
        except Exception:
            return None

    INLINE_BRAND_KEYWORDS = [
        "安热沙", "薇诺娜", "兰蔻", "雅诗兰黛", "珀莱雅", "理肤泉", "资生堂",
        "花西子", "科颜氏", "悦木之源", "贝德玛", "植村秀", "芙丽芳丝",
        "苏菲娜", "奥尔滨", "SK-II", "SK2", "欧莱雅", "玉兰油", "Olay",
        "雅漾", "修丽可", "适乐肤", "CeraVe", "悦诗风吟", "3CE", "迪奥",
        "香奈儿", "圣罗兰", "YSL", "纪梵希", "阿玛尼", "娇韵诗", "娇兰",
        "MAC", "mac", "魅可", "NARS", "nars", "芭比波朗", "Bobbi Brown",
        "露得清", "Neutrogena", "乐敦", "Rohto", "城野医生", "Dr.Ci:Labo",
        "珂润", "Curel", "怡丽丝尔", "ELIXIR", "茵芙莎", "IPSA",
        "海蓝之谜", "La Mer", "赫莲娜", "HR", "莱珀妮", "La Prairie",
        "倩碧", "Clinique", "雅顿", "伊丽莎白雅顿", "伊丽莎白·雅顿",
        "悦薇", "完美日记", "花知晓", "橘朵", "Judydoll", "卡姿兰",
        "玛丽黛佳", "毛戈平", "彩棠", "TIMAGE", "至本", "优时颜",
        "可复美", "敷尔佳", "芙清", "理肤泉", "雅莎尔", "修丽可",
        "The Ordinary", "the ordinary", "醉象", "Paula's Choice", "宝拉珍选",
        "CeraVe", "适乐肤", "丝塔芙", "Cetaphil", "凡士林", "Vaseline",
        "曼秀雷敦", "肌研", "乐敦CC", "黛珂", "DECORTE", "CPB", "肌肤之钥",
        "SUQQU", "Addiction", "Lunasol", "日月晶采", "KATE", "凯朵",
        "Canmake", "井田", "Kiss Me", "奇士美", "DHC", "蝶翠诗",
        "肌美精", "Kracie", "嘉娜宝", "Suisai", "水之天使",
    ]

    @classmethod
    def _extract_product_names_from_response(cls, response: str) -> List[str]:
        """从LLM输出中提取被作为商品推荐提及的名称。
        支持三种格式：
          1. **标签词：商品全名**  → 取冒号后的商品名
          2. **商品全名**（紧跟参考价格/核心参数行）→ 取整个粗体
          3. 正文段落中「比如xxx」「推荐xxx」「可以用xxx」等句式夹带的商品名（含知名品牌词）
        """
        names = []
        for line in (response or "").split("\n"):
            line = line.strip()
            if not line:
                continue
            # 格式1: **xxx：商品名** 或 **xxx:商品名**
            m = re.match(r"^\*\*(.+?)[：:]\s*(.+?)\*\*", line)
            if m:
                label_part = m.group(1).strip()
                product_part = m.group(2).strip()
                brand_keywords = cls.INLINE_BRAND_KEYWORDS
                label_contains_brand = any(b in label_part for b in brand_keywords)
                label_kws = ["首选", "之选", "推荐", "选择", "款", "友好", "性价比", "进阶", "平替",
                             "王牌", "明星", "网红", "敏感肌", "干皮", "油皮", "混油", "混干",
                             "扛把子", "战斗机", "天花板", "宝藏", "救星", "黑马", "平价", "贵妇",
                             "学生党", "通勤", "户外", "日常", "温和", "清爽", "保湿", "美白", "抗老"]
                is_label = (len(label_part) <= 10 and not label_contains_brand) or any(
                    kw in label_part for kw in label_kws
                )
                if is_label:
                    names.append(product_part)
                else:
                    names.append(label_part + "：" + product_part)
                continue
            # 格式2: **商品名** 独立成行（纯商品名加粗）
            m2 = re.match(r"^\*\*(.+?)\*\*", line)
            if m2:
                candidate = m2.group(1).strip()
                if not any(kw in candidate for kw in ["指南", "综合建议", "总结", "对比", "分析", "选购", "怎么选", "推荐理由", "注意事项"]):
                    if not candidate.startswith("#"):
                        names.append(candidate)
                continue
            # 格式3: 正文段落中夹带的商品名（括号/句式+品牌词）
            inline_patterns = [
                r"[（(](?:比如|例如|如|像|推荐|试试|可以选|可以用|建议用|建议选|比如试试|类似)[：: ]?([^）)]{2,40})[）)]",
                r"(?:比如|例如|如|像|推荐|试试|可以选|可以用|建议用|建议选)[：: ]?([^，。！？、；\n]{2,30}?)(?:[，。！？、；]|$)",
            ]
            for pat in inline_patterns:
                for mm in re.finditer(pat, line):
                    candidate = mm.group(1).strip()
                    if len(candidate) < 2 or len(candidate) > 35:
                        continue
                    if any(kw in candidate for kw in ["防晒", "保湿", "补水", "美白", "抗老", "精华", "面霜", "乳液", "步骤", "方法", "手法", "频率", "皮肤", "肌肤"]):
                        if not any(b in candidate for b in cls.INLINE_BRAND_KEYWORDS):
                            continue
                    if any(b in candidate for b in cls.INLINE_BRAND_KEYWORDS):
                        names.append(candidate)
        return names

    @classmethod
    def _extract_product_core_name(cls, name: str) -> str:
        """去掉SPF/PA/容量/括号/英文型号/独立数字等噪音，提取商品核心中文名用于模糊匹配。"""
        s = str(name or "")
        s = re.sub(r"[（(].*?[)）]", "", s)
        s = re.sub(r"SPF\s*\d*\+?", "", s, flags=re.I)
        s = re.sub(r"PA\s*\+*", "", s, flags=re.I)
        s = re.sub(r"\d+\s*(ml|g|片|支|盒|片装|套装|ml\+\d*g?)", "", s, flags=re.I)
        s = re.sub(r"SPF|PA|UV|UVA|UVB|UVMune\s*\d*", "", s, flags=re.I)
        s = re.sub(r"ANESSA|[A-Za-z][A-Za-z0-9]{2,}", "", s)
        s = re.sub(r"(?<![A-Za-z0-9])\d{2,4}(?![A-Za-z0-9])", "", s)
        s = re.sub(r"\s+", "", s)
        s = s.strip()
        return s

    @classmethod
    def _build_brand_variants(cls, brands_set: set) -> set:
        variants = set()
        for b in brands_set:
            for part in str(b).split("/"):
                part = part.strip()
                if part and len(part) >= 2:
                    variants.add(part)
        extra_aliases = {
            "YSL": {"圣罗兰"}, "圣罗兰": {"YSL"},
            "Dior": {"迪奥"}, "迪奥": {"Dior"},
            "MAC": {"魅可"}, "NARS": {"娜斯"},
            "SK-II": {"SK2", "SKII"}, "OLAY": {"玉兰油"},
            "IPSA": {"茵芙莎"}, "CPB": {"肌肤之钥"},
            "Shiseido": {"资生堂"}, "资生堂": {"Shiseido"},
            "Freeplus": {"芙丽芳丝"}, "芙丽芳丝": {"Freeplus"},
            "Winona": {"薇诺娜"}, "薇诺娜": {"Winona"},
            "ORIGINS": {"悦木之源"}, "悦木之源": {"ORIGINS"},
            "Dr.Yu": {"玉泽"}, "玉泽": {"Dr.Yu"},
            "Elta MD": {"安妍科"}, "EVE LOM": set(),
            "Fenty Beauty": set(), "The Ordinary": set(),
            "Urban Decay": set(),
        }
        for brand, aliases in extra_aliases.items():
            if brand in variants:
                variants.update(aliases)
        return variants

    @classmethod
    def _extract_brand_from_text(cls, text: str, known_brands: set) -> Optional[str]:
        for b in sorted(known_brands, key=len, reverse=True):
            if b in text:
                return b
        return None

    @classmethod
    def _bigrams(cls, s: str) -> set:
        return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) >= 2 else set()

    @classmethod
    def _matches_any_candidate(
        cls, core: str, allowed_names: set, known_brands: Optional[set] = None
    ) -> bool:
        if not core or len(core) < 3:
            return False
        brands_set = known_brands or set()
        known_brand_clean = cls._build_brand_variants(brands_set)
        generic_terms = {"防晒", "面霜", "精华", "眼霜", "乳液", "口红", "气垫", "粉底", "散粉",
                         "卸妆油", "水", "乳", "霜", "液", "膏", "护肤", "彩妆", "套装", "补水",
                         "保湿", "美白", "抗老", "控油", "清爽", "温和", "防水", "防汗", "高倍",
                         "遮瑕", "隔离", "妆前", "洁颜", "洁面", "洗面", "面膜", "水活", "凝蜜",
                         "凝露", "乳液", "精华水", "精华乳", "喷雾", "润色"}
        feature_nicknames = {"小金瓶", "小银瓶", "小金刚", "大红瓶", "小黑瓶", "小棕瓶", "神仙水",
                             "菌菇水", "双萃", "红腰子", "特护霜", "盾护", "金盏花", "高保湿",
                             "粉水", "流金水", "防晒水", "清透防晒", "大哥大", "小蓝瓶", "紫米",
                             "小方瓶", "权力", "无油", "有油", "B5", "DW", "水磁场", "黄油"}

        def _feature_tokens(s: str, brand: Optional[str]) -> set:
            tmp = s.replace(brand, "", 1) if brand else s
            tokens = set()
            for n in (2, 3, 4):
                for i in range(len(tmp) - n + 1):
                    t = tmp[i:i + n]
                    if t in generic_terms or t.isdigit():
                        continue
                    if all(ch in "，。！？、：；" for ch in t):
                        continue
                    tokens.add(t)
            for nick in feature_nicknames:
                if nick in tmp:
                    tokens.add(nick)
            return tokens

        def _bigram_overlap_ratio(s1: str, s2: str) -> float:
            bg1 = cls._bigrams(s1)
            bg2 = cls._bigrams(s2)
            if not bg1 or not bg2:
                return 0.0
            inter = bg1 & bg2
            return len(inter) / max(len(bg1), len(bg2))

        for a in allowed_names:
            a_core = cls._extract_product_core_name(a)
            if not a_core or len(a_core) < 3:
                continue
            if core in a_core:
                return True
            if a_core in core and a_core not in generic_terms:
                if len(a_core) >= 5 or cls._extract_brand_from_text(a_core, known_brand_clean):
                    return True
            core_brand = cls._extract_brand_from_text(core, known_brand_clean)
            a_brand = cls._extract_brand_from_text(a_core, known_brand_clean)
            if core_brand and a_brand and core_brand == a_brand:
                core_feats = _feature_tokens(core, core_brand)
                a_feats = _feature_tokens(a_core, a_brand)
                shared = core_feats & a_feats
                strong_shared = {f for f in shared if len(f) >= 3}
                if strong_shared:
                    return True
                nick_shared = shared & feature_nicknames
                if nick_shared:
                    return True
                ratio = _bigram_overlap_ratio(core.replace(core_brand, "", 1),
                                              a_core.replace(a_brand, "", 1))
                if ratio >= 0.5:
                    return True
        return False

    @classmethod
    def _response_violates_candidate_guard(
        cls,
        response: str,
        recommendations: List[Dict[str, Any]],
        budget: Optional[float] = None
    ) -> Tuple[bool, List[str]]:
        """检查生成结果是否混入候选外商品或明显越预算价格。
        返回 (是否违规, 违规商品名列表)。
        """
        if not response:
            return False, []

        allowed_names = {
            cls._canonical_product_name(str(item.get("name", "")))
            for item in recommendations
            if item.get("name")
        }
        allowed_brands = {
            str(item.get("brand", "")).strip()
            for item in recommendations
            if item.get("brand")
        }
        allowed_prices = {
            int(round(float(item["price"])))
            for item in recommendations
            if item.get("price") is not None
        }

        violations = []
        mentioned_names = cls._extract_product_names_from_response(response)
        for raw_name in mentioned_names:
            canon = cls._canonical_product_name(raw_name)
            if not canon or len(canon) < 2:
                continue
            # 精确匹配
            if canon in allowed_names:
                continue
            # 简称/昵称匹配：提取核心词（去掉SPF/PA/容量/括号内容/英文型号），
            # 看是否能匹配到候选商品名中的关键子串
            core = cls._extract_product_core_name(canon)
            if cls._matches_any_candidate(core, allowed_names, known_brands=allowed_brands):
                continue
            # 如果只是品牌名（如"薇诺娜""安热沙"），不视为违规（可能在综合建议里提到品牌）
            brand_canon = {cls._canonical_product_name(b) for b in cls._build_brand_variants(allowed_brands)}
            if canon in brand_canon:
                continue
            # 纯标签词(<4字)忽略
            if len(core) < 3:
                continue
            violations.append(raw_name)

        price_violation = False
        if budget:
            upper = float(budget) * 1.3
            for raw_price in re.findall(r"(?:¥|￥|约¥|参考价[：:]\s*约?¥?)\s*(\d{3,5})", response):
                try:
                    price = int(raw_price)
                except ValueError:
                    continue
                if price not in allowed_prices and price > upper:
                    price_violation = True
                    break

        return (bool(violations) or price_violation), violations

    @classmethod
    def _product_mentioned_in_response(cls, response: str, product: Dict[str, Any]) -> bool:
        text = response or ""
        raw_name = str(product.get("name", "") or "").strip()
        brand = str(product.get("brand", "") or "").strip()
        if not raw_name:
            return False
        variants = {
            raw_name,
            raw_name.split("：", 1)[0].strip(),
            raw_name.split(" ", 1)[0].strip(),
            cls._canonical_product_name(raw_name),
        }
        nickname_tokens = [
            "小黑瓶", "小棕瓶", "紫米", "ANESSA MEN", "DW", "小方瓶", "权力PRO",
            "小金瓶", "小银瓶", "小金刚", "大红瓶", "神仙水", "菌菇水", "双萃",
            "红腰子", "B5", "特护霜", "盾护", "金盏花", "高保湿", "粉水",
            "流金水", "防晒水", "清透防晒",
        ]
        matched_nicknames = [t for t in nickname_tokens if t in raw_name]
        variants.update(matched_nicknames)
        brand_clean = brand.split("/")[-1].strip() if "/" in brand else brand
        brands = {b for b in {brand, brand_clean} if b and len(b) >= 2}
        for b in brands:
            for nick in matched_nicknames:
                variants.add(b + nick)
            for v in list(variants):
                if len(v) <= 8 and v not in brands and b not in v:
                    variants.add(b + v)
        variants = {item for item in variants if item and len(item) >= 2}
        return any(item in text for item in variants)

    @classmethod
    def _missing_required_products(
        cls,
        response: str,
        recommendations: List[Dict[str, Any]],
        required_count: int,
    ) -> List[Dict[str, Any]]:
        """检查推荐正文是否覆盖了后端决定要展示的商品数量。"""
        if required_count <= 0:
            return []
        required = recommendations[:required_count]
        return [
            product
            for product in required
            if not cls._product_mentioned_in_response(response, product)
        ]

    @staticmethod
    def _format_price_for_copy(product: Dict[str, Any]) -> str:
        price = product.get("price")
        try:
            value = float(price)
            return f"¥{value:.0f}" if value >= 200 else f"¥{value:g}"
        except Exception:
            return "价格以官方页实时价为准"

    @classmethod
    def _build_missing_product_sections(cls, missing_products: List[Dict[str, Any]], category_name: str = "") -> str:
        """当模型漏写候选时，按后端候选事实补齐缺失商品段（对齐点点风格）。"""
        if not missing_products:
            return ""
        wrong_cat_keywords = {
            "面霜": ["洁面", "洗面奶", "洗面霜", "洁面霜", "眼霜", "面膜", "粉底液", "口红", "眼影", "眉笔", "散粉", "蜜粉", "卸妆", "精华水", "爽肤水", "防晒"],
            "精华": ["洁面", "洗面奶", "眼霜", "面膜", "粉底液", "口红", "面霜", "爽肤水", "防晒", "卸妆"],
            "爽肤水": ["洁面", "洗面奶", "眼霜", "面膜", "粉底液", "口红", "面霜", "精华液", "精华露", "防晒", "卸妆"],
            "防晒": ["洁面", "洗面奶", "眼霜", "面膜", "粉底液", "口红", "面霜", "爽肤水", "精华液", "卸妆"],
        }
        sections = []
        position_labels = ["同样推荐", "也值得看"]
        idx = 0
        for product in missing_products:
            if idx >= 2:
                break
            name = product.get("name") or "候选商品"
            p = product
            if p.get("key_ingredients") is None and p.get("is_real_detail") is None:
                p = cls._format_db_product(p, score=90.0, reason="备选商品")

            if category_name and category_name in wrong_cat_keywords:
                bad_kws = wrong_cat_keywords[category_name]
                name_str = name or ""
                desc_str = (p.get("description") or "")
                subcat = (p.get("subcategory") or "")

                def _has_cat(whole: str, cat: str) -> bool:
                    if cat not in whole:
                        return False
                    if cat == "面霜":
                        idx = whole.find("面霜")
                        prefix = whole[max(0, idx - 2):idx]
                        if prefix.endswith("洁") or prefix.endswith("洗") or "洁面" in prefix:
                            return False
                        return True
                    return True

                if subcat and subcat != category_name:
                    ok = False
                    if category_name == "精华" and "精华" in subcat:
                        ok = True
                    elif category_name == "面霜" and subcat in ("乳霜",):
                        ok = True
                    elif category_name == "防晒" and "防晒" in subcat:
                        ok = True
                    elif category_name == "爽肤水" and subcat in ("化妆水", "精华水", "精萃水", "精粹水", "美肤水"):
                        ok = True
                    if not ok:
                        continue

                is_wrong = False
                for bad in bad_kws:
                    if bad in name_str and not _has_cat(name_str, category_name):
                        is_wrong = True
                        break
                    if bad in desc_str[:80] and not _has_cat(desc_str[:80], category_name):
                        is_wrong = True
                        break
                if is_wrong:
                    continue

            price = cls._format_price_for_copy(p)
            label = position_labels[idx] if idx < len(position_labels) else "补充参考"
            idx += 1

            reason = (p.get("positioning") or "").strip()
            for turd in ["正常规格", "基础包装", "官方旗舰店", "定位：", "霜状", "瓶装", "净含量"]:
                reason = reason.replace(turd, "")
            reason = reason.strip("，。、；; ")
            if len(reason) > 50:
                reason = reason[:50].rstrip("，、；;。")
            if reason:
                reason = reason + "。"
            if not reason or len(reason) < 6:
                concerns = p.get("concerns") or []
                if isinstance(concerns, str):
                    concerns = [c.strip() for c in concerns.replace("；", ";").split(";") if c.strip()]
                brand = (p.get("brand") or "").strip()
                if concerns:
                    reason = f"{brand or name[:6]}这款{category_name or ''}主打{'、'.join(concerns[:2])}。"
                else:
                    desc = (p.get("description") or "").strip()
                    for sent in re.split(r"[。！!；;\n]", desc):
                        s = sent.strip(" ，,、")
                        if len(s) >= 8 and "未核" not in s and "以官方" not in s and "官方详情" not in s \
                           and "正常规格" not in s and "基础包装" not in s and "产地参数" not in s \
                           and "非特殊用途" not in s:
                            reason = s + "。"
                            break
            if not reason:
                reason = (p.get("rerank_reason") or "").strip()
                if len(reason) > 50:
                    reason = reason[:50].rstrip("，、；;。") + "。"
            if not reason:
                reason = "这款也在预算范围内，可作备选。"

            lines = [f"**{label}：{name}**", "", reason, ""]
            volume = (p.get("volume") or "").strip()
            ingredients = (p.get("key_ingredients") or "").strip()
            concerns = p.get("concerns") or []
            if isinstance(concerns, str):
                concerns = [c.strip() for c in concerns.replace("；", ";").split(";") if c.strip()]
            suitable = (p.get("suitable_skin_types") or "").strip()
            pitfalls = (p.get("pitfalls") or "").strip()

            lines.append(f"- 参考价格：约{price}" + (f" / {volume}" if volume else ""))
            if concerns:
                lines.append(f"- 核心功效：{'、'.join(concerns[:3])}")
            if ingredients and category_name in ("精华", "爽肤水"):
                lines.append(f"- 核心成分（品牌公开宣传）：{ingredients[:40]}")
            if suitable:
                lines.append(f"- 适合肤质：{suitable[:40]}")
            if not pitfalls:
                pitfalls = "下单前请核实实时价格，敏感肌建议先局部试用。"
            else:
                pitfalls = pitfalls[:80]
            lines.append(f"- 注意事项：{pitfalls}")
            sections.append("\n".join(lines))
        if not sections:
            return ""
        return "\n\n" + "\n\n".join(sections)

    @staticmethod
    def _insert_before_summary(full_text: str, patch_text: str) -> str:
        """把补齐的商品段插入到「综合建议/总结」收尾段之前。

        避免出现「总结收尾后又冒出新商品」的倒置阅读顺序；找不到收尾段时追加到末尾。
        """
        if not patch_text:
            return full_text
        body = patch_text.strip()
        if not body:
            return full_text
        match = re.search(r'(?m)^#{1,4}\s*(?:综合建议|总结建议|选购建议|总结)', full_text)
        if not match:
            return full_text.rstrip() + "\n\n" + body
        head = full_text[:match.start()].rstrip()
        tail = full_text[match.start():]
        return f"{head}\n\n{body}\n\n{tail}"

    @staticmethod
    def _product_text(product: Dict[str, Any]) -> str:
        specs = product.get("specifications") or product.get("specs") or {}
        if isinstance(specs, str):
            specs_text = specs
        else:
            specs_text = " ".join(str(v) for v in specs.values()) if isinstance(specs, dict) else ""
        return f"{product.get('name', '')} {product.get('category', '')} {product.get('description', '')} {specs_text}"

    @classmethod
    def _matches_subcategory(cls, product: Dict[str, Any], category: Optional[str]) -> bool:
        if not category or "-" not in str(category):
            return True
        subcat = str(category).split("-")[-1].strip()
        if not subcat:
            return True
        p_subcat = (product.get("subcategory") or "").strip()
        if p_subcat and p_subcat == subcat:
            return True
        if p_subcat and p_subcat != subcat:
            return False
        text = cls._product_text(product)
        if subcat == "面霜":
            for m in re.finditer(r"面霜", text):
                prefix = text[max(0, m.start() - 2):m.start()]
                if prefix.endswith("洁") or prefix.endswith("洗"):
                    continue
                return True
            return False
        if subcat == "精华":
            if any(kw in text for kw in ["精华水", "爽肤水", "精华爽"]):
                pass
            return "精华" in text
        return subcat in text

    @classmethod
    def _curate_ranked_products(
        cls,
        products: List[Dict[str, Any]],
        budget: Optional[float],
        budget_flexible: bool,
        category: Optional[str],
        limit: int = 5,
        preferred_brands: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """把 LLM/RAG 召回结果再做一层确定性整理，避免预算 1000 左右时混入 200/4000 档商品。"""
        import logging as _logc
        _logc.getLogger(__name__).warning(f"[DEBUG] _curate_ranked_products ENTRY: count={len(products) if products else 0}, ids={[p.get('id') for p in (products or [])]}")
        if not products:
            return []
        _logc.getLogger(__name__).warning(f"[DEBUG] _curate_ranked_products input: {len(products)} products, ids={[p.get('id') for p in products]}, cat={category}, budget={budget}, pref_brands={preferred_brands}")

        preferred_brands = [b for b in (preferred_brands or []) if b]

        deduped: List[Dict[str, Any]] = []
        seen = set()
        seen_canonical = set()
        seen_signatures = []
        for item in products:
            item_id = item.get("id") or item.get("name")
            if item_id in seen:
                continue
            canon = cls._canonical_product_name(str(item.get("name", "")))
            if canon in seen_canonical:
                continue
            brand = str(item.get("brand") or "").strip().lower()
            cat = str(item.get("subcategory") or item.get("category") or "").strip().lower()
            price = cls._as_price(item)
            desc = str(item.get("description") or "")[:200]
            name_raw = str(item.get("name") or "")
            core_tokens = set()
            for tk in re.findall(r'[\u4e00-\u9fa5A-Za-z0-9+]{2,}', name_raw):
                tl = tk.lower()
                if tl in {"防晒","精华","面霜","乳液","洁面","洗面奶","爽肤水","眼霜","面膜","口红","粉底","粉底液","气垫","遮瑕","散粉","高光","腮红","眼影","香水","新版","升级","官方","旗舰店","组合装","套装","正品","保湿","补水"}:
                    continue
                core_tokens.add(tl)
            sig = (brand, cat, price, desc, core_tokens)
            is_dup = False
            for prev_brand, prev_cat, prev_price, prev_desc, prev_tokens in seen_signatures:
                if brand and prev_brand and brand != prev_brand:
                    continue
                if cat and prev_cat and cat != prev_cat:
                    continue
                brand_match = (not brand) or (not prev_brand) or (brand == prev_brand)
                if not brand_match:
                    continue
                if price is not None and prev_price is not None and abs(price - prev_price) < 1.0:
                    if desc and prev_desc:
                        short = desc if len(desc) <= len(prev_desc) else prev_desc
                        long_ = prev_desc if short == desc else desc
                        if len(short) >= 20 and short[:int(len(short)*0.8)] in long_:
                            is_dup = True
                            break
                if core_tokens and prev_tokens:
                    overlap = 0
                    for t in core_tokens:
                        for pt in prev_tokens:
                            if t in pt or pt in t:
                                overlap += 1
                                break
                    max_side = max(len(core_tokens), len(prev_tokens))
                    if max_side > 0 and overlap >= max_side * 0.6:
                        is_dup = True
                        break
            if is_dup:
                continue
            seen.add(item_id)
            if canon:
                seen_canonical.add(canon)
            seen_signatures.append(sig)
            deduped.append(dict(item))
        _logc.getLogger(__name__).warning(f"[DEBUG] after dedup: {len(deduped)} products, ids={[p.get('id') for p in deduped]}")

        if category and "-" in str(category):
            matched = [p for p in deduped if cls._matches_subcategory(p, category)]
            if matched:
                deduped = matched
            else:
                subcat = str(category).split("-")[-1].strip()
                other_subcats = [w for w in cls.SUBCAT_EXCLUSION_WORDS if w != subcat]
                deduped = [p for p in deduped if not any(w in cls._product_text(p) for w in other_subcats)]

        budget_upper = None
        budget_lower = None
        if budget:
            budget = float(budget)
            if budget_flexible:
                budget_upper = budget * 1.25
                budget_lower = budget * 0.7
            else:
                budget_upper = budget
                budget_lower = None
            def _price_in_range(p):
                price = cls._as_price(p)
                if price is None:
                    return True
                if price > budget_upper:
                    return False
                if budget_lower is not None and price < budget_lower:
                    return False
                return True
            deduped = [p for p in deduped if _price_in_range(p)]
            if not deduped and budget_lower is not None:
                budget_lower = budget * 0.5
                deduped = [p for p in products if _price_in_range(p)]
            if not deduped:
                return []
        _logc.getLogger(__name__).warning(f"[DEBUG] after budget filter: {len(deduped)} products, ids={[p.get('id') for p in deduped]}")

        def score_product(product: Dict[str, Any]) -> float:
            raw_score = (
                product.get("match_score")
                or product.get("rerank_score")
                or product.get("relevance")
                or 50
            )
            try:
                score = float(raw_score)
            except Exception:
                score = 50.0
            if score <= 10:
                score *= 10

            if category and "-" in str(category) and not cls._matches_subcategory(product, category):
                score -= 200

            price = cls._as_price(product)
            if budget and price:
                distance = abs(price - float(budget)) / max(float(budget), 1)
                if distance <= 0.15:
                    score += 30
                elif distance <= 0.3:
                    score += 15 * (1 - (distance - 0.15) / 0.15)
                else:
                    score -= 10 * min(distance, 1.0)

            if product.get("image_url"):
                score += 4
            if product.get("detail_url"):
                score += 4

            prod_brand = str(product.get("brand", "")).strip()
            prod_name = str(product.get("name", ""))
            for pb in preferred_brands:
                if pb and (pb in prod_brand or pb in prod_name or prod_brand in pb):
                    score += 25
                    break

            return score

        curated = sorted(deduped, key=lambda p: (score_product(p), -(cls._as_price(p) or 0)), reverse=True)
        for product in curated:
            score = max(0, min(99, round(score_product(product), 1)))
            product["match_score"] = score
            price = cls._as_price(product)
            if budget and price:
                if budget_flexible:
                    if abs(price - budget) / budget <= 0.2:
                        budget_reason = f"价格约¥{price:.0f}，贴合¥{float(budget):.0f}左右预算"
                    elif price < budget:
                        budget_reason = f"价格约¥{price:.0f}，低于¥{float(budget):.0f}预算"
                    else:
                        budget_reason = f"价格约¥{price:.0f}，略高于¥{float(budget):.0f}预算（柔性预算）"
                else:
                    budget_reason = f"价格约¥{price:.0f}，在¥{float(budget):.0f}预算内"
                existing_reason = (product.get("rerank_reason") or "").strip()
                if existing_reason == "保持原排序":
                    existing_reason = ""
                product["rerank_reason"] = f"{existing_reason}；{budget_reason}" if existing_reason else budget_reason
        _logc.getLogger(__name__).warning(f"[DEBUG] curated list (before limit): {[(p.get('id'), p.get('match_score')) for p in curated]}, limit={limit}, returning {min(len(curated), limit)}")
        return curated[:limit]

    def __init__(self):
        """初始化Agent"""
        self.llm_service = get_llm_service()
        self.intent_service = get_intent_service()
        self.rag_retriever = get_rag_retriever()
        self.comparison_service = get_comparison_service()
        self.recommendation_service = get_recommendation_service()
        self.conversation_service = get_conversation_service()
        self.decision_visualizer = get_decision_visualizer()
        self.image_preprocessor = get_image_preprocessor()
        logger.info("✅ 智能导购Agent初始化成功")

    async def chat(
        self,
        message: str,
        session_id: Optional[str] = None,
        context: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """
        处理用户消息（支持多轮对话上下文）

        Args:
            message: 用户消息
            session_id: 会话ID（用于多轮对话）
            context: 额外上下文信息

        Returns:
            Agent响应结果
        """
        # 生成或使用提供的session_id
        if not session_id:
            session_id = f"session_{datetime.now().timestamp()}"

        # 1. 加载对话上下文
        conv_context = await self.conversation_service.get_context(session_id)

        # 2. 意图识别
        intent_result = await self.intent_service.recognize(message)
        self._post_process_intent(message, intent_result)

        logger.info(f"意图识别: {intent_result.intent} (置信度: {intent_result.confidence})")
        logger.info(f"提取实体: {intent_result.entities}")
        logger.info(f"用户画像: {conv_context.get_profile_hint()}")

        # 3. 更新用户画像
        await self.conversation_service.update_profile(session_id, intent_result)

        # 4. 保存用户消息
        await self.conversation_service.add_message(
            session_id, "user", message, intent_result.intent
        )

        # 5. 根据意图类型处理
        if intent_result.intent == "greeting":
            response = await self._handle_greeting(conv_context)

        elif intent_result.intent == "chitchat":
            response = await self._handle_chitchat(message, conv_context)

        elif intent_result.intent == "product_compare":
            result = await self._handle_compare(intent_result, conv_context)
            # 保存助手回复
            await self.conversation_service.add_message(
                session_id, "assistant", result["response"], intent_result.intent
            )
            return result

        elif intent_result.intent in ["product_search", "product_recommend", "purchase_advice"]:
            result = await self._handle_shopping(intent_result, message, conv_context)
            # 保存助手回复
            await self.conversation_service.add_message(
                session_id, "assistant", result["response"], intent_result.intent
            )
            return result

        elif intent_result.intent == "price_inquiry":
            result = await self._handle_price_inquiry(intent_result, conv_context)
            # 保存助手回复
            await self.conversation_service.add_message(
                session_id, "assistant", result["response"], intent_result.intent
            )
            return result

        else:
            # 使用RAG增强的通用回复
            result = await self._handle_rag_query(message, intent_result, conv_context)
            # 保存助手回复
            await self.conversation_service.add_message(
                session_id, "assistant", result["response"], intent_result.intent
            )
            return result

        # 保存助手回复（简单意图）
        await self.conversation_service.add_message(
            session_id, "assistant", response, intent_result.intent
        )

        return self._format_response(response, intent_result, source_type="rule")

    async def chat_stream_events(
        self,
        message: str,
        session_id: Optional[str] = None,
        context: Optional[Dict] = None,
        intent_result: Optional[IntentResult] = None
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        处理用户消息并尽早产出可渲染事件。

        非流式 chat 会等检索、排序、LLM 生成全部完成后才返回；这里把主链路拆成
        阶段事件 + 商品/引用事件 + LLM token，避免前端长时间空白。
        """
        if not session_id:
            session_id = f"session_{datetime.now().timestamp()}"

        conv_context = await self.conversation_service.get_context(session_id)

        yield {"event": "stage", "data": {"message": "理解你的需求", "status": "active"}}
        if intent_result is None:
            intent_result = await self.intent_service.recognize(message)
        self._post_process_intent(message, intent_result)

        yield {
            "event": "intent",
            "data": {
                "intent": intent_result.intent,
                "confidence": intent_result.confidence,
                "entities": intent_result.entities
            }
        }
        if intent_result.scenario_intent:
            yield {
                "event": "scenario_intent",
                "data": {
                    "scenario_intent": intent_result.scenario_intent,
                    "priority": intent_result.priority
                }
            }

        await self.conversation_service.update_profile(session_id, intent_result)
        await self.conversation_service.add_message(
            session_id, "user", message, intent_result.intent
        )

        if intent_result.intent == "greeting":
            response = await self._handle_greeting(conv_context)
            yield {"event": "message", "data": {"content": response, "done": False}}
            await self.conversation_service.add_message(session_id, "assistant", response, intent_result.intent)
            yield {"event": "end", "data": {"metadata": {"source_type": "rule"}}}
            return

        if intent_result.intent == "chitchat":
            yield {"event": "stage", "data": {"message": "整理一句自然回复", "status": "active"}}
            response = await self._handle_chitchat(message, conv_context)
            yield {"event": "message", "data": {"content": response, "done": False}}
            await self.conversation_service.add_message(session_id, "assistant", response, intent_result.intent)
            yield {"event": "end", "data": {"metadata": {"source_type": "llm"}}}
            return

        if intent_result.intent == "product_compare":
            async for event in self._stream_compare(intent_result, conv_context):
                yield event
            return

        if intent_result.intent in ["product_search", "product_recommend", "purchase_advice", "product_detail"]:
            async for event in self._stream_shopping(intent_result, message, conv_context):
                yield event
            return

        if intent_result.intent == "price_inquiry":
            result = await self._handle_price_inquiry(intent_result, conv_context)
            for event_name in ["products", "citations", "pitfalls", "decision_process"]:
                key = event_name if event_name != "decision_process" else "decision_process"
                if result.get(key):
                    yield {"event": event_name, "data": {key: result[key]}}
            yield {"event": "message", "data": {"content": result["response"], "done": False}}
            await self.conversation_service.add_message(session_id, "assistant", result["response"], intent_result.intent)
            yield {"event": "end", "data": {"metadata": result.get("metadata", {})}}
            return

        yield {"event": "stage", "data": {"message": "检索相关资料", "status": "active"}}
        result = await self._handle_rag_query(message, intent_result, conv_context)
        if result.get("products"):
            yield {"event": "products", "data": {"products": result["products"]}}
        if result.get("citations"):
            yield {"event": "citations", "data": {"citations": result["citations"]}}
        if result.get("pitfalls"):
            yield {"event": "pitfalls", "data": {"pitfalls": result["pitfalls"]}}
        yield {"event": "message", "data": {"content": result["response"], "done": False}}
        await self.conversation_service.add_message(session_id, "assistant", result["response"], intent_result.intent)
        yield {"event": "end", "data": {"metadata": result.get("metadata", {})}}

    async def _stream_compare(
        self,
        intent_result: IntentResult,
        context: ConversationContext
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """流式处理商品对比。"""
        yield {"event": "stage", "data": {"message": "定位要对比的商品", "status": "active"}}
        entities = intent_result.entities
        product_names = entities.get("products", [])
        desired_count = self._desired_compare_count(
            intent_result,
            fallback=len(context.mentioned_products) if context.mentioned_products else 3
        )

        if not product_names and context.mentioned_products:
            product_ids = [p["id"] for p in context.mentioned_products[:desired_count]]
        else:
            product_ids = []
            if product_names:
                for name in product_names[:desired_count]:
                    pid = await self._find_product_id_by_fuzzy_name(name)
                    if pid and pid not in product_ids:
                        product_ids.append(pid)

        if len(product_ids) < 2:
            category = entities.get("category") or self._preferred_category(context)
            if category:
                products = await execute_query(
                    "SELECT * FROM products WHERE category = $1 LIMIT 5",
                    category,
                    fetch="all"
                )
                product_ids = [p["id"] for p in products[:desired_count]]

        if len(product_ids) < 2:
            response = "请问你想对比哪两款商品？可以直接发商品名，例如“安热沙金瓶和兰蔻空气感怎么选”。"
            yield {"event": "message", "data": {"content": response, "done": False}}
            await self.conversation_service.add_message(context.session_id, "assistant", response, intent_result.intent)
            yield {"event": "end", "data": {"metadata": {"source_type": "rule"}}}
            return

        yield {"event": "stage", "data": {"message": "读取商品事实与价格口径", "status": "active"}}
        comparison = await self.comparison_service.compare_products(product_ids[:desired_count])
        products = [
            self._format_db_product(p, score=90.0, reason="商品对比场景")
            for p in comparison["products"][:desired_count]
        ]
        comparison["products"] = products
        yield {"event": "products", "data": {"products": products}}
        yield {"event": "comparison", "data": {"comparison": comparison}}

        from app.services.prompts import PRODUCT_COMPARE_PROMPT, format_comparison_table

        focus_parts = []
        profile_hint = context.get_profile_hint()
        if profile_hint:
            focus_parts.append(profile_hint)
        if entities.get("skin_types"):
            focus_parts.append(f"肤质: {', '.join(entities['skin_types'])}")
        if entities.get("skin_concerns"):
            focus_parts.append(f"诉求: {', '.join(entities['skin_concerns'])}")
        if entities.get("budget"):
            focus_parts.append(f"预算: {entities['budget']} 元左右")
        if entities.get("category"):
            focus_parts.append(f"品类: {entities['category']}")
        user_focus = " | ".join(focus_parts) or "综合对比"

        prompt = PRODUCT_COMPARE_PROMPT.format(
            products_info=format_comparison_table(products),
            user_focus=user_focus
        )
        messages = [
            {"role": "system", "content": AGENT_SYSTEM_PROMPT},
            {"role": "user", "content": prompt}
        ]

        yield {"event": "stage", "data": {"message": "生成对比结论", "status": "active"}}
        full_response = ""
        max_cmp_retries = 2
        for attempt in range(max_cmp_retries + 1):
            try:
                raw = await self.llm_service.chat(messages, temperature=0.5, max_tokens=1000)
            except Exception as e:
                logger.warning(f"对比LLM生成失败(尝试{attempt+1}): {e}")
                raw = ""
            candidate_response = (raw or "").strip()
            if not candidate_response:
                continue
            is_violation, violated_names = self._response_violates_candidate_guard(
                candidate_response, products)
            if not is_violation:
                full_response = candidate_response
                logger.info(f"[candidate_guard] compare attempt={attempt+1} 通过校验")
                break
            logger.warning(f"[candidate_guard] compare attempt={attempt+1} 幻觉: {violated_names}")
            correction = (
                f"你上一次对比中提到了不在候选商品中的产品：{', '.join(violated_names)}。"
                f"只能对比以下商品，不能编造：\n"
                + "\n".join([f"- {p.get('brand','')} {p.get('name','')}" for p in products])
                + "\n\n请重新生成对比结论，严格使用上述真实商品。"
            )
            messages.append({"role": "assistant", "content": raw or ""})
            messages.append({"role": "user", "content": correction})

        if not full_response.strip():
            logger.warning("[candidate_guard] compare 多次校验失败，切换本地对比兜底")
            yield {"event": "stage", "data": {"message": "切换本地确定性对比", "status": "fallback"}}
            full_response = await self._generate_compare_response(comparison, context.get_profile_hint())

        full_response = self._sanitize_recommendation_copy(full_response)
        yield {"event": "message", "data": {"content": full_response, "done": False}}

        await self.conversation_service.add_message(context.session_id, "assistant", full_response, intent_result.intent)
        yield {
            "event": "end",
            "data": {
                "metadata": {
                    "source_type": "decision_stream",
                    "timestamp": datetime.now().isoformat()
                }
            }
        }

    async def _stream_shopping(
        self,
        intent_result: IntentResult,
        message: str,
        context: ConversationContext
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """流式处理搜索、推荐、购买建议。"""
        entities = intent_result.entities
        profile = context.user_profile
        requirements = {
            "use_case": entities.get("use_case") or profile.get("use_case") or "通用",
            "priorities": []
        }

        budget = entities.get("budget") or profile.get("budget")
        if budget:
            requirements["budget"] = budget

        preferred_brands = profile.get("preferred_brands", [])
        if "brand" in entities:
            brand = entities["brand"]
            if brand not in preferred_brands:
                preferred_brands.append(brand)
        if preferred_brands:
            requirements["preferences"] = {"brand": preferred_brands[0]}

        preferred_cats = profile.get("preferred_categories", [])

        image_brand = None
        image_category = None
        image_product_keyword = None
        has_image_anchor = ("补充图片线索：" in message) or ("图片匹配商品" in message) or ("【图片匹配商品】" in message)
        if has_image_anchor:
            import re as _re
            bm = _re.search(r'品牌[：:]\s*([^，,；;。\n【】]{1,30})', message)
            if bm:
                image_brand = bm.group(1).strip()
            cm = _re.search(r'品类[：:]\s*([^，,；;。\n【】（）()]{1,20})', message)
            if cm:
                image_category = cm.group(1).strip()
            km = _re.search(r'单品[：:]\s*([^，,；;。\n【】]{1,50})', message)
            if km:
                image_product_keyword = km.group(1).strip()
            if not image_product_keyword:
                km2 = _re.search(r'识别商品是[：:]\s*([^（(，,；;。\n]{2,40})', message)
                if km2:
                    image_product_keyword = km2.group(1).strip()
            if image_brand and image_brand not in preferred_brands:
                preferred_brands.append(image_brand)
                requirements["preferences"] = {"brand": image_brand}
            if image_brand:
                entities["brand"] = image_brand
            if image_category and image_category not in preferred_cats:
                preferred_cats.insert(0, image_category)
            if image_category:
                entities["category"] = image_category
            if image_product_keyword:
                entities["product_keyword"] = image_product_keyword

        _CAT_ALIAS = {
            "妆前乳": "隔离妆前", "隔离": "隔离妆前",
            "蜜粉": "散粉", "粉饼": "散粉",
            "口红": "口红", "唇膏": "口红", "唇釉": "口红", "唇泥": "口红",
            "粉底液": "粉底液", "粉底": "粉底液", "粉霜": "粉底液",
            "香水": "香水", "香氛": "香水", "淡香水": "香水", "浓香水": "香水",
            "遮瑕": "遮瑕", "遮瑕液": "遮瑕", "遮瑕膏": "遮瑕", "遮瑕盘": "遮瑕",
        }

        def _normalize_category(cat: Optional[str]) -> Optional[str]:
            if not cat:
                return None
            cat = str(cat).strip()
            if "-" in cat:
                parts = [p.strip() for p in cat.split("-") if p.strip()]
                if len(parts) >= 2:
                    cat = parts[-1]
            if "/" in cat:
                candidates = [p.strip() for p in cat.split("/") if p.strip()]
                for c in candidates:
                    if c in _CAT_ALIAS:
                        return _CAT_ALIAS[c]
                cat = candidates[0]
            return _CAT_ALIAS.get(cat, cat)

        has_explicit_product_intent = bool(
            entities.get("category")
            or entities.get("brand")
            or entities.get("products")
            or entities.get("product_name")
            or image_category
            or image_brand
            or image_product_keyword
        )
        scenario = getattr(intent_result, "scenario_intent", "") or ""
        is_knowledge_query = (scenario == "知识咨询") and not has_explicit_product_intent

        if entities.get("category"):
            category = _normalize_category(entities["category"])
        elif image_category:
            category = _normalize_category(image_category)
        elif is_knowledge_query:
            category = None
        else:
            category = _normalize_category(preferred_cats[0]) if preferred_cats else None
        exact_product_name = entities.get("product_name") or image_product_keyword
        is_single_product_judgement = self._is_single_product_judgement(message, exact_product_name)

        rag_filters = {}
        if category:
            rag_filters["category"] = category
        if budget:
            rag_filters["budget"] = budget
            if entities.get("budget_flexible"):
                rag_filters["price_max"] = round(budget * 1.25)
                rag_filters["price_min"] = round(budget * 0.7)
            else:
                rag_filters["price_max"] = budget
            rag_filters["budget_flexible"] = bool(entities.get("budget_flexible"))
        if entities.get("skin_types"):
            rag_filters["skin_types"] = entities["skin_types"]
        if entities.get("skin_concerns"):
            rag_filters["skin_concerns"] = entities["skin_concerns"]

        yield {"event": "stage", "data": {"message": "检索知识库和商品库", "status": "active"}}

        is_image_followup = ("【图片匹配商品】" in message) or ("补充图片线索：" in message) or ("图片匹配商品" in message)
        is_image_recommendation = is_image_followup and bool(image_category) and bool(
            "相似" in message or "同类" in message or "替代" in message
            or "同款" in message or "类似" in message or "平替" in message
            or "还有什么" in message or "别的" in message or "其他" in message
            or "同价位" in message or "同品牌" in message
        )

        if is_image_followup and image_category:
            import re as _re2
            user_question = _re2.split(r'【图片匹配商品】|补充图片线索：|图片匹配商品', message, maxsplit=1)[0].strip()
            search_query = f"{user_question} {image_category}"
        else:
            search_query = message

        rag_result = await self.rag_retriever.retrieve(
            query=search_query,
            filters=rag_filters or None,
            top_k=10,
            search_products=True,
            search_knowledge=True
        )

        if is_image_recommendation and category:
            existing_pids = [p.get("id") for p in rag_result.get("products", []) if p.get("id")]
            import logging as _logfb
            _logfb.getLogger(__name__).warning(f"[DEBUG] image_reco fallback: cat={category}, existing_pids={existing_pids}, rag products count={len(rag_result.get('products',[]))}")
            try:
                if existing_pids:
                    extra_cat_sql = """
                        SELECT * FROM products
                        WHERE category = $1 AND id != ANY($2)
                        ORDER BY rating DESC NULLS LAST, price ASC
                        LIMIT 8
                    """
                    extra_rows = await execute_query(extra_cat_sql, category, existing_pids, fetch="all")
                else:
                    extra_cat_sql = """
                        SELECT * FROM products
                        WHERE category = $1
                        ORDER BY rating DESC NULLS LAST, price ASC
                        LIMIT 8
                    """
                    extra_rows = await execute_query(extra_cat_sql, category, fetch="all")
                _logfb.getLogger(__name__).warning(f"[DEBUG] fallback SQL got {len(extra_rows) if extra_rows else 0} rows")
                if extra_rows:
                    existing_names = {p.get("name", "") for p in rag_result["products"]}
                    for row in extra_rows:
                        ep = self._format_db_product(dict(row), score=45.0, reason=f"同品类({category})补充候选")
                        if ep.get("name") not in existing_names:
                            rag_result["products"].append(ep)
            except Exception as e:
                import logging as _logging
                _logging.getLogger(__name__).warning(f"图片推荐同品类兜底查询失败: {e}", exc_info=True)

        if rag_result.get("citations"):
            yield {"event": "citations", "data": {"citations": rag_result["citations"]}}
        if rag_result.get("pitfalls"):
            yield {"event": "pitfalls", "data": {"pitfalls": rag_result["pitfalls"]}}

        user_context = {
            "skin_type": profile.get("skin_type") or (entities.get("skin_types") or [None])[0],
            "skin_concerns": profile.get("skin_concerns", []) or entities.get("skin_concerns", []),
            "budget": budget,
            "preferred_brands": preferred_brands
        }
        user_context = {k: v for k, v in user_context.items() if v is not None}

        yield {"event": "stage", "data": {"message": "按肤质、场景和预算排序", "status": "active"}}
        rerank_result = await self.rag_retriever.rerank(
            results=rag_result,
            query=message,
            user_context=user_context
        )

        knowledge_context = ""
        if rerank_result.get("knowledge"):
            knowledge_context = "\n".join([
                f"- {k['title']}: {k['content'][:100]}..."
                for k in rerank_result["knowledge"][:2]
            ])

        ranked_products = rerank_result.get("products", [])[:10]

        if exact_product_name:
            exact_hits = await execute_query(
                """
                SELECT *
                FROM products
                WHERE name ILIKE $1
                ORDER BY
                    CASE
                        WHEN name = $2 THEN 0
                        WHEN name ILIKE $3 THEN 1
                        ELSE 2
                    END,
                    id DESC
                LIMIT 3
                """,
                f"%{exact_product_name}%",
                exact_product_name,
                f"%{entities.get('product_alias', exact_product_name)}%",
                fetch="all"
            )
            if exact_hits:
                exact_products = [
                    self._format_db_product(
                        product,
                        score=99.0,
                        reason=f"命中明确单品「{entities.get('product_alias', exact_product_name)}」，优先展示该商品。"
                    )
                    for product in exact_hits
                ]
                if is_single_product_judgement:
                    ranked_products = exact_products[:1]
                else:
                    ranked_products = self._merge_products(exact_products, ranked_products)

        ranked_products = self._curate_ranked_products(
            ranked_products,
            budget=budget,
            budget_flexible=bool(entities.get("budget_flexible")),
            category=category,
            limit=1 if is_single_product_judgement else 8,
            preferred_brands=preferred_brands,
        )
        import logging as _logx
        _logx.getLogger(__name__).warning(f"[DEBUG][{context.session_id}] after curate: {len(ranked_products)} products, ids={[p.get('id') for p in ranked_products]}")

        def _ensure_formatted(p: Dict[str, Any]) -> Dict[str, Any]:
            if p.get("key_ingredients") is not None or p.get("is_real_detail") is not None:
                return p
            score = float(p.get("match_score") or p.get("relevance") or 98.0)
            reason = p.get("rerank_reason") or ""
            return ShoppingAgent._format_db_product(p, score=score, reason=reason)

        _logx.getLogger(__name__).warning(f"[DEBUG][{context.session_id}] before _ensure_formatted: {len(ranked_products)}")
        ranked_products = [_ensure_formatted(p) for p in ranked_products]
        _logx.getLogger(__name__).warning(f"[DEBUG][{context.session_id}] after _ensure_formatted: {len(ranked_products)}")

        if is_knowledge_query and ranked_products:
            query_tokens = set(re.findall(r'[\u4e00-\u9fa5A-Za-z0-9+]{2,}', message))
            stop_tokens = {"什么","怎么","如何","可以","入门","新手","意思","推荐","建议","请问","一下","这个","那个","有没有","哪个","牌子","品牌","护肤","美妆","化妆"}
            query_tokens -= stop_tokens
            filtered = []
            for p in ranked_products:
                p_text = ShoppingAgent._product_text(p)
                p_tokens = set(re.findall(r'[\u4e00-\u9fa5A-Za-z0-9+]{2,}', p_text))
                overlap = len(query_tokens & p_tokens)
                score = float(p.get("match_score") or p.get("relevance") or 0)
                if overlap >= 1 or score >= 70:
                    filtered.append(p)
            ranked_products = filtered

        if ranked_products:
            latest_context = await self.conversation_service.get_context(context.session_id)
            if not isinstance(latest_context.mentioned_products, list):
                latest_context.mentioned_products = []
            latest_context.mentioned_products.extend(ranked_products)
            await self.conversation_service.save_context(latest_context)
            context = latest_context
            yield {"event": "products", "data": {"products": ranked_products[:6]}}
        elif is_knowledge_query and (rerank_result.get("knowledge") or rerank_result.get("citations") or rerank_result.get("pitfalls")):
            yield {"event": "stage", "data": {"message": "整理护肤知识回答", "status": "active"}}
            knowledge_answer = await self._generate_knowledge_answer(
                query=message,
                rag_result=rerank_result,
                profile_hint=context.get_profile_hint(),
            )
            yield {"event": "message", "data": {"content": knowledge_answer, "done": False}}
            await self.conversation_service.add_message(context.session_id, "assistant", knowledge_answer, intent_result.intent)
            yield {
                "event": "end",
                "data": {
                    "metadata": {
                        "source_type": "knowledge_rag",
                        "timestamp": datetime.now().isoformat(),
                        "has_citations": bool(rerank_result.get("citations")),
                        "has_pitfalls": bool(rerank_result.get("pitfalls"))
                    }
                }
            }
            return
        else:
            display_message = message.split("补充图片线索：")[0].strip() if "补充图片线索：" in message else message
            display_message = display_message.split("\n")[0].strip()[:60]
            fallback = self._build_no_product_match_reply(
                message=display_message,
                category=category,
                budget=budget,
                knowledge=knowledge_context
            )
            yield {"event": "message", "data": {"content": fallback, "done": False}}
            await self.conversation_service.add_message(context.session_id, "assistant", fallback, intent_result.intent)
            yield {
                "event": "end",
                "data": {
                    "metadata": {
                        "source_type": "knowledge_only_fallback",
                        "timestamp": datetime.now().isoformat(),
                        "has_citations": bool(rag_result.get("citations")),
                        "has_pitfalls": bool(rag_result.get("pitfalls"))
                    }
                }
            }
            return

        final_recommendation = ranked_products[0] if ranked_products else {}
        decision_tree = None
        if ranked_products:
            intent_dict = {
                "intent": intent_result.intent,
                "scenario_intent": intent_result.scenario_intent,
                "priority": intent_result.priority,
                "confidence": intent_result.confidence,
                "category": category,
                "budget": budget,
                "skin_types": entities.get("skin_types", []),
                "skin_concerns": entities.get("skin_concerns", []),
                "user_state": entities.get("user_state", {}),
                "preferences": {"brands": preferred_brands} if preferred_brands else []
            }
            decision_tree = await self.decision_visualizer.analyze_and_build_tree(
                session_id=context.session_id,
                user_query=message,
                intent_result=intent_dict,
                retrieved_products=ranked_products,
                final_recommendation=final_recommendation
            )
            yield {"event": "decision_process", "data": {"decision_process": decision_tree.to_dict()}}

        product_info = "\n".join([format_product_info(r) for r in ranked_products[:6]]) or "暂无直接命中的商品，请结合下面的知识依据给出购买判断。"
        profile_hint = context.get_profile_hint()
        profile_section = f"\n### 👤 用户画像\n{profile_hint}" if profile_hint else ""
        user_state_section = self._build_user_state_section(entities)
        prompt = SHOPPING_RECOMMEND_PROMPT.format(
            user_request=message,
            profile_section=profile_section,
            user_state_section=user_state_section,
            product_info=product_info,
            knowledge_section=knowledge_context if knowledge_context else "暂无相关知识"
        )
        messages = [
            {"role": "system", "content": AGENT_SYSTEM_PROMPT},
            {"role": "user", "content": prompt}
        ]

        yield {"event": "stage", "data": {"message": "生成推荐结论", "status": "active"}}

        candidate_pool = ranked_products[:6]
        max_retries = 2
        full_response = ""
        used_local_fallback = False

        retry_messages = [dict(m) for m in messages]
        for attempt in range(max_retries + 1):
            try:
                raw = await self.llm_service.chat(retry_messages, temperature=0.4, max_tokens=1200)
            except Exception as e:
                logger.warning(f"LLM生成失败(尝试{attempt+1}): {e}")
                raw = ""
            candidate_response = (raw or "").strip()
            if not candidate_response:
                continue
            is_violation, violated_names = self._response_violates_candidate_guard(
                candidate_response, candidate_pool, budget=budget)
            if not is_violation:
                full_response = candidate_response
                logger.info(f"[candidate_guard] attempt={attempt+1} 通过校验")
                break
            logger.warning(
                f"[candidate_guard] attempt={attempt+1} 检测到幻觉商品: {violated_names}，"
                f"允许列表: {[p.get('name','')[:20] for p in candidate_pool]}"
            )
            correction = (
                f"你上一次回答中推荐了以下不在候选列表中的商品：{', '.join(violated_names)}。"
                f"这些商品不在我给你的候选商品清单中，属于编造的。\n"
                f"你**只能**从以下候选商品中选择推荐（可全部推荐也可选部分，但不能编造）：\n"
                + "\n".join([f"- {p.get('brand','')} {p.get('name','')}（¥{p.get('price','?')}）" for p in candidate_pool])
                + "\n\n请重新生成推荐，严格使用上述真实存在的商品，不要编造任何不在列表中的商品名。"
            )
            retry_messages.append({"role": "assistant", "content": raw or ""})
            retry_messages.append({"role": "user", "content": correction})

        if not full_response.strip():
            logger.warning("[candidate_guard] 多次重试均未通过校验，切换本地确定性兜底")
            yield {"event": "stage", "data": {"message": "模型未严格遵循候选约束，切换本地确定性推荐", "status": "fallback"}}
            full_response = self._build_local_recommendation_fallback(
                message=message,
                recommendations=ranked_products,
                knowledge=knowledge_context
            )
            used_local_fallback = True

        full_response = self._sanitize_recommendation_copy(full_response)

        yield {"event": "message", "data": {"content": full_response, "done": False}}

        await self.conversation_service.add_message(context.session_id, "assistant", full_response, intent_result.intent)
        yield {
            "event": "end",
            "data": {
                "metadata": {
                    "source_type": "hybrid_rerank_stream",
                    "timestamp": datetime.now().isoformat(),
                    "has_citations": bool(rag_result.get("citations")),
                    "has_pitfalls": bool(rag_result.get("pitfalls"))
                }
            }
        }

    async def _handle_greeting(self, context: ConversationContext) -> str:
        """处理问候（使用优化的Prompt，根据上下文个性化）"""
        interaction_count = context.user_profile.get("interaction_count", 0)

        if interaction_count <= 1:
            # 首次访问
            return """你好！我是你的智能购物顾问 🛒

我可以帮你：
- 🔍 **找商品**：告诉我你的需求，我帮你筛选
- 📊 **做对比**：拿不准选哪个，我帮你分析
- 💡 **给建议**：不知道怎么选，我给你推荐
- 📚 **解疑惑**：对商品有疑问，我帮你解答

请问有什么可以帮你的？"""
        else:
            # 回访用户
            profile_hint = build_profile_hint(context.user_profile)
            last_interest = self._preferred_category(context) or "购物"

            return f"""欢迎回来！👋

{f"**你的偏好**：{profile_hint}" if profile_hint else ""}

你之前关注过 **{last_interest}**，要继续了解吗？还是有新的购物需求？

我可以继续帮你：
- 🔍 搜索商品
- 💡 推荐好物
- 📊 对比产品
- 📚 解答疑问"""

    async def _handle_chitchat(self, message: str, context: ConversationContext) -> str:
        """处理闲聊（带上下文，使用优化Prompt）"""
        profile_hint = build_profile_hint(context.user_profile)
        conversation_summary = context.get_context_summary(max_messages=3)

        prompt = CHITCHAT_PROMPT.format(
            profile_hint=profile_hint,
            conversation_summary=conversation_summary or "暂无对话历史",
            user_message=message
        )

        messages = [
            {"role": "system", "content": AGENT_SYSTEM_PROMPT},
            {"role": "user", "content": prompt}
        ]
        try:
            answer = await self.llm_service.chat(messages, temperature=0.8, max_tokens=200)
        except Exception as e:
            logger.warning(f"闲聊LLM失败: {e}")
            answer = "有什么护肤或美妆问题可以随时问我哦～"
        answer = (answer or "").strip()
        answer = answer.replace('\\"', '"').replace("\\'", "'")
        quote_pairs = [('"', '"'), ('"', '"'), ("'", "'"), ('「', '」'), ('『', '』'), ('(', ')'), ('（', '）')]
        changed = True
        while changed and len(answer) >= 2:
            changed = False
            for q_open, q_close in quote_pairs:
                if answer[0] == q_open and answer[-1] == q_close:
                    answer = answer[1:-1].strip()
                    changed = True
                    break
        for meta_pat in [r"（\d+字.*?）$", r"\(\d+字.*?\)$", r"（.*?符合.*?）$", r"\(.*?符合.*?\)$"]:
            answer = re.sub(meta_pat, "", answer).strip()
        return answer

    async def _handle_compare(
        self,
        intent_result: IntentResult,
        context: ConversationContext
    ) -> Dict[str, Any]:
        """处理商品对比（使用上下文）"""
        entities = intent_result.entities

        # 尝试从实体中提取商品名称
        product_names = entities.get("products", [])
        desired_count = self._desired_compare_count(
            intent_result,
            fallback=len(context.mentioned_products) if context.mentioned_products else 3
        )

        # 如果没有明确商品，从上下文中找
        if not product_names and context.mentioned_products:
            # 使用之前提到的商品
            product_ids = [p["id"] for p in context.mentioned_products[:desired_count]]
        else:
            # 根据商品名称查找ID（优化：批量查询，避免N+1）
            product_ids = []
            if product_names:
                # 每个商品名单独定位一条最匹配的，避免一个名称召回多条导致对比数量漂移。
                for name in product_names[:desired_count]:
                    pid = await self._find_product_id_by_fuzzy_name(name)
                    if pid and pid not in product_ids:
                        product_ids.append(pid)

        # 如果商品不足，搜索相关商品
        if len(product_ids) < 2:
            category = entities.get("category") or self._preferred_category(context)
            if category:
                products = await execute_query(
                    "SELECT * FROM products WHERE category = $1 LIMIT 5",
                    category,
                    fetch="all"
                )
                product_ids = [p["id"] for p in products[:desired_count]]

        # 执行对比
        if len(product_ids) >= 2:
            comparison = await self.comparison_service.compare_products(product_ids[:desired_count])
            comparison["products"] = comparison["products"][:desired_count]

            # 生成回复
            response = await self._generate_compare_response(
                comparison,
                context.get_profile_hint()
            )

            return self._format_response(
                response,
                intent_result,
                products=comparison["products"],
                comparison_data=comparison,
                source_type="decision"
            )
        else:
            response = "请问您想对比哪些商品？可以告诉我商品名称或类别。"
            return self._format_response(response, intent_result, source_type="rule")

    async def _handle_shopping(
        self,
        intent_result: IntentResult,
        message: str,
        context: ConversationContext
    ) -> Dict[str, Any]:
        """
        处理购物需求（搜索、推荐）

        整合RAG检索 + 商品搜索 + 决策辅助 + 用户画像
        """
        entities = intent_result.entities
        profile = context.user_profile

        # 1. 构建需求参数（结合实体和用户画像）
        requirements = {
            "use_case": entities.get("use_case") or profile.get("use_case") or "通用",
            "priorities": []
        }

        # 添加预算（优先使用当前请求，否则使用历史预算）
        budget = entities.get("budget") or profile.get("budget")
        if budget:
            requirements["budget"] = budget

        # 添加品牌偏好（合并当前和历史）
        preferred_brands = profile.get("preferred_brands", [])
        if "brand" in entities:
            brand = entities["brand"]
            if brand not in preferred_brands:
                preferred_brands.append(brand)

        if preferred_brands:
            requirements["preferences"] = {"brand": preferred_brands[0]}

        # 添加类别偏好
        preferred_cats = profile.get("preferred_categories", [])
        category = entities.get("category") or (preferred_cats[0] if preferred_cats else None)
        exact_product_name = entities.get("product_name")
        is_single_product_judgement = self._is_single_product_judgement(message, exact_product_name)

        rag_filters = {}
        if category:
            rag_filters["category"] = category
        if budget:
            rag_filters["budget"] = budget
            if entities.get("budget_flexible"):
                rag_filters["price_max"] = round(budget * 1.25)
                rag_filters["price_min"] = round(budget * 0.7)
            else:
                rag_filters["price_max"] = budget
            rag_filters["budget_flexible"] = bool(entities.get("budget_flexible"))
        if entities.get("skin_types"):
            rag_filters["skin_types"] = entities["skin_types"]
        if entities.get("skin_concerns"):
            rag_filters["skin_concerns"] = entities["skin_concerns"]

        # 2. 使用RAG检索相关知识（增强版：包含来源引用和避坑提示）
        rag_result = await self.rag_retriever.retrieve(
            query=message,
            filters=rag_filters or None,
            top_k=10,  # 先召回更多
            search_products=True,
            search_knowledge=True
        )

        # 3. LLM精排（结合用户画像）
        user_context = {
            "skin_type": profile.get("skin_type") or (entities.get("skin_types") or [None])[0],
            "skin_concerns": profile.get("skin_concerns", []) or entities.get("skin_concerns", []),
            "budget": budget,
            "preferred_brands": preferred_brands
        }
        # 移除None值
        user_context = {k: v for k, v in user_context.items() if v is not None}

        rerank_result = await self.rag_retriever.rerank(
            results=rag_result,
            query=message,
            user_context=user_context
        )

        knowledge_context = ""
        if rerank_result.get("knowledge"):
            knowledge_context = "\n".join([
                f"- {k['title']}: {k['content'][:100]}..."
                for k in rerank_result["knowledge"][:2]
            ])

        # 4. 获取商品推荐（使用精排后的结果）
        ranked_products = rerank_result.get("products", [])[:10]

        if exact_product_name:
            exact_hits = await execute_query(
                """
                SELECT *
                FROM products
                WHERE name ILIKE $1
                ORDER BY
                    CASE
                        WHEN name = $2 THEN 0
                        WHEN name ILIKE $3 THEN 1
                        ELSE 2
                    END,
                    id DESC
                LIMIT 3
                """,
                f"%{exact_product_name}%",
                exact_product_name,
                f"%{entities.get('product_alias', exact_product_name)}%",
                fetch="all"
            )
            if exact_hits:
                exact_products = [
                    self._format_db_product(
                        product,
                        score=99.0,
                        reason=f"命中明确单品「{entities.get('product_alias', exact_product_name)}」，优先展示该商品。"
                    )
                    for product in exact_hits
                ]
                if is_single_product_judgement:
                    ranked_products = exact_products[:1]
                else:
                    ranked_products = self._merge_products(exact_products, ranked_products)

        ranked_products = self._curate_ranked_products(
            ranked_products,
            budget=budget,
            budget_flexible=bool(entities.get("budget_flexible")),
            category=category,
            limit=1 if is_single_product_judgement else 8,
            preferred_brands=preferred_brands,
        )
        import logging as _logx
        _logx.getLogger(__name__).warning(f"[DEBUG][{context.session_id}] after curate: {len(ranked_products)} products, ids={[p.get('id') for p in ranked_products]}")

        def _ensure_formatted(p: Dict[str, Any]) -> Dict[str, Any]:
            if p.get("key_ingredients") is not None or p.get("is_real_detail") is not None:
                return p
            score = float(p.get("match_score") or p.get("relevance") or 98.0)
            reason = p.get("rerank_reason") or ""
            return ShoppingAgent._format_db_product(p, score=score, reason=reason)

        ranked_products = [_ensure_formatted(p) for p in ranked_products]

        if not ranked_products:
            no_match = self._build_no_product_match_reply(
                message=message, category=category, budget=budget,
                knowledge=knowledge_context,
            )
            await self.conversation_service.add_message(context.session_id, "assistant", no_match, intent_result.intent)
            return self._format_response(
                no_match, intent_result, sources=rerank_result.get("knowledge", []),
                citations=rag_result.get("citations", []), pitfalls=rag_result.get("pitfalls", []),
                source_type="knowledge_only_fallback",
            )

        # 5. 更新上下文中的商品记录
        final_recommendation = None
        if ranked_products:
            if not isinstance(context.mentioned_products, list):
                context.mentioned_products = []
            context.mentioned_products.extend(ranked_products)
            # 保存更新后的上下文
            await self.conversation_service.save_context(context)
            final_recommendation = ranked_products[0]

        # 6. 构建决策树（可视化决策过程）
        intent_dict = {
            "intent": intent_result.intent,
            "scenario_intent": intent_result.scenario_intent,
            "priority": intent_result.priority,
            "confidence": intent_result.confidence,
            "category": category,
            "budget": budget,
            "skin_types": entities.get("skin_types", []),
            "skin_concerns": entities.get("skin_concerns", []),
            "user_state": entities.get("user_state", {}),
            "preferences": {"brands": preferred_brands} if preferred_brands else []
        }

        decision_tree = await self.decision_visualizer.analyze_and_build_tree(
            session_id=context.session_id,
            user_query=message,
            intent_result=intent_dict,
            retrieved_products=ranked_products or [],
            final_recommendation=final_recommendation or {}
        )

        # 7. 生成回复（带上下文感知）
        response = await self._generate_recommendation_response(
            message=message,
            requirements=requirements,
            recommendations=ranked_products,
            knowledge=knowledge_context,
            advice=ranked_products[0].get("rerank_reason", "") if ranked_products else "",
            profile_hint=context.get_profile_hint(),
            entities=entities
        )

        return self._format_response(
            response,
            intent_result,
            products=ranked_products,
            sources=rerank_result.get("knowledge", []),
            citations=rag_result.get("citations", []),  # 增强的来源引用
            pitfalls=rag_result.get("pitfalls", []),    # 避坑提示
            source_type="hybrid_rerank",
            decision_tree=decision_tree.to_dict()
        )

    async def _handle_price_inquiry(
        self,
        intent_result: IntentResult,
        context: ConversationContext
    ) -> Dict[str, Any]:
        """处理价格询问（使用上下文）"""
        entities = intent_result.entities

        # 如果指定了商品，查询该商品价格
        if "products" in entities:
            product_name = entities["products"][0]
            products = await execute_query(
                "SELECT * FROM products WHERE name LIKE $1",
                f"%{product_name}%",
                fetch="all"
            )

            if products:
                p = products[0]
                override = get_product_fact_override(p)
                price_text = (
                    f"{override['price_label']}（{override['price_source']}）"
                    if override.get("price_label")
                    else f"¥{p['price']}"
                )
                response = f"{p['name']} 的参考价格是 {price_text}"
                if p.get("original_price") and float(p["original_price"]) > float(p["price"]):
                    response += f"（原价 ¥{p['original_price']}）"
                response += f"\n\n{p.get('description', '')}"

                return self._format_response(
                    response,
                    intent_result,
                    products=[p],
                    source_type="database"
                )

        # 如果上下文中有最近提到的商品
        elif context.mentioned_products:
            p = context.mentioned_products[-1]
            override = get_product_fact_override(p)
            price_text = (
                f"{override['price_label']}（{override['price_source']}）"
                if override.get("price_label")
                else f"¥{p.get('price', '请咨询客服')}"
            )
            response = f"您刚才问的 {p['name']} 参考价格是 {price_text}"
            return self._format_response(
                response,
                intent_result,
                products=[p],
                source_type="context"
            )

        response = "关于价格，请问您想了解哪款商品？我可以帮您查询。"
        return self._format_response(response, intent_result, source_type="rule")

    async def _handle_rag_query(
        self,
        message: str,
        intent_result: IntentResult,
        context: ConversationContext
    ) -> Dict[str, Any]:
        """
        使用RAG增强的通用问答（带上下文，使用优化的系统Prompt）

        检索知识库 + 商品信息，生成回复
        """
        is_knowledge_query = getattr(intent_result, 'scenario_intent', '') == '知识咨询' or intent_result.intent == 'knowledge_query'

        # 使用RAG检索
        rag_result = await self.rag_retriever.rag_query(message, top_k=5)

        # 知识问答不返回商品货架，避免推荐不相关商品
        if is_knowledge_query:
            rag_result["products"] = []
            rag_result["pitfalls"] = []

        # 如果需要重新生成回复以使用更好的系统提示
        if rag_result.get("response"):
            # 返回增强的RAG结果（包含来源引用和避坑提示）
            return self._format_response(
                rag_result["response"],
                intent_result,
                products=rag_result.get("products", []),
                sources=rag_result.get("knowledge", []),  # 旧格式兼容
                citations=rag_result.get("citations", []),  # 新格式：增强引用
                pitfalls=rag_result.get("pitfalls", []),    # 新格式：避坑提示
                source_type="rag"
            )

        # 如果没有RAG结果，使用系统提示生成通用回复
        profile_hint = build_profile_hint(context.user_profile)
        is_knowledge_query = getattr(intent_result, 'scenario_intent', '') == '知识咨询' or intent_result.intent == 'knowledge_query'
        if is_knowledge_query:
            prompt = f"""用户问题：{message}

{profile_hint if profile_hint else ""}

你是护肤/美妆领域的专业顾问，请直接回答用户的知识类问题（成分、用法、原理、适用肤质、注意事项等）。
回答要求：
1. 先直接给结论，再展开说原因和注意事项
2. 客观、克制，不要夸张，不要用营销话术和网络热词
3. 涉及敏感肌/孕妇/严重皮肤问题等高风险场景，必须提醒"建议咨询皮肤科医生"
4. 不要主动推荐商品，除非用户明确问推荐。如果参考信息里没有相关商品，就纯回答知识
5. 不要编造成分浓度或功效数据；不确定的就说"这一点没有权威公开资料支撑，建议查成分表或咨询品牌客服"
6. 全文控制在300字以内，简洁明了"""
        else:
            prompt = f"""用户问题：{message}

{profile_hint if profile_hint else ""}

请根据你的专业知识回答这个问题。如果涉及商品推荐，请给出具体建议。
如果不知道答案，诚实地说不清楚，不要编造。"""

        messages = [
            {"role": "system", "content": AGENT_SYSTEM_PROMPT},
            {"role": "user", "content": prompt}
        ]

        response = await self.llm_service.chat(messages, temperature=0.7)

        return self._format_response(
            response,
            intent_result,
            products=rag_result.get("products", []),
            sources=rag_result.get("knowledge", []),
            citations=rag_result.get("citations", []),
            pitfalls=rag_result.get("pitfalls", []),
            source_type="rag"
        )

    async def _generate_compare_response(
        self,
        comparison: Dict,
        profile_hint: str = ""
    ) -> str:
        """生成对比回复（使用优化的Prompt）"""
        from app.services.prompts import PRODUCT_COMPARE_PROMPT, format_comparison_table

        products = comparison["products"]

        # 构建商品信息表格
        products_table = format_comparison_table(products)

        # 构建用户关注点
        user_focus = profile_hint if profile_hint else "综合对比"

        # 使用优化的对比Prompt
        prompt = PRODUCT_COMPARE_PROMPT.format(
            products_info=products_table,
            user_focus=user_focus
        )

        messages = [
            {"role": "system", "content": AGENT_SYSTEM_PROMPT},
            {"role": "user", "content": prompt}
        ]

        try:
            response = await self.llm_service.chat(messages, temperature=0.7)
            return self._sanitize_recommendation_copy(response)
        except Exception as e:
            logger.warning(f"LLM对比生成失败，使用模板回复: {e}")
            # 降级到模板回复
            product_list = "\n".join([
                f"- {p['name']} ({p['brand']}) ¥{p['price']}"
                for p in products
            ])
            summary_text = comparison.get('summary', '各款产品各有特色，具体对比如上')
            profile_text = f"\n\n### 👤 您的偏好\n{profile_hint}" if profile_hint else ""

            return self._sanitize_recommendation_copy(f"""### 可信对比结论

{product_list}

### 最终建议
{summary_text}{profile_text}

需要我给出具体的购买建议吗？""")

    async def _generate_recommendation_response(
        self,
        message: str,
        requirements: Dict,
        recommendations: List[Dict],
        knowledge: str,
        advice: str,
        profile_hint: str = "",
        entities: Optional[Dict] = None
    ) -> str:
        """生成推荐回复（使用优化的Prompt）"""
        product_info = "\n".join([
            format_product_info(r)
            for r in recommendations[:6]
        ])

        profile_section = f"\n### 👤 用户画像\n{profile_hint}" if profile_hint else ""
        user_state_section = self._build_user_state_section(entities or {})
        knowledge_section = knowledge if knowledge else "暂无相关知识"

        prompt = SHOPPING_RECOMMEND_PROMPT.format(
            user_request=message,
            profile_section=profile_section,
            user_state_section=user_state_section,
            product_info=product_info,
            knowledge_section=knowledge_section
        )

        messages = [
            {"role": "system", "content": AGENT_SYSTEM_PROMPT},
            {"role": "user", "content": prompt}
        ]

        response = await self.llm_service.chat(
            messages=messages,
            temperature=0.4,
            max_tokens=820
        )
        if "模型服务刚刚有点不稳定" in response or "我刚刚没有稳定连上模型服务" in response:
            fallback_text = self._build_local_recommendation_fallback(
                message=message,
                recommendations=recommendations,
                knowledge=knowledge
            )
            return self._sanitize_recommendation_copy(fallback_text)
        response = self._sanitize_recommendation_copy(response)
        return response

    def _build_local_recommendation_fallback(
        self,
        message: str,
        recommendations: List[Dict],
        knowledge: str
    ) -> str:
        """基于本地检索与排序结果生成结构化导购答复（严格对齐点点风格，不依赖外部大模型）。"""
        if not recommendations:
            return (
                "我先帮你缩小范围～你可以补充一下**肤质、预算、主要诉求**（比如油皮/想抗老/300 内），"
                "我会结合商品库和护肤知识库给你筛 2-3 款更合适的。"
            )

        # 1. 从用户消息提取上下文
        budget_match = re.search(r'(\d{3,5})\s*(?:元|块|左右|以内|预算)', message or "")
        budget = int(budget_match.group(1)) if budget_match else None

        skin_type = ""
        skin_keywords = {
            "干敏肌": ["干敏", "干敏肌"],
            "油敏肌": ["油敏", "油敏肌"],
            "敏感肌": ["敏感", "泛红", "刺痛", "屏障受损"],
            "干皮": ["干皮", "干性皮肤"],
            "油皮": ["油皮", "油性皮肤", "容易出油"],
            "混油皮": ["混油", "混合皮", "T区油"],
            "中性皮": ["中性皮", "中性皮肤"],
        }
        for st, kws in skin_keywords.items():
            if any(kw in (message or "") for kw in kws):
                skin_type = st
                break
        skin_type_text = skin_type if skin_type else "适合的"

        # 识别品类
        def _has_real_serum(text: str) -> bool:
            import re as _re
            for m in _re.finditer(r'精华', text):
                start = m.start()
                prefix = text[max(0, start - 2):start]
                suffix = text[start + 2:start + 4]
                if suffix.startswith('水') or suffix.startswith('爽'):
                    continue
                if prefix.endswith('眼部'):
                    continue
                if any(kw in text for kw in ['精华液', '精华露', '精华素', '肌底液', '淡斑精华', '修护精华', '抗老精华', '双抗精华', '紫米精华', '小白瓶', '小棕瓶', '小黑瓶']):
                    return True
                if suffix.startswith('，') or suffix.startswith('。') or suffix.startswith('、') or suffix == '' or suffix.startswith('指') or suffix.startswith('的'):
                    if not any(kw in text for kw in ['精华水', '精华爽']):
                        return True
            return False

        combined_text = " ".join(
            [message or ""] + [
                f"{p.get('name', '')} {p.get('category', '')} {p.get('description', '')}"
                for p in recommendations[:5]
            ]
        )
        if "防晒" in combined_text:
            category_name = "防晒"
            core_need = "肤感轻薄好坚持"
            avoid_point = "盲目追高SPF导致闷痘"
            benefit = "日常通勤才愿意天天涂"
            param_label = "防晒指数"
        elif any(kw in combined_text for kw in ["素颜霜", "粉底", "底妆", "遮瑕"]):
            category_name = "底妆"
            core_need = "服帖自然不卡粉"
            avoid_point = "只追高遮瑕导致妆感厚重"
            benefit = "日常妆感才自然不假面"
            param_label = "妆效特点"
        elif any(kw in combined_text for kw in ["爽肤水", "化妆水", "精萃水", "精粹水", "精华水", "美肤水", "菌菇水", "流金水", "金盏花水", "金盏花植物"]):
            category_name = "爽肤水"
            core_need = "温和不刺激好吸收"
            avoid_point = "盲目追求高浓度导致刺激"
            benefit = "日常维稳补水才靠谱"
            param_label = "核心功效"
        elif any(kw in combined_text for kw in ["面霜", "乳霜", "保湿霜"]):
            category_name = "面霜"
            core_need = "保湿修护不闷痘"
            avoid_point = "只看厚重感导致闷闭口"
            benefit = "秋冬保湿才稳定不翻车"
            param_label = "核心功效"
        elif any(kw in combined_text for kw in ["眼霜", "眼部精华"]):
            category_name = "眼霜"
            core_need = "温和好吸收不长脂肪粒"
            avoid_point = "盲目追求速效导致刺激眼周"
            benefit = "长期用才温和有效"
            param_label = "核心成分"
        elif any(kw in combined_text for kw in ["面膜"]):
            category_name = "面膜"
            core_need = "补水修护急救稳"
            avoid_point = "频繁敷面膜导致过度水合"
            benefit = "关键时候才真有用"
            param_label = "核心功效"
        elif _has_real_serum(combined_text):
            category_name = "精华"
            core_need = "成分适配能建立耐受"
            avoid_point = "堆猛料导致泛红刺痛"
            benefit = "长期用才真的有效果"
            param_label = "核心成分"
        else:
            category_name = "好物"
            core_need = "匹配核心诉求"
            avoid_point = "被附加卖点带跑"
            benefit = "买了才不会闲置"
            param_label = "核心特点"

        price_range = "性价比"
        if budget and budget >= 800:
            price_range = "进阶"
        elif budget and budget <= 200:
            price_range = "高性价比"

        # 2. 对商品排序：子类目不匹配的降权，预算内优先，按价格升序（便宜的在前做"性价比首选"）
        def _name_implies_category(nm: str, cat: str) -> bool:
            if cat not in nm:
                return False
            if cat == "面霜":
                idx = nm.find("面霜")
                prefix = nm[max(0, idx - 2):idx]
                if prefix.endswith("洁") or prefix.endswith("洗") or "洁面" in prefix:
                    return False
                return True
            return True

        def _subcat_mismatch(p: Dict[str, Any]) -> int:
            subcat = (p.get("subcategory") or "").strip()
            name = (p.get("name") or "")
            desc = (p.get("description") or "")
            if category_name in ("好物",):
                return 0
            face_cream_bad = ["洁面", "洗面奶", "洗面霜", "洁面霜", "眼霜", "面膜", "粉底液", "口红", "眼影", "眉笔",
                             "散粉", "蜜粉", "卸妆", "精华水", "爽肤水", "防晒", "卸妆油", "卸妆水"]
            if category_name == "面霜":
                for bad in face_cream_bad:
                    if bad in name:
                        return 2
                if not _name_implies_category(name, "面霜") and ("霜" not in name and "乳" not in name):
                    return 1
            if not subcat:
                return 1
            if subcat == category_name:
                return 0
            if category_name == "精华" and "精华" in subcat:
                return 0
            if category_name == "面霜" and subcat in ("面霜", "乳霜"):
                return 0
            if category_name == "防晒" and subcat in ("防晒", "防晒霜", "防晒乳"):
                return 0
            if category_name == "爽肤水" and subcat in ("爽肤水", "化妆水", "精华水", "精萃水", "精粹水", "美肤水"):
                return 0
            return 2

        # 2. 选 top3：完全信任传入的 ranked_products 顺序（它已做过精排且和 products 事件一致），
        # 只做子类目不匹配的过滤：把错类的踢到末尾，保证前端 filterProductsForRenderedText 能按名字匹配上商品卡。
        mismatched = [p for p in recommendations if _subcat_mismatch(p) >= 2]
        matched = [p for p in recommendations if _subcat_mismatch(p) < 2]
        top = (matched + mismatched)[:3]
        count = len(top)

        # 3. 开头思路段（严格对齐点点句式）
        intro = f"给{skin_type_text}挑{price_range}{category_name}，思路要放在「{core_need}」而不是「{avoid_point}」，这样{benefit}。我帮你筛选了一圈，"
        if count == 1:
            intro += "锁定了一款最适配的款。"
        elif count == 2:
            intro += "锁定了一款性价比首选和一款进阶之选。"
        else:
            intro += "锁定了一款性价比首选、一款进阶之选和一款特殊场景款。"

        # 4. 大标题
        title = f"## {skin_type_text}{('的' + price_range) if price_range else ''}{category_name}指南"

        def _parse_desc(desc: str) -> Dict[str, str]:
            """从商品description解析结构化字段，兼容"字段：值"格式和自然语言格式。"""
            info: Dict[str, str] = {}
            if not desc:
                return info
            cleaned_desc_lines = []
            for raw_line in desc.split("\n"):
                line = raw_line.strip()
                if not line:
                    continue
                if line.startswith("备注") and any(bad in line for bad in ["产地参数", "非特殊用途化妆品", "价格按", "平台加补", "备案号", "生产企业"]):
                    continue
                if line.startswith("核心成分") and ("未展示" in line or "未核" in line or "具体以官方" in line):
                    continue
                cleaned_desc_lines.append(line)
            cleaned_desc = "\n".join(cleaned_desc_lines)
            for field in ["定位", "适合人群", "适合肤质", "核心成分", "主打功效", "规格"]:
                m = re.search(rf"{field}[：:]\s*([^\n]+)", cleaned_desc)
                if m:
                    info[field] = m.group(1).strip()
            if "适合肤质" not in info:
                skin_match = re.search(r"适合([^，。；\n]{2,12}?(?:肌|肤质|皮肤))", cleaned_desc)
                if skin_match:
                    info["适合肤质"] = skin_match.group(1).strip()
            return info

        def _extract_param(p: Dict[str, Any], parsed: Dict[str, str]) -> str:
            new_concerns = p.get("concerns") or []
            if isinstance(new_concerns, str):
                new_concerns = [c.strip() for c in new_concerns.replace("；", ";").split(";") if c.strip()]
            new_ingredients = (p.get("key_ingredients") or "").strip()
            desc = (p.get("description") or "")
            if category_name == "防晒":
                spf_match = re.search(r"SPF(\d+\+?)\s*(?:PA?(\+*))?", desc, re.IGNORECASE)
                if spf_match:
                    spf_val = spf_match.group(1)
                    pa_val = spf_match.group(2) or ""
                    return f"SPF{spf_val}" + (f" / PA{pa_val}" if pa_val else "")
                if parsed.get("核心成分") and ("SPF" in parsed["核心成分"] or "防晒" in parsed["核心成分"]):
                    return parsed["核心成分"][:30]
                return "SPF50+ / PA++++"
            elif category_name == "精华":
                if new_ingredients:
                    return new_ingredients[:30]
                ingredient_kw = ["玻色因", "胜肽", "烟酰胺", "A醇", "视黄醇", "维C", "维生素C",
                             "玻尿酸", "透明质酸", "神经酰胺", "二裂酵母", "Binary Complex",
                             "细胞修护复合物", "修护复合物", "发酵产物",
                             "水杨酸", "果酸", "杏仁酸", "传明酸", "曲酸", "熊果苷",
                             "虾青素", "寡肽", "角鲨烷", "B5", "泛醇", "积雪草", "红没药醇",
                             "麦角硫因", "依克多因", "蓝铜胜肽", "多肽", "腺苷", "视黄醛"]
                found = []
                for ing in ingredient_kw:
                    if ing in desc:
                        is_sub_or_super = False
                        for j, existing in enumerate(found):
                            if ing in existing:
                                is_sub_or_super = True
                                break
                            if existing in ing:
                                found[j] = ing
                                is_sub_or_super = True
                                break
                        if not is_sub_or_super:
                            found.append(ing)
                if parsed.get("核心成分"):
                    ci = parsed["核心成分"]
                    ci_clean = re.sub(r"为页面标题.*$", "", ci).strip("；;，, ")
                    if ci_clean and len(ci_clean) < 40:
                        return ci_clean
                if found:
                    return "、".join(found[:3])
                return parsed.get("核心成分", "核心修护成分")[:30] if parsed.get("核心成分") else "核心修护成分"
            elif category_name == "底妆":
                effect_kw = ["轻薄", "自然", "遮瑕", "持妆", "水润", "哑光", "雾面", "奶油肌", "服帖"]
                found = [kw for kw in effect_kw if kw in desc]
                if new_concerns:
                    return "、".join(new_concerns[:3])
                if found:
                    return "、".join(found[:3])
                return "自然服帖款"
            elif category_name == "爽肤水":
                if new_concerns:
                    return "、".join(new_concerns[:3])
                tonic_kw = ["补水", "保湿", "舒缓", "维稳", "调理", "控油", "镇静", "修护", "提亮", "二次清洁"]
                found = [kw for kw in tonic_kw if kw in desc]
                if parsed.get("主打功效"):
                    gx = parsed["主打功效"][:30]
                    if gx and "价格" not in gx:
                        return gx
                if found:
                    return "、".join(found[:3])
                return parsed.get("核心成分", "保湿补水")[:20] if parsed.get("核心成分") else "保湿调理"
            elif category_name in ("面霜", "眼霜", "面膜"):
                if new_concerns:
                    return "、".join(new_concerns[:3])
                effect_kw = ["保湿", "修护", "滋润", "抗老", "紧致", "提亮", "补水", "舒缓"]
                found = [kw for kw in effect_kw if kw in desc]
                if found:
                    return "、".join(found[:3])
                return (parsed.get("主打功效") or "保湿修护")[:20]
            else:
                if new_concerns:
                    return "、".join(new_concerns[:3])
                return (parsed.get("主打功效") or desc[:20] or "匹配本轮诉求")

        def _extract_suitable(p: Dict[str, Any], parsed: Dict[str, str]) -> str:
            new_skin = (p.get("suitable_skin_types") or "").strip()
            if new_skin:
                return new_skin
            if parsed.get("适合肤质"):
                st = parsed["适合肤质"]
                st = re.sub(r"多种肤质[；;，,]*", "", st).strip("；;，, ")
                if st:
                    return st
            if skin_type:
                return f"{skin_type}适配"
            return "大部分肤质可用，敏感肌建议先试"

        def _extract_note(p: Dict[str, Any], parsed: Dict[str, str]) -> str:
            new_pitfalls = (p.get("pitfalls") or "").strip()
            if new_pitfalls and "产地参数" not in new_pitfalls and "非特殊用途" not in new_pitfalls:
                return new_pitfalls
            if parsed.get("备注"):
                note = parsed["备注"]
                note = re.sub(r"(?:页面为|当前为|平台加补后|优惠前\d+[；;，,]?\s*|价格按[^；;，,]*?记录)", "", note).strip("；;，, ")
                if any(bad in note for bad in ["产地参数", "非特殊用途化妆品", "生产企业", "备案号"]):
                    note = ""
                if "价格随活动" in note or "活动价" in note:
                    note = "价格随活动波动，下单前看实时价"
                elif "完整" in note or "待详情页" in note or "未核" in note:
                    note = "完整成分和注意事项以下单页备案信息为准"
                if note and len(note) < 50:
                    return note
            desc = (p.get("description") or "")
            if category_name == "防晒":
                if "防水" in desc or "户外" in desc:
                    return "户外需每2小时补涂，防水款需要卸妆"
                if "清爽" in desc or "轻薄" in desc:
                    return "涂够量才有效（约一元硬币大小），记得补涂"
                return "刚上脸可能有轻微膜感，成膜后再出门，记得2-3小时补涂"
            elif category_name == "精华":
                if any(kw in desc for kw in ["玻色因", "A醇", "视黄醇", "视黄醛"]):
                    return "活性成分浓度不低，建议从低频次开始建立耐受"
                if any(kw in desc for kw in ["酸", "水杨酸", "果酸", "杏仁酸"]):
                    return "酸类产品有刺激性，敏感期停用，严格防晒"
                if "清爽" in desc or "不黏腻" in desc or "质地清爽" in desc:
                    return "质地偏清爽，混油/中性皮四季可用，干皮秋冬可能需叠加保湿"
                if "夜间" in desc or "晚上" in desc:
                    return "偏夜间修护型，建议晚上用，白天记得做好防晒"
                if "修护" in desc or "屏障" in desc:
                    return "修护类精华相对温和，但叠其他功效产品时注意间隔"
                return "第一次用先局部试，建立耐受再全脸用，不要和多种猛料叠加"
            elif category_name == "底妆":
                if "干皮" in desc or "保湿" in desc:
                    return "干皮妆前做好保湿，起皮期可能卡粉"
                if "油皮" in desc or "控油" in desc or "持妆" in desc:
                    return "油皮建议搭配散粉定妆，T区注意补妆"
                return "建议先试色号，不同肤色上脸效果有差异"
            elif category_name == "爽肤水":
                if "敏感" in desc or "温和" in desc or "舒缓" in desc:
                    return "温和调理型，敏感肌可用，建议用手轻拍或化妆棉擦拭"
                if "控油" in desc or "油皮" in desc:
                    return "偏清爽控油型，油皮/混油皮夏季可做二次清洁"
                if "湿敷" in desc:
                    return "可日常拍涂也可局部湿敷，但不要天天敷"
                return "洁面后使用，帮助后续护肤品吸收"
            elif category_name == "面霜":
                new_texture = (p.get("texture") or "").strip()
                if "清爽" in new_texture or "油皮" in (p.get("suitable_skin_types") or ""):
                    return "质地偏清爽，混油/油皮春秋可用，干皮冬天可能需叠加"
                return "掌心乳化后按压上脸更易吸收，干皮秋冬用更安心"
            elif category_name == "眼霜":
                return "取米粒大小用无名指轻点眼周，不要拉扯眼周肌肤"
            elif category_name == "面膜":
                if "清洁" in desc:
                    return "清洁类面膜不要频繁使用，一周1-2次即可"
                return "敷10-15分钟即可，不要超时，敷后记得后续保湿"
            else:
                return "价格为入库参考价，非实时，点商品链接查天猫实时价"

        def _extract_reason(p: Dict[str, Any], parsed: Dict[str, str]) -> str:
            brand = (p.get("brand") or "").strip()
            name = (p.get("name") or "")
            new_positioning = (p.get("positioning") or "").strip()
            new_concerns = p.get("concerns") or []
            if isinstance(new_concerns, str):
                new_concerns = [c.strip() for c in new_concerns.replace("；", ";").split(";") if c.strip()]

            if new_positioning and len(new_positioning) < 60:
                reason = new_positioning.rstrip("。") + "。"
            elif new_concerns:
                top_gx = "、".join(new_concerns[:2])
                brand_short = brand or name[:6]
                reason = f"{brand_short}这款{category_name}主打的是{top_gx}。"
            elif parsed.get("主打功效"):
                gx_raw = parsed["主打功效"]
                gx_parts = [x.strip() for x in re.split(r"[；;、，,]", gx_raw) if x.strip()
                            and x.strip() not in ("防晒", "SPF50+", "高倍防晒")]
                brand_short = brand or ""
                if not brand_short and name:
                    for sep in ["爽肤水", "精华水", "精华液", "精华露", "防晒乳", "防晒霜", "防晒", "面霜", "眼霜", "面膜", "粉底液", "口红"]:
                        if sep in name:
                            brand_short = name.split(sep)[0].strip()
                            break
                    if not brand_short:
                        brand_short = name[:6].strip()
                if gx_parts:
                    top_gx = "、".join(gx_parts[:2])
                    reason = f"{brand_short}这款{category_name}主打的是{top_gx}。"
                else:
                    reason = f"{brand_short}经典{category_name}，口碑比较稳。"
            elif brand and brand in name:
                reason = f"{brand}经典{category_name}，口碑比较稳。"
            else:
                reason = "这款匹配本轮需求。"

            for turd in ["是理想选择", "非常值得入手", "功效卓越", "是您的", "为您打造", "专为您",
                         "是不二之选", "性价比极高", "强烈推荐", "不容错过", "值得拥有",
                         "定位：", "官方旗舰店", "正常规格", "基础包装"]:
                reason = reason.replace(turd, "")
            return reason.strip("，。、；; ") + "。"

        # 5. 动态生成定位标签（严格对齐 prompt 要求：性价比首选/进阶之选/特殊场景款；敏感肌场景用敏感肌友好款）
        def _label_for(i: int, p: Dict[str, Any], parsed: Dict[str, str]) -> str:
            if i == 0:
                return "性价比首选"
            if i == 1:
                return "进阶之选"
            new_skin = (p.get("suitable_skin_types") or "")
            new_concerns = p.get("concerns") or []
            if isinstance(new_concerns, str):
                new_concerns = [c.strip() for c in new_concerns.replace("；", ";").split(";") if c.strip()]
            all_text = " ".join(new_concerns) + " " + new_skin + " " + (p.get("description") or "")
            if skin_type in ("敏感肌", "干敏肌", "油敏肌") and ("敏感" in all_text or "屏障" in all_text or "温和" in all_text):
                return "敏感肌友好款"
            return "特殊场景款"

        # 6. 生成每个商品块
        product_sections = []
        for i, p in enumerate(top):
            parsed = _parse_desc(p.get("description") or "")
            label = _label_for(i, p, parsed)
            name = p.get("name", "推荐商品")

            price_val = self._as_price(p)
            spec = parsed.get("规格", "")
            price_text = f"约¥{price_val:.0f}" + (f" / {spec}" if spec else "") if price_val else "价格见详情页"
            param = _extract_param(p, parsed)
            suitable = _extract_suitable(p, parsed)
            note = _extract_note(p, parsed)
            reason = _extract_reason(p, parsed)

            def _clean(text: str, max_len: int = 60) -> str:
                t = str(text or "").replace("\n", " ").replace("\r", " ").strip()
                t = re.sub(r"\s+", " ", t)
                if len(t) > max_len:
                    t = t[:max_len].rstrip("，。、；;，（(、,") + "…"
                return t

            param = _clean(param, 50)
            suitable = _clean(suitable, 40)
            note = _clean(note, 50)
            reason = _clean(reason, 80)

            section = (
                f"**{label}：{name}**\n\n"
                f"{reason}\n\n"
                f"- 参考价格：{price_text}\n"
                f"- {param_label}：{param}\n"
                f"- 适合肤质：{suitable}\n"
                f"- 注意事项：{note}"
            )
            product_sections.append(section)

        # 7. 组装最终输出（严格对齐四段结构：摘要 → 指南标题 → 3 款商品块 → 综合建议）
        # 7.1 生成综合建议段：明确告诉用户日常/预算/稳妥分别选哪款
        def _short_name(p: Dict[str, Any]) -> str:
            nm = (p.get("name") or "").strip()
            brand = (p.get("brand") or "").strip()
            if brand and nm.startswith(brand):
                nm = nm[len(brand):].strip()
            return nm or (p.get("name") or "这款")

        summary_lines = []
        if count >= 1:
            p0 = top[0]
            summary_lines.append(f"日常最贴预算直接选{_short_name(p0)}，性价比最高。")
        if count >= 2:
            p1 = top[1]
            p1_price = self._as_price(p1)
            budget_edge = ""
            if budget and p1_price and p1_price > budget * 1.1:
                budget_edge = "，预算边缘但综合体验进阶一档"
            summary_lines.append(f"想追求更好的使用感可以进阶看{_short_name(p1)}{budget_edge}。")
        if count >= 3:
            p2 = top[2]
            p2_label = _label_for(2, p2, _parse_desc(p2.get("description") or ""))
            if p2_label == "敏感肌友好款":
                summary_lines.append(f"皮肤敏感期或怕刺激就先用{_short_name(p2)}稳住。")
            else:
                summary_lines.append(f"{_short_name(p2)}适合特定场景按需选。")
        extra_caution = ""
        if skin_type in ("敏感肌", "干敏肌", "油敏肌"):
            extra_caution = "敏感肌第一次上脸建议先耳后测试，没不良反应再全脸用。"
        elif category_name == "精华":
            extra_caution = "新精华别和多种猛料叠涂，先从低频次开始建立耐受。"
        elif category_name == "底妆":
            extra_caution = "底妆建议先试小样或专柜色号，确认不卡粉不闷痘再入正装。"
        elif category_name == "防晒":
            extra_caution = "防晒涂够量才有效，户外记得每2小时补涂。"
        summary_text = " ".join(summary_lines)
        if extra_caution:
            summary_text += extra_caution

        parts = [
            intro,
            "",
            title,
            "",
            "\n\n".join(product_sections),
            "",
            "## 综合建议",
            "",
            summary_text,
        ]
        return "\n".join(parts)

    async def _generate_knowledge_answer(
        self,
        query: str,
        rag_result: Dict[str, Any],
        profile_hint: str = "",
    ) -> str:
        """纯知识问答：基于RAG检索到的知识生成回答，不推荐库外商品。"""
        knowledge_parts = []
        if rag_result.get("knowledge"):
            for k in rag_result["knowledge"][:4]:
                content = k.get("content", "")
                title = k.get("title", "")
                if content:
                    knowledge_parts.append(f"### {title}\n{content[:300]}")
        if rag_result.get("pitfalls"):
            for p in rag_result["pitfalls"][:2]:
                knowledge_parts.append(f"⚠️ {p.get('title', '避坑')}：{p.get('description', '')}")

        knowledge_text = "\n\n".join(knowledge_parts) if knowledge_parts else "暂无专门收录的知识条目"

        prompt = f"""你是一个专业的护肤/美妆顾问。请根据提供的参考知识回答用户的问题。

用户问题：{query}

参考知识：
{knowledge_text}

{profile_hint if profile_hint else ""}

回答要求：
1. 基于参考知识回答，语言简洁自然，用markdown小标题组织
2. 不要推荐或提及参考知识之外的具体商品/品牌名（包括在括号中「比如xxx」「推荐xxx」「如xxx」这类句式中夹带），避免编造商品
3. 如果参考知识不足，基于通用护肤知识回答，但不要编造具体商品或价格
4. 如果涉及具体产品推荐需求，提醒用户告知具体需求（肤质、预算等）以便精确推荐
5. 不要用引号包裹整个回答，直接输出正文

请回答："""

        messages = [
            {"role": "system", "content": AGENT_SYSTEM_PROMPT},
            {"role": "user", "content": prompt}
        ]
        try:
            answer = await self.llm_service.chat(messages, temperature=0.5, max_tokens=800)
            answer = answer.strip()
            answer = answer.replace('\\"', '"').replace("\\'", "'")
            if (answer.startswith('"') and answer.endswith('"')) or (answer.startswith('"') and answer.endswith('"')) or (answer.startswith('「') and answer.endswith('」')):
                answer = answer[1:-1].strip()
            return answer
        except Exception as e:
            logger.warning(f"知识问答LLM生成失败: {e}")
            return "抱歉，这个问题我暂时无法详细回答。你可以换个方式提问，或者告诉我具体想了解的产品/品类。"

    def _build_no_product_match_reply(
        self,
        message: str,
        category: Optional[str],
        budget: Optional[float],
        knowledge: str
    ) -> str:
        """当商品库没命中真实 SKU 时，诚实告知，不编造商品。"""
        category_text = (category or "").split("-")[-1] if category else "这类"
        budget_text = f"，预算{int(budget)}元以内" if budget else ""

        knowledge_hint = ""
        if knowledge and "暂无相关知识" not in knowledge:
            for raw_line in knowledge.split("\n")[:2]:
                cleaned = raw_line.lstrip("- ").strip()
                if cleaned and len(cleaned) > 5:
                    knowledge_hint = cleaned
                    break

        parts = [f"抱歉，我在当前已入库的商品里没有找到适合「{message.strip()}」的{category_text} SKU{budget_text}，不瞎推。"]
        if knowledge_hint:
            parts.append(f"参考：{knowledge_hint}")
        parts.append("你可以告诉我具体的商品名，或者发商品图给我，我帮你逐个分析判断。")
        return "\n\n".join(parts)

    def _format_response(
        self,
        response: str,
        intent_result: IntentResult,
        products: Optional[List[Dict]] = None,
        sources: Optional[List[Dict]] = None,
        comparison_data: Optional[Dict] = None,
        source_type: str = "agent",
        decision_tree: Optional[Dict] = None,
        citations: Optional[List[Dict]] = None,
        pitfalls: Optional[List[Dict]] = None
    ) -> Dict[str, Any]:
        """格式化响应结果（增强版：包含来源引用和避坑提示）"""
        def _fmt(p: Dict[str, Any]) -> Dict[str, Any]:
            if p.get("key_ingredients") is not None or p.get("is_real_detail") is not None:
                return p
            score = float(p.get("match_score") or p.get("relevance") or 90.0)
            reason = p.get("rerank_reason") or ""
            return ShoppingAgent._format_db_product(p, score=score, reason=reason)

        formatted_products = [_fmt(p) for p in (products or [])]
        result = {
            "response": response,
            "intent": {
                "type": intent_result.intent,
                "confidence": intent_result.confidence,
                "entities": intent_result.entities
            },
            "products": formatted_products,
            "sources": sources or [],  # 旧格式，保留兼容
            "citations": citations or [],  # 新格式：增强的来源引用
            "pitfalls": pitfalls or [],    # 新格式：避坑提示
            "comparison_data": comparison_data,
            "metadata": {
                "source_type": source_type,
                "timestamp": datetime.now().isoformat(),
                "has_citations": bool(citations),
                "has_pitfalls": bool(pitfalls)
            }
        }

        # 添加决策树数据（如果存在）
        if decision_tree:
            result["decision_process"] = decision_tree

        return result


# ==================== 全局实例 ====================

_agent: Optional[ShoppingAgent] = None


def get_shopping_agent() -> ShoppingAgent:
    """获取智能导购Agent实例"""
    global _agent
    if _agent is None:
        _agent = ShoppingAgent()
    return _agent
