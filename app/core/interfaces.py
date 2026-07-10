"""
服务接口抽象

定义所有服务的接口契约
使用 Protocol 实现鸭子类型，支持依赖注入
"""

from typing import (
    Protocol,
    List,
    Dict,
    Any,
    Optional,
    AsyncGenerator,
    runtime_checkable
)
from abc import ABC, abstractmethod


# ==================== LLM服务接口 ====================

@runtime_checkable
class ILLMService(Protocol):
    """LLM服务接口"""

    async def chat(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2000,
        provider: str = "kimi",
        use_cache: bool = True
    ) -> str:
        """非流式对话"""
        ...

    async def chat_stream(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2000,
        provider: str = "kimi"
    ) -> AsyncGenerator[str, None]:
        """流式对话"""
        ...


# ==================== 意图识别服务接口 ====================

class IntentResult:
    """意图识别结果"""
    intent: str
    confidence: float
    entities: Dict[str, Any]


@runtime_checkable
class IIntentService(Protocol):
    """意图识别服务接口"""

    async def recognize(
        self,
        message: str,
        use_llm: bool = True
    ) -> IntentResult:
        """识别用户意图"""
        ...


# ==================== RAG检索服务接口 ====================

@runtime_checkable
class IRAGRetriever(Protocol):
    """RAG检索服务接口"""

    async def retrieve(
        self,
        query: str,
        filters: Optional[Dict[str, Any]] = None,
        top_k: int = 10,
        search_products: bool = True,
        search_knowledge: bool = True
    ) -> Dict[str, Any]:
        """多路召回检索"""
        ...

    async def rerank(
        self,
        results: Dict[str, Any],
        query: str,
        user_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """重排序"""
        ...

    async def rag_query(
        self,
        query: str,
        top_k: int = 5
    ) -> Dict[str, Any]:
        """RAG增强查询"""
        ...


# ==================== 向量化服务接口 ====================

@runtime_checkable
class IEmbeddingService(Protocol):
    """文本向量化服务接口"""

    async def embed_text(self, text: str) -> List[float]:
        """文本向量化"""
        ...

    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """批量文本向量化"""
        ...


@runtime_checkable
class IImageEmbeddingService(Protocol):
    """图片向量化服务接口"""

    async def encode_image(
        self,
        image: Any,
        normalize: bool = True
    ) -> List[float]:
        """图片向量化"""
        ...

    async def encode_batch(
        self,
        images: List[Any],
        normalize: bool = True
    ) -> List[List[float]]:
        """批量图片向量化"""
        ...


# ==================== 向量数据库服务接口 ====================

@runtime_checkable
class IVectorDatabase(Protocol):
    """向量数据库服务接口"""

    def search_vectors(
        self,
        collection_name: str,
        query_vector: List[float],
        limit: int = 10,
        output_fields: Optional[List[str]] = None,
        filter_expression: Optional[str] = None
    ) -> List[Dict]:
        """向量搜索"""
        ...

    def insert_vectors(
        self,
        collection_name: str,
        vectors: List[List[float]],
        metadata: Optional[List[Dict]] = None
    ) -> List[int]:
        """插入向量"""
        ...

    def collection_exists(self, collection_name: str) -> bool:
        """检查集合是否存在"""
        ...

    def create_collection(
        self,
        collection_name: str,
        dimension: int,
        **kwargs
    ) -> bool:
        """创建集合"""
        ...


# ==================== 对比服务接口 ====================

@runtime_checkable
class IComparisonService(Protocol):
    """商品对比服务接口"""

    async def compare_products(
        self,
        product_ids: List[int]
    ) -> Dict[str, Any]:
        """对比商品"""
        ...


# ==================== 推荐服务接口 ====================

@runtime_checkable
class IRecommendationService(Protocol):
    """推荐服务接口"""

    async def get_recommendation(
        self,
        requirements: Dict[str, Any],
        category: Optional[str] = None,
        budget: Optional[float] = None,
        top_k: int = 5
    ) -> Dict[str, Any]:
        """获取推荐"""
        ...


# ==================== 会话服务接口 ====================

@runtime_checkable
class IConversationService(Protocol):
    """会话管理服务接口"""

    async def get_context(self, session_id: str) -> Any:
        """获取会话上下文"""
        ...

    async def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        intent: Optional[str] = None
    ) -> None:
        """添加消息"""
        ...

    async def update_profile(
        self,
        session_id: str,
        intent_result: Any
    ) -> None:
        """更新用户画像"""
        ...

    async def save_context(self, context: Any) -> None:
        """保存上下文"""
        ...


# ==================== 存储服务接口 ====================

@runtime_checkable
class IStorageService(Protocol):
    """对象存储服务接口"""

    async def upload_file(
        self,
        file_data: bytes,
        filename: str,
        content_type: str
    ) -> str:
        """上传文件，返回URL"""
        ...

    async def get_file(self, filename: str) -> bytes:
        """获取文件内容"""
        ...

    async def delete_file(self, filename: str) -> bool:
        """删除文件"""
        ...


# ==================== OCR服务接口 ====================

@runtime_checkable
class IOCRService(Protocol):
    """OCR识别服务接口"""

    async def recognize_image(self, image_data: bytes) -> Any:
        """识别图片中的文字"""
        ...


# ==================== 缓存服务接口 ====================

@runtime_checkable
class ICacheService(Protocol):
    """缓存服务接口"""

    async def get(self, key: str) -> Optional[Any]:
        """获取缓存"""
        ...

    async def set(
        self,
        key: str,
        value: Any,
        ttl: Optional[int] = None
    ) -> bool:
        """设置缓存"""
        ...

    async def delete(self, key: str) -> bool:
        """删除缓存"""
        ...

    async def exists(self, key: str) -> bool:
        """检查键是否存在"""
        ...


# ==================== 抽象基类（可选使用） ====================

class BaseLLMService(ABC):
    """LLM服务抽象基类"""

    @abstractmethod
    async def chat(self, messages: List[Dict], **kwargs) -> str:
        """对话"""
        pass

    @abstractmethod
    async def chat_stream(self, messages: List[Dict], **kwargs) -> AsyncGenerator:
        """流式对话"""
        pass


class BaseEmbeddingService(ABC):
    """向量化服务抽象基类"""

    @abstractmethod
    async def embed_text(self, text: str) -> List[float]:
        """文本向量化"""
        pass

    @abstractmethod
    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """批量向量化"""
        pass


# ==================== 使用示例 ====================

"""
# 方式1：使用Protocol（推荐）
from app.interfaces import ILLMService, IEmbeddingService

class MyAgent:
    def __init__(
        self,
        llm_service: ILLMService,  # 任何符合Protocol的类都可以
        embedding_service: IEmbeddingService
    ):
        self._llm = llm_service
        self._embedding = embedding_service

# 方式2：使用抽象基类
from app.interfaces import BaseLLMService

class MyLLMService(BaseLLMService):
    async def chat(self, messages, **kwargs) -> str:
        # 实现细节
        pass

    async def chat_stream(self, messages, **kwargs):
        # 实现细节
        pass

# 方式3：运行时检查
from app.interfaces import ILLMService

service = get_some_llm_service()
if isinstance(service, ILLMService):
    # 可以安全使用
    result = await service.chat(messages)
"""
