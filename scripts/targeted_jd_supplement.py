#!/usr/bin/env python3
"""
定向补全14个用户新下载的京东HTML+详情图OCR→7字段→DB
手动指定映射关系，避免自动匹配错
"""
import sys, os, json, re, io, time, asyncio
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.database.postgres import init_postgres_pool, close_postgres_pool, execute_query
from scripts.run_detail_ocr_from_downloads import (
    is_candidate_detail_image, extract_html_text, detect_ad_text,
)
from scripts.seven_field_extractor import extract_seven_fields

PROJECT_ROOT = Path(__file__).parent.parent
DOWNLOADS_DIR = Path.home() / "Downloads"

# 手动指定 HTML文件名关键字 → PID
MANUAL_MAP = [
    ("理肤泉大哥大", 53),
    ("科颜氏.*金盏花.*面膜", 100),
    ("贝德玛.*粉水", 70),
    ("悦木之源.*菌菇水", 60),
    ("怡丽丝尔.*眼霜|怡丽丝尔.*抚纹", 93),
    ("全新菁纯眼霜", 92),
    ("NARS.*定妆大白饼|NARS纳斯_娜斯.*粉饼", 81),
    ("YSL圣罗兰.*黑气垫|YSL.*黑气垫替换芯", 108),
    ("兰蔻.*菁纯精华气垫|兰蔻LANCOME菁纯.*13g", 109),
    ("NARS亮采柔滑遮瑕", 110),
    ("NARSnars腮红|NARS.*腮红.*ORGASM|NARS.*高潮", 118),
    ("阿玛尼.*红气垫|阿玛尼.*持久遮瑕.*15g", 107),
    ("五号之水经典套装", 121),
    ("五号香水.*经典.*花香调", 143),
]


def ocr_image(img_path: Path) -> str:
    try:
        from rapidocr_onnxruntime import RapidOCR
        from PIL import Image
    except ImportError as e:
        return ""
    try:
        ocr = RapidOCR()
    except Exception:
        return ""
    try:
        from pillow_avif import AvifImagePlugin
    except Exception:
        pass
    try:
        img = Image.open(img_path)
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
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
        return "\n".join(str(r[1]).strip() for r in result if len(r)>=2 and r[1])
    except Exception:
        return ""


async def get_brand(pid: int) -> str:
    rows = await execute_query("SELECT brand FROM products WHERE id=$1", pid, fetch="all")
    return rows[0]["brand"] if rows else ""


async def main():
    await init_postgres_pool()

    # 找文件
    all_htmls = list(DOWNLOADS_DIR.glob("*.html"))
    mapping = {}  # Path -> (pid, brand)
    for pat, pid in MANUAL_MAP:
        matched = [f for f in all_htmls if re.search(pat, f.name)]
        if not matched:
            print(f"⚠️ 未找到文件匹配: {pat} -> PID{pid}")
            continue
        f = matched[0]
        brand = await get_brand(pid)
        mapping[f] = (pid, brand)
        print(f"✅ {f.name[:55]:<56} -> PID{pid} [{brand}]")

    print(f"\n开始处理 {len(mapping)} 个商品...")
    total_new_imgs = 0
    for hf, (pid, brand) in mapping.items():
        print(f"\n--- PID{pid} [{brand}] ---")
        # HTML正文
        try:
            html_content = hf.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            html_content = ""
        html_text = extract_html_text(html_content) if html_content else ""
        print(f"  HTML正文: {len(html_text)}字")

        # 详情图OCR
        files_dir = DOWNLOADS_DIR / (hf.stem + "_files")
        ocr_parts = []
        n_cand = n_new = 0
        if files_dir.exists():
            for img in sorted(files_dir.iterdir(), key=lambda p: -p.stat().st_size if p.exists() else 0):
                if img.suffix.lower() not in (".jpg",".jpeg",".png",".webp",".avif"):
                    continue
                if not is_candidate_detail_image(img):
                    continue
                n_cand += 1
                txt = ocr_image(img)
                if not txt or len(txt) < 10:
                    continue
                if detect_ad_text(txt, brand):
                    continue
                ocr_parts.append(f"--- {img.name} ---\n{txt}")
                n_new += 1
                total_new_imgs += 1
                time.sleep(0.02)
        print(f"  详情图: 候选{n_cand}张, OCR有效{n_new}张")

        new_text = ""
        if html_text:
            new_text += f"===京东HTML正文(新下载)===\n{html_text}\n\n"
        if ocr_parts:
            new_text += "===新下载详情图OCR===\n" + "\n\n".join(ocr_parts)

        # 读DB现有text(聚合文本中已有的内容)
        existing_agg_path = PROJECT_ROOT / ".tmp_user_download_audit" / "aggregated_ocr_html_texts_20260708.json"
        existing_text = ""
        if existing_agg_path.exists():
            try:
                agg = json.loads(existing_agg_path.read_text(encoding="utf-8"))
                existing_text = agg.get(str(pid), {}).get("text", "")
            except Exception:
                pass
        # 合并文本: 已有 + 新增(避免重复)
        combined = existing_text
        if new_text and new_text not in combined:
            combined = (combined + "\n\n" + new_text).strip()
        print(f"  合并文本: {len(combined)}字")

        # 抽7字段
        fields = extract_seven_fields(combined)
        filled = sum(1 for k,v in fields.items() if v)
        print(f"  7字段: {filled}/7 QA:{len(fields['qa_facts'])} 机制:{len(fields['mechanism_notes'])} "
              f"用法:{len(fields['usage_steps'])} 注意:{len(fields['safety_notes'])} "
              f"质地:{len(fields['texture_notes'])} 宣称:{len(fields['claim_notes'])} "
              f"评价:{len(fields['user_review_notes'])}")

        # 写DB: 合并到现有skincare_info(非空保留)
        rows = await execute_query("SELECT specifications FROM products WHERE id=$1", pid, fetch="all")
        if not rows:
            print(f"  ⚠️ PID{pid} 不存在，跳过")
            continue
        specs = rows[0]["specifications"] or {}
        if isinstance(specs, str):
            try: specs = json.loads(specs)
            except: specs = {}
        if not isinstance(specs, dict):
            specs = {}
        old_sk = specs.get("skincare_info") or {}
        if not isinstance(old_sk, dict):
            old_sk = {}
        # 合并: 新字段有数据覆盖，没数据保留旧的
        merged_sk = dict(old_sk)
        for k, v in fields.items():
            if v:  # 非空才覆盖
                merged_sk[k] = v
            elif k not in merged_sk:
                merged_sk[k] = []
        specs["skincare_info"] = merged_sk
        await execute_query(
            "UPDATE products SET specifications=$1::jsonb, updated_at=NOW() WHERE id=$2",
            json.dumps(specs, ensure_ascii=False), pid
        )
        print(f"  ✅ 已写回DB")

        # 同步更新聚合文件
        if existing_agg_path.exists():
            try:
                agg = json.loads(existing_agg_path.read_text(encoding="utf-8"))
                agg[str(pid)] = {
                    "name": (await execute_query("SELECT name FROM products WHERE id=$1", pid, fetch="all"))[0]["name"],
                    "brand": brand,
                    "platform": "jd",
                    "text": combined,
                }
                existing_agg_path.write_text(json.dumps(agg, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception as e:
                print(f"  ⚠️ 更新聚合文件失败: {e}")

    print(f"\n=== 完成: 共OCR新图 {total_new_imgs} 张 ===")
    await close_postgres_pool()


if __name__ == "__main__":
    asyncio.run(main())
