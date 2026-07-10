"""
Embedding服务模块

封装外部Embedding API，并在配置不完整或服务不可用时使用本地稳定向量。
用于RAG检索、相似度计算等。
"""

from typing import List, Tuple
from openai import AsyncOpenAI
import logging
import numpy as np
import hashlib
import re

from app.config import settings

logger = logging.getLogger(__name__)


class EmbeddingService:
    """
    Embedding服务类

    优先使用配置的外部Embedding模型；无有效配置时使用本地确定性向量，
    避免演示环境反复打印外部模型降级日志。
    """

    def __init__(self):
        """初始化Embedding服务"""
        self.provider = settings.EMBEDDING_PROVIDER.lower()
        self.model = settings.DOUBAO_EMBEDDING_MODEL
        self.dimension = settings.EMBEDDING_DIMENSION
        self.client = None
        self._remote_disabled_reason = ""
        self._warned_remote_failure = False
        self._siliconflow_client = None
        self._siliconflow_disabled = False

        if self._should_use_doubao():
            self.client = AsyncOpenAI(
                api_key=settings.DOUBAO_API_KEY,
                base_url=settings.DOUBAO_API_BASE
            )
            logger.info(f"✅ Embedding服务初始化成功 provider=doubao model={self.model}")
        else:
            logger.info(
                "✅ Embedding服务初始化成功 provider=local "
                f"dimension={self.dimension} reason={self._remote_disabled_reason or 'configured'}"
            )

    async def encode_semantic_batch(self, texts: List[str], model: str) -> List[List[float]]:
        """用 SiliconFlow bge 做真语义编码（OpenAI 兼容）。

        仅供意图语义层使用。任何失败(无key/网络/接口)都返回空列表，让调用方静默降级，
        绝不抛错、绝不阻塞主链路。不走豆包/本地哈希那套。
        """
        if not texts:
            return []
        if self._siliconflow_disabled:
            return []
        api_key = getattr(settings, "SILICONFLOW_API_KEY", "") or ""
        base_url = getattr(settings, "SILICONFLOW_API_BASE", "") or ""
        if not api_key or not base_url:
            self._siliconflow_disabled = True
            return []
        try:
            if self._siliconflow_client is None:
                self._siliconflow_client = AsyncOpenAI(api_key=api_key, base_url=base_url)
            response = await self._siliconflow_client.embeddings.create(model=model, input=texts)
            vectors = [item.embedding for item in response.data]
            if len(vectors) != len(texts):
                return []
            return [self._normalize(v) for v in vectors]
        except Exception as exc:
            self._siliconflow_disabled = True
            logger.warning("SiliconFlow 语义编码不可用，意图语义层将跳过: %s", exc)
            return []


    def _should_use_doubao(self) -> bool:
        """判断是否调用远端豆包Embedding。"""
        if self.provider == "local":
            self._remote_disabled_reason = "EMBEDDING_PROVIDER=local"
            return False

        if self.provider not in {"auto", "doubao"}:
            self._remote_disabled_reason = f"unknown provider {self.provider}"
            return False

        if not settings.DOUBAO_API_KEY:
            self._remote_disabled_reason = "missing DOUBAO_API_KEY"
            return False

        if not self.model or "xxxxx" in self.model or not self.model.startswith("ep-"):
            self._remote_disabled_reason = "invalid DOUBAO_EMBEDDING_MODEL"
            if self.provider == "doubao":
                logger.warning("强制使用doubao embedding，但endpoint看起来无效，将先回退本地向量")
            return False

        return True

    async def encode(
        self,
        text: str,
        normalize: bool = True
    ) -> List[float]:
        """
        将单个文本编码为向量

        Args:
            text: 输入文本
            normalize: 是否归一化（L2范数）

        Returns:
            向量列表

        Example:
            service = EmbeddingService()
            vector = await service.encode("你好世界")
        """
        if not self.client:
            return self._fallback_encode(text, normalize=normalize)

        try:
            response = await self.client.embeddings.create(
                model=self.model,
                input=text
            )

            vector = response.data[0].embedding

            # L2归一化（用于余弦相似度计算）
            if normalize:
                vector = self._normalize(vector)

            logger.debug(f"✅ 文本编码成功: {text[:20]}...")

            return vector

        except Exception as e:
            self._disable_remote_after_failure(e)
            return self._fallback_encode(text, normalize=normalize)

    async def encode_batch(
        self,
        texts: List[str],
        normalize: bool = True
    ) -> List[List[float]]:
        """
        批量将文本编码为向量

        Args:
            texts: 输入文本列表
            normalize: 是否归一化

        Returns:
            向量列表

        Example:
            service = EmbeddingService()
            vectors = await service.encode_batch(["文本1", "文本2", "文本3"])
        """
        if not texts:
            return []

        if not self.client:
            return [self._fallback_encode(text, normalize=normalize) for text in texts]

        try:
            # 豆包API支持批量请求
            response = await self.client.embeddings.create(
                model=self.model,
                input=texts
            )

            vectors = [item.embedding for item in response.data]

            # 批量归一化
            if normalize:
                vectors = [self._normalize(v) for v in vectors]

            logger.info(f"✅ 批量编码成功: {len(texts)}条文本")

            return vectors

        except Exception as e:
            self._disable_remote_after_failure(e)
            return [self._fallback_encode(text, normalize=normalize) for text in texts]

    def _disable_remote_after_failure(self, error: Exception):
        """远端失败后本进程内熔断，避免每次检索都重复打印降级噪音。"""
        self.client = None
        self._remote_disabled_reason = str(error)
        if not self._warned_remote_failure:
            logger.warning(f"Embedding远端不可用，本进程后续使用本地稳定向量: {error}")
            self._warned_remote_failure = True

    def _normalize(self, vector: List[float]) -> List[float]:
        """
        L2归一化

        Args:
            vector: 输入向量

        Returns:
            归一化后的向量
        """
        arr = np.array(vector)
        norm = np.linalg.norm(arr)
        if norm == 0:
            return vector
        return (arr / norm).tolist()

    def _fallback_encode(
        self,
        text: str,
        normalize: bool = True
    ) -> List[float]:
        """
        API 不可用时的本地兜底向量。

        目标不是替代真实 embedding，而是在 demo/离线场景下保持
        检索、排序、相似度链路可运行。
        """
        tokens = self._tokenize(text)
        vector = np.zeros(self.dimension, dtype=float)

        for token in tokens:
            digest = hashlib.md5(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimension
            sign = 1 if digest[4] % 2 == 0 else -1
            vector[index] += sign

        if not tokens:
            seed = int(hashlib.md5((text or "").encode("utf-8")).hexdigest()[:8], 16)
            rng = np.random.default_rng(seed)
            vector = rng.normal(0, 0.01, self.dimension)

        vector_list = vector.tolist()
        return self._normalize(vector_list) if normalize else vector_list

    def _tokenize(self, text: str) -> List[str]:
        """轻量本地分词：英文按词，中文按单字和二元组，保证检索排序稳定。"""
        normalized = (text or "").lower()
        ascii_tokens = re.findall(r"[a-z0-9]+", normalized)
        cjk_chars = re.findall(r"[\u4e00-\u9fff]", normalized)
        cjk_bigrams = [
            "".join(cjk_chars[i:i + 2])
            for i in range(len(cjk_chars) - 1)
        ]
        return ascii_tokens + cjk_chars + cjk_bigrams

    async def compute_similarity(
        self,
        text1: str,
        text2: str
    ) -> float:
        """
        计算两个文本的相似度（余弦相似度）

        Args:
            text1: 文本1
            text2: 文本2

        Returns:
            相似度分数（0-1之间）
        """
        vec1 = await self.encode(text1, normalize=True)
        vec2 = await self.encode(text2, normalize=True)

        # 余弦相似度 = 点积（因为已归一化）
        similarity = sum(a * b for a, b in zip(vec1, vec2))

        return similarity

    async def find_most_similar(
        self,
        query: str,
        candidates: List[str],
        top_k: int = 5
    ) -> List[Tuple[str, float]]:
        """
        从候选文本中找到最相似的

        Args:
            query: 查询文本
            candidates: 候选文本列表
            top_k: 返回前K个

        Returns:
            [(文本, 相似度分数), ...]
        """
        query_vec = await self.encode(query, normalize=True)
        candidate_vecs = await self.encode_batch(candidates, normalize=True)

        # 计算相似度
        similarities = []
        for i, cand_vec in enumerate(candidate_vecs):
            sim = sum(a * b for a, b in zip(query_vec, cand_vec))
            similarities.append((candidates[i], sim))

        # 排序并返回top_k
        similarities.sort(key=lambda x: x[1], reverse=True)

        return similarities[:top_k]


# ==================== 全局实例 ====================

_embedding_service: EmbeddingService = None


def get_embedding_service() -> EmbeddingService:
    """
    获取Embedding服务单例

    Returns:
        EmbeddingService实例
    """
    global _embedding_service
    if _embedding_service is None:
        _embedding_service = EmbeddingService()
    return _embedding_service


# ==================== 使用示例 ====================

"""
# 单文本编码
from app.services.embedding import get_embedding_service

service = get_embedding_service()
vector = await service.encode("我想买一双运动鞋")
print(f"向量维度: {len(vector)}")

# 批量编码
texts = ["苹果手机", "华为手机", "香蕉"]
vectors = await service.encode_batch(texts)

# 计算相似度
similarity = await service.compute_similarity("运动鞋", "球鞋")
print(f"相似度: {similarity}")

# 找最相似的
candidates = ["iPhone 15", "耐克运动鞋", "索尼耳机", "阿迪达斯球鞋"]
results = await service.find_most_similar("运动鞋", candidates, top_k=2)
for text, score in results:
    print(f"{text}: {score:.4f}")
"""
