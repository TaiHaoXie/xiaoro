"""
统一错误处理模块

提供自定义异常类和错误处理装饰器
"""

from typing import Callable, Any, Optional, Dict
from functools import wraps
import logging
import traceback

logger = logging.getLogger(__name__)


# ==================== 自定义异常类 ====================

class ECommerceException(Exception):
    """电商系统基础异常"""
    def __init__(self, message: str, code: str = "EC_ERROR", details: Dict = None):
        self.message = message
        self.code = code
        self.details = details or {}
        super().__init__(self.message)


class DatabaseException(ECommerceException):
    """数据库异常"""
    def __init__(self, message: str, details: Dict = None):
        super().__init__(message, "DB_ERROR", details)


class LLMException(ECommerceException):
    """LLM服务异常"""
    def __init__(self, message: str, details: Dict = None):
        super().__init__(message, "LLM_ERROR", details)


class VectorSearchException(ECommerceException):
    """向量搜索异常"""
    def __init__(self, message: str, details: Dict = None):
        super().__init__(message, "VECTOR_ERROR", details)


class ValidationException(ECommerceException):
    """输入验证异常"""
    def __init__(self, message: str, field: str = None, details: Dict = None):
        details = details or {}
        if field:
            details["field"] = field
        super().__init__(message, "VALIDATION_ERROR", details)


class RateLimitException(ECommerceException):
    """速率限制异常"""
    def __init__(self, message: str = "请求过于频繁，请稍后再试", retry_after: int = 60):
        super().__init__(message, "RATE_LIMIT", {"retry_after": retry_after})
        self.retry_after = retry_after


# ==================== 错误处理装饰器 ====================

def handle_errors(
    default_return: Any = None,
    raise_on_error: bool = False,
    log_level: str = "ERROR"
):
    """
    统一错误处理装饰器

    Args:
        default_return: 发生错误时的默认返回值
        raise_on_error: 是否重新抛出异常
        log_level: 日志级别

    用法:
        @handle_errors(default_return={"error": "Internal error"})
        async def my_function():
            ...
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            except ECommerceException as e:
                logger.log(
                    logging.getLevelName(log_level),
                    f"业务异常 in {func.__name__}: {e.message}",
                    extra={"code": e.code, "details": e.details}
                )
                if raise_on_error:
                    raise
                return default_return
            except Exception as e:
                logger.log(
                    logging.getLevelName(log_level),
                    f"未捕获异常 in {func.__name__}: {str(e)}",
                    exc_info=True
                )
                if raise_on_error:
                    raise
                return default_return

        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except ECommerceException as e:
                logger.log(
                    logging.getLevelName(log_level),
                    f"业务异常 in {func.__name__}: {e.message}",
                    extra={"code": e.code, "details": e.details}
                )
                if raise_on_error:
                    raise
                return default_return
            except Exception as e:
                logger.log(
                    logging.getLevelName(log_level),
                    f"未捕获异常 in {func.__name__}: {str(e)}",
                    exc_info=True
                )
                if raise_on_error:
                    raise
                return default_return

        # 根据函数类型返回对应的包装器
        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper

    return decorator


def safe_execute(
    fallback_value: Any = None,
    exception_types: tuple = (Exception,)
):
    """
    安全执行装饰器（捕获指定异常）

    Args:
        fallback_value: 异常时的返回值
        exception_types: 要捕获的异常类型

    用法:
        @safe_execute(fallback_value=[], exception_types=(ValueError, KeyError))
        def parse_data(data):
            ...
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            except exception_types as e:
                logger.warning(f"{func.__name__} 被捕获: {e}")
                return fallback_value

        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except exception_types as e:
                logger.warning(f"{func.__name__} 被捕获: {e}")
                return fallback_value

        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper

    return decorator


# ==================== 重试装饰器 ====================

def retry_on_failure(
    max_attempts: int = 3,
    exceptions: tuple = (Exception,),
    backoff_factor: float = 1.0
):
    """
    失败重试装饰器

    Args:
        max_attempts: 最大尝试次数
        exceptions: 需要重试的异常类型
        backoff_factor: 退避因子（指数退避）

    用法:
        @retry_on_failure(max_attempts=3, exceptions=(ConnectionError,))
        async def fetch_data():
            ...
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            import asyncio

            last_exception = None
            for attempt in range(max_attempts):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt < max_attempts - 1:
                        wait_time = backoff_factor * (2 ** attempt)
                        logger.warning(
                            f"{func.__name__} 失败(尝试{attempt+1}/{max_attempts}), "
                            f"{wait_time}秒后重试: {e}"
                        )
                        await asyncio.sleep(wait_time)
                    else:
                        logger.error(f"{func.__name__} 达到最大重试次数")

            raise last_exception

        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            import time

            last_exception = None
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt < max_attempts - 1:
                        wait_time = backoff_factor * (2 ** attempt)
                        logger.warning(
                            f"{func.__name__} 失败(尝试{attempt+1}/{max_attempts}), "
                            f"{wait_time}秒后重试: {e}"
                        )
                        time.sleep(wait_time)
                    else:
                        logger.error(f"{func.__name__} 达到最大重试次数")

            raise last_exception

        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper

    return decorator


# ==================== 错误响应格式化 ====================

def format_error_response(exception: Exception) -> Dict[str, Any]:
    """
    格式化错误响应

    Args:
        exception: 异常对象

    Returns:
        标准化的错误响应字典
    """
    if isinstance(exception, ECommerceException):
        return {
            "success": False,
            "error": {
                "code": exception.code,
                "message": exception.message,
                "details": exception.details
            }
        }
    else:
        return {
            "success": False,
            "error": {
                "code": "INTERNAL_ERROR",
                "message": str(exception),
                "details": {}
            }
        }


# ==================== 使用示例 ====================

if __name__ == "__main__":
    # 示例1：使用自定义异常
    try:
        raise DatabaseException("数据库连接失败", details={"host": "localhost"})
    except ECommerceException as e:
        print(f"捕获异常: {e.code} - {e.message}")

    # 示例2：使用装饰器
    @handle_errors(default_return={"error": "Failed"})
    async def test_function():
        raise ValueError("测试错误")

    # 示例3：使用重试
    @retry_on_failure(max_attempts=3, exceptions=(ValueError,))
    async def test_retry():
        import random
        if random.random() < 0.7:
            raise ValueError("随机失败")
        return "成功"
