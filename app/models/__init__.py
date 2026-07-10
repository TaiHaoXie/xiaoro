"""
领域模型包

统一导出所有数据模型
"""

# 基础模型
from app.models.domain import (
    BaseModel,
    Field,
    field_validator,
)

# 枚举
from app.models.domain import (
    IntentType,
    MessageType,
    FeedbackType,
)

# 商品相关
from app.models.domain import (
    Product,
    ProductCreate,
    ProductUpdate,
    ProductFilter,
)

# 对话相关
from app.models.domain import (
    Message,
    Conversation,
    ChatRequest,
    ChatResponse,
)

# 用户相关
from app.models.domain import (
    User,
    UserProfile,
    UserCreate,
    UserLogin,
)

# 意图识别
from app.models.domain import IntentResult

# RAG检索
from app.models.domain import RAGRequest, RAGResponse

# 推荐
from app.models.domain import RecommendationRequest, RecommendationResult

# 对比
from app.models.domain import ComparisonRequest, ComparisonResult

# 反馈
from app.models.domain import FeedbackRequest, Feedback

# 认证
from app.models.domain import Token, TokenPayload

# 通用
from app.models.domain import (
    HealthCheck,
    ErrorResponse,
    PaginatedResponse,
)

__all__ = [
    # 枚举
    "IntentType",
    "MessageType",
    "FeedbackType",
    # 商品
    "Product",
    "ProductCreate",
    "ProductUpdate",
    "ProductFilter",
    # 对话
    "Message",
    "Conversation",
    "ChatRequest",
    "ChatResponse",
    # 用户
    "User",
    "UserProfile",
    "UserCreate",
    "UserLogin",
    # 意图
    "IntentResult",
    # RAG
    "RAGRequest",
    "RAGResponse",
    # 推荐
    "RecommendationRequest",
    "RecommendationResult",
    # 对比
    "ComparisonRequest",
    "ComparisonResult",
    # 反馈
    "FeedbackRequest",
    "Feedback",
    # 认证
    "Token",
    "TokenPayload",
    # 通用
    "HealthCheck",
    "ErrorResponse",
    "PaginatedResponse",
]
