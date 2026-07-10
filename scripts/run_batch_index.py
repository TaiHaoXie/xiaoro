#!/usr/bin/env python3
"""
批量建立商品图片索引 - 独立脚本

运行方式：
    python scripts/run_batch_index.py [limit] [--overwrite]

示例：
    python scripts/run_batch_index.py 50          # 索引前50个商品
    python scripts/run_batch_index.py --overwrite # 全部重建索引
"""

import asyncio
import sys
import os
import logging
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

# 设置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def init_services():
    """初始化服务"""
    try:
        # 初始化PostgreSQL
        from app.database.postgres import init_postgres_pool
        await init_postgres_pool()
        logger.info("✅ PostgreSQL已连接")

        # 初始化Milvus（自动）
        from app.database.milvus import get_milvus_manager
        milvus = get_milvus_manager()
        logger.info(f"✅ Milvus已连接")

        # 初始化图片向量服务（自动创建Collection）
        from app.services.image_vector import get_image_vector_service
        vector_service = get_image_vector_service()
        logger.info(f"✅ 图片向量服务已初始化")

        return True

    except Exception as e:
        logger.error(f"❌ 服务初始化失败: {e}")
        import traceback
        traceback.print_exc()
        return False


async def get_product_count():
    """获取商品数量"""
    from app.database.postgres import execute_query
    result = await execute_query(
        "SELECT COUNT(*) as count FROM products",
        fetch="one"
    )
    return result["count"]


async def batch_index_products(limit=None, overwrite=False):
    """批量索引商品图片"""
    from app.database.postgres import execute_query
    from app.services.image_vector import get_image_vector_service
    from PIL import Image, ImageDraw, ImageFont
    import io
    import colorsys
    import random

    vector_service = get_image_vector_service()

    # 获取商品列表
    sql = "SELECT * FROM products ORDER BY id"
    if limit:
        sql += f" LIMIT {limit}"

    products = await execute_query(sql, fetch="all")

    if not products:
        logger.warning("没有找到商品")
        return

    logger.info(f"开始批量索引 {len(products)} 个商品...")

    results = {
        "total": len(products),
        "success": 0,
        "failed": 0,
        "skipped": 0,
        "details": []
    }

    for i, product in enumerate(products, 1):
        product_id = product["id"]
        product_name = product.get("name", "Unknown")

        try:
            logger.info(f"[{i}/{len(products)}] 处理: {product_name}")

            # 检查是否已索引
            if not overwrite:
                is_indexed = await vector_service.is_product_indexed(product_id)
                if is_indexed:
                    logger.info(f"  ⊙ 已存在索引，跳过")
                    results["skipped"] += 1
                    results["details"].append({
                        "product_id": product_id,
                        "name": product_name,
                        "status": "skipped"
                    })
                    continue

            # 生成占位图片
            random.seed(product_id)
            hue = random.randint(0, 360)
            saturation = random.randint(20, 40)
            lightness = random.randint(75, 90)

            rgb = colorsys.hls_to_rgb(hue/360, lightness/100, saturation/100)
            bg_color = tuple(int(c * 255) for c in rgb)

            img = Image.new('RGB', (224, 224), bg_color)
            draw = ImageDraw.Draw(img)

            # 绘制品牌首字母
            brand = product.get("brand", "")
            brand_initial = brand[0] if brand else "P"

            try:
                font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 80)
            except:
                try:
                    font = ImageFont.truetype("arial.ttf", 80)
                except:
                    font = ImageFont.load_default()

            bbox = draw.textbbox((0, 0), brand_initial, font=font)
            text_width = bbox[2] - bbox[0]
            text_height = bbox[3] - bbox[1]
            x = (224 - text_width) // 2
            y = (224 - text_height) // 2 - 20

            draw.text((x, y), brand_initial, fill=(100, 100, 100), font=font)

            # 绘制商品名称
            name = product.get("name", "")
            if len(name) > 8:
                name = name[:8] + ".."

            try:
                font_small = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 16)
            except:
                try:
                    font_small = ImageFont.truetype("arial.ttf", 16)
                except:
                    font_small = ImageFont.load_default()

            bbox = draw.textbbox((0, 0), name, font=font_small)
            text_width = bbox[2] - bbox[0]
            x = (224 - text_width) // 2
            y = 180

            draw.text((x, y), name, fill=(100, 100, 100), font=font_small)

            # 绘制价格
            price = str(int(product.get("price", 0)))
            price_text = f"¥{price}"

            try:
                font_price = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 20)
            except:
                font_price = ImageFont.load_default()

            bbox = draw.textbbox((0, 0), price_text, font=font_price)
            text_width = bbox[2] - bbox[0]
            x = (224 - text_width) // 2
            y = 150

            draw.text((x, y), price_text, fill=(180, 140, 150), font=font_price)

            # 转为bytes
            output = io.BytesIO()
            img.save(output, format='JPEG', quality=85)
            image_data = output.getvalue()

            # 建立索引
            success = await vector_service.index_product_image(
                product_id=product_id,
                image_data=image_data,
                metadata={
                    "category": product.get("category"),
                    "brand": product.get("brand"),
                    "price": float(product.get("price", 0))
                }
            )

            if success:
                logger.info(f"  ✅ 索引成功")
                results["success"] += 1
                results["details"].append({
                    "product_id": product_id,
                    "name": product_name,
                    "status": "success"
                })
            else:
                logger.warning(f"  ❌ 索引失败")
                results["failed"] += 1
                results["details"].append({
                    "product_id": product_id,
                    "name": product_name,
                    "status": "failed"
                })

        except Exception as e:
            logger.error(f"  ❌ 错误: {e}")
            results["failed"] += 1
            results["details"].append({
                "product_id": product_id,
                "name": product_name,
                "status": "failed",
                "error": str(e)
            })

    return results


def print_results(results):
    """打印结果"""
    print("\n" + "=" * 60)
    print("批量索引结果")
    print("=" * 60)
    print(f"总计处理: {results['total']}")
    print(f"成功: {results['success']} ✅")
    print(f"失败: {results['failed']} ❌")
    print(f"跳过: {results['skipped']} ⊙")
    print("=" * 60)

    if results["failed"] > 0:
        print("\n失败列表:")
        for detail in results["details"]:
            if detail["status"] == "failed":
                print(f"  - [{detail['product_id']}] {detail['name']}: {detail.get('error', 'Unknown')}")


async def main():
    """主函数"""
    # 解析参数
    limit = None
    overwrite = False

    if len(sys.argv) > 1:
        try:
            if sys.argv[1] != "--overwrite":
                limit = int(sys.argv[1])
        except ValueError:
            pass

    if "--overwrite" in sys.argv:
        overwrite = True

    # 初始化服务
    logger.info("正在初始化服务...")
    if not await init_services():
        logger.error("服务初始化失败，退出")
        return

    # 显示商品数量
    count = await get_product_count()
    logger.info(f"数据库中共有 {count} 个商品")

    if limit:
        logger.info(f"将索引前 {limit} 个商品")
    else:
        logger.info(f"将索引全部 {count} 个商品")

    if overwrite:
        logger.warning("⚠️  覆盖模式已启用，将重建所有索引")

    # 确认
    print("\n是否继续? (y/n): ", end="")
    if input().lower() != 'y':
        print("已取消")
        return

    # 执行索引
    print()
    results = await batch_index_products(limit=limit, overwrite=overwrite)

    # 打印结果
    print_results(results)


if __name__ == "__main__":
    asyncio.run(main())
