#!/usr/bin/env python3
"""
V2: 更强的HTML->PID映射 + 详情图OCR
- 优先通过京东URL item.jd.com/(\d+).html 精确匹配
- 其次品牌+商品名模糊匹配（放宽阈值）
- 对所有映射到的HTML，扫描资源目录详情长图跑OCR
- 广告/推荐图过滤（OCR文本出现≥2个别家品牌）
- 跳过已有充足详情图OCR(>=1000字详情OCR内容)的商品
"""
import asyncio
import sys
import os
import json
import re
import io
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database.postgres import init_postgres_pool, execute_query, close_postgres_pool

PROJECT_ROOT = Path(__file__).parent.parent
AUDIT_DIR = PROJECT_ROOT / ".tmp_user_download_audit"
DOWNLOADS_DIR = Path.home() / "Downloads"
OUT_PATH = AUDIT_DIR / "aggregated_ocr_html_texts_20260708.json"

BRAND_WORDS = [
    "兰蔻","LANCOME","Lancome","雅诗兰黛","ESTEE","Estee","Lauder",
    "海蓝之谜","LA MER","LAMER","赫莲娜","HR","Helena",
    "修丽可","SkinCeuticals","SK-II","SK2","sk2",
    "资生堂","SHISEIDO","Shiseido","科颜氏","KIEHL","Kiehl",
    "倩碧","CLINIQUE","Clinique","碧欧泉","BIOTHERM",
    "欧莱雅","L'OREAL","Loreal","珀莱雅","PROYA","Proya",
    "玉泽","Dr.Yu","DR.YU","薇诺娜","WINONA","Winona",
    "理肤泉","LA ROCHE","Laroche","雅漾","Avene","AVENE",
    "珂润","Curel","CUREL","花王","芙丽芳丝","freeplus","FREEPLUS",
    "怡思丁","ISDIN","Isdin","安热沙","ANESSA","Anessa","安耐晒",
    "碧柔","Biore","BIORE","苏菲娜","SOFINA","Sofina",
    "茵芙莎","IPSA","ipsa","澳尔滨","ALBION","Albion","奥尔滨",
    "蒂佳婷","Dr.Jart","DR.JART","敷尔佳","可复美","馥蕾诗","Fresh","FRESH",
    "悦木之源","ORIGINS","Origins","植村秀","SHU UEMURA","Shu Uemura",
    "贝德玛","BIODERMA","Bioderma","芭妮兰","BANILA","Banila",
    "怡丽丝尔","ELIXIR","Elixir","夸迪","可复美",
    "香奈儿","CHANEL","Chanel","迪奥","Dior","DIOR",
    "阿玛尼","ARMANI","Armani","YSL","ysl","圣罗兰",
    "纪梵希","GIVENCHY","Givenchy","NARS","Nars","纳斯","娜斯",
    "MAC","M.A.C","魅可","花西子","衰败城市","URBAN DECAY","Urban Decay",
    "CPB","肌肤之钥","祖玛珑","JO MALONE","Jo Malone",
    "TOM FORD","Tom Ford","玉兰油","OLAY","Olay",
    "The Ordinary","THE ORDINARY","研度公式",
    "EVE LOM","伊芙珑","EltaMD","安妍科","eltamd",
]

NOISE_NAV_PATTERNS = [
    "京东首页","购物车","我的订单","我的京东","桌面版","同款搜低价",
    "中国大陆版","港澳版","台灣版","全球版","京东物流","登录","注册",
    "全部商品分类","京东超市","京东电器","秒杀","优惠券","PLUS会员",
    "客户服务","网站导航","手机京东","企业采购","客户服务",
    "降价通知","累计评价","加入购物车","立即抢购","促销","赠品",
    "规格","商品详情","商品评价","售后保障","商品介绍","规格参数",
]


def normalize(s: str) -> str:
    return re.sub(r"\s+", "", s or "").lower()


def extract_jd_id(html_file: Path):
    """从HTML内容或文件名提取京东商品ID"""
    # 先读HTML前50KB，找item.jd.com/(\d+).html
    try:
        with open(html_file, "rb") as f:
            chunk = f.read(50000).decode("utf-8", errors="ignore")
    except Exception:
        return None
    # URL格式 //item.jd.com/100123456789.html
    m = re.search(r"item\.jd\.com/(\d{5,})\.html", chunk)
    if m:
        return int(m.group(1))
    # skuId
    m = re.search(r"skuId\s*[:=]\s*['\"]?(\d{5,})", chunk)
    if m:
        return int(m.group(1))
    # 文件名里的数字（123.html/1234.html这种）
    m = re.match(r"^(\d{5,})\.html$", html_file.name)
    if m:
        return int(m.group(1))
    return None


def map_html_to_products(products):
    """返回 {html_file_path: product_id}"""
    mapping = {}
    unmatched = []
    detail_url_to_pid = {}
    name_jd_to_pid = {}
    for p in products:
        url = p.get("detail_url") or ""
        m = re.search(r"item\.jd\.com/(\d+)", url)
        if m:
            detail_url_to_pid[int(m.group(1))] = p["id"]
        if p.get("platform") == "jd":
            name_jd_to_pid[p["id"]] = p

    for html_file in DOWNLOADS_DIR.glob("*.html"):
        fn = html_file.name
        # 先尝试JD ID
        jd_id = extract_jd_id(html_file)
        if jd_id and jd_id in detail_url_to_pid:
            mapping[html_file] = detail_url_to_pid[jd_id]
            continue
        if jd_id:
            # JD ID不在现有URL映射里，尝试匹配产品名里的商品信息
            # 直接通过文件名包含的品牌+主词匹配
            pass
        # 123.html / 1234.html 短名直接跳过（不是商品页或太小）
        if re.match(r"^\d{1,4}\.html$", fn):
            continue
        # 品牌+关键词模糊匹配
        fn_low = fn.lower()
        best_pid = None
        best_score = 0
        for p in products:
            score = 0
            brand = (p.get("brand") or "").lower()
            name = (p.get("name") or "").lower()
            if brand and brand in fn_low:
                score += 10
            # 从name抽核心产品词
            # 简化：用n-gram匹配name的主要子串
            name_clean = re.sub(r"[（）()\[\]【】\s]+", "", p.get("name", ""))
            # 取长度>=2的词/数字组合
            tokens = re.findall(r"[\u4e00-\u9fa5A-Za-z0-9+.]+", name_clean)
            for tok in tokens:
                if len(tok) >= 2 and tok.lower() in fn_low:
                    score += min(len(tok), 6)
            if score > best_score:
                best_score = score
                best_pid = p["id"]
        if best_pid and best_score >= 10:
            mapping[html_file] = best_pid
        else:
            unmatched.append((html_file.name, best_score, best_pid))
    return mapping, unmatched


def get_resource_dir(html_file: Path) -> Path | None:
    d = html_file.with_suffix("")
    d = Path(str(d) + "_files")
    return d if d.exists() else None


def is_candidate_detail_image(img_path: Path) -> bool:
    """筛选真正的详情介绍长图
    详情图特征：竖向长图(高>宽*1.4)，高度>1000像素，宽度400-1500，文件>30KB
    排除：正方形主图/SKU缩略图/ICON/头像/横版广告Banner
    """
    try:
        from PIL import Image
        size_kb = img_path.stat().st_size / 1024
        if size_kb < 30:
            return False
        with Image.open(img_path) as im:
            w, h = im.size
        if w < 480 or w > 2000:
            return False
        if h < 1000:
            return False
        ratio = h / w
        # 详情长图: h/w > 1.4（竖向），极少数横版详情图 h/w > 0.7 且 width > 1500
        if ratio >= 1.4:
            return True
        # 横版大图(banner/成分表)，但要宽>1500，高>600
        if ratio >= 0.6 and w >= 1500 and h >= 600:
            return True
        return False
    except Exception:
        return False


def detect_ad_text(text: str, current_brand: str) -> bool:
    if not text or len(text) < 20:
        return False
    brands_found = set()
    tlow = text.lower()
    for b in BRAND_WORDS:
        bl = b.lower()
        if bl in tlow:
            brands_found.add(bl)
    cb = (current_brand or "").lower()
    brands_found = {b for b in brands_found if b and (not cb or (b not in cb and cb not in b))}
    # 同品牌别名兼容
    return len(brands_found) >= 2


def extract_html_text(html_content: str) -> str:
    try:
        text = re.sub(r"<script[\s\S]*?</script>", "", html_content, flags=re.I)
        text = re.sub(r"<style[\s\S]*?</style>", "", text, flags=re.I)
        text = re.sub(r"<[^>]+>", "\n", text)
        text = re.sub(r"&nbsp;|&lt;|&gt;|&amp;|&quot;|&yen;", " ", text)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{2,}", "\n", text)
        return text.strip()
    except Exception:
        return ""


def clean_html_body(text: str) -> str:
    """去除导航/菜单/价格/按钮等噪声，保留规格/评价/QA/介绍正文"""
    lines = text.split("\n")
    kept = []
    for line in lines:
        s = line.strip()
        if not s or len(s) < 4:
            continue
        if len(s) > 500:
            kept.append(s)
            continue
        # 过滤导航噪声
        is_noise = False
        for pat in NOISE_NAV_PATTERNS:
            if pat in s and len(s) < 30:
                is_noise = True
                break
        if re.match(r"^[￥¥]\s*\d", s):
            continue
        if re.match(r"^(首页|登录|注册|购物车|我的)", s) and len(s) < 20:
            continue
        if is_noise:
            continue
        kept.append(s)
    return "\n".join(kept)


def count_detail_ocr_chars(text: str) -> int:
    """统计详情图OCR部分的字数（历史OCR+新跑OCR，不算HTML正文）"""
    total = 0
    for marker in ("===详情图OCR===", "===历史详情OCR===", "===manual补充OCR==="):
        if marker in text:
            parts = text.split(marker)
            total += sum(len(p) for p in parts[1:])
    return total


async def main():
    await init_postgres_pool()
    try:
        products = await execute_query("SELECT id, name, brand, category, platform, detail_url, specifications FROM products ORDER BY id")
        products_by_id = {p["id"]: p for p in products}
        print(f"DB 商品数: {len(products)}")

        mapping, unmatched = map_html_to_products(products)
        print(f"HTML->PID 映射数: {len(mapping)}")
        if unmatched:
            print(f"未匹配HTML文件数: {len(unmatched)}")
            for n, sc, pid in unmatched[:15]:
                print(f"  - score={sc} pid={pid}  {n[:70]}")

        # 加载已有聚合
        result = {}
        # 1) 先加载detail_review历史OCR作为基础
        dr_dir = AUDIT_DIR / "detail_review"
        if dr_dir.exists():
            for f in dr_dir.glob("detail_*_ocr.json"):
                m = re.match(r"detail_(\d+)_ocr\.json", f.name)
                if not m:
                    continue
                pid = int(m.group(1))
                try:
                    data = json.loads(f.read_text(encoding="utf-8", errors="ignore"))
                    imgs = data.get("images", []) if isinstance(data, dict) else []
                    parts = []
                    for img in imgs:
                        if isinstance(img, dict):
                            t = img.get("ocr_text") or ""
                            if t.strip():
                                parts.append(t.strip())
                    text = "\n".join(parts)
                    if text:
                        result[pid] = {
                            "text": "===历史详情OCR===\n" + text,
                            "sources": [f"detail_review/{f.name}"],
                            "images_count": len(imgs),
                        }
                except Exception:
                    pass
        # 2) 再加full_manual_batches
        fm_dir = AUDIT_DIR / "full_manual_review_batches"
        if fm_dir.exists():
            for sub in fm_dir.iterdir():
                if not sub.is_dir():
                    continue
                ocrf = sub / "ocr_results.json"
                if not ocrf.exists():
                    continue
                try:
                    data = json.loads(ocrf.read_text(encoding="utf-8", errors="ignore"))
                except Exception:
                    continue
                def _xt(o):
                    if isinstance(o, str): return o
                    if isinstance(o, dict):
                        for k in ("ocr_text","text","content"):
                            if k in o: return _xt(o[k])
                        for v in o.values():
                            r=_xt(v)
                            if r: return r
                    if isinstance(o,list):
                        for v in o:
                            r=_xt(v)
                            if r: return r
                    return ""
                if isinstance(data, dict):
                    for k, v in data.items():
                        m = re.match(r"(\d+)", str(k))
                        if not m: continue
                        pid = int(m.group(1))
                        t = _xt(v)
                        if not t: continue
                        if pid not in result:
                            result[pid] = {"text":"", "sources":[], "images_count":0}
                        result[pid]["text"] += "\n\n===manual补充OCR===\n" + t
                        result[pid]["sources"].append(f"manual/{sub.name}")
        print(f"从历史OCR预加载: {len(result)}个商品")

        from rapidocr_onnxruntime import RapidOCR
        ocr_engine = RapidOCR()

        new_pids = set()
        total_images_ocrd = 0

        for html_file, pid in mapping.items():
            prod = products_by_id[pid]
            current_brand = prod.get("brand", "") or ""

            # 处理HTML正文
            html_text_raw = ""
            try:
                html_raw = html_file.read_text(encoding="utf-8", errors="ignore")
                html_text_raw = extract_html_text(html_raw)
            except Exception:
                pass
            html_text = clean_html_body(html_text_raw) if html_text_raw else ""

            if pid not in result:
                result[pid] = {"text": "", "sources": [], "images_count": 0}

            # 添加HTML正文（去重）
            if html_text and len(html_text) > 200:
                # 提取html body里的核心内容（评价/规格/QA）
                if "===HTML正文===" not in result[pid]["text"]:
                    if result[pid]["text"]:
                        result[pid]["text"] += "\n\n===HTML正文===\n" + html_text
                    else:
                        result[pid]["text"] = html_text
                    if f"html_body:{html_file.name[:30]}" not in result[pid]["sources"]:
                        result[pid]["sources"].append(f"html_body:{html_file.name}")

            res_dir = get_resource_dir(html_file)
            if not res_dir:
                continue

            # 判断是否需要跑详情图OCR
            existing_detail_chars = count_detail_ocr_chars(result[pid]["text"])
            if existing_detail_chars >= 1200:
                continue  # 已有足够详情图OCR

            # 收集图片
            imgs = []
            for ext in ("*.avif", "*.jpg", "*.jpeg", "*.png", "*.webp"):
                imgs.extend(res_dir.glob(ext))
            cand_imgs = [p for p in imgs if is_candidate_detail_image(p)]
            cand_imgs.sort(key=lambda p: p.name)

            if not cand_imgs:
                continue

            print(f"[PID {pid}] {current_brand} - {prod['name'][:35]} | 候选{len(cand_imgs)}张, 现有详情OCR {existing_detail_chars}字")

            new_texts = []
            good_count = 0
            for img_path in cand_imgs:
                try:
                    if img_path.suffix.lower() == ".avif":
                        from PIL import Image
                        with Image.open(img_path) as im:
                            rgb = im.convert("RGB")
                            buf = io.BytesIO()
                            rgb.save(buf, format="JPEG", quality=90)
                            buf.seek(0)
                            ocr_result, _ = ocr_engine(buf.read())
                    else:
                        ocr_result, _ = ocr_engine(str(img_path))
                    if not ocr_result:
                        continue
                    lines = [item[1] for item in ocr_result if len(item) >= 2 and item[1]]
                    text = "\n".join(lines).strip()
                    if len(text) < 10:
                        continue
                    if detect_ad_text(text, current_brand):
                        continue
                    new_texts.append(text)
                    good_count += 1
                except Exception:
                    continue

            if new_texts:
                block = "\n".join(new_texts)
                result[pid]["text"] += "\n\n===详情图OCR===\n" + block
                result[pid]["sources"].append(f"detail_ocr:{res_dir.name}")
                result[pid]["images_count"] += good_count
                new_pids.add(pid)
                total_images_ocrd += good_count
                print(f"  + {good_count}张有用图, 新增{len(block)}字")

        # 保存
        serializable = {str(pid): info for pid, info in result.items()}
        OUT_PATH.write_text(json.dumps(serializable, ensure_ascii=False), encoding="utf-8")

        # 覆盖率统计（按详情图OCR+正文总字数）
        ok = weak = miss = 0
        for p in products:
            pid = p["id"]
            info = result.get(pid)
            tl = len(info["text"]) if info else 0
            dc = count_detail_ocr_chars(info["text"]) if info else 0
            if dc >= 800 or tl >= 2500:
                ok += 1
            elif tl >= 300:
                weak += 1
            else:
                miss += 1
        print(f"\n=== 最终覆盖率 ===")
        print(f"OK(详情OCR>=800字或总>=2500字): {ok}")
        print(f"弱(300~2500字): {weak}")
        print(f"缺失(<300字): {miss}")
        print(f"本轮新跑详情图OCR商品: {len(new_pids)}，图: {total_images_ocrd}张")
        print(f"\n聚合文本: {OUT_PATH}")

    finally:
        await close_postgres_pool()


if __name__ == "__main__":
    asyncio.run(main())
