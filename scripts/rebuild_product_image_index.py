"""
重建Milvus真实商品图片向量索引

流程：
1. drop旧的product_image_vectors collection（清理占位图假向量）
2. 从PostgreSQL读取所有100个商品的id/name/brand/category/price/image_url
3. 用CLIP (ViT-B/32) 编码真实商品图
4. 批量写入Milvus
"""

import asyncio
import os
import sys
import logging
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("rebuild_index")

BATCH_SIZE = 10


async def main():
    # ---------------- 1. 初始化依赖 ----------------
    from app.database.postgres import init_postgres_pool, get_postgres_pool, close_postgres_pool
    from app.database.milvus import get_milvus_manager
    from app.services.image_embedding import get_image_embedding_service

    await init_postgres_pool()
    milvus = get_milvus_manager()
    embed = get_image_embedding_service()
    COLLECTION = "product_image_vectors"
    DIM = embed.dimension
    logger.info(f"CLIP dimension={DIM}")

    # ---------------- 2. Drop 旧collection ----------------
    if milvus.collection_exists(COLLECTION):
        logger.info(f"dropping old collection: {COLLECTION}")
        milvus.drop_collection(COLLECTION)
        await asyncio.sleep(0.5)

    milvus.create_collection(
        collection_name=COLLECTION,
        dimension=DIM,
        id_type="int",
        vector_field_name="vector",
        auto_id=True,
    )
    logger.info(f"created fresh collection: {COLLECTION}")

    # ---------------- 3. 读商品列表 ----------------
    pool = get_postgres_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, name, brand, category, price, image_url FROM products ORDER BY id"
        )
    logger.info(f"loaded {len(rows)} products from postgres")

    # ---------------- 4. 逐批编码并写入 ----------------
    success = 0
    skipped = 0
    failed = 0

    for batch_start in range(0, len(rows), BATCH_SIZE):
        batch = rows[batch_start:batch_start + BATCH_SIZE]
        vectors = []
        metadatas = []
        for r in batch:
            image_url = r["image_url"] or ""
            local_path = ("app" + image_url) if image_url.startswith("/") else os.path.join(
                "app/static/images/products/", image_url
            )
            if not os.path.exists(local_path):
                logger.warning(f"skip (file missing): pid={r['id']} path={local_path}")
                skipped += 1
                continue

            try:
                with open(local_path, "rb") as f:
                    img_bytes = f.read()
                vec = await embed.encode_image(img_bytes)
                if not vec or len(vec) != DIM:
                    logger.warning(f"skip (bad vector): pid={r['id']}")
                    skipped += 1
                    continue
                vectors.append(vec)
                metadatas.append({
                    "product_id": int(r["id"]),
                    "name": r["name"] or "",
                    "brand": r["brand"] or "",
                    "category": r["category"] or "",
                    "price": float(r["price"] or 0),
                })
                success += 1
            except Exception as e:
                logger.error(f"encode failed: pid={r['id']} err={e}")
                failed += 1

        if vectors:
            milvus.insert_vectors(
                collection_name=COLLECTION,
                vectors=vectors,
                metadata=metadatas,
            )
            pct = min(100, int((batch_start + len(batch)) / len(rows) * 100))
            logger.info(f"  inserted batch {batch_start}-{batch_start+len(batch)-1} "
                        f"({pct}% done, success={success} failed={failed} skipped={skipped})")

    # 确保load进内存供搜索（用原生Collection确保flush/load）
    try:
        from pymilvus import Collection, connections, utility
        if not connections.has_connection("default"):
            connections.connect("default", host="localhost", port="19530")
        col = Collection(COLLECTION)
        col.flush()
        col.load()
        logger.info(f"flushed & loaded collection. num_entities={col.num_entities}")
    except Exception as e:
        logger.warning(f"load warning: {e}")

    # ---------------- 5. 快速自测：找一张真图搜自己 ----------------
    logger.info("--- sanity check: search first product against itself ---")
    if rows:
        r0 = rows[0]
        lp = "app" + r0["image_url"]
        with open(lp, "rb") as f:
            test_bytes = f.read()
        qv = await embed.encode_image(test_bytes)
        results = milvus.search_vectors(
            collection_name=COLLECTION,
            query_vector=qv,
            limit=5,
            output_fields=["product_id", "name", "brand", "category", "price"],
        )
        for hit in results:
            dist = hit.get("distance", 0)
            ent = hit.get("entity", {})
            logger.info(
                f"  hit pid={ent.get('product_id')} brand={ent.get('brand')} "
                f"name={(ent.get('name') or '')[:20]} dist={dist:.4f} sim={dist*100:.2f}%"
            )

    await close_postgres_pool()
    logger.info(f"DONE. success={success} failed={failed} skipped={skipped}")


if __name__ == "__main__":
    asyncio.run(main())
