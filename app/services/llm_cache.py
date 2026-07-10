"""
LLM 调用缓存服务

降低 API 成本，提高响应速度
"""

import hashlib
import json
import logging
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta
from functools import wraps

from app.database.postgres import get_postgres_pool
from app.config import settings

logger = logging.getLogger(__name__)


def _resolve_model_name(service: Any, model: Optional[str], kwargs: Dict[str, Any]) -> str:
    """
    解析缓存用的模型名称

    优先级：
    1. 显式传入的 model
    2. service.model 属性（如果存在）
    3. 根据 provider 推断默认模型
    """
    if model:
        return model

    service_model = getattr(service, "model", None)
    if service_model:
        return service_model

    provider = kwargs.get("provider") or settings.DEFAULT_LLM_PROVIDER
    if provider == "doubao":
        return settings.DOUBAO_CHAT_MODEL
    if provider == "siliconflow":
        return settings.SILICONFLOW_CHAT_MODEL
    return settings.KIMI_CHAT_MODEL


class LLMCache:
    """
    LLM 调用缓存

    支持两种存储方式：
    1. Redis（推荐，生产环境）
    2. PostgreSQL（开发环境）
    """

    def __init__(self):
        """初始化缓存服务"""
        # 修复: 移除 "or True" 逻辑错误
        # 优先使用Redis，但当Redis未配置时自动降级到PostgreSQL
        redis_host = getattr(settings, 'REDIS_HOST', 'localhost')
        self.use_redis = redis_host != "localhost" and redis_host is not None
        self.ttl = getattr(settings, 'LLM_CACHE_TTL', 86400)  # 缓存24小时（可配置）

        # 尝试连接 Redis
        self._redis_client = None
        if self.use_redis:
            try:
                import redis.asyncio as aioredis
                self._redis_client = aioredis.from_url(
                    f"redis://{settings.REDIS_HOST}:{settings.REDIS_PORT}/{settings.REDIS_DB}",
                    encoding="utf-8",
                    decode_responses=True
                )
                logger.info("✅ LLM缓存使用Redis")
            except ImportError:
                logger.warning("Redis未安装，使用PostgreSQL作为缓存")
                self.use_redis = False

        if not self.use_redis:
            logger.info("✅ LLM缓存使用PostgreSQL")

    def _generate_key(self, messages: List[Dict], model: str, **kwargs) -> str:
        """
        生成缓存键

        Args:
            messages: 消息列表
            model: 模型名称
            **kwargs: 其他参数

        Returns:
            缓存键
        """
        # 将输入序列化为字符串
        cache_input = {
            "messages": messages,
            "model": model,
            "provider": kwargs.get("provider") or settings.DEFAULT_LLM_PROVIDER,
            "temperature": kwargs.get("temperature", 0.7),
            "max_tokens": kwargs.get("max_tokens", 2000),
        }

        # 生成哈希
        input_str = json.dumps(cache_input, sort_keys=True, ensure_ascii=False)
        return f"llm_cache:{hashlib.md5(input_str.encode()).hexdigest()}"

    async def get(self, messages: List[Dict], model: str, **kwargs) -> Optional[str]:
        """
        获取缓存

        Args:
            messages: 消息列表
            model: 模型名称
            **kwargs: 其他参数

        Returns:
            缓存的响应，如果不存在返回 None
        """
        cache_key = self._generate_key(messages, model, **kwargs)

        if self.use_redis and self._redis_client:
            try:
                cached = await self._redis_client.get(cache_key)
                if cached:
                    logger.debug(f"✅ LLM缓存命中: {cache_key[:16]}...")
                    return cached
            except Exception as e:
                logger.warning(f"Redis缓存读取失败: {e}")

        # PostgreSQL 缓存
        try:
            pool = get_postgres_pool()
            async with pool.acquire() as conn:
                result = await conn.fetchrow(
                    "SELECT response FROM llm_cache WHERE cache_key = $1 AND expires_at > NOW()",
                    cache_key
                )
                if result:
                    logger.debug(f"✅ LLM缓存命中(PostgreSQL): {cache_key[:16]}...")
                    return result["response"]
        except Exception as e:
            logger.warning(f"PostgreSQL缓存读取失败: {e}")

        return None

    async def set(
        self,
        messages: List[Dict],
        model: str,
        response: str,
        **kwargs
    ):
        """
        设置缓存

        Args:
            messages: 消息列表
            model: 模型名称
            response: LLM 响应
            **kwargs: 其他参数
        """
        cache_key = self._generate_key(messages, model, **kwargs)

        if self.use_redis and self._redis_client:
            try:
                await self._redis_client.setex(cache_key, self.ttl, response)
                logger.debug(f"💾 LLM缓存已存: {cache_key[:16]}...")
                return
            except Exception as e:
                logger.warning(f"Redis缓存写入失败: {e}")

        # PostgreSQL 缓存
        try:
            pool = get_postgres_pool()
            async with pool.acquire() as conn:
                await conn.execute(
                    """INSERT INTO llm_cache (cache_key, response, expires_at)
                       VALUES ($1, $2, NOW() + INTERVAL '1 day')
                       ON CONFLICT (cache_key) DO UPDATE
                       SET response = $2, expires_at = NOW() + INTERVAL '1 day'""",
                    cache_key, response
                )
                logger.debug(f"💾 LLM缓存已存: {cache_key[:16]}...")
        except Exception as e:
            logger.warning(f"PostgreSQL缓存写入失败: {e}")

    async def invalidate(self, pattern: str = None):
        """
        清空缓存

        Args:
            pattern: 缓存键模式，None表示清空所有
        """
        if self.use_redis and self._redis_client:
            try:
                if pattern:
                    keys = await self._redis_client.keys(f"llm_cache:{pattern}*")
                    if keys:
                        await self._redis_client.delete(*keys)
                else:
                    # 清空所有 LLM 缓存
                    keys = await self._redis_client.keys("llm_cache:*")
                    if keys:
                        await self._redis_client.delete(*keys)
                logger.info(f"🗑️ 缓存已清空: {pattern or 'all'}")
            except Exception as e:
                logger.warning(f"Redis缓存清空失败: {e}")


# 全局实例
_llm_cache: Optional[LLMCache] = None


def get_llm_cache() -> LLMCache:
    """获取 LLM 缓存单例"""
    global _llm_cache
    if _llm_cache is None:
        _llm_cache = LLMCache()
    return _llm_cache


def cached_llm(func):
    """
    LLM 调用缓存装饰器

    用法：
    @cached_llm
    async def chat(self, messages, model="gpt-4"):
        ...
    """
    @wraps(func)
    async def wrapper(self, messages: List[Dict], model: str = None, **kwargs):
        if kwargs.get("use_cache") is False:
            return await func(self, messages, model=model, **kwargs)

        resolved_model = _resolve_model_name(self, model, kwargs)

        # 尝试从缓存获取
        cache = get_llm_cache()
        cached_result = await cache.get(messages, resolved_model, **kwargs)
        if cached_result is not None:
            return cached_result

        # 调用原函数
        result = await func(self, messages, model=model, **kwargs)

        # 存入缓存
        await cache.set(messages, resolved_model, result, **kwargs)

        return result

    return wrapper


# ==================== 初始化缓存表 ====================

async def init_cache_table():
    """初始化缓存表"""
    pool = get_postgres_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS llm_cache (
                cache_key VARCHAR(64) PRIMARY KEY,
                response TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT NOW(),
                expires_at TIMESTAMP NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_llm_cache_expires ON llm_cache(expires_at);

            -- 清理过期缓存
            DELETE FROM llm_cache WHERE expires_at < NOW();
        """)
        logger.info("✅ LLM缓存表已初始化")
