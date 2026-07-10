"""
领域模型模块

定义系统中所有核心数据结构
所有API和服务共享使用，避免重复定义
"""

from pydantic import BaseModel, Field, field_validator
from typing import List, Optional, Dict, Any
from datetime import datetime
from enum import Enum


# ==================== 枚举类型 ====================

class IntentType(str, Enum):
    """意图类型"""
    GREETING = "greeting"
    CHITCHAT = "chitchat"
    PRODUCT_SEARCH = "product_search"
    PRODUCT_RECOMMEND = "product_recommend"
    PRODUCT_COMPARE = "product_compare"
    PURCHASE_ADVICE = "purchase_advice"
    PRICE_INQUIRY = "price_inquiry"
    KNOWLEDGE_QUERY = "knowledge_query"
    UNKNOWN = "unknown"


class MessageType(str, Enum):
    """消息类型"""
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class FeedbackType(str, Enum):
    """反馈类型"""
    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"


# ==================== 商品相关模型 ====================

class Product(BaseModel):
    """商品基础模型"""
    id: int
    name: str = Field(..., description="商品名称")
    category: Optional[str] = Field(None, description="商品类别")
    brand: Optional[str] = Field(None, description="品牌")
    price: Optional[float] = Field(None, description="现价")
    original_price: Optional[float] = Field(None, description="原价")
    description: Optional[str] = Field(None, description="商品描述")
    specifications: Optional[Dict[str, Any]] = Field(default_factory=dict, description="规格参数")
    image_url: Optional[str] = Field(None, description="主图URL")
    detail_url: Optional[str] = Field(None, description="详情页URL")
    platform: Optional[str] = Field(None, description="平台")
    stock: Optional[int] = Field(0, description="库存")
    sales_count: Optional[int] = Field(0, description="销量")
    rating: Optional[float] = Field(None, ge=0, le=5, description="评分")
    review_count: Optional[int] = Field(0, description="评论数")

    @field_validator("price", "original_price")
    @classmethod
    def validate_price(cls, v):
        if v is not None and v < 0:
            raise ValueError("价格不能为负数")
        return v

    @property
    def discount(self) -> Optional[float]:
        """折扣率"""
        if self.original_price and self.price and self.original_price > self.price:
            return round((1 - self.price / self.original_price) * 100, 1)
        return None


class ProductCreate(BaseModel):
    """商品创建模型"""
    name: str = Field(..., min_length=1, max_length=500)
    category: Optional[str] = None
    brand: Optional[str] = None
    price: Optional[float] = Field(None, ge=0)
    original_price: Optional[float] = Field(None, ge=0)
    description: Optional[str] = None
    specifications: Optional[Dict[str, Any]] = None
    image_url: Optional[str] = None
    detail_url: Optional[str] = None
    platform: Optional[str] = None


class ProductUpdate(BaseModel):
    """商品更新模型"""
    name: Optional[str] = Field(None, min_length=1, max_length=500)
    category: Optional[str] = None
    brand: Optional[str] = None
    price: Optional[float] = Field(None, ge=0)
    original_price: Optional[float] = Field(None, ge=0)
    description: Optional[str] = None
    specifications: Optional[Dict[str, Any]] = None
    image_url: Optional[str] = None
    detail_url: Optional[str] = None
    stock: Optional[int] = Field(None, ge=0)


class ProductFilter(BaseModel):
    """商品筛选模型"""
    category: Optional[str] = None
    brand: Optional[str] = None
    min_price: Optional[float] = Field(None, ge=0)
    max_price: Optional[float] = Field(None, ge=0)
    min_rating: Optional[float] = Field(None, ge=0, le=5)
    search_text: Optional[str] = None
    limit: int = Field(20, ge=1, le=100)
    offset: int = Field(0, ge=0)


# ==================== 对话相关模型 ====================

class Message(BaseModel):
    """对话消息"""
    role: MessageType
    content: str
    timestamp: datetime = Field(default_factory=datetime.now)
    intent: Optional[IntentType] = None
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)


class Conversation(BaseModel):
    """会话模型"""
    id: Optional[int] = None
    session_id: str = Field(..., min_length=1)
    user_id: Optional[int] = None
    messages: List[Message] = Field(default_factory=list)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class ChatRequest(BaseModel):
    """聊天请求"""
    message: str = Field(..., min_length=1, max_length=5000, description="用户消息")
    session_id: Optional[str] = Field(None, description="会话ID")
    conversation_history: Optional[List[Message]] = Field(
        default_factory=list,
        description="历史对话记录"
    )
    stream: bool = Field(default=False, description="是否流式输出")


class ChatResponse(BaseModel):
    """聊天响应"""
    response: str
    intent: Dict[str, Any] = Field(default_factory=dict)
    scenario_intent: Dict[str, Any] = Field(default_factory=dict)
    sources: List[Dict] = Field(default_factory=list)
    products: List[Product] = Field(default_factory=list)
    comparison_data: Optional[Dict] = None
    decision_process: Optional[Dict] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    session_id: str


# ==================== 用户相关模型 ====================

class UserProfile(BaseModel):
    """用户画像"""
    user_id: Optional[int] = None
    preferred_categories: List[str] = Field(default_factory=list)
    preferred_brands: List[str] = Field(default_factory=list)
    budget_range: Optional[Dict[str, float]] = Field(None, description="预算范围")
    skin_type: Optional[str] = Field(None, description="肤质（护肤场景）")
    skin_concerns: List[str] = Field(default_factory=list, description="皮肤困扰")
    interaction_count: int = Field(0, description="互动次数")
    last_interaction: Optional[datetime] = None

    def add_category_preference(self, category: str):
        """添加类别偏好"""
        if category not in self.preferred_categories:
            self.preferred_categories.append(category)

    def add_brand_preference(self, brand: str):
        """添加品牌偏好"""
        if brand not in self.preferred_brands:
            self.preferred_brands.append(brand)


# ==================== 意图识别模型 ====================

class IntentResult(BaseModel):
    """意图识别结果"""
    intent: IntentType
    confidence: float = Field(..., ge=0, le=1, description="置信度")
    entities: Dict[str, Any] = Field(default_factory=dict, description="提取的实体")
    scenario_intent: Optional[str] = Field(None, description="场景意图")
    priority: str = Field("中", description="优先级：高/中/低")
    raw_response: Optional[Dict] = Field(None, description="原始响应")


# ==================== 来源引用相关模型 ====================

class SourceType(str, Enum):
    """来源类型"""
    PRODUCT = "product"           # 商品库
    KNOWLEDGE = "knowledge"       # 知识库
    FAQ = "faq"                   # FAQ
    REVIEW = "review"             # 用户评价
    GUIDE = "guide"               # 使用指南
    PITFALL = "pitfall"           # 避坑指南


class Citation(BaseModel):
    """引用来源"""
    type: SourceType
    id: Optional[int] = None
    title: str
    snippet: Optional[str] = None      # 引用的片段
    url: Optional[str] = None          # 可跳转的链接
    confidence: Optional[float] = None # 相关性置信度
    metadata: Optional[Dict[str, Any]] = None


class PitfallWarning(BaseModel):
    """避坑提示"""
    title: str = Field(..., description="避坑标题")
    category: str = Field(..., description="避坑类别：质量/价格/适用性等")
    severity: str = Field("中", description="严重程度：高/中/低")
    description: str = Field(..., description="详细说明")
    affected_products: List[int] = Field(default_factory=list, description="相关商品ID")
    recommendation: Optional[str] = None  # 建议方案


class ProductWithWarnings(BaseModel):
    """带避坑提示的商品"""
    product: Product
    warnings: List[PitfallWarning] = Field(default_factory=list)
    match_score: Optional[float] = None


# ==================== RAG检索模型 ====================

class RAGRequest(BaseModel):
    """RAG检索请求"""
    query: str = Field(..., min_length=1, description="检索查询")
    top_k: int = Field(10, ge=1, le=50, description="返回数量")
    filters: Optional[Dict[str, Any]] = Field(None, description="过滤条件")
    search_products: bool = Field(True, description="是否搜索商品")
    search_knowledge: bool = Field(True, description="是否搜索知识库")


class RAGResponse(BaseModel):
    """RAG检索响应"""
    query: str
    products: List[Product] = Field(default_factory=list)
    knowledge: List[Dict[str, Any]] = Field(default_factory=list)
    sources: List[Dict[str, Any]] = Field(default_factory=list)


# ==================== 推荐相关模型 ====================

class RecommendationRequest(BaseModel):
    """推荐请求"""
    category: Optional[str] = None
    budget: Optional[float] = Field(None, ge=0)
    requirements: Optional[Dict[str, Any]] = None
    top_k: int = Field(5, ge=1, le=20)


class RecommendationResult(BaseModel):
    """推荐结果"""
    recommendations: List[Product]
    reason: str
    total_count: int
    filters_applied: Optional[Dict[str, Any]] = None


# ==================== 对比相关模型 ====================

class ComparisonRequest(BaseModel):
    """对比请求"""
    product_ids: List[int] = Field(..., min_length=2, max_length=5, description="商品ID列表")


class ComparisonResult(BaseModel):
    """对比结果"""
    products: List[Product]
    summary: str
    best_choice: Optional[Product] = None
    comparison_matrix: Optional[Dict[str, Dict[str, Any]]] = None


# ==================== 反馈相关模型 ====================

class FeedbackRequest(BaseModel):
    """反馈请求"""
    conversation_id: Optional[int] = None
    session_id: Optional[str] = None
    product_id: Optional[int] = None
    feedback_type: FeedbackType
    reason: Optional[str] = None
    rating: Optional[int] = Field(None, ge=1, le=5)


class Feedback(BaseModel):
    """反馈记录"""
    id: Optional[int] = None
    conversation_id: Optional[int] = None
    product_id: Optional[int] = None
    feedback_type: FeedbackType
    reason: Optional[str] = None
    rating: Optional[int] = None
    created_at: Optional[datetime] = None


# ==================== 用户认证模型 ====================

class User(BaseModel):
    """用户模型"""
    id: int
    username: str
    email: Optional[str] = None
    is_active: bool = True
    created_at: Optional[datetime] = None


class UserCreate(BaseModel):
    """用户创建"""
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=6)
    email: Optional[str] = None


class UserLogin(BaseModel):
    """用户登录"""
    username: str = Field(..., min_length=3)
    password: str = Field(..., min_length=6)


class Token(BaseModel):
    """JWT令牌"""
    access_token: str
    refresh_token: Optional[str] = None
    token_type: str = "bearer"
    expires_in: Optional[int] = None


class TokenPayload(BaseModel):
    """Token载荷"""
    sub: int = Field(..., description="用户ID")
    exp: int = Field(..., description="过期时间")
    iat: int = Field(..., description="签发时间")


# ==================== 文档相关模型 ====================

class Document(BaseModel):
    """文档模型"""
    id: int
    title: str
    content: str
    type: str
    metadata: Optional[Dict[str, Any]] = None
    created_at: Optional[datetime] = None


class DocumentUpload(BaseModel):
    """文档上传"""
    title: Optional[str] = None
    type: str = Field(..., description="文档类型：faq/product_desc/user_guide/review")


# ==================== 图片相关模型 ====================

class ImageSearchRequest(BaseModel):
    """图片搜索请求"""
    top_k: int = Field(10, ge=1, le=50)
    min_score: float = Field(0.5, ge=0, le=1)
    enable_ocr: bool = Field(True)
    category: Optional[str] = None


class ImageSearchResponse(BaseModel):
    """图片搜索响应"""
    query_id: str
    results: List[Dict[str, Any]]
    total: int
    ocr_info: Optional[Dict[str, Any]] = None


# ==================== 通用模型 ====================

class HealthCheck(BaseModel):
    """健康检查"""
    status: str
    version: str
    timestamp: datetime


class ErrorResponse(BaseModel):
    """错误响应"""
    success: bool = False
    error: Dict[str, Any]
    timestamp: datetime = Field(default_factory=datetime.now)


class PaginatedResponse(BaseModel):
    """分页响应"""
    items: List[Any]
    total: int
    page: int
    page_size: int
    total_pages: int


# ==================== 使用示例 ====================

"""
# 在API中使用
from app.models.domain import ChatRequest, ChatResponse

@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    ...


# 在服务中使用
from app.models.domain import Product, IntentResult

async def search_products(query: str) -> List[Product]:
    ...
"""
