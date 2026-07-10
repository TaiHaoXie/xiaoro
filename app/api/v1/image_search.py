"""
图片搜索API模块

处理以图搜图功能 + OCR文字识别
"""

from fastapi import APIRouter, UploadFile, File, HTTPException, Form, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime
import logging
import io
import re
from pathlib import Path
from PIL import Image

from app.services.image_embedding import get_image_embedding_service
from app.services.ocr import get_ocr_service
from app.services.image_vector import get_image_vector_service
from app.database.postgres import execute_query
from app.core.rate_limit import limit_image_search, limit_upload

logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[3]

# 创建路由器
router = APIRouter(prefix="/image-search", tags=["图片搜索"])


def _image_url_to_local_path(image_url: str) -> Optional[Path]:
    """把 /static/... 图片 URL 转成本地文件路径。只处理本地静态资源。"""
    if not image_url or not image_url.startswith("/static/"):
        return None
    path = PROJECT_ROOT / "app" / image_url.lstrip("/")
    return path if path.exists() else None


def _visual_signature(image_data: bytes) -> Dict[str, Any]:
    """
    轻量本地视觉向量：颜色直方图 + dHash。
    不依赖 CLIP 权重，适合演示环境做视觉相似度兜底。
    """
    img = Image.open(io.BytesIO(image_data)).convert("RGB")

    small = img.resize((64, 64))
    bins = [0] * 64
    pixels = list(small.getdata())
    for r, g, b in pixels:
        idx = (r // 64) * 16 + (g // 64) * 4 + (b // 64)
        bins[idx] += 1
    total = float(len(pixels) or 1)
    hist = [v / total for v in bins]

    gray = img.convert("L").resize((9, 8))
    vals = list(gray.getdata())
    dhash = []
    for y in range(8):
        row = vals[y * 9:(y + 1) * 9]
        dhash.extend(1 if row[x] > row[x + 1] else 0 for x in range(8))

    return {"hist": hist, "dhash": dhash}


def _visual_similarity(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    """返回 0-100 的本地视觉相似度。"""
    hist_score = sum(min(x, y) for x, y in zip(a["hist"], b["hist"]))
    hamming = sum(1 for x, y in zip(a["dhash"], b["dhash"]) if x != y)
    hash_score = 1 - hamming / max(1, len(a["dhash"]))
    return round((0.62 * hist_score + 0.38 * hash_score) * 100, 2)


def _safe_price(value: Any) -> Optional[float]:
    """把数据库价格安全转成 float；待核价商品允许为空。"""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _item_get(item: Any, key: str, default: Any = None) -> Any:
    if hasattr(item, "get"):
        return item.get(key, default)
    try:
        return item[key]
    except Exception:
        return default


def _normalize_ocr_match_text(value: Any) -> str:
    text = str(value or "").upper()
    return re.sub(r"[^A-Z0-9\u4e00-\u9fff]+", "", text)


def _rank_ocr_fallback_products(
    products: List[Dict[str, Any]],
    key_info: Dict[str, Any],
    local_vector_map: Dict[Any, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """品牌召回后，用 OCR 单品词和本地视觉分重排，避免同品牌泛品排到同款前面。"""
    raw_text = key_info.get("raw_text") or ""
    token_sources = [key_info.get("product_keyword"), key_info.get("brand")]
    token_sources.extend(re.split(r"[\s\n\r\t/|,，;；:：]+", raw_text))

    tokens = []
    for token in token_sources:
        normalized = _normalize_ocr_match_text(token)
        if not normalized:
            continue
        if len(normalized) < 4 and normalized not in {"MEN", "SPF", "PA"}:
            continue
        if normalized not in tokens:
            tokens.append(normalized)

    def product_score(product: Dict[str, Any]) -> float:
        product_id = _item_get(product, "id")
        product_text = " ".join(
            str(_item_get(product, field, "") or "")
            for field in ("name", "brand", "category", "description", "image_url")
        )
        normalized_product_text = _normalize_ocr_match_text(product_text)

        score = 0.0
        for token in tokens:
            if token in normalized_product_text:
                score += 22.0 if token in {"MEN", "MULTICONTROL", "ULTICONTROL", "UVSUNSCREENGEL"} else 8.0

        visual_score = _item_get(local_vector_map.get(product_id) or {}, "visual_similarity")
        if visual_score is not None:
            score += float(visual_score) * 0.18

        if _item_get(product, "detail_url"):
            score += 1.0
        return score

    return sorted(products, key=product_score, reverse=True)


async def _search_local_visual_vectors(image_data: bytes, top_k: int = 5) -> List[Dict[str, Any]]:
    """基于本地已绑定商品图做轻量视觉相似召回。"""
    query_sig = _visual_signature(image_data)
    products = await execute_query(
        """
        SELECT id, name, brand, category, price, description, image_url, detail_url
        FROM products
        WHERE image_url IS NOT NULL AND image_url <> ''
        ORDER BY id
        """,
        fetch="all"
    )

    scored = []
    for product in products:
        path = _image_url_to_local_path(product.get("image_url") or "")
        if not path:
            continue
        try:
            candidate_sig = _visual_signature(path.read_bytes())
        except Exception:
            continue
        score = _visual_similarity(query_sig, candidate_sig)
        scored.append((score, product))

    scored.sort(key=lambda item: item[0], reverse=True)
    results = []
    for score, product in scored[:top_k]:
        results.append({
            "id": product["id"],
            "name": product["name"],
            "brand": product["brand"],
            "category": product["category"],
            "price": _safe_price(product.get("price")),
            "description": product["description"],
            "similarity": score,
            "visual_similarity": score,
            "match_reason": f"本地视觉向量相似度 {score}%",
            "image_url": product.get("image_url") or "",
            "detail_url": product.get("detail_url") or "",
        })
    return results


def _score_product_visual_similarity(image_data: bytes, product: Dict[str, Any]) -> Optional[float]:
    """对单个候选商品计算本地视觉相似度。"""
    image_url = product.get("image_url") or ""
    path = _image_url_to_local_path(image_url)
    if not path:
        return None
    try:
        return _visual_similarity(_visual_signature(image_data), _visual_signature(path.read_bytes()))
    except Exception:
        return None


# ==================== 数据模型 ====================

class OCRInfo(BaseModel):
    """OCR识别信息"""
    text: str = Field(description="识别出的文字")
    confidence: float = Field(description="置信度")
    key_info: Dict[str, Any] = Field(default_factory=dict, description="提取的关键信息")


class ImageSearchResponse(BaseModel):
    """图片搜索响应"""
    query_id: str = Field(description="查询ID")
    results: List[Dict] = Field(default_factory=list, description="搜索结果")
    total: int = Field(description="结果数量")
    ocr_info: Optional[OCRInfo] = Field(None, description="OCR识别信息")
    analysis: Optional[Dict[str, Any]] = Field(None, description="图片理解分析")


class ImageSimilarityRequest(BaseModel):
    """图文相似度请求"""
    image_url: str = Field(..., description="图片URL")
    text: str = Field(..., description="文本描述")


def _extract_focus_points(image_type: str, category: Optional[str], ocr_info: Optional[OCRInfo]) -> List[str]:
    ingredients = (ocr_info.key_info.get("ingredients") if ocr_info and ocr_info.key_info else []) or []
    focus_points: List[str] = []

    if image_type == "unclear_image":
        focus_points.extend([
            "没有识别到品牌、品名或成分文字，不能直接判断具体产品",
            "建议补拍商品正面包装或成分表，再判断是否适合敏感肌",
        ])
    elif image_type == "ingredient_label":
        focus_points.extend([
            "优先判断核心成分、潜在刺激点和适合肤质",
            "结合成分表看是否适合敏感肌、刷酸期或屏障不稳阶段",
        ])
        if ingredients:
            focus_points.append(f"已识别重点成分：{'、'.join(ingredients[:4])}")
    elif image_type == "packaging_label":
        focus_points.extend([
            "先识别产品品类、主打功效和使用场景",
            "再判断它更适合哪类肤质，以及是否值得买",
        ])
    else:
        focus_points.extend([
            "先判断商品大概率是什么，再给适用人群和使用建议",
            "如果候选商品不够准，提醒用户补一张包装或成分图会更稳",
        ])

    if category:
        focus_points.append(f"当前识别品类：{category}")

    return focus_points[:4]


_BRAND_ALIASES = {
    "迪奥": {"dior", "迪奥", "cd", "christian dior"},
    "dior": {"dior", "迪奥", "cd", "christian dior"},
    "香奈儿": {"chanel", "香奈儿", "夏奈尔"},
    "chanel": {"chanel", "香奈儿", "夏奈尔"},
    "兰蔻": {"lancome", "lancôme", "兰蔻", "兰寇"},
    "lancome": {"lancome", "lancôme", "兰蔻", "兰寇"},
    "雅诗兰黛": {"estee", "estée", "雅诗兰黛", "esteelauder"},
    "纪梵希": {"givenchy", "纪梵希"},
    "givenchy": {"givenchy", "纪梵希"},
    "圣罗兰": {"ysl", "yves saint", "圣罗兰", "杨树林"},
    "ysl": {"ysl", "yves saint", "圣罗兰", "杨树林"},
    "阿玛尼": {"armani", "阿玛尼", "giorgio"},
    "armani": {"armani", "阿玛尼", "giorgio"},
    "魅可": {"mac", "魅可", "m.a.c"},
    "mac": {"mac", "魅可", "m.a.c"},
    "资生堂": {"shiseido", "资生堂"},
    "sk-ii": {"sk-ii", "sk2", "skii", "美之匙"},
    "sk2": {"sk-ii", "sk2", "skii", "美之匙"},
    "赫莲娜": {"hr", "helena", "赫莲娜"},
    "欧莱雅": {"l'oreal", "loreal", "欧莱雅", "巴黎欧莱雅"},
    "修丽可": {"skinceuticals", "修丽可", "杜克"},
    "倩碧": {"clinique", "倩碧"},
    "雅漾": {"avene", "雅漾"},
    "理肤泉": {"la roche", "理肤泉", "laroche"},
    "薇诺娜": {"winona", "薇诺娜"},
    "玉泽": {"yuze", "玉泽", "dr.yu"},
    "适乐肤": {"cerave", "适乐肤", "c乳"},
    "科颜氏": {"kiehl", "科颜氏", "契尔氏"},
    "nars": {"nars", "娜斯"},
    "娜斯": {"nars", "娜斯"},
    "fenty": {"fenty", "芬迪 beauty", "rihanna"},
    "祖玛珑": {"jo malone", "祖玛珑", "祖马龙"},
    "祖马龙": {"jo malone", "祖玛珑", "祖马龙"},
}


def _get_brand_aliases(brand_text: str) -> set:
    if not brand_text:
        return set()
    key = brand_text.strip().lower()
    for canon, aliases in _BRAND_ALIASES.items():
        if key in {a.lower() for a in aliases}:
            return aliases
    return {brand_text.strip()}


def _build_image_analysis(
    file_name: str,
    ocr_info: Optional[OCRInfo],
    results: List[Dict[str, Any]],
    vector_status: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    text = (ocr_info.text if ocr_info else "") or ""
    lowered = text.lower()
    key_info = ocr_info.key_info if ocr_info else {}
    ingredients = key_info.get("ingredients", []) or []
    category = key_info.get("category")
    brand = key_info.get("brand")
    product_keyword = key_info.get("product_keyword")

    ingredient_markers = ["ingredients", "全成分", "成分", "烟酰胺", "玻尿酸", "神经酰胺", "水杨酸", "视黄醇"]
    packaging_markers = ["净含量", "使用方法", "适用肤质", "功效", "spf", "pa", "防晒", "妆前", "粉底", "口红",
                        "爽肤水", "精华水", "化妆水", "精萃水", "精粹水", "美肤水",
                        "面霜", "眼霜", "面膜", "卸妆", "洁面", "乳液"]
    has_structured_clue = bool(
        brand
        or category
        or ingredients
        or any(marker.lower() in lowered for marker in ingredient_markers + packaging_markers)
    )

    if not results and not has_structured_clue:
        image_type = "unclear_image"
    elif len(ingredients) >= 3 or any(marker.lower() in lowered for marker in ingredient_markers):
        image_type = "ingredient_label"
    elif text and (key_info.get("brand") or key_info.get("category") or any(marker.lower() in lowered for marker in packaging_markers)):
        image_type = "packaging_label"
    else:
        image_type = "product_photo"

    candidate_names = [item.get("name") for item in results[:3] if item.get("name")]
    focus_points = _extract_focus_points(image_type, category, ocr_info)

    if image_type == "unclear_image":
        prompt_seed = "这张图没有识别到明确商品信息。请不要编造产品名，只说明当前无法判断具体产品是否适合敏感肌，并提示用户补拍商品正面包装或成分表。"
        summary = "这张图没有识别到品牌、品名或成分文字，暂时不能判断具体商品是否适合敏感肌。"
    elif image_type == "ingredient_label":
        prompt_seed = "请优先分析这张成分表里的核心成分、潜在刺激点、适合肤质和使用注意事项。"
        summary = "这更像是一张成分表或配方信息图，适合做成分安全与适配分析。"
    elif image_type == "packaging_label":
        prompt_seed = "请先识别这款产品的品类、核心功效、适用肤质和使用场景，再给购买建议。"
        summary = "这更像是一张包装或瓶身信息图，适合先识别产品，再判断值不值得买。"
    else:
        prompt_seed = "请结合图片外观与候选商品，判断它大概率是什么产品，并给出适用人群和购买建议。"
        summary = "这更像是一张商品外观图，适合先猜测产品，再给使用建议。"

    if brand and category:
        summary += f" 当前识别到的线索偏向 {brand} 的 {category}。"
    if product_keyword:
        exact_hit = any(product_keyword.lower() in str(name).lower() for name in candidate_names)
        if not exact_hit and candidate_names:
            summary += f" 识别到单品线索「{product_keyword}」，但商品库暂未命中完全同款，下面仅展示同品牌/同品类候选。"

    return {
        "image_type": image_type,
        "brand": brand,
        "category": category,
        "ingredients": ingredients[:8],
        "shade": key_info.get("shade"),
        "spf": key_info.get("spf"),
        "pa": key_info.get("pa"),
        "focus_points": focus_points,
        "candidate_products": candidate_names,
        "product_keyword": product_keyword,
        "vector_status": vector_status or {},
        "prompt_seed": prompt_seed,
        "summary": summary,
        "file_name": file_name,
    }


# ==================== 图片搜索接口 ====================

@router.post("/upload", response_model=ImageSearchResponse)
async def search_by_upload(
    file: UploadFile = File(..., description="查询图片"),
    top_k: int = Form(10, description="返回结果数量"),
    min_score: float = Form(0.5, description="最低相似度"),
    enable_ocr: bool = Form(True, description="是否启用OCR识别")
):
    """
    上传图片进行搜索（支持OCR识别）

    上传一张图片，找到最相似的商品，并识别图片中的文字

    请求格式：multipart/form-data
    - file: 图片文件
    - top_k: 返回结果数量（默认10）
    - min_score: 最低相似度（默认0.5）
    - enable_ocr: 是否启用OCR识别（默认True）

    返回：
    - 相似商品列表
    - OCR识别的文字和关键信息（价格、品牌、型号等）
    """
    try:
        # 1. 读取并验证图片
        contents = await file.read()
        if len(contents) > 10 * 1024 * 1024:  # 10MB
            raise HTTPException(status_code=400, detail="图片过大，最大10MB")

        # 2. OCR识别（异步执行，不阻塞主流程）
        ocr_info = None
        if enable_ocr:
            try:
                ocr_service = get_ocr_service()
                ocr_result = await ocr_service.recognize_image(contents)

                if ocr_result.text:
                    # 提取关键信息
                    key_info = ocr_service.extract_key_info(ocr_result.text)

                    ocr_info = OCRInfo(
                        text=ocr_result.text,
                        confidence=round(ocr_result.confidence, 3),
                        key_info=key_info
                    )
                    logger.info(f"✅ OCR识别成功: 提取到 {len(ocr_result.text)} 字符")
                    if key_info.get("price"):
                        logger.info(f"   💰 识别价格: ¥{key_info['price']}")
                    if key_info.get("brand"):
                        logger.info(f"   🏷️ 识别品牌: {key_info['brand']}")
            except Exception as e:
                logger.warning(f"OCR识别失败（不影响后续流程）: {e}")

        # 3. 图片向量化（CLIP 模型可能未就绪，失败则跳过，不阻塞主流程）
        query_vector = None
        try:
            image_service = get_image_embedding_service()
            query_vector = await image_service.encode_image(contents)
            logger.info(f"✅ 图片编码成功: {file.filename}, 向量维度: {len(query_vector)}")
        except Exception as e:
            logger.warning(f"图片向量化跳过（CLIP 未就绪，转用 OCR/类目召回）: {e}")

        # 4. 向量搜索：优先 CLIP/Milvus；不可用时走本地轻量视觉向量兜底
        milvus_results = []
        local_vector_results = []
        vector_status = {
            "clip_milvus": "未启用",
            "local_visual_vector": "未调用",
            "local_top_score": None,
        }
        if query_vector is not None:
            try:
                image_vector_service = get_image_vector_service()

                vector_results = await image_vector_service.search_similar_images(
                    query_image=contents,
                    top_k=top_k * 2,
                    min_score=min_score * 0.7,
                    filters=None
                )

                if ocr_info and ocr_info.key_info:
                    ocr_brand = ocr_info.key_info.get("brand")
                    ocr_category = ocr_info.key_info.get("category")
                    ocr_keyword = ocr_info.key_info.get("product_keyword")
                    ocr_price = ocr_info.key_info.get("price")
                    ocr_model = ocr_info.key_info.get("model")
                    ocr_shade = ocr_info.key_info.get("shade")
                    raw_text = ocr_info.text or ""
                    brand_aliases = _get_brand_aliases(ocr_brand) if ocr_brand else set()

                    cat_tokens = set()
                    if ocr_category:
                        for part in re.split(r"[\-/|,，;；\s]+", str(ocr_category)):
                            p = part.strip()
                            if len(p) >= 2:
                                cat_tokens.add(p.lower())
                    for marker in ["口红", "气垫", "粉底", "防晒", "精华", "面霜", "眼霜", "面膜",
                                   "卸妆", "洁面", "水", "乳液", "唇膏", "蜜粉", "粉饼", "散粉"]:
                        if marker in raw_text:
                            cat_tokens.add(marker)

                    keyword_tokens = set()
                    if ocr_keyword:
                        for kw in re.split(r"[\s\n\r\t/|,，;；:：]+", str(ocr_keyword)):
                            kw_norm = _normalize_ocr_match_text(kw)
                            if len(kw_norm) >= 2:
                                keyword_tokens.add(kw_norm)
                    for kw in re.split(r"[\s\n\r\t/|,，;；:：]+", raw_text):
                        kw_norm = _normalize_ocr_match_text(kw)
                        if len(kw_norm) >= 3 and any('\u4e00' <= c <= '\u9fff' for c in kw_norm):
                            keyword_tokens.add(kw_norm)
                    if ocr_model:
                        keyword_tokens.add(_normalize_ocr_match_text(ocr_model))
                    if ocr_shade:
                        keyword_tokens.add(_normalize_ocr_match_text(ocr_shade))
                    keyword_tokens = {t for t in keyword_tokens if len(t) >= 2}

                    for item in vector_results:
                        score = float(item.get("similarity", 0) or 0)
                        item_brand = str(item.get("brand") or "").lower()
                        item_name = str(item.get("name") or "").lower()
                        item_cat = str(item.get("category") or "").lower()
                        item_text = f"{item_brand} {item_name} {item_cat}"
                        item_price = item.get("price")
                        boost = 0.0

                        if ocr_brand and brand_aliases:
                            for alias in brand_aliases:
                                a = alias.lower()
                                if a in item_brand or a in item_name:
                                    boost += 12.0
                                    break

                        for ct in cat_tokens:
                            if ct in item_cat or ct in item_name:
                                boost += 6.0
                                break

                        for kw in keyword_tokens:
                            if kw in item_name:
                                boost += 15.0
                            elif kw in item_text:
                                boost += 8.0

                        if ocr_price and item_price:
                            try:
                                price_diff = abs(float(ocr_price) - float(item_price))
                                if price_diff <= 50:
                                    boost += 5.0
                                elif price_diff <= 150:
                                    boost += 2.0
                            except (TypeError, ValueError):
                                pass

                        item["similarity"] = min(100.0, score + boost)

                    vector_results.sort(key=lambda x: x.get("similarity", 0), reverse=True)
                    vector_results = vector_results[:top_k]

                milvus_results = vector_results
                logger.info(f"🔍 向量搜索完成: {len(milvus_results)}个结果")
                vector_status["clip_milvus"] = f"已调用，返回 {len(milvus_results)} 个结果"
            except Exception as e:
                logger.warning(f"向量搜索跳过（转用 OCR/类目召回）: {e}")
                vector_status["clip_milvus"] = "调用失败，已降级"
        else:
            vector_status["clip_milvus"] = "已检查，CLIP 权重未就绪"

        try:
            local_vector_results = await _search_local_visual_vectors(contents, top_k=min(top_k, 5))
            vector_status["local_visual_vector"] = f"已调用，返回 {len(local_vector_results)} 个结果"
            if local_vector_results:
                vector_status["local_top_score"] = local_vector_results[0].get("visual_similarity")
        except Exception as e:
            logger.warning(f"本地视觉向量检索失败，继续使用 OCR/关键词召回: {e}")
            vector_status["local_visual_vector"] = "调用失败，已降级"

        # 5. 构建响应结果
        results = []
        for item in milvus_results:
            results.append({
                "id": item.get("id"),
                "name": item.get("name"),
                "brand": item.get("brand"),
                "category": item.get("category"),
                "price": item.get("price", 0),
                "description": item.get("description", ""),
                "similarity": item.get("similarity", 0),
                 "image_url": item.get("image_url", ""),
                 "detail_url": item.get("detail_url", "")
            })

        # 如果Milvus没有结果，根据OCR信息智能推荐（品牌 > 类目 > 成分关键词 > 兜底）
        local_vector_map = {item.get("id"): item for item in local_vector_results}

        if not results:
            logger.info("向量搜索无结果，转用 OCR/类目智能推荐...")
            key_info = (ocr_info.key_info if ocr_info else {}) or {}
            products = []

            brand = key_info.get("brand")
            category = key_info.get("category")
            product_keyword = key_info.get("product_keyword")
            fallback_similarity = 50.0
            fallback_reason = "OCR线索召回"
            has_structured_ocr = bool(product_keyword or brand or category or key_info.get("ingredients"))

            # 没有 OCR 结构化线索时，允许本地视觉向量兜底；有 OCR 线索时，视觉向量只参与解释/加权，不抢主路由。
            if not has_structured_ocr and local_vector_results and (local_vector_results[0].get("visual_similarity") or 0) >= 70:
                results.extend(local_vector_results[:min(top_k, 5)])
                analysis = _build_image_analysis(file.filename or "uploaded-image", ocr_info, results, vector_status)
                return ImageSearchResponse(
                    query_id=f"query_{datetime.now().timestamp()}",
                    results=results,
                    total=len(results),
                    ocr_info=ocr_info,
                    analysis=analysis
                )

            if product_keyword:
                logger.info(f"根据识别单品关键词 '{product_keyword}' 推荐商品...")
                products = await execute_query(
                    """
                    SELECT *
                    FROM products
                    WHERE name ILIKE $1 OR description ILIKE $1
                    ORDER BY
                        CASE
                            WHEN detail_url IS NOT NULL AND detail_url <> '' THEN 0
                            ELSE 1
                        END,
                        CASE
                            WHEN image_url IS NOT NULL AND image_url <> '' THEN 0
                            ELSE 1
                        END,
                        id DESC
                    LIMIT $2
                    """,
                    f"%{product_keyword}%", min(top_k, 5), fetch="all"
                )
                if products:
                    fallback_similarity = 92.0
                    fallback_reason = f"命中图片文字里的单品关键词「{product_keyword}」"

            if not products and brand:
                logger.info(f"根据识别品牌 '{brand}' 推荐商品...")
                products = await execute_query(
                    """
                    SELECT *
                    FROM products
                    WHERE brand ILIKE $1
                    ORDER BY
                        CASE
                            WHEN detail_url IS NOT NULL AND detail_url <> '' THEN 0
                            ELSE 1
                        END,
                        CASE
                            WHEN image_url IS NOT NULL AND image_url <> '' THEN 0
                            ELSE 1
                        END,
                        id DESC
                    LIMIT $2
                    """,
                    f"%{brand}%", min(top_k, 5), fetch="all"
                )
                if products:
                    fallback_similarity = 72.0
                    fallback_reason = f"未命中完全同款，按识别品牌「{brand}」召回同品牌候选"

            if not products and category:
                logger.info(f"根据识别类目 '{category}' 推荐商品...")
                products = await execute_query(
                    "SELECT * FROM products WHERE category ILIKE $1 OR specifications->>'subcategory' ILIKE $1 ORDER BY price ASC LIMIT $2",
                    f"%{category}%", min(top_k, 5), fetch="all"
                )
                if products:
                    fallback_similarity = 64.0
                    fallback_reason = f"按识别品类「{category}」召回同类候选"

            if not products:
                ingredients = key_info.get("ingredients") or []
                if ingredients:
                    term = ingredients[0]
                    logger.info(f"根据识别成分 '{term}' 推荐商品...")
                    products = await execute_query(
                        "SELECT * FROM products WHERE name ILIKE $1 OR description ILIKE $1 ORDER BY price ASC LIMIT $2",
                        f"%{term}%", min(top_k, 5), fetch="all"
                    )
                    if products:
                        fallback_similarity = 68.0
                        fallback_reason = f"按识别成分「{term}」召回候选"

            if not products:
                logger.info("无 OCR/向量线索，不返回随机热门商品，避免误判。")

            products = _rank_ocr_fallback_products(products, key_info, local_vector_map)

            for product in products:
                local_match = local_vector_map.get(product["id"])
                visual_score = local_match.get("visual_similarity") if local_match else None
                if visual_score is None:
                    visual_score = _score_product_visual_similarity(contents, product)
                final_similarity = fallback_similarity
                final_reason = fallback_reason
                if visual_score is not None:
                    if has_structured_ocr:
                        # OCR/品牌/品类是更强线索；本地视觉向量只负责补充加分，避免生活图或截图把正确品牌候选反向拉低。
                        blended_score = round(fallback_similarity * 0.86 + visual_score * 0.14, 2)
                        final_similarity = max(fallback_similarity, blended_score)
                        final_reason = f"{fallback_reason}；本地视觉向量相似度 {visual_score}%（辅助校验，不反向降权）"
                    else:
                        final_similarity = round(fallback_similarity * 0.78 + visual_score * 0.22, 2)
                        final_reason = f"{fallback_reason}；本地视觉向量相似度 {visual_score}%"
                results.append({
                    "id": product["id"],
                    "name": product["name"],
                    "brand": product["brand"],
                    "category": product["category"],
                    "price": _safe_price(product.get("price")),
                    "description": product["description"],
                    "similarity": final_similarity,
                    "visual_similarity": visual_score,
                    "match_reason": final_reason,
                    "image_url": product.get("image_url", f"/images/{product['id']}.jpg"),
                    "detail_url": product.get("detail_url") or ""
                })

        analysis = _build_image_analysis(file.filename or "uploaded-image", ocr_info, results, vector_status)

        return ImageSearchResponse(
            query_id=f"query_{datetime.now().timestamp()}",
            results=results,
            total=len(results),
            ocr_info=ocr_info,
            analysis=analysis
        )

    except Exception as e:
        logger.error(f"图片搜索错误: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/similarity")
async def compute_similarity(request: ImageSimilarityRequest):
    """
    计算图文相似度

    给定图片URL和文本描述，计算相似度分数
    """
    try:
        image_vector_service = get_image_vector_service()

        # TODO: 从URL下载图片
        # 目前先返回提示信息
        return {
            "image_url": request.image_url,
            "text": request.text,
            "similarity": 0.75,
            "message": "图片URL功能待实现，请使用上传接口"
        }
    except Exception as e:
        logger.error(f"相似度计算错误: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class TextSearchRequest(BaseModel):
    """文本搜图请求"""
    text: str = Field(..., description="文本描述")
    top_k: int = Field(10, description="返回结果数量")
    min_score: float = Field(0.5, description="最低相似度")
    category: Optional[str] = Field(None, description="类别过滤")
    brand: Optional[str] = Field(None, description="品牌过滤")


@router.post("/search-by-text")
async def search_by_text(request: TextSearchRequest):
    """
    文本搜图

    用文本描述搜索相似的图片商品

    Args:
        request: 搜索请求

    Returns:
        匹配的商品列表
    """
    try:
        image_vector_service = get_image_vector_service()

        # 构建过滤条件
        filters = {}
        if request.category:
            filters["category"] = request.category
        if request.brand:
            filters["brand"] = request.brand

        # 执行搜索
        results = await image_vector_service.search_by_text(
            text_query=request.text,
            top_k=request.top_k,
            min_score=request.min_score,
            filters=filters if filters else None
        )

        return {
            "query": request.text,
            "results": results,
            "total": len(results)
        }

    except Exception as e:
        logger.error(f"文本搜图错误: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/products/{product_id}/vectorize")
async def vectorize_product_image(product_id: int):
    """
    为商品图片生成向量（管理接口）

    用于批量处理已有商品图片
    """
    try:
        # 获取商品信息
        product = await execute_query(
            "SELECT * FROM products WHERE id = $1",
            product_id,
            fetch="one"
        )

        if not product:
            raise HTTPException(status_code=404, detail="商品不存在")

        # 检查是否已有图片
        # TODO: 从商品获取图片URL并下载
        # 这里需要根据实际的图片存储方式来获取图片
        return {
            "product_id": product_id,
            "message": "图片获取功能待实现（需要配置图片存储）",
            "product": {
                "id": product["id"],
                "name": product["name"],
                "image_url": product.get("image_url")
            }
        }

    except Exception as e:
        logger.error(f"商品向量化错误: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/index")
async def index_product_image(
    product_id: int = Form(..., description="商品ID"),
    file: UploadFile = File(..., description="商品图片")
):
    """
    为商品建立图片向量索引

    上传商品图片并建立向量索引，用于以图搜图

    Args:
        product_id: 商品ID
        file: 图片文件

    Returns:
        索引结果
    """
    try:
        # 读取图片
        contents = await file.read()
        if len(contents) > 10 * 1024 * 1024:  # 10MB
            raise HTTPException(status_code=400, detail="图片过大，最大10MB")

        # 索引图片
        image_vector_service = get_image_vector_service()
        success = await image_vector_service.index_product_image(
            product_id=product_id,
            image_data=contents
        )

        if success:
            return {
                "success": True,
                "message": "图片索引成功",
                "product_id": product_id,
                "filename": file.filename
            }
        else:
            raise HTTPException(status_code=500, detail="图片索引失败")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"图片索引错误: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/batch/vectorize")
async def batch_vectorize(
    product_ids: List[int],
    overwrite: bool = False
):
    """
    批量向量化商品图片（同步执行）

    Args:
        product_ids: 商品ID列表
        overwrite: 是否覆盖已有向量

    Returns:
        处理结果统计
    """
    image_vector_service = get_image_vector_service()

    results = {
        "total": len(product_ids),
        "success": 0,
        "failed": 0,
        "skipped": 0,
        "details": []
    }

    for product_id in product_ids:
        try:
            # 检查是否已存在向量
            if not overwrite:
                is_indexed = await image_vector_service.is_product_indexed(product_id)
                if is_indexed:
                    results["skipped"] += 1
                    results["details"].append({
                        "product_id": product_id,
                        "status": "skipped",
                        "message": "已存在向量，跳过"
                    })
                    continue

            # 获取商品信息
            product = await execute_query(
                "SELECT * FROM products WHERE id = $1",
                product_id,
                fetch="one"
            )

            if not product:
                results["failed"] += 1
                results["details"].append({
                    "product_id": product_id,
                    "status": "failed",
                    "error": "商品不存在"
                })
                continue

            # TODO: 从商品获取图片数据
            # 这里需要根据实际的图片存储方式来获取图片
            # 如果是 URL，需要下载；如果是本地路径，需要读取文件

            results["skipped"] += 1
            results["details"].append({
                "product_id": product_id,
                "status": "skipped",
                "message": "图片获取功能待实现（需要配置图片存储）"
            })

        except Exception as e:
            results["failed"] += 1
            results["details"].append({
                "product_id": product_id,
                "status": "failed",
                "error": str(e)
            })

    return results


@router.post("/batch/index")
async def batch_index_products(
    product_ids: Optional[List[int]] = None,
    batch_size: int = 50,
    overwrite: bool = False,
    async_mode: bool = True
):
    """
    批量建立商品图片索引（支持异步）

    Args:
        product_ids: 商品ID列表（为空则处理所有商品）
        batch_size: 每批处理数量
        overwrite: 是否覆盖已有索引
        async_mode: 是否异步执行（默认True）

    Returns:
        任务信息或处理结果
    """
    try:
        if async_mode:
            # 异步模式：提交到Celery任务队列
            from app.tasks.worker import celery_app

            task = celery_app.send_task(
                'tasks.image.batch_index',
                kwargs={
                    'product_ids': product_ids,
                    'batch_size': batch_size,
                    'overwrite': overwrite
                }
            )

            return {
                "success": True,
                "message": "批量索引任务已提交",
                "task_id": task.id,
                "task_status": "PENDING",
                "check_url": f"/api/v1/tasks/status/{task.id}"
            }
        else:
            # 同步模式：直接执行
            from app.tasks.image_tasks import batch_index_product_images, index_all_products

            if product_ids is None:
                result = index_all_products(batch_size=batch_size, overwrite=overwrite)
            else:
                result = batch_index_product_images(product_ids, overwrite=overwrite)

            return {
                "success": True,
                "message": "批量索引完成",
                "result": result
            }

    except Exception as e:
        logger.error(f"批量索引提交失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 测试接口 ====================

@router.post("/ocr")
async def recognize_text(
    file: UploadFile = File(..., description="要识别文字的图片"),
    return_details: bool = Form(False, description="是否返回文字位置信息")
):
    """
    OCR文字识别接口

    识别图片中的文字内容

    请求格式：multipart/form-data
    - file: 图片文件
    - return_details: 是否返回文字位置信息（默认False）

    返回：
    - text: 识别出的完整文字
    - confidence: 平均置信度
    - key_info: 提取的关键信息（价格、品牌、型号、参数）
    - boxes: 文字框位置（仅当return_details=True时）
    """
    try:
        contents = await file.read()
        if len(contents) > 10 * 1024 * 1024:  # 10MB
            raise HTTPException(status_code=400, detail="图片过大，最大10MB")

        ocr_service = get_ocr_service()
        ocr_result = await ocr_service.recognize_image(contents, return_details=return_details)

        # 提取关键信息
        key_info = ocr_service.extract_key_info(ocr_result.text)

        return {
            "success": bool(ocr_result.text),
            "text": ocr_result.text,
            "confidence": round(ocr_result.confidence, 3),
            "key_info": key_info,
            "boxes": ocr_result.boxes if return_details else None,
            "text_length": len(ocr_result.text),
            "message": "识别成功" if ocr_result.text else "未识别到文字"
        }

    except Exception as e:
        logger.error(f"OCR识别错误: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/test/encode")
async def test_encode(file: UploadFile = File(...)):
    """
    测试图片编码

    返回图片信息和向量维度
    """
    try:
        contents = await file.read()

        image_service = get_image_embedding_service()

        # 获取图片信息
        from PIL import Image
        img = Image.open(io.BytesIO(contents))
        info = {
            "format": img.format,
            "mode": img.mode,
            "size": img.size,
            "file_size": len(contents)
        }

        # 编码图片
        vector = await image_service.encode_image(contents)

        return {
            "filename": file.filename,
            "image_info": info,
            "vector_dimension": len(vector),
            "vector_sample": vector[:5],  # 前5个值
            "message": "图片编码成功"
        }

    except Exception as e:
        logger.error(f"测试编码错误: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/test/models")
async def list_models():
    """
    列出可用的CLIP模型
    """
    from app.services.image_embedding import ImageEmbeddingService

    return {
        "available_models": ImageEmbeddingService.MODELS,
        "current_model": "ViT-B-32",
        "device": get_image_embedding_service().device
    }


@router.get("/stats")
async def get_vector_stats():
    """
    获取图片向量统计信息

    Returns:
        Collection统计信息
    """
    try:
        image_vector_service = get_image_vector_service()
        stats = await image_vector_service.get_collection_stats()

        # 添加Milvus连接信息
        from app.config import settings
        stats["milvus_host"] = settings.MILVUS_HOST
        stats["milvus_port"] = settings.MILVUS_PORT

        return stats

    except Exception as e:
        logger.error(f"获取统计信息错误: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/admin/batch-index")
async def admin_batch_index(
    limit: int = 100,
    overwrite: bool = False
):
    """
    管理员接口：批量建立图片索引

    Args:
        limit: 限制数量（默认100）
        overwrite: 是否覆盖已有索引

    Returns:
        索引结果
    """
    try:
        from app.services.image_vector import get_image_vector_service
        from app.database.postgres import execute_query
        from PIL import Image, ImageDraw, ImageFont
        import io
        import colorsys
        import random

        vector_service = get_image_vector_service()

        # 获取商品列表
        products = await execute_query(
            "SELECT * FROM products ORDER BY id LIMIT $1",
            limit,
            fetch="all"
        )

        results = {
            "total": len(products),
            "success": 0,
            "failed": 0,
            "skipped": 0
        }

        for product in products:
            try:
                product_id = product["id"]

                # 检查是否已索引
                if not overwrite:
                    is_indexed = await vector_service.is_product_indexed(product_id)
                    if is_indexed:
                        results["skipped"] += 1
                        continue

                # 生成占位图片
                random.seed(product_id)
                hue = random.randint(0, 360)
                saturation = random.randint(20, 40)
                lightness = random.randint(75, 90)

                rgb = colorsys.hls_to_rgb(hue/360, lightness/100, saturation/100)
                bg_color = tuple(int(c * 255) for c in rgb)

                img = Image.new('RGB', (224, 224), bg_color)
                draw = ImageDraw.Draw(img)

                # 绘制品牌首字母
                brand_initial = product.get("brand", "P")[0]
                try:
                    font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 80)
                except:
                    font = ImageFont.load_default()

                bbox = draw.textbbox((0, 0), brand_initial, font=font)
                x = (224 - (bbox[2] - bbox[0])) // 2
                y = (224 - (bbox[3] - bbox[1])) // 2 - 20
                draw.text((x, y), brand_initial, fill=(100, 100, 100), font=font)

                # 绘制商品名
                name = product.get("name", "")[:8]
                try:
                    font_small = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 16)
                except:
                    font_small = ImageFont.load_default()

                bbox = draw.textbbox((0, 0), name, font=font_small)
                x = (224 - (bbox[2] - bbox[0])) // 2
                draw.text((x, 180), name, fill=(100, 100, 100), font=font_small)

                # 转为bytes
                output = io.BytesIO()
                img.save(output, format='JPEG', quality=85)
                image_data = output.getvalue()

                # 建立索引
                success = await vector_service.index_product_image(
                    product_id=product_id,
                    image_data=image_data
                )

                if success:
                    results["success"] += 1
                else:
                    results["failed"] += 1

            except Exception as e:
                logger.error(f"索引失败 {product.get('name')}: {e}")
                results["failed"] += 1

        return results

    except Exception as e:
        logger.error(f"批量索引错误: {e}")
        raise HTTPException(status_code=500, detail=str(e))
