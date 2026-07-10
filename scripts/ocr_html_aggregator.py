#!/usr/bin/env python3
"""
历史OCR与HTML聚合器 + 覆盖率审计

聚合来源：
- .tmp_user_download_audit/detail_review/detail_{id}_ocr.json  (主结构：{pid,name,images:[{file,ocr_text,...}]})
- .tmp_user_download_audit/full_manual_review_batches/*/ocr_results.json
- .tmp_user_download_audit/full_manual_review_batches/nonlong_high_potential_ocr_results.json
- .tmp_user_download_audit/ocr_batch_{1,2,3}/*ocr*.json
- .tmp_user_download_audit/new_lotion_html_ocr_20260707/*ocr*.json
- ~/Downloads/*.html （HTML正文，用于抽规格/评价/QA）
"""
import asyncio
import sys
import os
import json
import re
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database.postgres import init_postgres_pool, execute_query, close_postgres_pool

PROJECT_ROOT = Path(__file__).parent.parent
AUDIT_DIR = PROJECT_ROOT / ".tmp_user_download_audit"
DETAIL_REVIEW_DIR = AUDIT_DIR / "detail_review"
FULL_MANUAL_DIR = AUDIT_DIR / "full_manual_review_batches"
NEW_LOTION_OCR_DIR = AUDIT_DIR / "new_lotion_html_ocr_20260707"
OCR_BATCH_DIRS = [AUDIT_DIR / "ocr_batch_1", AUDIT_DIR / "ocr_batch_2", AUDIT_DIR / "ocr_batch_3"]
DOWNLOADS_DIR = Path.home() / "Downloads"
PROCESSED_DIR = AUDIT_DIR / "jd_all_processed"
PRECISE_DIR = AUDIT_DIR / "precise_processed"


def _extract_text_from_any(obj) -> str:
    """递归提取对象中所有人类可读文本，拼接返回"""
    parts = []
    if isinstance(obj, str):
        s = obj.strip()
        if s:
            parts.append(s)
    elif isinstance(obj, dict):
        # 优先ocr_text/text/content字段
        for key in ("ocr_text", "text", "content", "full_text", "raw_text"):
            if key in obj:
                t = _extract_text_from_any(obj[key])
                if t:
                    parts.append(t)
        # 再递归其它值
        for k, v in obj.items():
            if k in ("ocr_text", "text", "content", "full_text", "raw_text"):
                continue
            t = _extract_text_from_any(v)
            if t:
                parts.append(t)
    elif isinstance(obj, list):
        for item in obj:
            t = _extract_text_from_any(item)
            if t:
                parts.append(t)
    return "\n".join(parts)


def load_detail_review() -> dict:
    """detail_review: 主OCR目录，返回 {pid: {source, text, images_count}}"""
    out = {}
    if not DETAIL_REVIEW_DIR.exists():
        return out
    for f in DETAIL_REVIEW_DIR.glob("detail_*_ocr.json"):
        m = re.match(r"detail_(\d+)_ocr\.json", f.name)
        if not m:
            continue
        pid = int(m.group(1))
        try:
            data = json.loads(f.read_text(encoding="utf-8", errors="ignore"))
            images = data.get("images", []) if isinstance(data, dict) else []
            parts = []
            for img in images:
                if isinstance(img, dict):
                    t = img.get("ocr_text") or ""
                    if t and t.strip():
                        parts.append(t.strip())
            text = "\n".join(parts)
            if not text:
                text = _extract_text_from_any(data)
            out[pid] = {
                "source": f"detail_review/{f.name}",
                "text": text,
                "images_count": len(images),
            }
        except Exception as e:
            out[pid] = {"source": f"detail_review/{f.name}", "error": str(e), "text": "", "images_count": 0}
    return out


def load_full_manual_batches() -> dict:
    out = {}
    if not FULL_MANUAL_DIR.exists():
        return out
    for sub in FULL_MANUAL_DIR.iterdir():
        if not sub.is_dir():
            continue
        ocr_file = sub / "ocr_results.json"
        if not ocr_file.exists():
            continue
        try:
            data = json.loads(ocr_file.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            continue
        if isinstance(data, dict):
            for k, v in data.items():
                m = re.match(r"(\d+)", str(k))
                if not m:
                    continue
                pid = int(m.group(1))
                text = _extract_text_from_any(v)
                if text:
                    out.setdefault(pid, {"source": f"full_manual/{sub.name}", "text": "", "images_count": 0})
                    if out[pid]["text"]:
                        out[pid]["text"] += "\n" + text
                    else:
                        out[pid]["text"] = text
        elif isinstance(data, list):
            for item in data:
                if not isinstance(item, dict):
                    continue
                pid = item.get("product_id") or item.get("pid") or item.get("id")
                if not pid:
                    continue
                pid = int(pid)
                text = _extract_text_from_any(item)
                if text:
                    out.setdefault(pid, {"source": f"full_manual/{sub.name}", "text": "", "images_count": 0})
                    if out[pid]["text"]:
                        out[pid]["text"] += "\n" + text
                    else:
                        out[pid]["text"] = text
    # nonlong
    nonlong = FULL_MANUAL_DIR / "nonlong_high_potential_ocr_results.json"
    if nonlong.exists():
        try:
            data = json.loads(nonlong.read_text(encoding="utf-8", errors="ignore"))
            if isinstance(data, dict):
                for k, v in data.items():
                    m = re.match(r"(\d+)", str(k))
                    if not m:
                        continue
                    pid = int(m.group(1))
                    text = _extract_text_from_any(v)
                    if text:
                        out.setdefault(pid, {"source": "full_manual/nonlong", "text": "", "images_count": 0})
                        if out[pid]["text"]:
                            out[pid]["text"] += "\n" + text
                        else:
                            out[pid]["text"] = text
        except Exception:
            pass
    return out


def load_ocr_batches() -> dict:
    out = {}
    for bdir in OCR_BATCH_DIRS:
        if not bdir.exists():
            continue
        for f in bdir.rglob("*.json"):
            if "ocr" not in f.name.lower():
                continue
            try:
                data = json.loads(f.read_text(encoding="utf-8", errors="ignore"))
            except Exception:
                continue
            # 尝试从文件名或内容找pid
            pid_from_name = None
            nums = re.findall(r"(\d{3,})", f.name)
            for n in nums:
                v = int(n)
                if v > 50:
                    pid_from_name = v
                    break
            # 内容里
            text = _extract_text_from_any(data)
            pids_found = set()
            if pid_from_name:
                pids_found.add(pid_from_name)
            if isinstance(data, dict):
                for key in ("pid", "product_id", "id"):
                    if key in data:
                        try:
                            v = int(data[key])
                            if v > 50:
                                pids_found.add(v)
                        except Exception:
                            pass
            if not text:
                continue
            for pid in pids_found:
                out.setdefault(pid, {"source": f"{bdir.name}/{f.name}", "text": "", "images_count": 0})
                if out[pid]["text"]:
                    out[pid]["text"] += "\n" + text
                else:
                    out[pid]["text"] = text
    return out


def load_new_lotion() -> dict:
    out = {}
    if not NEW_LOTION_OCR_DIR.exists():
        return out
    for f in NEW_LOTION_OCR_DIR.glob("*.json"):
        if "ocr" not in f.name.lower():
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            continue
        nums = re.findall(r"(\d{3,})", f.name)
        pid = None
        for n in nums:
            v = int(n)
            if v > 50:
                pid = v
                break
        text = _extract_text_from_any(data)
        if pid and text:
            out[pid] = {"source": f"new_lotion/{f.name}", "text": text, "images_count": 0}
    return out


def load_jd_processed_html() -> dict:
    """从 jd_all_processed / precise_processed 加载已处理的HTML正文"""
    out = {}
    for d in [PROCESSED_DIR, PRECISE_DIR]:
        if not d.exists():
            continue
        for f in d.rglob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8", errors="ignore"))
            except Exception:
                continue
            if not isinstance(data, dict):
                continue
            pid = data.get("pid") or data.get("product_id") or data.get("id")
            if not pid:
                nums = re.findall(r"(\d{3,})", f.name)
                for n in nums:
                    v = int(n)
                    if v > 50:
                        pid = v
                        break
            if not pid:
                continue
            pid = int(pid)
            # 正文可能在 content / html_content / description 等字段
            body_parts = []
            for key in ("content", "html_content", "description", "detail_content", "main_text", "raw_html"):
                if key in data and isinstance(data[key], str):
                    body_parts.append(data[key])
            # 规格/评价单独
            for key in ("specs", "specifications", "comments", "reviews", "qa", "qa_list"):
                if key in data:
                    t = _extract_text_from_any(data[key])
                    if t:
                        body_parts.append(t)
            text = "\n".join(body_parts)
            if not text:
                text = _extract_text_from_any(data)
            if text:
                out.setdefault(pid, {"source": f"processed:{d.name}/{f.name}", "text": "", "images_count": 0})
                if out[pid]["text"]:
                    out[pid]["text"] += "\n" + text
                else:
                    out[pid]["text"] = text
    return out


def aggregate_all_sources():
    """汇总所有来源，按pid聚合全文"""
    sources = [
        ("detail_review", load_detail_review()),
        ("full_manual", load_full_manual_batches()),
        ("ocr_batches", load_ocr_batches()),
        ("new_lotion", load_new_lotion()),
        ("jd_processed_html", load_jd_processed_html()),
    ]
    agg = defaultdict(lambda: {"text": "", "sources": [], "images_count": 0})
    for src_name, src_data in sources:
        for pid, info in src_data.items():
            t = info.get("text", "")
            if not t:
                continue
            if agg[pid]["text"]:
                agg[pid]["text"] += "\n\n" + t
            else:
                agg[pid]["text"] = t
            agg[pid]["sources"].append(info.get("source", src_name))
            agg[pid]["images_count"] += info.get("images_count", 0)
    return dict(agg)


async def main():
    await init_postgres_pool()
    try:
        products = await execute_query("SELECT id, name, brand, category, platform, detail_url, specifications, description FROM products ORDER BY id")
        print(f"数据库商品总数: {len(products)}")

        agg = aggregate_all_sources()
        print(f"已聚合OCR/正文的商品数: {len(agg)}")

        no_ocr = []
        weak_ocr = []  # <800字（约合详情2-3张图的文字量）
        ok_ocr = []

        SEVEN_FIELDS = ["qa_facts", "mechanism_notes", "usage_steps", "safety_notes", "texture_notes", "claim_notes", "user_review_notes"]

        coverage_rows = []
        for p in products:
            pid = p["id"]
            spec = p.get("specifications") or {}
            skincare = spec.get("skincare_info", {}) if isinstance(spec, dict) else {}
            existing_count = sum(1 for f in SEVEN_FIELDS if skincare.get(f))

            info = agg.get(pid)
            text_len = len(info["text"]) if info else 0
            images = info["images_count"] if info else 0
            sources = info["sources"] if info else []

            if not info or text_len < 80:
                status = "missing"
                no_ocr.append({"id": pid, "name": p["name"], "brand": p.get("brand"), "platform": p.get("platform")})
            elif text_len < 800:
                status = "weak"
                weak_ocr.append({"id": pid, "name": p["name"], "brand": p.get("brand"), "platform": p.get("platform"), "text_len": text_len, "images": images, "sources": sources})
            else:
                status = "ok"
                ok_ocr.append(pid)

            coverage_rows.append({
                "id": pid,
                "name": p["name"],
                "brand": p.get("brand"),
                "category": p.get("category"),
                "platform": p.get("platform"),
                "status": status,
                "text_len": text_len,
                "images_count": images,
                "sources": sources,
                "existing_seven_fields": existing_count,
            })

        print(f"\n=== 覆盖率 ===")
        print(f"OK  (>=800字):  {len(ok_ocr)}")
        print(f"弱   (<800字):  {len(weak_ocr)}")
        print(f"缺失(<80字):   {len(no_ocr)}")

        print(f"\n=== 缺失OCR的商品（需补OCR）===")
        for r in no_ocr:
            print(f"  ID {r['id']:3d}  [{r['platform']}] {r['brand']} - {r['name']}")

        print(f"\n=== OCR偏弱商品（可能需要补，先看文字质量）===")
        for r in weak_ocr[:30]:
            print(f"  ID {r['id']:3d}  len={r['text_len']:5d}  imgs={r['images']:2d}  {r['brand']} - {r['name'][:40]}")
        if len(weak_ocr) > 30:
            print(f"  ... 还有 {len(weak_ocr)-30} 个")

        # 把聚合后的纯文本落盘一份，给后续抽取脚本复用
        texts_out = {}
        for pid, info in agg.items():
            texts_out[str(pid)] = {"text": info["text"], "sources": info["sources"], "images_count": info["images_count"]}
        text_path = AUDIT_DIR / "aggregated_ocr_html_texts_20260708.json"
        text_path.write_text(json.dumps(texts_out, ensure_ascii=False), encoding="utf-8")
        print(f"\n聚合文本落盘: {text_path}")

        report = {
            "total_products": len(products),
            "ok_count": len(ok_ocr),
            "weak_count": len(weak_ocr),
            "missing_count": len(no_ocr),
            "missing": no_ocr,
            "weak": weak_ocr,
            "ok_ids": ok_ocr,
            "coverage_rows": coverage_rows,
        }
        report_path = AUDIT_DIR / "seven_field_coverage_audit_20260708.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"覆盖率报告: {report_path}")

    finally:
        await close_postgres_pool()


if __name__ == "__main__":
    asyncio.run(main())
