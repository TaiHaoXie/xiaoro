"""语义向量意图层：用真 embedding 按语义命中意图样本。

定位：规则层、字符向量层之后，大模型兜底之前。绕话口语靠语义命中样本，
命中就不必调大模型；embedding 不可用时整层静默跳过，绝不抛错、绝不阻塞主链路。

encode_batch 可注入（默认走 SiliconFlow bge，OpenAI 兼容），便于离线测试。
"""

import logging
import math
from typing import Any, Awaitable, Callable, List, Optional

from .models import SemanticIntent
from .semantic_intent_retriever import IntentSample

logger = logging.getLogger(__name__)

EncodeBatch = Callable[[List[str]], Awaitable[List[List[float]]]]


class SemanticEmbeddingIntentMatcher:
    def __init__(
        self,
        samples: List[IntentSample],
        encode_batch: Optional[EncodeBatch] = None,
        min_score: float = 0.55,
    ):
        self.samples = list(samples or [])
        self._encode_batch = encode_batch
        self.min_score = min_score
        self._sample_vectors: Optional[List[List[float]]] = None
        self._ready = False
        self._disabled = False

    async def _ensure_sample_vectors(self) -> bool:
        """懒加载：首次使用时把样本编码并缓存。失败则整层禁用(返回False)。"""
        if self._ready:
            return True
        if self._disabled:
            return False
        if not self.samples or self._encode_batch is None:
            self._disabled = True
            return False
        try:
            texts = [s.text for s in self.samples]
            vectors = await self._encode_batch(texts)
            if not vectors or len(vectors) != len(self.samples):
                self._disabled = True
                return False
            self._sample_vectors = [self._normalize(v) for v in vectors]
            self._ready = True
            return True
        except Exception as exc:  # embedding 不可用 -> 静默降级
            logger.warning("语义意图层样本编码失败，本层跳过: %s", exc)
            self._disabled = True
            return False

    async def match(self, message: str, has_history: bool) -> Optional[SemanticIntent]:
        """规则/字符向量没拦住时调用。命中返回 SemanticIntent，否则/异常返回 None。"""
        text = (message or "").strip()
        if not text:
            return None
        try:
            if not await self._ensure_sample_vectors():
                return None
            query_vectors = await self._encode_batch([text])
            if not query_vectors:
                return None
            query_vec = self._normalize(query_vectors[0])
            if not any(query_vec):
                return None

            best_idx = -1
            best_score = 0.0
            for idx, sample in enumerate(self.samples):
                if sample.needs_history and not has_history:
                    continue
                score = self._cosine(query_vec, self._sample_vectors[idx])
                if score > best_score:
                    best_score = score
                    best_idx = idx

            if best_idx < 0 or best_score < self.min_score:
                return None

            sample = self.samples[best_idx]
            return SemanticIntent(
                answer_mode=sample.answer_mode,
                confidence=min(0.9, max(0.7, best_score)),
                reason=f"semantic_embedding:{sample.label}",
                followup_type=sample.followup_type,
                usage_time_focus=sample.usage_time_focus,
                needs_history=sample.needs_history,
                matched_example=sample.text,
                vector_score=round(best_score, 4),
            )
        except Exception as exc:  # 运行期任何异常都不得影响主链路
            logger.warning("语义意图层匹配异常，本次跳过: %s", exc)
            return None

    @staticmethod
    def _normalize(vector: List[float]) -> List[float]:
        norm = math.sqrt(sum(v * v for v in vector))
        if norm == 0:
            return list(vector)
        return [v / norm for v in vector]

    @staticmethod
    def _cosine(left: List[float], right: List[float]) -> float:
        n = min(len(left), len(right))
        return sum(left[i] * right[i] for i in range(n))
