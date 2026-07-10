"""
轮次状态框架（TurnFrame）

统一管理每一轮对话的状态，解决意图识别冲突、上下文污染、指代消解等核心问题。
是整个智能导购 Agent 的核心数据模型。
"""

from typing import Dict, List, Optional, Any, Set
from pydantic import BaseModel, Field
from enum import Enum
from dataclasses import dataclass, field
import logging
import re

logger = logging.getLogger(__name__)


class TaskType(str, Enum):
    """本轮任务类型（唯一、明确，不存在双轨冲突）"""
    GREETING = "greeting"                    # 问候/闲聊
    KNOWLEDGE_QUERY = "knowledge_query"      # 纯知识问答（不推荐商品）
    PRODUCT_RECOMMEND = "product_recommend"  # 商品推荐（含模糊需求探索）
    PRODUCT_DETAIL = "product_detail"        # 单品详情/判断/问价
    PRODUCT_COMPARE = "product_compare"      # 多品对比决策
    AFTER_SALES = "after_sales"              # 售后相关
    CHITCHAT = "chitchat"                    # 闲聊
    UNKNOWN = "unknown"                      # 无法判断


class Scope(str, Enum):
    """任务作用域"""
    GLOBAL = "global"               # 全局（无特定锚点，全新搜索）
    CURRENT_ANCHOR = "current_anchor"  # 当前图片/单品锚点
    PREVIOUS_RECOMMEND = "previous_recommend"  # 上一轮推荐列表范围
    HISTORY = "history"             # 历史对话范围


class ReferenceType(str, Enum):
    """指代类型"""
    NONE = "none"
    THIS = "this"                   # 这款/这个/它
    THAT = "that"                   # 刚才那个/之前的
    CHEAPER = "cheaper"             # 便宜点的
    MORE_EXPENSIVE = "more_expensive"  # 贵点的
    OTHER = "other"                 # 别的/换一个


class BudgetType(str, Enum):
    """预算类型"""
    NONE = "none"
    RIGID = "rigid"                 # 刚性预算（不超过）
    FLEXIBLE = "flexible"           # 弹性预算（参考）


@dataclass
class ProductAnchor:
    """商品锚点（图片识别结果或当前聚焦单品）"""
    product_id: Optional[str] = None
    brand: Optional[str] = None
    category_l1: Optional[str] = None
    category_l2: Optional[str] = None
    product_name: Optional[str] = None
    confidence: float = 0.0         # 识别置信度 0-1
    image_index: int = 0            # 多图场景下的图片序号
    evidence: Dict[str, Any] = field(default_factory=dict)  # 识别证据（CLIP分、OCR命中、视觉分等）

    def is_confident(self, threshold: float = 0.65) -> bool:
        """是否达到可信阈值"""
        return self.confidence >= threshold


@dataclass
class Constraints:
    """本轮约束条件"""
    budget_max: Optional[float] = None
    budget_min: Optional[float] = None
    budget_type: BudgetType = BudgetType.NONE
    brands: List[str] = field(default_factory=list)
    exclude_brands: List[str] = field(default_factory=list)
    categories_l1: List[str] = field(default_factory=list)
    categories_l2: List[str] = field(default_factory=list)
    skin_types: List[str] = field(default_factory=list)
    skin_concerns: List[str] = field(default_factory=list)
    exclude_product_ids: Set[str] = field(default_factory=set)
    keywords: List[str] = field(default_factory=list)

    def has_constraints(self) -> bool:
        return bool(
            self.budget_max is not None
            or self.brands
            or self.exclude_brands
            or self.categories_l1
            or self.categories_l2
            or self.skin_types
            or self.skin_concerns
            or self.exclude_product_ids
            or self.keywords
        )


@dataclass
class TurnFrame:
    """
    一轮对话的完整状态框架

    设计原则：
    1. 单一真相源：所有模块都从 TurnFrame 读取状态，不再各自解析
    2. 短期/长期分离：本轮约束只在当前轮生效，不污染长期画像
    3. 指代消解前置：在意图识别阶段就解析"这款""便宜点的"等指代
    4. 证据可追溯：每个判断都保留置信度和证据来源
    """
    # 基础信息
    session_id: str
    turn_id: int
    raw_query: str
    cleaned_query: str = ""

    # 任务判定
    task_type: TaskType = TaskType.UNKNOWN
    task_confidence: float = 0.0
    scope: Scope = Scope.GLOBAL

    # 指代消解
    reference_type: ReferenceType = ReferenceType.NONE
    reference_target: Optional[str] = None  # 指向的具体商品ID或"last_list"

    # 锚点信息
    image_anchors: List[ProductAnchor] = field(default_factory=list)  # 本轮上传图片的锚点
    active_anchor: Optional[ProductAnchor] = None  # 当前激活的锚点

    # 约束条件（本轮生效）
    constraints: Constraints = field(default_factory=Constraints)

    # 上下文引用
    last_recommended_ids: List[str] = field(default_factory=list)  # 上一轮推荐的商品ID
    last_topic: Optional[str] = None  # 上一轮话题

    # 处理结果
    products: List[Dict[str, Any]] = field(default_factory=list)
    response_type: str = "normal"  # normal/image_judge/price/compare/knowledge
    need_followup: bool = False
    followup_question: str = ""

    # 元信息
    is_followup: bool = False
    has_images: bool = False
    debug_log: List[str] = field(default_factory=list)

    def log(self, msg: str):
        """记录调试日志"""
        self.debug_log.append(f"[Turn{self.turn_id}] {msg}")
        logger.debug(f"[TurnFrame] {msg}")

    def get_primary_anchor(self) -> Optional[ProductAnchor]:
        """获取主要锚点（多图时取置信度最高的）"""
        if not self.image_anchors:
            return self.active_anchor
        return max(self.image_anchors, key=lambda x: x.confidence)

    def is_image_turn(self) -> bool:
        return self.has_images and bool(self.image_anchors)

    def is_anchor_followup(self) -> bool:
        """是否是针对锚点的追问"""
        return self.scope in (Scope.CURRENT_ANCHOR, Scope.PREVIOUS_RECOMMEND) or self.reference_type != ReferenceType.NONE

    def should_return_products(self) -> bool:
        """本轮是否应该返回商品推荐"""
        return self.task_type in (
            TaskType.PRODUCT_RECOMMEND,
            TaskType.PRODUCT_DETAIL,
            TaskType.PRODUCT_COMPARE
        )

    def is_knowledge_only(self) -> bool:
        """是否是纯知识问答（不返回商品）"""
        return self.task_type == TaskType.KNOWLEDGE_QUERY and not self.constraints.has_constraints()


class TurnFrameBuilder:
    """
    TurnFrame 构造器

    负责从原始输入、历史上下文、图片结果构建完整的 TurnFrame。
    是意图识别、指代消解、上下文融合的统一入口。
    """

    # 指代词模式
    REFERENCE_PATTERNS = {
        ReferenceType.THIS: re.compile(r"(这款|这个|它|这只|这支|这瓶|这个产品|这个东西)"),
        ReferenceType.THAT: re.compile(r"(刚才那个|之前那个|之前推荐|上一个|上次那个|前面那个)"),
        ReferenceType.CHEAPER: re.compile(r"(便宜点的|便宜的|性价比高的|更划算|预算低一点)"),
        ReferenceType.MORE_EXPENSIVE: re.compile(r"(贵点的|好一点的|高端一点|档次高一点)"),
        ReferenceType.OTHER: re.compile(r"(别的|其他|换一个|换个|再来一个|还有吗|推荐别的)"),
    }

    # 知识问答触发词（强特征）
    KNOWLEDGE_STRONG_PATTERNS = [
        re.compile(r"(怎么用|用法|使用方法|步骤|顺序)"),
        re.compile(r"(成分|含有什么|配方|原料|有没有酒精|有没有香精|有没有防腐剂)"),
        re.compile(r"(功效|作用|效果|能干什么|有什么用)"),
        re.compile(r"(区别|不同|差异|对比哪个好|怎么选)"),
        re.compile(r"(适合.*[吗呢？?]|可以.*[吗呢？?]|能.*[吗呢？?])"),
        re.compile(r"(注意事项|禁忌|副作用|过敏|不耐受)"),
        re.compile(r"(原理|为什么|怎么回事|什么原因)"),
        re.compile(r"(保质期|开封后|储存|保存)"),
        re.compile(r"(卸妆|防晒.*要注意|护肤步骤|搭配)"),
    ]

    # 商品推荐强特征
    RECOMMEND_STRONG_PATTERNS = [
        re.compile(r"(推荐|求推荐|帮我选|给我挑|有什么好的|介绍几款)"),
        re.compile(r"(想买|打算买|准备入手|种草|求种草)"),
        re.compile(r"(多少钱|价格|价位|贵不贵|便宜吗)"),
        re.compile(r"(哪里买|链接|购买|下单|有货吗)"),
        re.compile(r"(对比|比较|哪个好|选哪个|二选一|三选一)"),
        re.compile(r"(测评|评测|好用吗|值得买吗|踩雷|避雷)"),
    ]

    def __init__(self, session_id: str, turn_id: int):
        self.frame = TurnFrame(
            session_id=session_id,
            turn_id=turn_id,
            raw_query=""
        )

    def with_query(self, query: str) -> "TurnFrameBuilder":
        """设置原始查询并做基础清洗"""
        self.frame.raw_query = query
        # 移除【图片匹配商品】等前端注入的anchor标记
        cleaned = re.sub(r"【[^】]*图片[^】]*】", "", query)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        self.frame.cleaned_query = cleaned
        return self

    def with_images(self, image_results: List[Dict[str, Any]]) -> "TurnFrameBuilder":
        """添加图片识别结果，构造 image_anchors"""
        if not image_results:
            self.frame.has_images = False
            return self

        self.frame.has_images = True
        anchors = []
        for idx, img_res in enumerate(image_results):
            # 从图片搜索结果构造 ProductAnchor
            anchor = ProductAnchor(
                product_id=str(img_res.get("product_id", "")),
                brand=img_res.get("brand"),
                category_l1=img_res.get("category_l1") or img_res.get("category"),
                category_l2=img_res.get("category_l2") or img_res.get("subcategory"),
                product_name=img_res.get("name") or img_res.get("product_name"),
                confidence=float(img_res.get("similarity", img_res.get("confidence", 0.0))) / 100.0,
                image_index=idx,
                evidence={
                    "clip_score": img_res.get("clip_score", img_res.get("clip_similarity")),
                    "ocr_hit": img_res.get("ocr_brand") or img_res.get("ocr_category"),
                    "visual_score": img_res.get("visual_score"),
                    "final_score": img_res.get("final_score", img_res.get("similarity")),
                    "price": img_res.get("price"),
                    "image_url": img_res.get("image_url"),
                }
            )
            if anchor.product_id or anchor.brand:
                anchors.append(anchor)

        self.frame.image_anchors = anchors
        if anchors:
            self.frame.active_anchor = max(anchors, key=lambda x: x.confidence)
        return self

    def with_history_context(self, history_ctx: Any) -> "TurnFrameBuilder":
        """融合历史上下文"""
        if history_ctx is None:
            return self

        # 上一轮推荐的商品ID
        if hasattr(history_ctx, "last_recommended_ids"):
            self.frame.last_recommended_ids = history_ctx.last_recommended_ids
        elif isinstance(history_ctx, dict):
            self.frame.last_recommended_ids = history_ctx.get("last_recommended_ids", [])

        # 上一轮话题/锚点
        if hasattr(history_ctx, "last_topic"):
            self.frame.last_topic = history_ctx.last_topic
        elif isinstance(history_ctx, dict):
            self.frame.last_topic = history_ctx.get("last_topic")

        # 上一轮激活的锚点（如果是识图后的连续追问）
        if hasattr(history_ctx, "active_anchor") and history_ctx.active_anchor:
            if not self.frame.active_anchor:
                self.frame.active_anchor = history_ctx.active_anchor

        return self

    def resolve_reference(self) -> "TurnFrameBuilder":
        """
        指代消解：检测"这款""便宜点的"等指代词

        优先级：
        1. 如果有本轮图片锚点 -> 指代图片
        2. 如果有上一轮推荐列表 -> 指代上一轮列表
        3. 否则 -> 全局搜索
        """
        query = self.frame.cleaned_query
        if not query:
            return self

        # 检测指代类型
        detected_ref = ReferenceType.NONE
        for ref_type, pattern in self.REFERENCE_PATTERNS.items():
            if pattern.search(query):
                detected_ref = ref_type
                break

        self.frame.reference_type = detected_ref

        # 如果检测到指代，判断作用域
        if detected_ref != ReferenceType.NONE:
            self.frame.is_followup = True
            if self.frame.active_anchor and self.frame.active_anchor.is_confident():
                self.frame.scope = Scope.CURRENT_ANCHOR
                self.frame.reference_target = self.frame.active_anchor.product_id
                self.frame.log(f"指代消解: {detected_ref} -> 当前图片锚点 {self.frame.active_anchor.product_name}")
            elif self.frame.last_recommended_ids:
                self.frame.scope = Scope.PREVIOUS_RECOMMEND
                self.frame.reference_target = "last_list"
                self.frame.log(f"指代消解: {detected_ref} -> 上一轮推荐列表")
            else:
                self.frame.scope = Scope.GLOBAL
                self.frame.log(f"指代消解: {detected_ref} -> 无明确目标，降级为全局")
        elif self.frame.has_images and self.frame.active_anchor and self.frame.active_anchor.is_confident():
            # 有图且无明确指代时，如果问题很短或只是问"这是什么"，默认锚定图片
            if len(query) <= 15 or re.search(r"(是什么|什么|求鉴定|认一下|这是啥|帮我看看)", query):
                self.frame.scope = Scope.CURRENT_ANCHOR
                self.frame.log(f"短问句+图片 -> 默认锚定当前图片")

        return self

    def classify_task(self) -> "TurnFrameBuilder":
        """
        统一任务分类

        决策优先级（从高到低）：
        1. 售后关键词 -> AFTER_SALES
        2. 问候/闲聊 -> GREETING/CHITCHAT
        3. 知识问答强特征 + 无商品实体 -> KNOWLEDGE_QUERY
        4. 商品相关强特征 -> 对应商品任务
        5. 有可信图片锚点 -> PRODUCT_DETAIL
        6. 有约束条件 -> PRODUCT_RECOMMEND
        7. 兜底 -> UNKNOWN
        """
        query = self.frame.cleaned_query
        anchor = self.frame.active_anchor
        has_confident_anchor = anchor and anchor.is_confident()

        # 1. 售后检测
        if re.search(r"(退货|退款|换货|售后|客服|投诉|快递|物流|没收到|发错|破损)", query):
            self.frame.task_type = TaskType.AFTER_SALES
            self.frame.task_confidence = 0.95
            self.frame.log("任务分类: 售后")
            return self

        # 2. 问候/闲聊
        if re.match(r"^(你好|您好|hi|hello|嗨|在吗|在不在|早上好|晚上好|中午好|谢谢|感谢)[!！。.\s]*$", query, re.I):
            self.frame.task_type = TaskType.GREETING
            self.frame.task_confidence = 0.9
            self.frame.log("任务分类: 问候")
            return self

        # 3. 纯知识问答判断：有强知识特征，且没有"推荐""买""价格"等强商品特征
        has_knowledge_signal = any(p.search(query) for p in self.KNOWLEDGE_STRONG_PATTERNS)
        has_recommend_signal = any(p.search(query) for p in self.RECOMMEND_STRONG_PATTERNS)

        # 如果是锚点追问（"这款适合敏感肌吗"），属于PRODUCT_DETAIL，不是纯知识问答
        is_anchor_detail_question = (
            self.frame.is_anchor_followup()
            and re.search(r"(适合|怎么样|好用吗|成分|功效|可以吗|能吗)", query)
        )

        if has_knowledge_signal and not has_recommend_signal and not is_anchor_detail_question:
            # 检查是否提到了具体品牌/商品 - 如果提到了，可能是单品咨询而非纯知识
            brand_mention = self._extract_brand_mention(query)
            product_mention = re.search(r"(的\S{2,15}(霜|水|乳|精华|口红|粉底|防晒|面膜|气垫)|小黑瓶|小金条|神仙水|小棕瓶)", query)
            if not brand_mention and not product_mention and not has_confident_anchor:
                self.frame.task_type = TaskType.KNOWLEDGE_QUERY
                self.frame.task_confidence = 0.8
                self.frame.log("任务分类: 纯知识问答")
                return self

        # 4. 对比决策
        if re.search(r"(对比|比较|哪个好|选哪个|二选一|三选一|区别.*[？?]|哪个更)", query):
            self.frame.task_type = TaskType.PRODUCT_COMPARE
            self.frame.task_confidence = 0.85
            self.frame.log("任务分类: 商品对比")
            return self

        # 5. 单品详情/问价/判断
        if has_confident_anchor:
            if re.search(r"(多少钱|价格|价位|贵不贵|便宜吗|售价|多少钱)", query):
                self.frame.response_type = "price"
            elif re.search(r"(是什么|什么|求鉴定|认一下|这是啥|帮我看看|真假|正不正宗)", query):
                self.frame.response_type = "image_judge"
            self.frame.task_type = TaskType.PRODUCT_DETAIL
            self.frame.task_confidence = 0.75 + anchor.confidence * 0.2
            self.frame.log(f"任务分类: 单品详情 (锚点置信度={anchor.confidence:.2f})")
            return self

        # 追问场景下的单品判断
        if self.frame.scope == Scope.PREVIOUS_RECOMMEND and re.search(
            r"(怎么样|好用吗|成分|功效|适合|可以买吗|值得买吗|测评|评测)",
            query
        ):
            self.frame.task_type = TaskType.PRODUCT_DETAIL
            self.frame.task_confidence = 0.7
            self.frame.log("任务分类: 单品详情 (上轮推荐追问)")
            return self

        # "换个便宜点的" -> 在推荐列表内做价格调整
        if self.frame.reference_type in (ReferenceType.CHEAPER, ReferenceType.MORE_EXPENSIVE, ReferenceType.OTHER):
            self.frame.task_type = TaskType.PRODUCT_RECOMMEND
            self.frame.task_confidence = 0.75
            if self.frame.reference_type == ReferenceType.CHEAPER:
                self.frame.log("任务分类: 商品推荐 (要更便宜的)")
            else:
                self.frame.log("任务分类: 商品推荐 (换/其他)")
            return self

        # 6. 商品推荐（有推荐信号或有约束条件）
        if has_recommend_signal or self._has_budget_or_category_constraint() or has_confident_anchor is False and self.frame.has_images is False and (
            re.search(r"(口红|粉底|精华|面霜|水乳|防晒|面膜|气垫|遮瑕|眼影|香水|卸妆|洁面)", query)
        ):
            self.frame.task_type = TaskType.PRODUCT_RECOMMEND
            self.frame.task_confidence = 0.7 if has_recommend_signal else 0.5
            self.frame.log("任务分类: 商品推荐")
            return self

        # 有图但锚点置信度低 -> 尝试做图片相似推荐
        if self.frame.has_images and not has_confident_anchor:
            self.frame.task_type = TaskType.PRODUCT_RECOMMEND
            self.frame.task_confidence = 0.4
            self.frame.need_followup = True
            self.frame.followup_question = "我看了一下图片，但不太确定是哪款产品。你能告诉我你想找类似什么效果的产品，或者大概的预算范围吗？"
            self.frame.log("任务分类: 商品推荐 (图片锚点低置信度)")
            return self

        # 7. 兜底：根据是否有实体决定
        if has_knowledge_signal:
            self.frame.task_type = TaskType.KNOWLEDGE_QUERY
            self.frame.task_confidence = 0.4
            self.frame.log("任务分类: 知识问答 (兜底)")
        else:
            self.frame.task_type = TaskType.PRODUCT_RECOMMEND
            self.frame.task_confidence = 0.3
            self.frame.log("任务分类: 商品推荐 (兜底)")

        return self

    def extract_constraints(self) -> "TurnFrameBuilder":
        """提取本轮约束条件（预算、品牌、品类、肤质等）"""
        query = self.frame.cleaned_query
        c = self.frame.constraints

        # --- 预算提取 ---
        budget_patterns = [
            (re.compile(r"(\d+)\s*[到\-~至]\s*(\d+)\s*(元|块|rmb|￥|¥)?"), "range"),
            (re.compile(r"(预算|大概|差不多|左右|价位).{0,5}(\d+)\s*(元|块|以内|以下|左右)?"), "max"),
            (re.compile(r"(\d+)\s*(元|块)\s*(以内|以下)"), "max"),
            (re.compile(r"(\d+)\s*(以内|以下|内)"), "max"),
            (re.compile(r"不超过.{0,3}(\d+)"), "max"),
            (re.compile(r"低于.{0,3}(\d+)"), "max"),
            (re.compile(r"(\d+)\s*(元|块)?\s*(左右|上下|差不多)"), "flexible"),
            (re.compile(r"(便宜点|性价比|平价|学生党|百元内)"), "cheap"),
            (re.compile(r"(贵一点|高端|贵妇|大牌)"), "expensive"),
        ]

        for pattern, btype in budget_patterns:
            m = pattern.search(query)
            if m:
                if btype == "range":
                    c.budget_min = float(m.group(1))
                    c.budget_max = float(m.group(2))
                    c.budget_type = BudgetType.RIGID
                    self.frame.log(f"约束: 预算范围 {c.budget_min}-{c.budget_max}")
                    break
                elif btype in ("max", "flexible"):
                    val = None
                    for g in m.groups():
                        if g and re.match(r'^\d+(\.\d+)?$', g.strip()):
                            val = float(g.strip())
                            break
                    if val is None:
                        for g in m.groups():
                            if g and re.search(r'\d', g):
                                num_match = re.search(r'(\d+(?:\.\d+)?)', g)
                                if num_match:
                                    val = float(num_match.group(1))
                                    break
                    if val is not None:
                        c.budget_max = val
                        c.budget_type = BudgetType.RIGID if btype == "max" else BudgetType.FLEXIBLE
                        self.frame.log(f"约束: budget_max={c.budget_max} ({'刚性' if btype == 'max' else '弹性'})")
                        break
                elif btype == "cheap":
                    c.budget_max = 200
                    c.budget_type = BudgetType.FLEXIBLE
                    self.frame.log("约束: 便宜/平价 -> 预算max=200")
                    break
                elif btype == "expensive":
                    c.budget_min = 500
                    c.budget_type = BudgetType.FLEXIBLE
                    self.frame.log("约束: 高端/贵妇 -> 预算min=500")
                    break

        # "换个便宜点的"/"换个贵点的"特殊处理
        if self.frame.reference_type == ReferenceType.CHEAPER:
            for pid in self.frame.last_recommended_ids:
                c.exclude_product_ids.add(pid)
            c.budget_type = BudgetType.FLEXIBLE
            if self.frame.active_anchor:
                anchor_price = self.frame.active_anchor.evidence.get("price")
                if anchor_price and anchor_price > 0:
                    c.budget_max = anchor_price * 0.85
                    self.frame.log(f"约束: 便宜点 -> 预算<=锚点价85%={c.budget_max:.0f}")
            if c.budget_max is None:
                c.budget_max = 200
                self.frame.log("约束: 便宜点 -> 默认预算max=200")
        elif self.frame.reference_type == ReferenceType.MORE_EXPENSIVE:
            for pid in self.frame.last_recommended_ids:
                c.exclude_product_ids.add(pid)
            c.budget_type = BudgetType.FLEXIBLE
            if self.frame.active_anchor:
                anchor_price = self.frame.active_anchor.evidence.get("price")
                if anchor_price and anchor_price > 0:
                    c.budget_min = anchor_price * 1.15
                    self.frame.log(f"约束: 贵点 -> 预算>=锚点价115%={c.budget_min:.0f}")
            if c.budget_min is None:
                c.budget_min = 500
                self.frame.log("约束: 贵点 -> 默认预算min=500")

        # --- 排除上一轮不想要的商品（换一个场景）---
        if self.frame.reference_type == ReferenceType.OTHER:
            for pid in self.frame.last_recommended_ids:
                c.exclude_product_ids.add(pid)
            self.frame.log(f"约束: 排除上轮推荐的 {len(c.exclude_product_ids)} 个商品")

        # --- 品牌提取（简化版，后续接入完整品牌词典）---
        brand_list = [
            "兰蔻", "雅诗兰黛", "香奈儿", "迪奥", "YSL", "圣罗兰", "纪梵希", "阿玛尼",
            "欧莱雅", "资生堂", "SK-II", "SK2", "海蓝之谜", "娇兰", "娇韵诗", "倩碧",
            "理肤泉", "雅漾", "薇姿", "修丽可", "科颜氏", "悦木之源", "茵芙莎", "IPSA",
            "完美日记", "花西子", "毛戈平", "彩棠", "橘朵", "酵色", "3CE", "MAC",
            "魅可", "NARS", "纳斯", "芭比波朗", "BOBBI BROWN", "植村秀", "资生堂",
            "CPB", "肌肤之钥", "赫莲娜", "HR", "玉兰油", "OLAY", "羽西", "佰草集",
            "薇诺娜", "玉泽", "润百颜", "夸迪", "米蓓尔", "瑷尔博士", "颐莲",
        ]
        for brand in brand_list:
            if brand in query:
                if "不要" in query[:query.find(brand) + len(brand) + 5] or "别" in query[:query.find(brand) + len(brand) + 3]:
                    c.exclude_brands.append(brand)
                    self.frame.log(f"约束: 排除品牌 {brand}")
                else:
                    c.brands.append(brand)
                    self.frame.log(f"约束: 品牌={brand}")

        # --- 品类提取（简化版一级+二级）---
        category_map = {
            # 一级 -> 二级关键词
            "护肤": ["精华", "面霜", "水乳", "乳液", "爽肤水", "化妆水", "眼霜", "面膜", "洁面", "卸妆", "防晒", "精华水", "精华乳", "护肤油"],
            "彩妆": ["口红", "唇膏", "唇釉", "粉底", "粉底液", "气垫", "粉饼", "散粉", "蜜粉", "遮瑕", "眼影", "腮红", "高光", "修容", "眉笔", "眼线", "睫毛膏", "定妆喷雾", "隔离", "妆前乳"],
            "香水": ["香水", "香氛", "淡香", "浓香", "古龙水"],
        }
        for l1, l2_list in category_map.items():
            for l2 in l2_list:
                if l2 in query:
                    if l1 not in c.categories_l1:
                        c.categories_l1.append(l1)
                    if l2 not in c.categories_l2:
                        c.categories_l2.append(l2)
                    self.frame.log(f"约束: 品类={l1}/{l2}")

        # --- 肤质提取 ---
        skin_types = ["干皮", "油皮", "混干", "混油", "敏感肌", "中性皮", "油痘肌", "干敏肌", "混油敏感", "混干敏感"]
        for st in skin_types:
            if st in query:
                c.skin_types.append(st)
                self.frame.log(f"约束: 肤质={st}")

        # --- 诉求提取 ---
        concerns = [
            "保湿", "补水", "美白", "淡斑", "抗老", "抗皱", "紧致", "修护", "舒缓",
            "控油", "祛痘", "去黑头", "收缩毛孔", "提亮", "去黄", "抗氧化", "屏障修复",
            "防晒黑", "防水", "防汗", "持久", "不脱妆", "遮瑕力强", "自然", "轻薄",
            "滋润", "哑光", "珠光", "雾面", "光泽", "奶油肌", "玻璃唇",
        ]
        for concern in concerns:
            if concern in query:
                c.skin_concerns.append(concern)

        # --- 从锚点继承品类（如果有锚点且本轮没指明代品类）---
        anchor = self.frame.active_anchor
        if anchor and anchor.is_confident():
            if anchor.category_l1 and not c.categories_l1:
                c.categories_l1.append(anchor.category_l1)
            if anchor.category_l2 and not c.categories_l2:
                c.categories_l2.append(anchor.category_l2)
            if anchor.brand and not c.brands and self.frame.scope == Scope.CURRENT_ANCHOR:
                c.brands.append(anchor.brand)
                # anchor的品牌不是"约束"而是"锚定"，不要强过滤
                self.frame.log(f"锚点继承: brand={anchor.brand} (仅参考)")

        return self

    def _extract_brand_mention(self, query: str) -> Optional[str]:
        """快速检测是否提到品牌"""
        brands = ["兰蔻", "雅诗兰黛", "香奈儿", "迪奥", "YSL", "圣罗兰", "纪梵希", "阿玛尼",
                  "欧莱雅", "资生堂", "SK-II", "SK2", "海蓝之谜", "娇兰", "娇韵诗", "倩碧",
                  "理肤泉", "雅漾", "修丽可", "科颜氏", "IPSA", "茵芙莎", "MAC", "NARS",
                  "薇诺娜", "玉泽", "润百颜", "完美日记", "花西子", "毛戈平", "彩棠", "橘朵", "3CE"]
        for b in brands:
            if b in query:
                return b
        return None

    def _has_budget_or_category_constraint(self) -> bool:
        """判断是否有明确的预算/品类约束"""
        c = self.frame.constraints
        return (c.budget_max is not None
                or c.brands
                or c.categories_l1
                or c.categories_l2
                or c.skin_types
                or c.skin_concerns)

    def build(self) -> TurnFrame:
        """构建最终的 TurnFrame"""
        self.frame.log(f"===== TurnFrame 构建完成 =====")
        self.frame.log(f"任务: {self.frame.task_type.value} (置信度={self.frame.task_confidence:.2f})")
        self.frame.log(f"作用域: {self.frame.scope.value}")
        if self.frame.reference_type != ReferenceType.NONE:
            self.frame.log(f"指代: {self.frame.reference_type.value} -> {self.frame.reference_target}")
        if self.frame.active_anchor:
            self.frame.log(f"锚点: {self.frame.active_anchor.brand} {self.frame.active_anchor.product_name} (conf={self.frame.active_anchor.confidence:.2f})")
        if self.frame.constraints.has_constraints():
            self.frame.log(f"约束: budget_max={self.frame.constraints.budget_max}, brands={self.frame.constraints.brands}, categories_l2={self.frame.constraints.categories_l2}")
        return self.frame


def build_turn_frame(
    session_id: str,
    turn_id: int,
    query: str,
    image_results: Optional[List[Dict[str, Any]]] = None,
    history_ctx: Any = None
) -> TurnFrame:
    """
    便捷函数：构建 TurnFrame

    Args:
        session_id: 会话ID
        turn_id: 轮次ID
        query: 用户原始输入
        image_results: 图片搜索结果列表（来自image_search接口）
        history_ctx: 历史上下文（包含上一轮推荐ID、激活锚点等）

    Returns:
        构建完成的 TurnFrame
    """
    return (
        TurnFrameBuilder(session_id, turn_id)
        .with_query(query)
        .with_images(image_results or [])
        .with_history_context(history_ctx)
        .resolve_reference()
        .extract_constraints()
        .classify_task()
        .build()
    )
