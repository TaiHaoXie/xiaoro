"""
批量建立商品图片索引

为数据库中的商品生成图片向量并存储到Milvus
"""

import asyncio
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional
from PIL import Image, ImageDraw, ImageFont
import io
import random

from app.database.postgres import execute_query
from app.services.image_vector import get_image_vector_service
from app.services.storage import get_minio_service

logger = logging.getLogger(__name__)


class BatchImageIndexer:
    """批量图片索引器"""

    def __init__(self):
        """初始化"""
        self.vector_service = get_image_vector_service()
        self.storage_service = get_minio_service()

    async def get_all_products(self) -> List[Dict]:
        """获取所有商品"""
        products = await execute_query(
            "SELECT * FROM products ORDER BY id",
            fetch="all"
        )
        logger.info(f"获取到 {len(products)} 个商品")
        return products

    async def get_product_image(self, product: Dict) -> Optional[bytes]:
        """
        获取商品图片

        优先级：
        1. 从MinIO/存储下载
        2. 从image_url下载
        3. 生成占位图片
        """
        product_id = product["id"]

        # 1. 尝试从存储下载
        try:
            image_data = await self.storage_service.download_product_image(product_id)
            if image_data:
                logger.debug(f"从存储获取图片: {product['name']}")
                return image_data
        except Exception as e:
            logger.debug(f"存储下载失败: {e}")

        # 2. 尝试从image_url下载
        if product.get("image_url"):
            try:
                image_data = await self._download_from_url(product["image_url"])
                if image_data:
                    logger.debug(f"从URL下载图片: {product['name']}")
                    return image_data
            except Exception as e:
                logger.debug(f"URL下载失败: {e}")

        # 3. 生成占位图片
        logger.debug(f"生成占位图片: {product['name']}")
        return self._generate_placeholder_image(product)

    async def _download_from_url(self, url: str) -> Optional[bytes]:
        """从URL下载图片"""
        import httpx

        # 处理相对URL
        if url.startswith("/"):
            # 尝试本地文件
            local_path = Path(__file__).parent.parent.parent / "static" / url.lstrip("/")
            if local_path.exists():
                return local_path.read_bytes()
            return None

        # 下载远程图片
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(url)
                if response.status_code == 200:
                    return response.content
        except Exception as e:
            logger.debug(f"下载失败 {url}: {e}")

        return None

    def _generate_placeholder_image(self, product: Dict) -> bytes:
        """
        生成占位图片

        根据商品属性生成不同颜色/图案的图片
        """
        # 图片尺寸
        width, height = 224, 224

        # 根据品牌/类别生成颜色
        brand = product.get("brand", "")
        category = product.get("category", "")

        # 生成随机但确定的颜色（基于商品ID）
        random.seed(product["id"])
        hue = random.randint(0, 360)
        saturation = random.randint(20, 40)
        lightness = random.randint(75, 90)

        # HSL转RGB
        import colorsys
        rgb = colorsys.hls_to_rgb(hue/360, lightness/100, saturation/100)
        bg_color = tuple(int(c * 255) for c in rgb)

        # 创建图片
        img = Image.new('RGB', (width, height), bg_color)
        draw = ImageDraw.Draw(img)

        # 绘制品牌首字母
        brand_initial = brand[0] if brand else "P"
        text_color = (100, 100, 100)

        # 尝试使用系统字体
        try:
            font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 80)
        except:
            font = ImageFont.load_default()

        # 计算文字位置（居中）
        bbox = draw.textbbox((0, 0), brand_initial, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        x = (width - text_width) // 2
        y = (height - text_height) // 2 - 20

        draw.text((x, y), brand_initial, fill=text_color, font=font)

        # 绘制商品名称
        name = product.get("name", "")
        if len(name) > 8:
            name = name[:8] + ".."

        try:
            font_small = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 16)
        except:
            font_small = ImageFont.load_default()

        bbox = draw.textbbox((0, 0), name, font=font_small)
        text_width = bbox[2] - bbox[0]
        x = (width - text_width) // 2
        y = height - 40

        draw.text((x, y), name, fill=text_color, font=font_small)

        # 绘制价格
        price = str(int(product.get("price", 0)))
        try:
            font_price = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 20)
        except:
            font_price = ImageFont.load_default()

        price_text = f"¥{price}"
        bbox = draw.textbbox((0, 0), price_text, font=font_price)
        text_width = bbox[2] - bbox[0]
        x = (width - text_width) // 2
        y = y - 30

        draw.text((x, y), price_text, fill=(180, 140, 150), font=font_price)

        # 转为bytes
        output = io.BytesIO()
        img.save(output, format='JPEG', quality=85)
        return output.getvalue()

    async def index_single_product(self, product: Dict) -> Dict[str, Any]:
        """为单个商品建立索引"""
        product_id = product["id"]
        product_name = product.get("name", "Unknown")

        try:
            # 检查是否已索引
            is_indexed = await self.vector_service.is_product_indexed(product_id)
            if is_indexed:
                return {
                    "product_id": product_id,
                    "name": product_name,
                    "status": "skipped",
                    "reason": "已存在索引"
                }

            # 获取图片
            image_data = await self.get_product_image(product)
            if not image_data:
                return {
                    "product_id": product_id,
                    "name": product_name,
                    "status": "failed",
                    "reason": "无法获取图片"
                }

            # 建立索引
            success = await self.vector_service.index_product_image(
                product_id=product_id,
                image_data=image_data,
                metadata={
                    "category": product.get("category"),
                    "brand": product.get("brand"),
                    "price": float(product.get("price", 0))
                }
            )

            if success:
                return {
                    "product_id": product_id,
                    "name": product_name,
                    "status": "success"
                }
            else:
                return {
                    "product_id": product_id,
                    "name": product_name,
                    "status": "failed",
                    "reason": "索引失败"
                }

        except Exception as e:
            logger.error(f"索引失败 {product_name}: {e}")
            return {
                "product_id": product_id,
                "name": product_name,
                "status": "failed",
                "reason": str(e)
            }

    async def batch_index(
        self,
        limit: Optional[int] = None,
        overwrite: bool = False
    ) -> Dict[str, Any]:
        """
        批量索引商品

        Args:
            limit: 限制数量（None表示全部）
            overwrite: 是否覆盖已有索引

        Returns:
            索引结果统计
        """
        logger.info("开始批量商品图片索引...")

        # 获取商品列表
        products = await self.get_all_products()
        if limit:
            products = products[:limit]

        results = {
            "total": len(products),
            "success": 0,
            "failed": 0,
            "skipped": 0,
            "details": []
        }

        # 逐个处理
        for i, product in enumerate(products, 1):
            logger.info(f"处理进度: {i}/{len(products)}")

            result = await self.index_single_product(product)
            results["details"].append(result)

            # 更新统计
            status = result["status"]
            if status == "success":
                results["success"] += 1
            elif status == "failed":
                results["failed"] += 1
            elif status == "skipped":
                results["skipped"] += 1

        logger.info(f"批量索引完成: 成功={results['success']}, 失败={results['failed']}, 跳过={results['skipped']}")

        return results


# ==================== 命令行执行 ====================

async def main():
    """主函数"""
    import sys

    # 日志配置
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # 解析参数
    limit = None
    overwrite = False

    if len(sys.argv) > 1:
        try:
            limit = int(sys.argv[1])
        except ValueError:
            pass

    if len(sys.argv) > 2 and sys.argv[2] == "--overwrite":
        overwrite = True

    # 执行索引
    indexer = BatchImageIndexer()
    results = await indexer.batch_index(limit=limit, overwrite=overwrite)

    # 打印结果
    print("\n" + "="*50)
    print("批量索引结果")
    print("="*50)
    print(f"总计: {results['total']}")
    print(f"成功: {results['success']}")
    print(f"失败: {results['failed']}")
    print(f"跳过: {results['skipped']}")
    print("="*50)

    # 显示失败项
    if results["failed"] > 0:
        print("\n失败列表:")
        for detail in results["details"]:
            if detail["status"] == "failed":
                print(f"  - {detail['name']}: {detail.get('reason', 'Unknown')}")


if __name__ == "__main__":
    asyncio.run(main())
