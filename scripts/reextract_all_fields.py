#!/usr/bin/env python3
"""用最新抽取器重抽全库103个商品的7字段（非空覆盖，空字段保留旧值）"""
import sys, json, asyncio
sys.path.insert(0, ".")
from pathlib import Path
from app.database.postgres import init_postgres_pool, close_postgres_pool, execute_query
from scripts.seven_field_extractor import extract_seven_fields

AGG_PATH = Path(".tmp_user_download_audit/aggregated_ocr_html_texts_20260708.json")
FIELDS = ["qa_facts","mechanism_notes","usage_steps","safety_notes","texture_notes","claim_notes","user_review_notes"]
LABEL = {"qa_facts":"QA","mechanism_notes":"机制","usage_steps":"用法","safety_notes":"注意","texture_notes":"质地","claim_notes":"宣称","user_review_notes":"评价"}

async def main():
    await init_postgres_pool()
    agg = json.loads(AGG_PATH.read_text(encoding="utf-8")) if AGG_PATH.exists() else {}
    all_pids = await execute_query("SELECT id, name, brand, specifications FROM products ORDER BY id", fetch="all")
    print(f"全库商品数: {len(all_pids)}")
    total_before = total_after = 0
    cleaned_usage = 0
    for row in all_pids:
        pid = row["id"]
        specs = row["specifications"] or {}
        if isinstance(specs, str):
            try: specs = json.loads(specs)
            except: specs = {}
        if not isinstance(specs, dict): specs = {}
        old_sk = specs.get("skincare_info") or {}
        if not isinstance(old_sk, dict): old_sk = {}
        before_filled = sum(1 for k in FIELDS if old_sk.get(k))
        text = agg.get(str(pid), {}).get("text", "")
        if not text:
            continue
        new_fields = extract_seven_fields(text)
        merged = dict(old_sk)
        for k, v in new_fields.items():
            if v:
                # 检查用法段是否从"脏"变"干净"或新增
                if k == "usage_steps":
                    old_us = old_sk.get("usage_steps") or []
                    if old_us and len(old_us) > len(v):
                        cleaned_usage += 1
                merged[k] = v
            elif k not in merged:
                merged[k] = []
        specs["skincare_info"] = merged
        await execute_query("UPDATE products SET specifications=$1::jsonb, updated_at=NOW() WHERE id=$2",
                            json.dumps(specs, ensure_ascii=False), pid)
        after_filled = sum(1 for k in FIELDS if merged.get(k))
        total_before += before_filled
        total_after += after_filled
        if before_filled != after_filled or any(len(new_fields.get(k,[])) != len(old_sk.get(k) or []) for k in FIELDS if new_fields.get(k)):
            print(f"PID{pid} {row['brand']} {row['name'][:22]:<24} {before_filled}→{after_filled}/7 " +
                  " ".join(f"{LABEL[k]}:{len(merged[k])}" for k in FIELDS if merged.get(k)))
    print(f"\n汇总: 字段覆盖率 {total_before}→{total_after} (共{len(all_pids)*7}字段), 清理脏用法段商品数={cleaned_usage}")
    await close_postgres_pool()

if __name__ == "__main__":
    asyncio.run(main())
