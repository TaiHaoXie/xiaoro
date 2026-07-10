#!/usr/bin/env python3
"""
增量补全：处理用户新下载的14个京东HTML，完成：
1. HTML→PID映射（优先JD ID精确匹配）
2. 筛选详情长图，跑RapidOCR
3. 读取对应商品的specifications.skincare_info，叠加新OCR+HTML文本
4. 重新抽取7字段
5. 增量写回DB
"""
import sys, os, json, re, io, time, asyncio
from pathlib import Path
from datetime import datetime
from typing import List

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.database.postgres import init_postgres_pool, close_postgres_pool, execute_query
from scripts.run_detail_ocr_from_downloads import (
    extract_jd_id, map_html_to_products, is_candidate_detail_image,
    extract_html_text, detect_ad_text, BRAND_WORDS,
)
from scripts.seven_field_extractor import extract_seven_fields

PROJECT_ROOT = Path(__file__).parent.parent
AUDIT_DIR = PROJECT_ROOT / ".tmp_user_download_audit"
DOWNLOADS_DIR = Path.home() / "Downloads"

# 筛选今天(7月8日)新下载的京东HTML(10:30以后)
CUTOFF_TS = datetime(2026, 7, 8, 10, 30).timestamp()


def find_new_jd_htmls() -> List[Path]:
    """找出最近新下载的京东HTML"""
    htmls = []
    for f in DOWNLOADS_DIR.glob("*.html"):
        if "京东" not in f.name and "-京东.html" not in f.name:
            continue
        try:
            st = f.stat()
            if st.st_mtime >= CUTOFF_TS:
                htmls.append(f)
        except Exception:
            pass
    return sorted(htmls, key=lambda p: -p.stat().st_mtime)


def ocr_image(img_path: Path) -> str:
    """跑RapidOCR单张图"""
    try:
        from rapidocr_onnxruntime import RapidOCR
        from PIL import Image
    except ImportError as e:
        return f"[OCR依赖缺失: {e}]"
    try:
        ocr = RapidOCR()
    except Exception as e:
        return f"[OCR初始化失败: {e}]"
    img_path = Path(img_path)
    try:
        from pillow_avif import AvifImagePlugin
    except Exception:
        pass
    try:
        img = Image.open(img_path)
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        # 大图缩放
        w, h = img.size
        if max(w, h) > 2400:
            ratio = 2400 / max(w, h)
            img = img.resize((int(w*ratio), int(h*ratio)), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=90)
        buf.seek(0)
        result, _ = ocr(buf.read())
        if not result:
            return ""
        lines = []
        for r in result:
            if len(r) >= 2 and r[1]:
                lines.append(str(r[1]).strip())
        return "\n".join(lines)
    except Exception as e:
        return f"[OCR失败{img_path.name}: {e}]"


async def load_all_products():
    rows = await execute_query("SELECT id, name, brand, category, detail_url, specifications FROM products ORDER BY id", fetch="all")
    return [dict(r) for r in rows]


async def main():
    await init_postgres_pool()
    AUDIT_DIR.mkdir(exist_ok=True)

    html_files = find_new_jd_htmls()
    print(f"找到 {len(html_files)} 个新下载的京东HTML")
    for f in html_files:
        print(f"  - {f.name[:80]}")

    products = await load_all_products()
    print(f"DB商品数: {len(products)}")

    # 自己做映射：优先JD ID精确匹配，其次品牌+规格匹配
    html_to_pid = {}
    detail_url_jd_to_pid = {}
    for p in products:
        url = p.get("detail_url") or ""
        m = re.search(r"item\.jd\.com/(\d+)", url)
        if m:
            detail_url_jd_to_pid[int(m.group(1))] = p["id"]

    def score_match(hf: Path, p: dict) -> int:
        fn = hf.name.lower()
        fn_nospace = re.sub(r"\s+|[（）()\[\]【】_\-]", "", fn)
        brand = (p.get("brand") or "").lower()
        name = (p.get("name") or "")
        name_nospace = re.sub(r"\s+|[（）()\[\]【】_\-]", "", name).lower()
        sc = 0
        if brand and brand in fn:
            sc += 10
        # 抽规格数字(g/ml)
        spec_pat = re.findall(r"(\d+(?:\.\d+)?)\s?(?:ml|g|ML|G)", name)
        fn_spec_pat = re.findall(r"(\d+(?:\.\d+)?)\s?(?:ml|g|ML|G)", fn)
        for sp in spec_pat:
            if sp in fn:
                sc += 5
                break
        # 核心词匹配
        core_words = []
        # 去掉品牌、规格、通用词后看核心词
        cleaned = name
        for bw in BRAND_WORDS:
            cleaned = cleaned.replace(bw, "")
        cleaned = re.sub(r"\d+(?:\.\d+)?\s?(?:ml|g|ML|G|支|瓶|盒|片|枚|袋)", "", cleaned)
        cleaned = re.sub(r"[（）()\[\]【】_\-,，。·.]+", " ", cleaned)
        tokens = [t for t in re.split(r"\s+", cleaned) if len(t) >= 2]
        hit = 0
        for t in tokens:
            t_low = t.lower()
            if len(t_low) < 2: continue
            if t_low in ("正品","官方","礼物","送女友","生日","行情","报价","价格","评测","京东","新版","全新","套装","礼盒","香氛"):
                continue
            if t_low in fn_nospace:
                sc += 2
                hit += 1
        return sc

    for hf in html_files:
        jid = extract_jd_id(hf)
        if jid and jid in detail_url_jd_to_pid:
            html_to_pid[hf] = detail_url_jd_to_pid[jid]
            continue
        best_pid, best_score = None, 0
        for p in products:
            sc = score_match(hf, p)
            if sc > best_score:
                best_score, best_pid = sc, p["id"]
        if best_pid and best_score >= 12:
            html_to_pid[hf] = best_pid

    mapped = len(html_to_pid)
    print(f"映射成功: {mapped}/{len(html_files)}")
    for hf, pid in html_to_pid.items():
        p = next((x for x in products if x["id"] == pid), {})
        print(f"  PID{pid}: [{p.get('brand','')}] {p.get('name','')[:40]} <- {hf.name[:50]}")

    # 读取已有聚合文本（如果存在）
    existing_agg_path = AUDIT_DIR / "aggregated_ocr_html_texts_20260708.json"
    existing_agg = {}
    if existing_agg_path.exists():
        existing_agg = json.loads(existing_agg_path.read_text(encoding="utf-8"))
    # 对每个映射到的商品跑长图OCR + HTML文本提取
    new_texts_count = 0
    ocr_new_imgs = 0
    for hf, pid in html_to_pid.items():
        p = next((x for x in products if x["id"] == pid), {})
        brand = p.get("brand") or ""
        pid_s = str(pid)

        # 解析HTML正文
        try:
            html_content = hf.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            html_content = ""
        html_text = extract_html_text(html_content) if html_content else ""

        # 找_files目录
        files_dir = DOWNLOADS_DIR / (hf.stem + "_files")
        img_text_parts = []
        if files_dir.exists() and files_dir.is_dir():
            imgs = []
            for img in files_dir.iterdir():
                if img.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp", ".avif"):
                    if is_candidate_detail_image(img):
                        imgs.append(img)
            imgs.sort(key=lambda x: x.stat().st_size if x.exists() else 0, reverse=True)
            # 对每个候选图OCR
            for img in imgs:
                print(f"  OCR: {img.name} ({img.stat().st_size//1024}KB)", end=" ", flush=True)
                txt = ocr_image(img)
                if not txt or txt.startswith("[OCR"):
                    print("跳过")
                    continue
                # 广告过滤
                if detect_ad_text(txt, brand):
                    print("广告/跨品")
                    continue
                img_text_parts.append(f"--- {img.name} ---\n{txt}")
                ocr_new_imgs += 1
                print(f"OK ({len(txt)}字)")
                time.sleep(0.05)

        # 组装新文本块
        new_block_parts = []
        if html_text:
            new_block_parts.append(f"===京东HTML正文(新下载)===\n{html_text}")
        if img_text_parts:
            new_block_parts.append("===新下载详情图OCR===\n" + "\n\n".join(img_text_parts))
        new_block = "\n\n".join(new_block_parts)

        if pid_s in existing_agg:
            old_text = existing_agg[pid_s].get("text", "")
            # 追加新块（避免重复追加）
            if new_block not in old_text:
                existing_agg[pid_s]["text"] = old_text + "\n\n" + new_block
                new_texts_count += 1
        else:
            existing_agg[pid_s] = {
                "name": p.get("name", ""),
                "brand": brand,
                "platform": "jd",
                "text": new_block,
            }
            new_texts_count += 1

    # 保存聚合
    existing_agg_path.write_text(json.dumps(existing_agg, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nOCR完成: 新图 {ocr_new_imgs} 张, 更新/新增商品 {new_texts_count} 个")

    # 重新抽取所有商品的7字段（增量：对所有有文本的商品重新抽取再写）
    print("\n=== 重新抽取7字段 ===")
    updated = 0
    skipped = 0
    for pid_s, info in existing_agg.items():
        pid = int(pid_s)
        text = info.get("text", "")
        if len(text) < 100:
            skipped += 1
            continue
        fields = extract_seven_fields(text)
        # 读取DB原有specifications
        rows = await execute_query(
            "SELECT specifications FROM products WHERE id=$1", pid, fetch="all"
        )
        if not rows:
            continue
        specs = rows[0]["specifications"] or {}
        if isinstance(specs, str):
            try: specs = json.loads(specs)
            except: specs = {}
        if not isinstance(specs, dict):
            specs = {}
        # 合并：新抽取结果覆盖旧skincare_info
        specs["skincare_info"] = fields
        await execute_query(
            "UPDATE products SET specifications=$1::jsonb, updated_at=NOW() WHERE id=$2",
            json.dumps(specs, ensure_ascii=False), pid
        )
        # 统计
        filled = sum(1 for k, v in fields.items() if v)
        print(f"  PID{pid} {info.get('name','')[:30]:<32} 有字段:{filled}/7 "
              f"qa:{len(fields['qa_facts'])} mech:{len(fields['mechanism_notes'])} "
              f"use:{len(fields['usage_steps'])} safe:{len(fields['safety_notes'])} "
              f"tex:{len(fields['texture_notes'])} clm:{len(fields['claim_notes'])} "
              f"rev:{len(fields['user_review_notes'])}")
        updated += 1

    print(f"\n更新商品数: {updated}, 跳过(文本不足): {skipped}")

    # 备份
    backup_name = "products_backup_before_jd_incremental_20260708"
    try:
        await execute_query(f"CREATE TABLE IF NOT EXISTS {backup_name} AS TABLE products")
        print(f"已确保备份表存在: {backup_name}")
    except Exception as e:
        print(f"备份表操作: {e}")

    await close_postgres_pool()
    print("✅ 增量补全完成")


if __name__ == "__main__":
    asyncio.run(main())
