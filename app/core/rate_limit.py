"""
速率限制模块

使用 slowapi 实现 API 速率限制
防止 OOM 和滥用
"""

from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from fastapi import Request, HTTPException, status
from typing import Callable, Optional
import logging

logger = logging.getLogger(__name__)


# ==================== 速率限制器配置 ====================

def get_user_id(request: Request) -> str:
    """
    获取用户唯一标识

    优先级：
    1. API Key（如果有）
    2. Session ID（如果有）
    3. IP 地址

    Args:
        request: FastAPI 请求对象

    Returns:
        用户唯一标识字符串
    """
    # 尝试从 header 获取 API Key
    api_key = request.headers.get("X-API-Key")
    if api_key:
        return f"apikey:{api_key}"

    # 尝试从 query 获取 session_id
    session_id = request.query_params.get("session_id")
    if session_id:
        return f"session:{session_id}"

    # 默认使用 IP 地址
    return f"ip:{get_remote_address(request)}"


# 创建速率限制器实例
limiter = Limiter(
    key_func=get_user_id,
    default_limits=["200/hour"],  # 默认限制：每小时 200 次
    storage_uri="memory://",      # 使用内存存储（开发环境）
    # 生产环境建议使用 Redis：
    # storage_uri="redis://localhost:6379"
)


# ==================== 速率限制配置 ====================

# 不同接口的限制规则
RATE_LIMITS = {
    # 对话接口（最严格 - LLM 调用成本高）
    "chat": "10/minute",      # 每分钟 10 次
    "chat_stream": "10/minute",  # 每分钟 10 次

    # 图片搜索（中等 - CLIP 推理成本）
    "image_search": "20/minute",  # 每分钟 20 次
    "image_upload": "5/minute",   # 每分钟 5 次

    # RAG 检索（宽松 - 主要是向量搜索）
    "rag_search": "30/minute",    # 每分钟 30 次

    # 文档上传（严格 - 存储和解析成本）
    "document_upload": "3/minute",  # 每分钟 3 次

    # 决策辅助（中等）
    "decision": "15/minute",        # 每分钟 15 次

    # 默认限制
    "default": "60/minute",         # 每分钟 60 次
}


# ==================== 自定义限制装饰器 ====================

def limit(request_limit: Optional[str] = None):
    """
    自定义速率限制装饰器

    Args:
        request_limit: 限制规则（如 "10/minute"），不指定则使用默认

    Example:
        @limit("10/minute")
        async def my_endpoint():
            ...
    """
    if request_limit:
        return limiter.limit(request_limit)
    return limiter.limit(RATE_LIMITS["default"])


# ==================== 针对不同场景的装饰器 ====================

def limit_chat(func: Callable):
    """对话接口限制（10/minute）"""
    return limiter.limit(RATE_LIMITS["chat"])(func)


def limit_image_search(func: Callable):
    """图片搜索限制（20/minute）"""
    return limiter.limit(RATE_LIMITS["image_search"])(func)


def limit_rag_search(func: Callable):
    """RAG 检索限制（30/minute）"""
    return limiter.limit(RATE_LIMITS["rag_search"])(func)


def limit_upload(func: Callable):
    """上传限制（5/minute）"""
    return limiter.limit(RATE_LIMITS["image_upload"])(func)


def limit_document(func: Callable):
    """文档上传限制（3/minute）"""
    return limiter.limit(RATE_LIMITS["document_upload"])(func)


def limit_decision(func: Callable):
    """决策辅助限制（15/minute）"""
    return limiter.limit(RATE_LIMITS["decision"])(func)


# ==================== 错误处理 ====================

async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    """
    速率限制错误处理

    Args:
        request: FastAPI 请求对象
        exc: 速率限制异常

    Returns:
        标准化的错误响应
    """
    # 获取限制信息
    retry_after = getattr(exc, "retry_after", 60)

    logger.warning(
        f"速率限制触发: {get_user_id(request)} - "
        f"路径: {request.url.path}"
    )

    from fastapi.responses import JSONResponse

    return JSONResponse(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        content={
            "success": False,
            "error": {
                "code": "RATE_LIMIT_EXCEEDED",
                "message": "请求过于频繁，请稍后再试",
                "retry_after": retry_after,
                "detail": f"请在 {retry_after} 秒后重试"
            }
        },
        headers={
            "Retry-After": str(retry_after),
            "X-RateLimit-Limit": str(exc.detail),
            "X-RateLimit-Reset": str(retry_after)
        }
    )


# ==================== 速率限制状态查询 ====================

class RateLimitInfo:
    """速率限制信息"""

    @staticmethod
    def get_info(user_id: str) -> dict:
        """
        获取用户的速率限制信息

        Args:
            user_id: 用户标识

        Returns:
            速率限制信息字典
        """
        # 从 limiter 的 storage 中获取信息
        # 注意：内存存储的 slowapi 可能不提供完整信息
        return {
            "user_id": user_id,
            "limits": RATE_LIMITS,
            "message": "请参考具体接口的限制规则"
        }


# ==================== 使用示例 ====================

"""
# 在 main.py 中添加速率限制
from app.core.rate_limit import limiter, rate_limit_handler

app = FastAPI()
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_handler)


# 在路由中使用
from app.core.rate_limit import limit_chat, limit_image_search

@router.post("/message")
@limit_chat  # 应用对话限制
async def chat_message(request: ChatRequest):
    ...


# 或者使用自定义限制
from app.core.rate_limit import limit

@router.post("/custom")
@limit("5/minute")  # 自定义限制
async def custom_endpoint():
    ...
"""
