"""
图片预处理服务

处理用户上传的图片：
- 压缩（减少大小）
- 格式转换（统一为WebP/JPEG）
- 尺寸调整（限制最大尺寸）
- 质量优化
"""

from typing import Tuple, Optional, Dict, Any
from PIL import Image
import io
import logging
import base64

logger = logging.getLogger(__name__)


class ImagePreprocessor:
    """图片预处理器"""

    def __init__(
        self,
        max_size: Tuple[int, int] = (1920, 1920),
        max_file_size: int = 2 * 1024 * 1024,  # 2MB
        quality: int = 85,
        output_format: str = "WEBP"
    ):
        """
        初始化预处理器

        Args:
            max_size: 最大尺寸 (width, height)
            max_file_size: 最大文件大小（字节）
            quality: JPEG/WebP质量 (1-100)
            output_format: 输出格式 (WEBP, JPEG, PNG)
        """
        self.max_size = max_size
        self.max_file_size = max_file_size
        self.quality = quality
        self.output_format = output_format

    async def process(
        self,
        image_data: bytes,
        maintain_aspect: bool = True
    ) -> Dict[str, Any]:
        """
        处理图片

        Args:
            image_data: 图片二进制数据
            maintain_aspect: 是否保持宽高比

        Returns:
            处理结果
        """
        try:
            # 打开图片
            img = Image.open(io.BytesIO(image_data))

            # 转换为RGB（如果是RGBA）
            if img.mode in ('RGBA', 'LA', 'P'):
                background = Image.new('RGB', img.size, (255, 255, 255))
                if img.mode == 'P':
                    img = img.convert('RGBA')
                background.paste(img, mask=img.split()[-1] if img.mode in ('RGBA', 'LA') else None)
                img = background

            original_size = img.size
            original_format = img.format

            # 调整尺寸
            img = self._resize(img, maintain_aspect)

            # 压缩并转换格式
            output_data = self._compress(img)

            result = {
                "success": True,
                "data": output_data,
                "format": self.output_format,
                "size": len(output_data),
                "dimensions": img.size,
                "original_size": original_size,
                "original_format": original_format,
                "compression_ratio": round(len(output_data) / len(image_data), 2)
            }

            logger.info(
                f"图片处理完成: {original_format} → {self.output_format}, "
                f"{original_size} → {img.size}, "
                f"压缩率: {result['compression_ratio']}"
            )

            return result

        except Exception as e:
            logger.error(f"图片处理失败: {e}")
            return {
                "success": False,
                "error": str(e),
                "data": image_data  # 返回原始数据
            }

    def _resize(self, img: Image.Image, maintain_aspect: bool) -> Image.Image:
        """调整图片尺寸"""
        width, height = img.size
        max_width, max_height = self.max_size

        # 如果图片尺寸小于最大值，不需要调整
        if width <= max_width and height <= max_height:
            return img

        if maintain_aspect:
            # 保持宽高比
            img.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
        else:
            # 强制调整
            img = img.resize((max_width, max_height), Image.Resampling.LANCZOS)

        return img

    def _compress(self, img: Image.Image) -> bytes:
        """压缩图片并转换格式"""
        output = io.BytesIO()

        if self.output_format == "WEBP":
            img.save(output, format="WEBP", quality=self.quality, method=6)
        elif self.output_format == "JPEG":
            img.save(output, format="JPEG", quality=self.quality, optimize=True)
        else:  # PNG
            img.save(output, format="PNG", optimize=True)

        return output.getvalue()

    async def process_from_base64(
        self,
        base64_data: str,
        maintain_aspect: bool = True
    ) -> Dict[str, Any]:
        """
        处理Base64编码的图片

        Args:
            base64_data: Base64编码的图片数据
            maintain_aspect: 是否保持宽高比

        Returns:
            处理结果
        """
        # 移除data URL前缀
        if ',' in base64_data:
            base64_data = base64_data.split(',')[1]

        # 解码
        image_data = base64.b64decode(base64_data)

        # 处理
        result = await self.process(image_data, maintain_aspect)

        # 重新编码为Base64
        if result["success"]:
            result["base64"] = base64.b64encode(result["data"]).decode('utf-8')
            del result["data"]  # 移除二进制数据

        return result

    def get_image_info(self, image_data: bytes) -> Dict[str, Any]:
        """
        获取图片信息（不处理）

        Args:
            image_data: 图片二进制数据

        Returns:
            图片信息
        """
        try:
            img = Image.open(io.BytesIO(image_data))

            return {
                "format": img.format,
                "mode": img.mode,
                "size": img.size,
                "width": img.width,
                "height": img.height,
                "file_size": len(image_data),
                "has_transparency": img.mode in ('RGBA', 'LA', 'P')
            }
        except Exception as e:
            return {
                "error": str(e)
            }


# 全局实例
_preprocessor: Optional[ImagePreprocessor] = None


def get_image_preprocessor() -> ImagePreprocessor:
    """获取图片预处理器单例"""
    global _preprocessor
    if _preprocessor is None:
        _preprocessor = ImagePreprocessor()
    return _preprocessor


async def preprocess_image(
    image_data: bytes,
    max_size: Tuple[int, int] = (1920, 1920),
    quality: int = 85
) -> bytes:
    """
    便捷函数：预处理图片

    Args:
        image_data: 图片二进制数据
        max_size: 最大尺寸
        quality: 压缩质量

    Returns:
        处理后的图片数据
    """
    preprocessor = ImagePreprocessor(max_size=max_size, quality=quality)
    result = await preprocessor.process(image_data)

    if result["success"]:
        return result["data"]
    else:
        return image_data  # 失败时返回原始数据
