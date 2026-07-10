"""
LLM服务模块

封装豆包、Kimi等大语言模型的API调用
支持流式和非流式输出
支持缓存以降低API成本
"""

from typing import List, Dict, Any, Optional, AsyncGenerator
from openai import AsyncOpenAI, OpenAI
import logging
import httpx

from app.config import settings
from app.services.llm_cache import get_llm_cache, cached_llm
from app.services.cost_monitor import get_cost_monitor, LLMProvider

logger = logging.getLogger(__name__)


def _graceful_fallback_reply(messages: List[Dict[str, str]]) -> str:
    """
    当外部 LLM 服务不可用时，返回可展示的兜底文案。
    """
    user_message = ""
    for message in reversed(messages):
        if message.get("role") == "user":
            user_message = message.get("content", "")
            break

    if any(keyword in user_message for keyword in ["对比", "哪个好", "选哪个", "区别"]):
        return (
            "我这边暂时没连上模型服务，但可以先按导购逻辑帮你判断："
            "优先看预算、核心用途、品牌偏好和关键参数差异。"
            "你把想对比的商品名称发我，我会继续基于商品库给你做结构化对比。"
        )

    if any(keyword in user_message for keyword in ["推荐", "预算", "想买", "送礼", "礼物"]):
        return (
            "模型服务刚刚有点不稳定，不过系统主链路还在。"
            "你可以继续告诉我预算、使用场景、偏好品牌和在意点，"
            "我会先基于当前商品库给你整理一版推荐。"
        )

    return (
        "我刚刚没有稳定连上模型服务，但导购系统还在正常运行。"
        "你可以继续补充预算、用途、品牌偏好或上传商品图片，我会继续帮你分析。"
    )


class LLMService:
    """
    LLM服务类

    封装多个LLM提供商的API调用
    主用：Kimi（临时 - 豆包充值后切换回豆包）
    备用：豆包（字节挑战赛要求）
    """

    def __init__(self):
        """初始化LLM服务"""
        # 豆包客户端（备用 - 字节挑战赛）
        self.doubao_client = AsyncOpenAI(
            api_key=settings.DOUBAO_API_KEY,
            base_url=settings.DOUBAO_API_BASE
        )

        # Kimi客户端（备用 - 开发调试）
        self.kimi_client = AsyncOpenAI(
            api_key=settings.KIMI_API_KEY,
            base_url=settings.KIMI_API_BASE
        )

        # SiliconFlow客户端（OpenAI兼容，当前主用 - DeepSeek）
        self.siliconflow_client = AsyncOpenAI(
            api_key=settings.SILICONFLOW_API_KEY,
            base_url=settings.SILICONFLOW_API_BASE
        )

        # 同步客户端（用于非异步场景）
        self.doubao_sync = OpenAI(
            api_key=settings.DOUBAO_API_KEY,
            base_url=settings.DOUBAO_API_BASE
        )
        self.kimi_sync = OpenAI(
            api_key=settings.KIMI_API_KEY,
            base_url=settings.KIMI_API_BASE
        )
        self.siliconflow_sync = OpenAI(
            api_key=settings.SILICONFLOW_API_KEY,
            base_url=settings.SILICONFLOW_API_BASE
        )

        logger.info(
            f"✅ LLM服务初始化成功（默认provider={settings.DEFAULT_LLM_PROVIDER}, "
            f"model={self._default_model_name(settings.DEFAULT_LLM_PROVIDER)}）"
        )

    def _select_client_and_model(self, provider: Optional[str], model: Optional[str] = None):
        """根据 provider 返回 (async_client, sync_client, model_name)。未知 provider 回退到默认。"""
        p = (provider or settings.DEFAULT_LLM_PROVIDER).lower()
        if p == "doubao":
            return self.doubao_client, self.doubao_sync, model or settings.DOUBAO_CHAT_MODEL
        if p == "kimi":
            return self.kimi_client, self.kimi_sync, model or settings.KIMI_CHAT_MODEL
        if p == "siliconflow":
            return self.siliconflow_client, self.siliconflow_sync, model or settings.SILICONFLOW_CHAT_MODEL
        logger.warning("未知LLM provider=%s，回退到默认provider=%s", p, settings.DEFAULT_LLM_PROVIDER)
        return self._select_client_and_model(settings.DEFAULT_LLM_PROVIDER, model)

    @staticmethod
    def _default_model_name(provider: str) -> str:
        p = (provider or "").lower()
        if p == "doubao":
            return settings.DOUBAO_CHAT_MODEL
        if p == "kimi":
            return settings.KIMI_CHAT_MODEL
        return settings.SILICONFLOW_CHAT_MODEL

    # ==================== 非流式对话 ====================

    @cached_llm  # 自动缓存装饰器
    async def chat(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2000,
        provider: Optional[str] = None,
        use_cache: bool = True  # 是否使用缓存
    ) -> str:
        """
        非流式对话（一次性返回完整回复）

        Args:
            messages: 对话消息列表
                [{"role": "user", "content": "你好"}, ...]
            model: 模型名称（不指定则使用默认）
            temperature: 温度参数（0-1，越高越随机）
            max_tokens: 最大生成token数
            provider: 提供商（doubao/kimi）
            use_cache: 是否使用缓存

        Returns:
            AI回复文本

        Example:
            service = LLMService()
            response = await service.chat([
                {"role": "user", "content": "你好"}
            ])
        """
        # 如果不使用缓存，直接调用（装饰器会被跳过）
        if not use_cache:
            return await self._chat_with_fallback(
                messages, model, temperature, max_tokens, provider or settings.DEFAULT_LLM_PROVIDER
            )

        # 使用缓存调用（装饰器自动处理）
        return await self._chat_with_fallback(
            messages, model, temperature, max_tokens, provider or settings.DEFAULT_LLM_PROVIDER
        )

    async def _chat_with_fallback(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2000,
        provider: Optional[str] = None,
    ) -> str:
        primary = provider or settings.DEFAULT_LLM_PROVIDER
        providers = []
        for candidate in [primary, "kimi", "doubao"]:
            if candidate and candidate not in providers:
                providers.append(candidate)

        last_error = None
        for candidate in providers:
            try:
                return await self._chat_without_cache(
                    messages, model, temperature, max_tokens, candidate
                )
            except Exception as exc:
                last_error = exc
                logger.warning("LLM provider=%s 不可用，准备尝试下一个: %s", candidate, exc)

        logger.error("❌ 所有LLM provider均失败: %s", last_error)
        return _graceful_fallback_reply(messages)

    async def _chat_without_cache(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2000,
        provider: Optional[str] = None
    ) -> str:
        """实际执行LLM调用的方法（无缓存）"""
        try:
            provider = (provider or settings.DEFAULT_LLM_PROVIDER).lower()
            client, _, model_name = self._select_client_and_model(provider, model)

            response = await client.chat.completions.create(
                model=model_name,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens
            )

            if not response or not response.choices:
                raise ValueError(f"LLM({provider}) returned empty response (no choices)")

            content = response.choices[0].message.content
            if not content or not content.strip():
                raise ValueError(f"LLM({provider}) returned empty content")

            monitor = get_cost_monitor()
            usage = getattr(response, "usage", None)
            input_tokens = getattr(usage, "prompt_tokens", 0) if usage else 0
            output_tokens = getattr(usage, "completion_tokens", 0) if usage else 0
            total_tokens = getattr(usage, "total_tokens", input_tokens + output_tokens) if usage else 0
            try:
                monitor.record_usage(
                    model=model_name,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    provider=LLMProvider(provider),
                    cached=False
                )
            except ValueError:
                monitor.record_usage(
                    model=model_name,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    provider=LLMProvider.SILICONFLOW,
                    cached=False
                )

            logger.info(f"✅ LLM调用成功 (provider={provider}, model={model_name}, tokens={total_tokens})")
            return content

        except ValueError:
            raise
        except Exception as e:
            logger.error(f"❌ LLM({provider})调用失败: {e}")
            raise

    # ==================== 流式对话 ====================

    async def chat_stream(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2000,
        provider: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        """
        流式对话（逐字返回回复）

        Args:
            messages: 对话消息列表
            model: 模型名称
            temperature: 温度参数
            max_tokens: 最大生成token数
            provider: 提供商（默认走 settings.DEFAULT_LLM_PROVIDER）

        Yields:
            每次生成的一个文本片段
        """
        primary = (provider or settings.DEFAULT_LLM_PROVIDER).lower()
        providers = []
        for candidate in [primary, "siliconflow", "kimi", "doubao"]:
            if candidate and candidate not in providers:
                providers.append(candidate)

        last_error = None
        for candidate in providers:
            try:
                client, _, model_name = self._select_client_and_model(candidate, model)
                stream = await client.chat.completions.create(
                    model=model_name,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    stream=True,
                )
                async for chunk in stream:
                    try:
                        if hasattr(chunk, "choices") and chunk.choices:
                            choice = chunk.choices[0]
                            if hasattr(choice, "delta"):
                                delta = choice.delta
                                if hasattr(delta, "content") and delta.content:
                                    yield delta.content
                    except (IndexError, AttributeError, KeyError) as e:
                        logger.warning("流式响应块处理失败(provider=%s): %s，跳过此块", candidate, e)
                        continue
                return
            except Exception as e:
                last_error = e
                logger.warning("流式LLM provider=%s 不可用，尝试下一个: %s", candidate, e)

        logger.error("❌ 所有流式LLM provider均失败: %s", last_error)
        raise (last_error if last_error else RuntimeError("All streaming LLM providers failed"))

    # ==================== 同步版本（用于非异步场景）====================

    def chat_sync(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2000,
        provider: Optional[str] = None,
    ) -> str:
        """
        同步版本的对话（用于非异步场景/脚本）

        Args:
            messages: 对话消息列表
            model: 模型名称
            temperature: 温度参数
            max_tokens: 最大生成token数
            provider: 提供商（默认走 settings.DEFAULT_LLM_PROVIDER）

        Returns:
            AI回复文本
        """
        p = (provider or settings.DEFAULT_LLM_PROVIDER).lower()
        _, sync_client, model_name = self._select_client_and_model(p, model)

        try:
            response = sync_client.chat.completions.create(
                model=model_name,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            if not response or not response.choices:
                raise ValueError(f"LLM({p}) sync returned empty response")
            content = response.choices[0].message.content
            if not content or not content.strip():
                raise ValueError(f"LLM({p}) sync returned empty content")
            return content
        except Exception as e:
            logger.error(f"❌ 同步LLM({p})调用失败: {e}")
            raise e


# ==================== 全局实例 ====================

_llm_service: Optional[LLMService] = None


def get_llm_service() -> LLMService:
    """
    获取LLM服务单例

    Returns:
        LLMService实例
    """
    global _llm_service
    if _llm_service is None:
        _llm_service = LLMService()
    return _llm_service


# ==================== 使用示例 ====================

"""
# 异步场景（FastAPI路由中）
from app.services.llm import get_llm_service

@router.post("/chat")
async def chat_endpoint(message: str):
    service = get_llm_service()

    # 非流式
    response = await service.chat([
        {"role": "user", "content": message}
    ])

    return {"response": response}

# 流式
from fastapi.responses import StreamingResponse

@router.post("/chat/stream")
async def chat_stream_endpoint(message: str):
    service = get_llm_service()

    async def generate():
        async for chunk in service.chat_stream([
            {"role": "user", "content": message}
        ]):
            yield f"data: {chunk}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


# 同步场景（脚本中）
from app.services.llm import get_llm_service

service = get_llm_service()
response = service.chat_sync([
    {"role": "user", "content": "什么是RAG？"}
])
print(response)
"""
