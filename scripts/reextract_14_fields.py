#!/usr/bin/env python3
"""用更新后的抽取器，重抽14个补全商品的7字段"""
import sys, os, json, asyncio
sys.path.insert(0, ".")
from pathlib import Path
from app.database.postgres import init_postgres_pool, close_postgres_pool, execute_query
from scripts.seven_field_extractor import extract_seven_fields

TARGET_PIDS = [53, 100, 70, 60, 93, 92, 81, 108, 109, 110, 118, 107, 121, 143]
AGG_PATH = Path(".tmp_user_download_audit/aggregated_ocr_html_texts_20260708.json")

async def main():
    await init_postgres_pool()
    agg = json.loads(AGG_PATH.read_text(encoding="utf-8")) if AGG_PATH.exists() else {}
    FIELDS = ["qa_facts","mechanism_notes","usage_steps","safety_notes","texture_notes","claim_notes","user_review_notes"]
    LABEL = {"qa_facts":"QA","mechanism_notes":"机制","usage_steps":"用法","safety_notes":"注意","texture_notes":"质地","claim_notes":"宣称","user_review_notes":"评价"}
    for pid in TARGET_PIDS:
        text = agg.get(str(pid), {}).get("text", "")
        if not text:
            print(f"PID{pid}: 无聚合文本，跳过")
            continue
        fields = extract_seven_fields(text)
        filled = sum(1 for v in fields.values() if v)
        print(f"PID{pid} {filled}/7 " + " ".join(f"{LABEL[k]}:{len(fields[k])}" for k in FIELDS))
        rows = await execute_query("SELECT specifications, name, brand FROM products WHERE id=$1", pid, fetch="all")
        if not rows:
            print(f"  ⚠️ PID{pid} 不存在"); continue
        specs = rows[0]["specifications"] or {}
        if isinstance(specs, str):
            specs = json.loads(specs)
        if not isinstance(specs, dict): specs = {}
        old_sk = specs.get("skincare_info") or {}
        if not isinstance(old_sk, dict): old_sk = {}
        # 只覆盖有新数据的字段
        merged = dict(old_sk)
        for k, v in fields.items():
            if v:
                merged[k] = v
            elif k not in merged:
                merged[k] = []
        specs["skincare_info"] = merged
        await execute_query(
            "UPDATE products SET specifications=$1::jsonb, updated_at=NOW() WHERE id=$2",
            json.dumps(specs, ensure_ascii=False), pid
        )
        # 特别打印前3条usage_steps
        if fields["usage_steps"]:
            for u in fields["usage_steps"][:3]:
                print(f"    用法: {u[:100]}")
    await close_postgres_pool()

if __name__ == "__main__":
    asyncio.run(main())
