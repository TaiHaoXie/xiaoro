"""
美妆护肤商品 seed 导入脚本

把 data/beauty_products_seed.json 导入 PostgreSQL 的 products 表。
- 去重键：brand + name（已存在则更新缺失字段，不重复插入）
- 来源标签：写入 specifications.source_batch，便于日后与队友数据合并、区分批次
- 价格非数字（如预售/活动文案）时，price 置空但保留原始文本到 specifications.price_raw

用法：
    .venv/bin/python scripts/seed_beauty_products.py
    .venv/bin/python scripts/seed_beauty_products.py --reset   # 先清掉本批次旧数据再导
"""

import argparse
import asyncio
import json
import logging
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.database.postgres import (  # noqa: E402
    init_postgres_pool,
    close_postgres_pool,
    execute_query,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("seed_beauty_products")

SEED_PATH = PROJECT_ROOT / "data" / "beauty_products_seed.json"
PLATFORM = "天猫/淘宝"


def parse_price(raw: str):
    """从价格文本里取第一个数字；取不到返回 None。"""
    if not raw:
        return None
    m = re.search(r"\d+(?:\.\d+)?", str(raw).replace(",", ""))
    return float(m.group()) if m else None


def build_description(p: dict) -> str:
    parts = []
    if p.get("positioning"):
        parts.append(f"定位：{p['positioning']}")
    if p.get("target_users"):
        parts.append(f"适合人群：{p['target_users']}")
    if p.get("suitable_skin_types"):
        parts.append(f"适合肤质：{p['suitable_skin_types']}")
    if p.get("key_ingredients"):
        parts.append(f"核心成分：{p['key_ingredients']}")
    if p.get("concerns"):
        parts.append(f"主打功效：{p['concerns']}")
    if p.get("pitfalls"):
        parts.append(f"备注：{p['pitfalls']}")
    return "\n".join(parts)


def build_specs(p: dict, batch: str) -> dict:
    return {
        "subcategory": p.get("subcategory"),
        "price_band": p.get("price_band"),
        "price_updated_at": p.get("price_updated_at"),
        "price_raw": p.get("price"),
        "suitable_skin_types": p.get("suitable_skin_types"),
        "target_users": p.get("target_users"),
        "key_ingredients": p.get("key_ingredients"),
        "concerns": p.get("concerns"),
        "positioning": p.get("positioning"),
        "pitfalls": p.get("pitfalls"),
        "source_type": p.get("source_type"),
        "source_batch": batch,
    }


async def upsert_product(p: dict, batch: str) -> str:
    name = p["name"]
    brand = p.get("brand") or None
    category = p.get("category") or "护肤"
    price = parse_price(p.get("price"))
    description = build_description(p)
    specs = build_specs(p, batch)
    detail_url = p.get("detail_url") or None
    image_url = p.get("image_url") or None

    existing = await execute_query(
        "SELECT id FROM products WHERE name = $1 AND COALESCE(brand,'') = COALESCE($2,'')",
        name, brand, fetch="one",
    )

    if existing:
        await execute_query(
            """
            UPDATE products
               SET category = $1,
                   price = COALESCE($2, price),
                   description = $3,
                   specifications = $4,
                   detail_url = COALESCE($5, detail_url),
                   image_url = COALESCE($6, image_url),
                   platform = $7,
                   updated_at = NOW()
             WHERE id = $8
            """,
            category, price, description, json.dumps(specs, ensure_ascii=False),
            detail_url, image_url, PLATFORM, existing["id"], fetch="none",
        )
        return "updated"

    await execute_query(
        """
        INSERT INTO products
            (name, category, brand, price, description, specifications, detail_url, image_url, platform, stock, created_at, updated_at)
        VALUES
            ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, NOW(), NOW())
        """,
        name, category, brand, price, description,
        json.dumps(specs, ensure_ascii=False), detail_url, image_url, PLATFORM, 999,
        fetch="none",
    )
    return "inserted"


async def main(reset: bool) -> None:
    if not SEED_PATH.exists():
        logger.error("找不到 seed 文件：%s（先跑 scripts/build_beauty_seed.py）", SEED_PATH)
        return

    data = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    batch = data.get("source_batch", "feishu_seed")
    products = data.get("products", [])
    logger.info("准备导入 %d 条商品（批次 %s）", len(products), batch)

    await init_postgres_pool()
    try:
        if reset:
            deleted = await execute_query(
                "DELETE FROM products WHERE specifications->>'source_batch' = $1 RETURNING id",
                batch, fetch="all",
            )
            logger.info("已清理本批次旧数据 %d 条", len(deleted or []))

        inserted = updated = 0
        for p in products:
            if not p.get("name"):
                continue
            result = await upsert_product(p, batch)
            inserted += result == "inserted"
            updated += result == "updated"

        total = await execute_query("SELECT COUNT(*) AS c FROM products", fetch="one")
        logger.info("=" * 48)
        logger.info("导入完成：新增 %d 条，更新 %d 条", inserted, updated)
        logger.info("products 表当前总计：%s 条", total["c"])
    finally:
        await close_postgres_pool()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="导入美妆护肤商品 seed")
    parser.add_argument("--reset", action="store_true", help="先删除同批次旧数据再导入")
    args = parser.parse_args()
    asyncio.run(main(args.reset))
