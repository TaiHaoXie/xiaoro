"""一期Golden Cases - 强断言回归测试
检查维度：
- intent: 场景意图必须匹配
- products_count: 返回商品数量阈值
- brand/category: 必须包含的品牌/品类关键词
- forbidden_phrases: 禁止出现的死循环追问/无结果话术
- forbidden_chars: 禁止出现的markdown表格/emoji
- min_length: 回答最短字数
"""
import json, os, sys, urllib.request, urllib.error, uuid, re

sys.path.insert(0, os.getcwd())
BASE = "http://127.0.0.1:8000"
RUN_ID = uuid.uuid4().hex[:8]

FORBIDDEN_PHRASES = [
    "补充一下",
    "补充信息",
    "告诉我你的肤质",
    "请告诉我你的",
    "你可以补充",
    "能告诉我更多",
    "需要你补充",
    "暂时还没理解",
    "槽位",
    "先说说你的",
    "简单结论：预算敏感选",
    "追求更全功效可以根据肤质和具体诉求再细看",
    "预算收紧",
    "低价优先排",
]

FORBIDDEN_CHARS_PATTERNS = [
    (r"\|[-:]+\|", "markdown表格"),
    (r"[\U0001F300-\U0001FAFF\U00002600-\U000027BF🔗]", "emoji"),
]


def post_chat_raw(msg, sid, image_context=None, history=None):
    """返回 (events_list, error)；events是SSE所有解析后事件dict的列表"""
    payload = {"message": msg, "session_id": sid, "stream": True}
    if image_context:
        payload["image_context"] = image_context
    if history:
        payload["conversation_history"] = history
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{BASE}/api/v1/chat/stream",
        data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    events = []
    error = None
    content_parts = []
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            text = resp.read().decode()
        for line in text.split("\n"):
            if line.startswith("data:"):
                try:
                    d = json.loads(line[5:].strip())
                except Exception:
                    continue
                if not isinstance(d, dict):
                    continue
                events.append(d)
                if d.get("content") is not None:
                    content_parts.append(d["content"])
                if d.get("error") or d.get("code") == "AGENT_ERROR":
                    error = d.get("message") or d.get("error")
    except urllib.error.HTTPError as e:
        error = f"HTTP {e.code}: {e.read().decode()[:300]}"
    except Exception as e:
        error = str(e)
    return {
        "events": events,
        "content": "".join(content_parts),
        "error": error,
        "intent": next((e for e in events if "intent" in e and "v2" in e), None),
        "products": next((e.get("products", []) for e in events if isinstance(e.get("products"), list)), []),
        "pitfalls": next((e.get("pitfalls", []) for e in events if isinstance(e.get("pitfalls"), list)), []),
        "answer_contract": next((e.get("answer_contract") for e in events if isinstance(e.get("answer_contract"), dict)), None),
    }


def post_image_search(path):
    boundary = "----TestBoundary" + uuid.uuid4().hex
    files = []
    files.append(f"--{boundary}\r\n".encode())
    files.append(f'Content-Disposition: form-data; name="file"; filename="{os.path.basename(path)}"\r\n'.encode())
    files.append(b"Content-Type: image/png\r\n\r\n")
    with open(path, "rb") as f:
        files.append(f.read())
    files.append(f"\r\n--{boundary}\r\n".encode())
    files.append(b'Content-Disposition: form-data; name="min_score"\r\n\r\n0.3\r\n')
    files.append(f"--{boundary}--\r\n".encode())
    body = b"".join(files)
    req = urllib.request.Request(
        f"{BASE}/api/v1/image-search/upload",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


def build_image_context(sr):
    results = sr.get("results", [])[:3]
    top1 = results[0] if results else {}
    return {
        "results": [{k: r.get(k) for k in ("id", "brand", "name", "category", "price", "similarity", "image_url") if k in r} for r in results],
        "ocr_info": sr.get("ocr_info"),
        "analysis": sr.get("analysis"),
        "policy": "image_recommendation",
        "brand": top1.get("brand"),
        "category": top1.get("category"),
    }


def check_content(content, case):
    """返回 (ok, failures)；failures是问题描述字符串列表"""
    failures = []
    if case.get("error"):
        failures.append(f"请求错误: {case['error']}")
        return False, failures

    text = content or ""
    min_len = case.get("min_length", 80)
    if len(text) < min_len:
        failures.append(f"回答过短({len(text)}字)<{min_len}")

    # 禁止短语
    for phrase in FORBIDDEN_PHRASES:
        if phrase in text:
            failures.append(f"出现死循环/追问引导语:「{phrase}」")
            break

    # 禁止字符
    for pat, label in FORBIDDEN_CHARS_PATTERNS:
        if re.search(pat, text):
            failures.append(f"出现{label}")

    # 必须包含关键词
    for kw in case.get("must_contain", []):
        if kw not in text:
            failures.append(f"缺少关键词「{kw}」")

    for kw in case.get("must_not_contain", []):
        if kw in text:
            failures.append(f"不应出现关键词「{kw}」")

    # 必须包含的品牌
    for brand in case.get("must_brands", []):
        if brand not in text:
            failures.append(f"缺少品牌「{brand}」")

    # 必须包含的品类
    for cat in case.get("must_categories", []):
        if cat not in text:
            failures.append(f"缺少品类「{cat}」")

    return len(failures) == 0, failures


def check_chat_result(r, case):
    failures = []
    if r["error"]:
        failures.append(f"请求错误: {r['error']}")
        return False, failures, None, 0

    intent_name = r["intent"].get("intent") if r["intent"] else None
    expected_intent = case.get("expect_intent")
    if expected_intent and intent_name != expected_intent:
        failures.append(f"intent={intent_name} 期望={expected_intent}")
    expected_followup_type = case.get("expect_followup_type")
    if expected_followup_type:
        entities = r["intent"].get("entities") if r["intent"] else {}
        actual_followup_type = (entities or {}).get("followup_type")
        if actual_followup_type != expected_followup_type:
            failures.append(f"followup_type={actual_followup_type} 期望={expected_followup_type}")

    products = r["products"]
    min_products = case.get("min_products", 0)
    if min_products and len(products) < min_products:
        failures.append(f"商品数={len(products)} 期望≥{min_products}")
    max_products = case.get("max_products")
    if max_products is not None and len(products) > max_products:
        failures.append(f"商品数={len(products)} 期望≤{max_products}")

    min_pitfalls = case.get("min_pitfalls", 0)
    if min_pitfalls and len(r.get("pitfalls") or []) < min_pitfalls:
        failures.append(f"避坑提示数={len(r.get('pitfalls') or [])} 期望≥{min_pitfalls}")

    price_min = case.get("product_price_min")
    price_max = case.get("product_price_max")
    if products and (price_min is not None or price_max is not None):
        checked = products[:case.get("price_check_count", min(3, len(products)))]
        for p in checked:
            price = float(p.get("price") or 0)
            if price_min is not None and price < price_min:
                failures.append(f"商品价格过低: {price} < {price_min} ({p.get('brand')} {p.get('name')})")
            if price_max is not None and price > price_max:
                failures.append(f"商品价格过高: {price} > {price_max} ({p.get('brand')} {p.get('name')})")

    # 必须包含品牌（在products数据里）
    p_brands = set((p.get("brand") or "") for p in products)
    for b in case.get("must_product_brands", []):
        if not any(b in pb for pb in p_brands):
            failures.append(f"商品列表缺品牌「{b}」 brands={list(p_brands)[:5]}")

    # 必须包含品类（在products数据里）
    p_cats = set((p.get("category") or "") for p in products)
    for c in case.get("must_product_categories", []):
        if not any(c in pc for pc in p_cats):
            failures.append(f"商品列表缺品类「{c}」 cats={list(p_cats)[:5]}")

    brand_prefix = case.get("product_brand_prefix", [])
    if brand_prefix:
        checked = products[:len(brand_prefix)]
        if len(checked) < len(brand_prefix):
            failures.append(f"商品前缀数量不足: {len(checked)} < {len(brand_prefix)}")
        else:
            for idx, expected in enumerate(brand_prefix):
                actual = checked[idx].get("brand") or ""
                if expected not in actual:
                    failures.append(f"第{idx + 1}个商品品牌={actual} 期望包含={expected}")

    contract = r.get("answer_contract") or {}
    if case.get("require_answer_contract") and not contract:
        failures.append("缺少answer_contract")
    if contract:
        expected_contract_mode = case.get("contract_answer_mode")
        if expected_contract_mode and contract.get("answer_mode") != expected_contract_mode:
            failures.append(f"answer_contract.answer_mode={contract.get('answer_mode')} 期望={expected_contract_mode}")
        if case.get("contract_require_display_sections", True):
            sections = contract.get("display_sections") or []
            if "answer" not in sections:
                failures.append("answer_contract.display_sections缺少answer")
            if products and "products" not in sections:
                failures.append("answer_contract.display_sections缺少products")
            if r.get("pitfalls") and "pitfalls" not in sections:
                failures.append("answer_contract.display_sections缺少pitfalls")
            if "decision_process" not in sections:
                failures.append("answer_contract.display_sections缺少decision_process（V2合同必须声明思考链路）")
        if case.get("contract_require_product_order", True) and products:
            contract_ids = [str(x) for x in (contract.get("primary_product_ids") or [])]
            product_ids = [str(p.get("id")) for p in products if p.get("id") is not None]
            if product_ids and contract_ids[:len(product_ids)] != product_ids[:len(contract_ids)]:
                failures.append(f"answer_contract商品顺序不一致: contract={contract_ids[:5]} products={product_ids[:5]}")
        for expected in case.get("contract_secondary_followup_types", []):
            secondary = contract.get("secondary_intents") or []
            if not any(item.get("followup_type") == expected for item in secondary if isinstance(item, dict)):
                failures.append(f"answer_contract缺少secondary followup_type={expected}")

    text = r["content"]
    ok, content_failures = check_content(text, case)
    failures.extend(content_failures)

    order_count = case.get("products_follow_text_order_count", 0)
    if products and order_count:
        checked = products[:order_count]
        positions = []
        for p in checked:
            candidates = _product_text_candidates(p)
            pos = min((text.find(c) for c in candidates if c and text.find(c) >= 0), default=-1)
            if pos < 0:
                failures.append(f"正文未出现商品: {p.get('brand')} {p.get('name')}")
            positions.append(pos)
        valid_positions = [p for p in positions if p >= 0]
        if len(valid_positions) == len(positions) and valid_positions != sorted(valid_positions):
            names = [f"{p.get('brand')}/{(p.get('name') or '')[:12]}" for p in checked]
            failures.append(f"商品事件顺序与正文顺序不一致: positions={positions}, products={names}")
    return len(failures) == 0, failures, intent_name, len(products)


def check_frontend_static(case):
    failures = []
    with open(case["file"], "r", encoding="utf-8") as f:
        content = f.read()

    for kw in case.get("must_contain_static", []):
        if kw not in content:
            failures.append(f"前端文件缺少静态片段「{kw}」")

    for kw in case.get("forbid_static", []):
        if kw in content:
            failures.append(f"前端文件不应包含静态片段「{kw}」")

    for pattern, label in case.get("must_match_static", []):
        if not re.search(pattern, content, re.S):
            failures.append(f"前端文件缺少规则: {label}")

    return len(failures) == 0, failures


def _compact_product_name(product):
    raw = str(product.get("display_name") or product.get("name") or "").strip()
    brand = str(product.get("brand") or "").strip()
    for word in ["现货", "官方", "旗舰", "同款", "推荐", "护肤品", "化妆品", "礼盒", "套装", "生日礼物", "送女友", "送老婆", "正品"]:
        raw = raw.replace(word, "")
    raw = re.sub(r"\s+", " ", raw).strip()
    if brand and raw.startswith(brand):
        raw = raw[len(brand):].strip()
    for word in ["防晒霜", "防晒乳", "防晒", "精华液", "精华乳", "精华", "面霜", "乳液", "面膜", "洁面", "眼霜", "爽肤水", "化妆水"]:
        idx = raw.find(word)
        if idx >= 0:
            raw = raw[:idx + len(word)]
            break
    final_name = f"{brand if brand and brand not in raw else ''}{raw}".strip()
    return final_name[:22] if len(final_name) > 22 else final_name


def _product_text_candidates(product):
    raw_values = [
        product.get("name") or "",
        product.get("display_name") or "",
        _compact_product_name(product),
        " ".join([product.get("brand") or "", product.get("name") or ""]).strip(),
    ]
    candidates = []
    for raw in raw_values:
        text = str(raw).strip()
        if not text:
            continue
        candidates.append(text)
        compact = re.sub(r"\s+", "", text)
        if compact and compact != text:
            candidates.append(compact)
        for splitter in ["（", "(", "：", ":", "，", ",", "、", " "]:
            head = text.split(splitter)[0].strip()
            if len(head) >= 4:
                candidates.append(head)
        if len(text) >= 8:
            candidates.append(text[:12])
        if len(compact) >= 8:
            candidates.append(compact[:12])
    seen = set()
    deduped = []
    for item in candidates:
        if item and item not in seen:
            deduped.append(item)
            seen.add(item)
    return deduped


# ====================== CASES ======================
CASES = [
    # ---- 推荐场景 ----
    {
        "id": 1, "type": "chat", "name": "300以内面霜",
        "msg": "300以内面霜",
        "expect_intent": "recommendation",
        "min_products": 3,
        "must_categories": ["面霜"],
        "must_product_categories": ["面霜"],
        "must_contain": ["综合建议", "薇诺娜特护霜更偏敏感泛红"],
        "must_not_contain": ["想追求更好的使用感", "适合特定场景按需选", "按需选", "优先看薇诺娜"],
        "min_length": 150,
    },
    {
        "id": 2, "type": "chat", "name": "敏感肌面霜",
        "msg": "敏感肌面霜",
        "expect_intent": "recommendation",
        "min_products": 2,
        "must_categories": ["面霜"],
        "must_contain": ["综合建议", "敏感"],
        "min_length": 150,
    },
    {
        "id": 3, "type": "chat", "name": "200以内防晒",
        "msg": "200以内防晒",
        "expect_intent": "recommendation",
        "min_products": 2,
        "must_categories": ["防晒"],
        "must_product_categories": ["防晒"],
        "must_contain": ["综合建议"],
        "min_length": 150,
    },
    {
        "id": 30, "type": "chat", "name": "400以内防晒",
        "msg": "400以内防晒推荐",
        "expect_intent": "recommendation",
        "min_products": 3,
        "must_categories": ["防晒"],
        "must_product_categories": ["防晒"],
        "product_price_min": 120,
        "product_price_max": 400,
        "price_check_count": 3,
        "must_contain": ["综合建议"],
        "must_not_contain": ["修护方式和肤感厚薄", "抗老淡纹", "提亮淡斑", "资生堂蓝胖子防晒霜更偏敏感肌"],
        "min_length": 150,
    },
    {
        "id": 4, "type": "chat", "name": "油皮精华",
        "msg": "油皮精华",
        "expect_intent": "recommendation",
        "min_products": 2,
        "must_categories": ["精华"],
        "must_product_categories": ["精华"],
        "must_contain": ["综合建议"],
        "min_length": 150,
    },
    {
        "id": 21, "type": "chat", "name": "干敏肌1000左右抗初老精华",
        "msg": "干敏肌想要抗初老精华，预算 1000 左右",
        "expect_intent": "recommendation",
        "min_products": 3,
        "must_categories": ["精华"],
        "must_product_categories": ["精华"],
        "must_contain": ["综合建议"],
        "product_price_min": 800,
        "product_price_max": 1200,
        "price_check_count": 3,
        "products_follow_text_order_count": 3,
        "min_pitfalls": 2,
        "min_length": 180,
    },
    {
        "id": 5, "type": "chat", "name": "眼霜推荐",
        "msg": "眼霜推荐",
        "expect_intent": "recommendation",
        "min_products": 2,
        "must_categories": ["眼霜"],
        "must_product_categories": ["眼霜"],
        "must_contain": ["综合建议"],
        "min_length": 150,
    },
    {
        "id": 6, "type": "chat", "name": "氨基酸洗面奶",
        "msg": "氨基酸洗面奶",
        "expect_intent": "recommendation",
        "min_products": 2,
        "must_categories": ["洁面", "洗面奶"],
        "must_contain": ["综合建议"],
        "min_length": 100,
    },
    # ---- 单品判断 ----
    {
        "id": 7, "type": "chat", "name": "理肤泉B5敏感肌能用吗",
        "msg": "理肤泉B5敏感肌能用吗",
        "expect_intent": "judgement",
        "min_products": 1,
        "must_brands": ["理肤泉"],
        "must_contain": ["能不能用", "适合谁", "风险点"],
        "must_categories": ["B5"],
        "min_length": 100,
    },
    {
        "id": 8, "type": "chat", "name": "珀莱雅防晒油皮可以吗",
        "msg": "珀莱雅防晒油皮可以用吗",
        "expect_intent": "judgement",
        "min_products": 1,
        "must_brands": ["珀莱雅"],
        "must_categories": ["防晒"],
        "must_contain": ["能不能用", "适合谁", "风险点"],
        "min_length": 100,
    },
    {
        "id": 9, "type": "chat", "name": "小棕瓶适合什么年龄",
        "msg": "小棕瓶适合什么年龄",
        "expect_intent": "judgement",
        "min_products": 1,
        "must_brands": ["雅诗兰黛"],
        "must_contain": ["小棕瓶", "能不能用"],
        "min_length": 80,
    },
    # ---- 对比场景 ----
    {
        "id": 10, "type": "chat", "name": "理肤泉B5和玉泽哪个好",
        "msg": "理肤泉B5和玉泽哪个好",
        "expect_intent": "compare",
        "min_products": 2,
        "must_brands": ["理肤泉", "玉泽"],
        "must_product_brands": ["理肤泉", "玉泽"],
        "must_contain": ["对比结论", "分项判断", "怎么选", "风险点", "理肤泉", "玉泽"],
        "min_length": 100,
    },
    {
        "id": 11, "type": "chat", "name": "兰蔻小白管和安热沙对比",
        "msg": "兰蔻小白管和安热沙对比",
        "expect_intent": "compare",
        "min_products": 2,
        "max_products": 2,
        "product_brand_prefix": ["兰蔻", "安热沙"],
        "must_product_brands": ["兰蔻", "安热沙"],
        "must_product_categories": ["防晒"],
        "must_contain": ["对比结论", "分项判断", "怎么选", "风险点", "兰蔻", "安热沙"],
        "min_length": 100,
    },
    {
        "id": 33, "type": "chat", "name": "小棕瓶小黑瓶对比卡片收敛",
        "msg": "小棕瓶和小黑瓶怎么选",
        "expect_intent": "compare",
        "min_products": 2,
        "max_products": 2,
        "product_brand_prefix": ["雅诗兰黛", "兰蔻"],
        "must_product_brands": ["雅诗兰黛", "兰蔻"],
        "must_product_categories": ["精华"],
        "must_contain": ["对比结论", "分项判断", "怎么选", "小棕瓶", "小黑瓶"],
        "min_length": 100,
    },
    # ---- 识图 ----
    {
        "id": 12, "type": "image_search", "name": "识图-珀莱雅防晒",
        "image": "app/static/images/products/tmall_v3_768314295559.png",
        "expect_top1_brand": "珀莱雅",
        "expect_top1_category": "防晒",
        "min_top1_sim": 70,
    },
    {
        "id": 13, "type": "image_search", "name": "识图-OLAY小白瓶",
        "image": "app/static/images/products/jd_v3_100241283549.png",
        "expect_top1_brand_any": ["玉兰油", "OLAY"],
        "expect_top1_category": "精华",
        "min_top1_sim": 70,
    },
    # ---- 识图+追问平替 ----
    {
        "id": 14, "type": "image_chat", "name": "识图平替-珀莱雅防晒",
        "image": "app/static/images/products/tmall_v3_768314295559.png",
        "msg": "有没有平替",
        "expect_intent": "recommendation",
        "anchor_must_contain": ["看了你发的图片", "图里"],
        "must_categories": ["防晒"],
        "must_product_categories": ["防晒"],
        "must_contain": ["综合建议"],
        "min_length": 150,
    },
    {
        "id": 15, "type": "image_chat", "name": "识图推荐-珀莱雅其他款",
        "image": "app/static/images/products/tmall_v3_768314295559.png",
        "msg": "还有什么推荐",
        "expect_intent": "recommendation",
        "must_categories": ["防晒"],
        "min_length": 100,
    },
    # ---- 图文联合 ----
    {
        "id": 16, "type": "image_chat", "name": "图文-珀莱雅+油皮",
        "image": "app/static/images/products/tmall_v3_768314295559.png",
        "msg": "油皮能用吗",
        "expect_intent": "judgement",
        "anchor_must_contain": ["看了你发的图片", "图里", "匹配度最高"],
        "must_categories": ["防晒"],
        "must_contain": ["能不能用", "风险点"],
        "min_length": 80,
    },
    {
        "id": 17, "type": "image_chat", "name": "图文-OLAY+200内平替",
        "image": "app/static/images/products/jd_v3_100241283549.png",
        "msg": "200以内的平替",
        "expect_intent": "recommendation",
        "min_products": 1,
        "min_length": 100,
    },
    {
        "id": 34, "type": "image_chat", "name": "图文-小棕瓶判断卡片收敛",
        "image": "app/static/images/products/jd_v3_100022610146.png",
        "msg": "这张图是什么？适合熬夜暗沉吗",
        "expect_intent": "judgement",
        "min_products": 1,
        "max_products": 1,
        "must_product_brands": ["雅诗兰黛"],
        "must_product_categories": ["精华"],
        "must_contain": ["看了你发的图片", "小棕瓶", "能不能用"],
        "min_length": 120,
    },
    {
        "id": 35, "type": "image_chat", "name": "图文-小棕瓶便宜平替",
        "image": "app/static/images/products/jd_v3_100022610146.png",
        "msg": "有没有便宜点的平替",
        "expect_intent": "recommendation",
        "min_products": 1,
        "must_product_categories": ["精华"],
        "product_price_max": 900,
        "price_check_count": 1,
        "must_contain": ["看了你发的图片", "综合建议"],
        "min_length": 120,
    },
    {
        "id": 23, "type": "frontend_static", "name": "前端消费answer_contract",
        "file": "app/static/chat.html",
        "must_contain_static": ["renderContract", "answer_contract", "primary_product_ids", "display_sections"],
        "must_match_static": [
            (r"eventName\s*===\s*'answer_contract'", "SSE处理answer_contract事件"),
        ],
    },
    {
        "id": 24, "type": "frontend_static", "name": "前端按合同渲染商品和插图",
        "file": "app/static/chat.html",
        "must_contain_static": ["orderProductsByContract", "contractAllowsSection", "render_inline_images"],
        "must_match_static": [
            (r"const finalProducts\s*=\s*orderProductsByContract\(deferredPanels\.products,\s*renderContract\)\.slice\(0,\s*3\)", "底部推荐卡片按answer_contract顺序"),
            (r"renderAssistantTextWithTypewriter\(fullText,\s*bubble,\s*inlineProducts,\s*renderContract\)", "最终正文打字机渲染传入answer_contract"),
        ],
        "forbid_static": ["nonInlineDeduped", "renderedInlineIds", "renderUnusedInlineProductImages", "filterProductsForRenderedText(fullText"],
    },
    {
        "id": 27, "type": "frontend_static", "name": "识图回答不在前端短路",
        "file": "app/static/chat.html",
        "must_contain_static": [
            "composeImagePrompt(text, imageContextResult, policy)",
            "composeImagePrompt(text, lastImageTurnRef.imageContextResult, followupPolicy)",
        ],
        "forbid_static": [
            "buildLocalImageIdentificationReply",
            "buildLocalImagePriceReply",
            "buildLocalImageJudgementReply",
            "policy.policy === 'image_identification' || policy.policy === 'image_price' || policy.policy === 'image_judgement'",
            "followupPolicy.policy === 'image_price' || followupPolicy.policy === 'image_judgement'",
        ],
    },
    {
        "id": 28, "type": "frontend_static", "name": "推荐卡商品名短展示",
        "file": "app/static/chat.html",
        "must_contain_static": [
            "getCompactProductDisplayName",
            '<strong>${escapeHtml(getCompactProductDisplayName(product))}</strong>',
            '<div class="recommendation-name" title="${escapeHtml(p.name || \'\')}">${escapeHtml(getCompactProductDisplayName(p))}</div>',
        ],
        "forbid_static": [
            '''<div class="recommendation-name">${escapeHtml(p.name || '推荐商品')}</div>''',
        ],
    },
    {
        "id": 38, "type": "frontend_static", "name": "历史记录逐条删除",
        "file": "app/static/chat.html",
        "must_contain_static": [
            "deleteHistorySession",
            "history-delete-btn",
            "删除这条历史",
            "event.stopPropagation(); deleteHistorySession",
        ],
        "forbid_static": [
            "clearHistoryBtn",
            "clearConversationHistory",
            "清空左侧所有对话历史",
        ],
    },
    {
        "id": 39, "type": "frontend_static", "name": "前端发送锁和打字机",
        "file": "app/static/chat.html",
        "must_contain_static": [
            "let isSendingMessage = false",
            "function setSendingState",
            "if (isSendingMessage) return",
            "sendBtn.disabled = isSendingMessage",
            "const imagesForSend = uploadedImages.slice()",
            "chatInput.value = '';",
            "function renderAssistantTextWithTypewriter",
            "await renderAssistantTextWithTypewriter",
        ],
    },
    {
        "id": 40, "type": "frontend_static", "name": "V2合同驱动决策弹窗与正文插图",
        "file": "app/static/chat.html",
        "must_contain_static": [
            "contractAllowsSection('decision_process'",
            "eventName === 'decision_process'",
            "deferredPanels.decisionProcess",
            "renderContract.inline_images",
            "inline-product-image",
            "inlineProducts = renderContract.inline_images",
            "buildImmediateDecisionProcess",
            "renderDecisionProcessIfReady",
            "const liveDecisionProcess = displayDecisionProcess",
            "buildImmediateDecisionProcess(visibleText || text, images)",
            "persistUntilFirstToken: true",
            "if (options.persistUntilFirstToken) return;",
            "displayDecisionProcess(deferredPanels.decisionProcess, aiDiv)",
            "decision-process diandian-thinking",
            "diandian-thinking-card",
        ],
        "forbid_static": [
            "SHOW_USER_DECISION_PROCESS = false",
            "const inlineProducts = [];",
            "inline-image-strip",
            "decision-process collapsed",
            "pipeline-toggle open",
            "displayDecisionProcess(deferredPanels.decisionProcess);",
        ],
    },
]

FOLLOWUPS = [
    {
        "id": 18, "name": "更便宜",
        "seed": "300以内面霜", "followup": "有没有更便宜的",
        "expect_intent": "followup",
        "min_length": 80,
    },
    {
        "id": 19, "name": "敏感肌防晒",
        "seed": "200以内防晒", "followup": "敏感肌能用的",
        "expect_intent": "followup",
        "must_categories": ["防晒"],
        "min_length": 80,
    },
    {
        "id": 20, "name": "平价替代",
        "seed": "油皮精华", "followup": "有没有平价替代",
        "expect_intent": "followup",
        "min_length": 80,
    },
    {
        "id": 22, "name": "夜间用法",
        "seed": "干敏肌想要抗初老精华，预算 1000 左右",
        "followup": "哪个适合夜间用",
        "expect_intent": "followup",
        "min_products": 3,
        "product_brand_prefix": ["雅诗兰黛"],
        "must_contain": ["夜间用优先", "判断理由", "干敏肌夜间用法"],
        "min_length": 120,
    },
    {
        "id": 25, "name": "白天用法",
        "seed": "干敏肌想要抗初老精华，预算 1000 左右",
        "followup": "哪一款更适合白天使用",
        "expect_intent": "followup",
        "min_products": 3,
        "product_brand_prefix": ["赫莲娜"],
        "must_contain": ["白天用优先", "判断理由", "白天用法"],
        "must_not_contain": ["夜间用优先"],
        "min_length": 120,
    },
    {
        "id": 26, "name": "日夜分工",
        "seed": "干敏肌想要抗初老精华，预算 1000 左右",
        "followup": "白天和晚上怎么选",
        "expect_intent": "followup",
        "min_products": 3,
        "product_brand_prefix": ["赫莲娜", "雅诗兰黛"],
        "must_contain": ["白天优先", "夜间优先", "日夜分工"],
        "min_length": 140,
    },
    {
        "id": 29, "name": "敏感肌单选追问",
        "seed": "300以内精华推荐",
        "followup": "哪款更适合敏感肌",
        "expect_intent": "followup",
        "min_products": 1,
        "max_products": 1,
        "must_contain": ["玉泽", "敏感肌"],
        "must_not_contain": ["玉泽国货线", "国货线", "玉泽（Dr.Yu）精华乳皮肤屏障修护精华乳50ml"],
        "min_length": 80,
    },
    {
        "id": 31, "name": "防晒敏感肌单选追问",
        "seed": "400以内防晒推荐",
        "followup": "哪款更适合敏感肌",
        "expect_intent": "followup",
        "min_products": 1,
        "max_products": 1,
        "product_brand_prefix": ["理肤泉"],
        "must_contain": ["理肤泉", "敏感肌"],
        "must_not_contain": ["怡思丁", "油皮/混油/油痘肌", "现货 理肤泉"],
        "min_length": 80,
    },
    {
        "id": 32, "name": "防晒通勤单选追问",
        "seed": "400以内防晒推荐",
        "followup": "哪款适合通勤",
        "expect_intent": "followup",
        "min_products": 1,
        "max_products": 1,
        "must_contain": ["通勤"],
        "must_not_contain": ["补充：适合性补充", "适合性补充", "精华后面一定叠防晒", "维稳打底"],
        "min_length": 80,
    },
    {
        "id": 36, "name": "排除上轮-更高预算",
        "seed": "300以内面霜",
        "followup": "除了上面这些，有没有预算更高更好一点的",
        "expect_intent": "followup",
        "expect_followup_type": "more_options",
        "contract_secondary_followup_types": ["higher_budget"],
        "product_price_min": 303,
        "price_check_count": 3,
        "must_contain": ["排除刚才", "预算更高"],
        "must_not_contain": ["筛选了一圈", "核对点"],
        "min_products": 1,
        "min_length": 80,
    },
    {
        "id": 37, "name": "排除上轮-更温和",
        "seed": "300以内面霜",
        "followup": "除了上面这些，有没有更温和一点的",
        "expect_intent": "followup",
        "expect_followup_type": "more_options",
        "contract_secondary_followup_types": ["suitability"],
        "must_contain": ["排除刚才", "更温和"],
        "must_not_contain": ["筛选了一圈", "核对点"],
        "min_products": 1,
        "min_length": 80,
    },
]


def run():
    results = []
    print("=" * 78)
    print("一期Golden Cases 强断言回归测试")
    print("=" * 78)

    for c in CASES:
        cid, name, t = c["id"], c["name"], c["type"]
        failures = []
        detail_parts = []
        try:
            if t == "image_search":
                sr = post_image_search(c["image"])
                top1 = (sr.get("results") or [{}])[0]
                sim = float(top1.get("similarity", 0) or 0)
                brand = top1.get("brand", "")
                cat = top1.get("category", "")
                cnt = len(sr.get("results", []))

                detail_parts.append(f"top1={brand}/{cat} sim={sim:.1f}% cnt={cnt}")

                min_sim = c.get("min_top1_sim", 0)
                if sim < min_sim:
                    failures.append(f"top1相似度{sim:.1f}% < {min_sim}%")

                exp_brand = c.get("expect_top1_brand")
                if exp_brand and exp_brand not in brand:
                    failures.append(f"top1品牌≠{exp_brand} (实际={brand})")

                exp_brands_any = c.get("expect_top1_brand_any")
                if exp_brands_any and not any(b in brand for b in exp_brands_any):
                    failures.append(f"top1品牌不在{exp_brands_any} (实际={brand})")

                exp_cat = c.get("expect_top1_category")
                if exp_cat and exp_cat not in cat:
                    failures.append(f"top1品类≠{exp_cat} (实际={cat})")

            elif t == "image_chat":
                sr = post_image_search(c["image"])
                img_ctx = build_image_context(sr)
                r = post_chat_raw(c["msg"], f"gcase{c['id']}_{RUN_ID}", image_context=img_ctx)
                ok, fl, intent_name, p_cnt = check_chat_result(r, c)
                failures.extend(fl)
                # anchor检查
                text = r["content"]
                anchor_must = c.get("anchor_must_contain", ["看了你发的图片", "图里", "图片上"])
                if not any(a in text for a in anchor_must):
                    failures.append("未提及图片锚点（图里/看了你发的图片）")
                detail_parts.append(f"intent={intent_name} products={p_cnt}")

            elif t == "frontend_static":
                ok, fl = check_frontend_static(c)
                failures.extend(fl)
                detail_parts.append(f"file={c['file']}")

            else:  # chat
                r = post_chat_raw(c["msg"], f"gcase{c['id']}_{RUN_ID}")
                ok, fl, intent_name, p_cnt = check_chat_result(r, c)
                failures.extend(fl)
                detail_parts.append(f"intent={intent_name} products={p_cnt}")

            ok = len(failures) == 0
            results.append({"id": cid, "name": name, "ok": ok, "failures": failures})
            mark = "PASS" if ok else "FAIL"
            print(f"[{cid:2d}] {mark} {name:<32} {' '.join(detail_parts)}")
            if not ok:
                for f in failures:
                    print(f"      ✗ {f}")
        except Exception as e:
            import traceback
            traceback.print_exc()
            results.append({"id": cid, "name": name, "ok": False, "failures": [f"EXCEPTION {e}"]})
            print(f"[{cid:2d}] EXC  {name:<32} {e}")

    # 追问
    for f in FOLLOWUPS:
        cid = f["id"]
        sid = f"gcase{cid}_{RUN_ID}"
        try:
            r1 = post_chat_raw(f["seed"], sid)
            hist = [{"role": "user", "content": f["seed"]}, {"role": "assistant", "content": r1["content"]}]
            r2 = post_chat_raw(f["followup"], sid, history=hist)
            ok, fl, intent_name, p_cnt = check_chat_result(r2, f)
            mark = "PASS" if ok else "FAIL"
            print(f"[{cid:2d}] {mark} 追问-{f['name']:<24} seed='{f['seed']}'→'{f['followup']}' intent={intent_name} products={p_cnt}")
            if not ok:
                for x in fl:
                    print(f"      ✗ {x}")
            results.append({"id": cid, "name": f"追问-{f['name']}", "ok": ok, "failures": fl})
        except Exception as e:
            results.append({"id": cid, "name": f"追问-{f['name']}", "ok": False, "failures": [f"EXCEPTION {e}"]})
            print(f"[{cid:2d}] EXC  追问-{f['name']:<24} {e}")

    passed = sum(1 for r in results if r["ok"])
    failed = [r for r in results if not r["ok"]]
    total = len(results)
    print("\n" + "=" * 78)
    print(f"RESULT: PASS {passed}/{total}, FAIL {len(failed)}")
    print("=" * 78)
    if failed:
        for r in failed:
            print(f"  ✗ [{r['id']}] {r['name']}")
            for x in r["failures"]:
                print(f"      - {x}")
    sys.exit(0 if not failed else 1)


if __name__ == "__main__":
    run()
