#!/usr/bin/env python3
"""
审计商品库的 OCR/HTML 覆盖率，识别需要补OCR的缺口
"""
import asyncio
import sys
import os
import json
import re
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database.postgres import init_postgres_pool, execute_query, close_postgres_pool

PROJECT_ROOT = Path(__file__).parent.parent
AUDIT_DIR = PROJECT_ROOT / ".tmp_user_download_audit"
DETAIL_REVIEW_DIR = AUDIT_DIR / "detail_review"
FULL_MANUAL_DIR = AUDIT_DIR / "full_manual_review_batches"
NEW_LOTION_OCR_DIR = AUDIT_DIR / "new_lotion_html_ocr_20260707"
OCR_BATCH_DIRS = [AUDIT_DIR / "ocr_batch_1", AUDIT_DIR / "ocr_batch_2", AUDIT_DIR / "ocr_batch_3"]
DOWNLOADS_DIR = Path.home() / "Downloads"

SEVEN_FIELDS = ["qa_facts", "mechanism_notes", "usage_steps", "safety_notes", "texture_notes", "claim_notes", "user_review_notes"]


def collect_ocr_product_ids():
    """从历史OCR目录收集所有商品ID"""
    ocr_ids = set()
    ocr_sources = {}

    # detail_review: detail_{id}_ocr.json
    if DETAIL_REVIEW_DIR.exists():
        for f in DETAIL_REVIEW_DIR.glob("detail_*_ocr.json"):
            m = re.match(r"detail_(\d+)_ocr\.json", f.name)
            if m:
                pid = int(m.group(1))
                ocr_ids.add(pid)
                ocr_sources.setdefault(pid, []).append("detail_review")

    # new_lotion_html_ocr_20260707
    if NEW_LOTION_OCR_DIR.exists():
        for f in NEW_LOTION_OCR_DIR.glob("*ocr*.json"):
            # try to extract id from name
            nums = re.findall(r"(\d{3,})", f.name)
            for n in nums:
                pid = int(n)
                if pid > 50:
                    ocr_ids.add(pid)
                    ocr_sources.setdefault(pid, []).append(f"new_lotion:{f.name}")

    # full_manual_review_batches
    if FULL_MANUAL_DIR.exists():
        for sub in FULL_MANUAL_DIR.iterdir():
            if sub.is_dir() and "long_details" in sub.name:
                ocr_file = sub / "ocr_results.json"
                if ocr_file.exists():
                    try:
                        data = json.loads(ocr_file.read_text(encoding="utf-8", errors="ignore"))
                        if isinstance(data, dict):
                            for k in data.keys():
                                m = re.match(r"(\d+)", str(k))
                                if m:
                                    pid = int(m.group(1))
                                    ocr_ids.add(pid)
                                    ocr_sources.setdefault(pid, []).append(f"manual:{sub.name}")
                        elif isinstance(data, list):
                            for item in data:
                                if isinstance(item, dict):
                                    pid = item.get("product_id") or item.get("id")
                                    if pid:
                                        ocr_ids.add(int(pid))
                    except Exception as e:
                        pass
        nonlong = FULL_MANUAL_DIR / "nonlong_high_potential_ocr_results.json"
        if nonlong.exists():
            try:
                data = json.loads(nonlong.read_text(encoding="utf-8", errors="ignore"))
                if isinstance(data, dict):
                    for k in data.keys():
                        m = re.match(r"(\d+)", str(k))
                        if m:
                            pid = int(m.group(1))
                            ocr_ids.add(pid)
            except Exception:
                pass

    # ocr_batch_1/2/3
    for bdir in OCR_BATCH_DIRS:
        if bdir.exists():
            for f in bdir.glob("*ocr*.json"):
                nums = re.findall(r"(\d{3,})", f.name)
                for n in nums:
                    pid = int(n)
                    if pid > 50:
                        ocr_ids.add(pid)
                        ocr_sources.setdefault(pid, []).append(f"{bdir.name}:{f.name}")

    return ocr_ids, ocr_sources


def collect_html_files():
    """从Downloads收集用户下载的HTML文件，映射到商品名/ID（先粗收集，后通过DB反查）"""
    html_files = []
    for f in DOWNLOADS_DIR.glob("*.html"):
        html_files.append(str(f))
    for f in DOWNLOADS_DIR.glob("*.htm"):
        html_files.append(str(f))
    return html_files


def assess_ocr_quality(pid):
    """粗评OCR质量：看文件大小和文本长度"""
    candidates = list(DETAIL_REVIEW_DIR.glob(f"detail_{pid}_ocr.json"))
    if not candidates:
        return None
    f = candidates[0]
    try:
        data = json.loads(f.read_text(encoding="utf-8", errors="ignore"))
        texts = []
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    t = item.get("text") or item.get("content") or ""
                    texts.append(t)
                elif isinstance(item, str):
                    texts.append(item)
        elif isinstance(data, dict):
            for v in data.values():
                if isinstance(v, str):
                    texts.append(v)
                elif isinstance(v, list):
                    for item in v:
                        if isinstance(item, dict):
                            texts.append(item.get("text", ""))
        full = "\n".join(texts)
        return {"file": str(f), "length": len(full), "sample": full[:200]}
    except Exception as e:
        return {"file": str(f), "error": str(e)}


async def main():
    await init_postgres_pool()
    try:
        products = await execute_query("SELECT id, name, brand, platform, image_url, detail_url, specifications, description FROM products ORDER BY id")
        print(f"数据库商品总数: {len(products)}")

        ocr_ids, ocr_sources = collect_ocr_product_ids()
        html_files = collect_html_files()
        print(f"历史OCR覆盖商品ID数: {len(ocr_ids)}")
        print(f"Downloads HTML文件数: {len(html_files)}")

        coverage = []
        no_ocr = []
        weak_ocr = []
        has_existing_fields = []

        for p in products:
            pid = p["id"]
            spec = p.get("specifications") or {}
            skincare = {}
            if isinstance(spec, dict):
                skincare = spec.get("skincare_info") or {}
            existing_field_count = sum(1 for f in SEVEN_FIELDS if skincare.get(f))

            has_ocr = pid in ocr_ids
            ocr_q = assess_ocr_quality(pid) if has_ocr else None

            is_weak = False
            if has_ocr and ocr_q and ocr_q.get("length", 0) < 200:
                is_weak = True

            row = {
                "id": pid,
                "name": p["name"],
                "brand": p.get("brand"),
                "platform": p.get("platform"),
                "has_ocr": has_ocr,
                "ocr_sources": ocr_sources.get(pid, []),
                "ocr_length": ocr_q.get("length") if ocr_q else 0,
                "existing_seven_fields": existing_field_count,
                "need_new_ocr": (not has_ocr) or is_weak,
            }
            coverage.append(row)
            if not has_ocr:
                no_ocr.append(row)
            elif is_weak:
                weak_ocr.append(row)
            if existing_field_count > 0:
                has_existing_fields.append(row)

        print(f"\n=== 覆盖率统计 ===")
        print(f"有历史OCR的商品: {sum(1 for r in coverage if r['has_ocr'])}")
        print(f"无OCR的商品: {len(no_ocr)}")
        print(f"OCR太弱(<200字符): {len(weak_ocr)}")
        print(f"已有7字段填充(非空): {len(has_existing_fields)}")
        print(f"需要补OCR的商品总数: {len(no_ocr) + len(weak_ocr)}")

        print(f"\n=== 无OCR商品列表 ===")
        for r in no_ocr:
            print(f"  ID {r['id']:3d}  [{r['platform']}]  {r['brand']} - {r['name']}")

        print(f"\n=== OCR太弱商品列表 ===")
        for r in weak_ocr:
            print(f"  ID {r['id']:3d}  len={r['ocr_length']:4d}  {r['brand']} - {r['name']}")

        out = {
            "total_products": len(products),
            "ocr_covered": sum(1 for r in coverage if r["has_ocr"]),
            "no_ocr": no_ocr,
            "weak_ocr": weak_ocr,
            "need_ocr_count": len(no_ocr) + len(weak_ocr),
            "coverage_details": coverage,
            "html_files_in_downloads": html_files,
        }
        out_path = AUDIT_DIR / "seven_field_coverage_audit_20260708.json"
        out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n审计结果已写入: {out_path}")

    finally:
        await close_postgres_pool()


if __name__ == "__main__":
    asyncio.run(main())
