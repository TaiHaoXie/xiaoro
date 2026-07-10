"""
图片处理服务模块

使用 CLIP 模型进行图片向量化
支持以图搜图功能
"""

from typing import List, Union, Optional, BinaryIO, Tuple
from pathlib import Path
import io
import logging
import base64

import torch
import open_clip
from PIL import Image
import numpy as np

from app.config import settings

logger = logging.getLogger(__name__)


class ImageEmbeddingService:
    """
    图片向量化服务

    使用 CLIP 模型将图片转换为向量
    """

    # 可用的 CLIP 模型
    MODELS = {
        "ViT-B-32": "laion2b_s34b_b79k",      # 轻量级，快速
        "ViT-B-16": "laion2b_s34b_b88k",      # 平衡
        "ViT-L-14": "laion2b_s32b_b82k",      # 高精度
    }

    def __init__(
        self,
        model_name: str = "ViT-B-32",
        pretrained: str = None,
        device: str = None
    ):
        """
        初始化图片嵌入服务

        Args:
            model_name: 模型名称
            pretrained: 预训练权重（可选）
            device: 设备（cuda/mps/cpu，自动检测）
        """
        self.model_name = model_name
        self.pretrained = pretrained or self.MODELS[model_name]

        # 自动检测设备
        if device is None:
            if torch.cuda.is_available():
                self.device = "cuda"
            elif torch.backends.mps.is_available():
                self.device = "mps"
            else:
                self.device = "cpu"

        logger.info(f"🖼️  正在加载 CLIP 模型: {model_name}/{self.pretrained}")
        logger.info(f"📱 设备: {self.device}")

        # 加载模型
        self.model, _, self.preprocess = open_clip.create_model_and_transforms(
            model_name,
            pretrained=self.pretrained,
            device=self.device
        )

        # 获取向量维度
        self.dimension = self.model.visual.output_dim

        logger.info(f"✅ CLIP模型加载成功 (向量维度: {self.dimension})")

        # 模型预热（首次推理优化）
        self._warmup()

    def _warmup(self):
        """
        模型预热

        执行一次虚拟推理，确保：
        1. 模型完全加载到内存
        2. GPU内核初始化完成
        3. 首次真实请求响应更快
        """
        try:
            logger.info("🔥 CLIP模型预热中...")

            # 创建虚拟图片
            dummy_image = Image.new("RGB", (224, 224), color="white")
            image_input = self.preprocess(dummy_image).unsqueeze(0).to(self.device)

            # 虚拟推理
            with torch.no_grad():
                _ = self.model.encode_image(image_input)

            # 虚拟文本编码
            import open_clip
            tokenizer = open_clip.get_tokenizer(self.model_name)
            text_tokens = tokenizer(["warmup"]).to(self.device)
            with torch.no_grad():
                _ = self.model.encode_text(text_tokens)

            logger.info("✅ CLIP模型预热完成")

        except Exception as e:
            logger.warning(f"⚠️  CLIP模型预热失败（不影响使用）: {e}")

    async def encode_image(
        self,
        image: Union[str, Path, bytes, BinaryIO, Image.Image],
        normalize: bool = True
    ) -> List[float]:
        """
        将图片编码为向量

        Args:
            image: 图片（路径、字节、文件对象或PIL Image）
            normalize: 是否归一化

        Returns:
            图片向量

        Example:
            service = ImageEmbeddingService()
            vector = await service.encode_image("product.jpg")
        """
        try:
            # 加载图片
            if isinstance(image, (str, Path)):
                img = Image.open(image).convert("RGB")
            elif isinstance(image, bytes):
                img = Image.open(io.BytesIO(image)).convert("RGB")
            elif hasattr(image, "read"):  # 文件对象
                img = Image.open(image).convert("RGB")
            elif isinstance(image, Image.Image):
                img = image.convert("RGB")
            else:
                raise ValueError(f"不支持的图片类型: {type(image)}")

            # 预处理
            image_input = self.preprocess(img).unsqueeze(0).to(self.device)

            # 提取特征
            with torch.no_grad():
                image_features = self.model.encode_image(image_input)

            # 转换为列表
            vector = image_features.cpu().numpy()[0].tolist()

            # 归一化
            if normalize:
                vector = self._normalize(vector)

            logger.debug(f"✅ 图片编码成功: {vector[:3]}... (维度: {len(vector)})")

            return vector

        except Exception as e:
            logger.error(f"❌ 图片编码失败: {e}")
            raise e

    async def encode_batch(
        self,
        images: List[Union[str, Path, bytes, Image.Image]],
        normalize: bool = True
    ) -> List[List[float]]:
        """
        批量编码图片

        Args:
            images: 图片列表
            normalize: 是否归一化

        Returns:
            向量列表
        """
        vectors = []

        # 批量处理（提高效率）
        processed_images = []
        for img in images:
            if isinstance(img, (str, Path)):
                pil_img = Image.open(img).convert("RGB")
            elif isinstance(img, bytes):
                pil_img = Image.open(io.BytesIO(img)).convert("RGB")
            elif isinstance(img, Image.Image):
                pil_img = img.convert("RGB")
            else:
                logger.warning(f"跳过不支持的类型: {type(img)}")
                continue

            processed_images.append(self.preprocess(pil_img))

        if not processed_images:
            return []

        # 批量推理
        image_input = torch.stack(processed_images).to(self.device)

        with torch.no_grad():
            image_features = self.model.encode_image(image_input)

        # 转换为列表
        vectors = image_features.cpu().numpy().tolist()

        # 归一化
        if normalize:
            vectors = [self._normalize(v) for v in vectors]

        logger.info(f"✅ 批量编码成功: {len(vectors)}张图片")

        return vectors

    async def encode_text(
        self,
        text: str,
        normalize: bool = True
    ) -> List[float]:
        """
        将文本编码为向量（用于图文匹配）

        Args:
            text: 文本描述
            normalize: 是否归一化

        Returns:
            文本向量
        """
        try:
            import open_clip
            tokenizer = open_clip.get_tokenizer(self.model_name)

            text_tokens = tokenizer([text]).to(self.device)

            with torch.no_grad():
                text_features = self.model.encode_text(text_tokens)

            vector = text_features.cpu().numpy()[0].tolist()

            if normalize:
                vector = self._normalize(vector)

            return vector

        except Exception as e:
            logger.error(f"❌ 文本编码失败: {e}")
            raise e

    async def compute_image_text_similarity(
        self,
        image: Union[str, Path, bytes],
        text: str
    ) -> float:
        """
        计算图片和文本的相似度

        Args:
            image: 图片
            text: 文本描述

        Returns:
            相似度分数（0-1）
        """
        image_vector = await self.encode_image(image, normalize=True)
        text_vector = await self.encode_text(text, normalize=True)

        # 余弦相似度
        similarity = sum(a * b for a, b in zip(image_vector, text_vector))

        return similarity

    async def find_similar_images(
        self,
        query_image: Union[str, Path, bytes],
        candidate_images: List[Union[str, Path]],
        top_k: int = 5
    ) -> List[Tuple[str, float]]:
        """
        从候选图片中找到最相似的

        Args:
            query_image: 查询图片
            candidate_images: 候选图片列表
            top_k: 返回前K个

        Returns:
            [(图片路径, 相似度), ...]
        """
        query_vector = await self.encode_image(query_image, normalize=True)
        candidate_vectors = await self.encode_batch(candidate_images, normalize=True)

        # 计算相似度
        similarities = []
        for i, cand_vec in enumerate(candidate_vectors):
            sim = sum(a * b for a, b in zip(query_vector, cand_vec))
            similarities.append((str(candidate_images[i]), sim))

        # 排序
        similarities.sort(key=lambda x: x[1], reverse=True)

        return similarities[:top_k]

    def _normalize(self, vector: List[float]) -> List[float]:
        """L2归一化"""
        arr = np.array(vector)
        norm = np.linalg.norm(arr)
        if norm == 0:
            return vector
        return (arr / norm).tolist()

    def get_image_info(self, image: Union[str, Path, bytes]) -> dict:
        """
        获取图片信息

        Args:
            image: 图片

        Returns:
            图片信息字典
        """
        if isinstance(image, (str, Path)):
            img = Image.open(image)
        elif isinstance(image, bytes):
            img = Image.open(io.BytesIO(image))
        else:
            raise ValueError(f"不支持的类型: {type(image)}")

        return {
            "format": img.format,
            "mode": img.mode,
            "size": img.size,
            "width": img.width,
            "height": img.height,
        }


# ==================== 全局实例 ====================

_image_embedding_service: Optional[ImageEmbeddingService] = None


def get_image_embedding_service() -> ImageEmbeddingService:
    """
    获取图片嵌入服务单例

    Returns:
        ImageEmbeddingService实例
    """
    global _image_embedding_service
    if _image_embedding_service is None:
        _image_embedding_service = ImageEmbeddingService()
    return _image_embedding_service


# ==================== 使用示例 ====================

"""
from app.services.image_embedding import get_image_embedding_service

service = get_image_embedding_service()

# 单张图片编码
vector = await service.encode_image("product.jpg")
print(f"向量维度: {len(vector)}")

# 批量编码
vectors = await service.encode_batch(["img1.jpg", "img2.jpg"])

# 图文相似度
similarity = await service.compute_image_text_similarity(
    "shoe.jpg",
    "a red nike sneaker"
)
print(f"相似度: {similarity}")

# 找相似图片
results = await service.find_similar_images(
    "query.jpg",
    ["img1.jpg", "img2.jpg", "img3.jpg"],
    top_k=3
)
for path, score in results:
    print(f"{path}: {score:.4f}")
"""
