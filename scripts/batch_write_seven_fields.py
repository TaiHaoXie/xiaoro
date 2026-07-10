#!/usr/bin/env python3
"""
批量抽取7字段，并写入products表的 specifications.skincare_info JSONB字段
"""
import asyncio
import sys
import os
import json
from pathlib import Path
from copy import deepcopy

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database.postgres import init_postgres_pool, execute_query, close_postgres_pool, get_postgres_connection

PROJECT_ROOT = Path(__file__).parent.parent
AUDIT_DIR = PROJECT_ROOT / ".tmp_user_download_audit"
AGG_PATH = AUDIT_DIR / "aggregated_ocr_html_texts_20260708.json"

sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
from seven_field_extractor import extract_seven_fields

SEVEN_FIELDS = ["qa_facts","mechanism_notes","usage_steps","safety_notes","texture_notes","claim_notes","user_review_notes"]


async def main():
    await init_postgres_pool()
    try:
        agg = json.loads(AGG_PATH.read_text(encoding="utf-8"))
        products = await execute_query("SELECT id, name, brand, specifications FROM products ORDER BY id")
        print(f"DB 商品数: {len(products)}, 聚合文本数: {len(agg)}")

        # 先备份表（轻量备份：将当前specifications写入备份表）
        async with get_postgres_connection() as conn:
            await conn.execute("CREATE TABLE IF NOT EXISTS products_backup_before_seven_fields_20260708 AS SELECT * FROM products")
            print("已创建备份表 products_backup_before_seven_fields_20260708")

        stats = {k: 0 for k in SEVEN_FIELDS}
        updated = 0
        empty = 0

        for p in products:
            pid = p["id"]
            spec = p.get("specifications")
            if not isinstance(spec, dict):
                spec = {}
            new_spec = deepcopy(spec)
            skincare = new_spec.get("skincare_info")
            if not isinstance(skincare, dict):
                skincare = {}
            # 清空旧的7字段（如果有）
            for f in SEVEN_FIELDS:
                skincare.pop(f, None)

            info = agg.get(str(pid))
            if info and info.get("text"):
                fields = extract_seven_fields(info["text"])
                for f in SEVEN_FIELDS:
                    v = fields.get(f, [])
                    if v:
                        skincare[f] = v
                        stats[f] += 1
            else:
                empty += 1

            new_spec["skincare_info"] = skincare
            # 写库
            await execute_query(
                "UPDATE products SET specifications = $1::jsonb, updated_at = NOW() WHERE id = $2",
                json.dumps(new_spec, ensure_ascii=False),
                pid,
                fetch="none",
            )
            updated += 1

        print(f"\n=== 更新完成 ===")
        print(f"更新商品: {updated}")
        print(f"无文本(空7字段): {empty}")
        for k, v in stats.items():
            print(f"  {k}: {v}个商品有数据")

    finally:
        await close_postgres_pool()


if __name__ == "__main__":
    asyncio.run(main())
