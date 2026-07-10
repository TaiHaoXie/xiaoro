import re
from typing import List, Dict, Any, Optional
from app.database.postgres import execute_query


SEO_JUNK_PATTERNS = [
    (r"【[^】]*】", ""),
    (r"\[[^\]]*\]", ""),
    (r"（[^）]*礼盒[^）]*）", ""),
    (r"\([^)]*礼盒[^)]*\)", ""),
    (r"生日礼物.*$", ""),
    (r"送老婆.*$", ""),
    (r"送女友.*$", ""),
    (r"送妈妈.*$", ""),
    (r"送男友.*$", ""),
    (r"送老公.*$", ""),
    (r"护肤化妆.*$", ""),
    (r"化妆品套装.*$", ""),
    (r"\d+月\d+日发售", ""),
    (r"官方正品", ""),
    (r"旗舰店", ""),
    (r"专柜正品", ""),
]


def clean_product_name(name: str) -> str:
    if not name:
        return ""
    result = name
    for pattern, replacement in SEO_JUNK_PATTERNS:
        result = re.sub(pattern, replacement, result)
    result = re.sub(r"\s+", " ", result).strip()
    result = re.sub(r"^[的 ，,、]+", "", result)
    result = re.sub(r"[的 ，,、]+$", "", result)
    if len(result) < 2:
        return name[:20]
    return result


def extract_spec_field(product: Dict[str, Any], field: str, default: Any = None) -> Any:
    specs = product.get("specifications") or {}
    if isinstance(specs, str):
        import json
        try:
            specs = json.loads(specs)
        except:
            specs = {}
    return specs.get(field, default)


class Retriever:
    async def retrieve_products(
        self,
        category: Optional[str] = None,
        brand: Optional[str] = None,
        concerns: Optional[List[str]] = None,
        skin_type: Optional[str] = None,
        budget_min: Optional[float] = None,
        budget_max: Optional[float] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        conditions = []
        params = []
        idx = 1

        if category:
            conditions.append(f"(category = ${idx} OR specifications->>'subcategory' = ${idx})")
            params.append(category)
            idx += 1

        if brand:
            conditions.append(f"brand ILIKE ${idx}")
            params.append(f"%{brand}%")
            idx += 1

        if budget_min is not None:
            conditions.append(f"price >= ${idx}")
            params.append(budget_min)
            idx += 1

        if budget_max is not None:
            conditions.append(f"price <= ${idx}")
            params.append(budget_max)
            idx += 1

        if concerns:
            concern_clauses = []
            for concern in concerns:
                concern_clauses.append(f"(description ILIKE ${idx} OR specifications->>'concerns' ILIKE ${idx} OR specifications->>'positioning' ILIKE ${idx})")
                params.append(f"%{concern}%")
                idx += 1
            if concern_clauses:
                conditions.append("(" + " OR ".join(concern_clauses) + ")")

            # 护肤诉求但没有明确品类时，排除彩妆/香水，避免“修护/保湿/泛红”这类词把口红、
            # 粉底、散粉等带进护肤推荐。用户明确问口红/粉底时不会触发这里。
            if not category:
                makeup_categories = [
                    "口红", "散粉", "粉底液", "遮瑕", "气垫",
                    "香水", "腮红", "眼影", "隔离妆前",
                ]
                placeholders = []
                for cat in makeup_categories:
                    placeholders.append(f"${idx}")
                    params.append(cat)
                    idx += 1
                conditions.append(f"(category IS NULL OR category NOT IN ({', '.join(placeholders)}))")

        where_sql = " AND ".join(conditions) if conditions else "1=1"
        sql = f"SELECT * FROM products WHERE {where_sql} ORDER BY sales_count DESC NULLS LAST, rating DESC NULLS LAST LIMIT ${idx}"
        params.append(limit)

        products = await execute_query(sql, *params, fetch="all")
        return [self._normalize_product(p) for p in products]

    async def retrieve_by_ids(self, product_ids: List[int]) -> List[Dict[str, Any]]:
        if not product_ids:
            return []
        placeholders = ",".join(f"${i+1}" for i in range(len(product_ids)))
        sql = f"SELECT * FROM products WHERE id = ANY(ARRAY[{placeholders}]::bigint[])"
        products = await execute_query(sql, *product_ids, fetch="all")
        return [self._normalize_product(p) for p in products]

    async def retrieve_by_name_fuzzy(self, name: str, limit: int = 5) -> List[Dict[str, Any]]:
        # 1. 先尝试完整 ILIKE 匹配
        sql = "SELECT * FROM products WHERE name ILIKE $1 OR brand ILIKE $1 ORDER BY sales_count DESC NULLS LAST LIMIT $2"
        products = await execute_query(sql, f"%{name}%", limit, fetch="all")
        if len(products) >= limit:
            return [self._normalize_product(p) for p in products]
        existing_ids = {p["id"] for p in products}

        # 2. 提取有意义的核心词：品牌词、品类词、核心名片段
        #    a) 按显式分隔符切分
        tokens = [t for t in re.split(r"[\s/·、,，]+", name) if len(t) >= 2]
        #    b) 连续中文里提取 >=2字 的有意义片段——按已知品类词做切分
        cn_text = "".join(re.findall(r"[\u4e00-\u9fa5A-Za-z0-9]+", name))
        # 已知品类/品类尾词（出现在词尾说明前面的是核心名）
        category_suffixes = ["面膜","面霜","眼霜","精华","防晒","洁面","洗面奶","爽肤水","化妆水",
                             "乳液","粉底","粉底液","气垫","散粉","粉饼","妆前","隔离","遮瑕","腮红",
                             "口红","唇膏","卸妆水","卸妆油","卸妆膏","卸妆","香水","套装","精华水","精华露"]
        subtokens = []
        for suf in category_suffixes:
            idx = cn_text.find(suf)
            if idx >= 1:
                # 提取suf前面的2-4字作为核心名片段
                prefix = cn_text[max(0, idx-4):idx]
                for pl in range(min(4, len(prefix)), 1, -1):
                    seg = prefix[-pl:]
                    if len(seg) >= 2 and seg not in subtokens:
                        subtokens.append(seg)
                # 品类词本身也作为token
                if suf not in subtokens:
                    subtokens.append(suf)
        #    c) 整段中文作为一个token（兜底）
        if len(cn_text) >= 2 and cn_text not in tokens:
            tokens.append(cn_text)
        # 合并去重
        all_tokens = []
        for t in tokens + subtokens:
            if t and len(t) >= 2 and t not in all_tokens and t != name:
                all_tokens.append(t)

        # 3. 对每个token单独LIKE，合并结果（OR语义），命中数越多越相关
        if all_tokens:
            scored = {}  # pid -> (hit_count, product)
            for t in all_tokens[:8]:
                rows = await execute_query(
                    "SELECT * FROM products WHERE name ILIKE $1 OR brand ILIKE $1 LIMIT 20",
                    f"%{t}%", fetch="all"
                )
                for p in rows:
                    pid = p["id"]
                    if pid not in scored:
                        scored[pid] = [0, p]
                    scored[pid][0] += 1
            # 按命中数倒序、sales_count倒序
            ranked = sorted(scored.values(), key=lambda x: (-x[0], -(x[1].get("sales_count") or 0)))
            for _, p in ranked:
                if p["id"] not in existing_ids:
                    products.append(p)
                    existing_ids.add(p["id"])
                if len(products) >= limit:
                    break
        return [self._normalize_product(p) for p in products][:limit]

    async def retrieve_by_text_terms(self, terms: List[str], limit: int = 8) -> List[Dict[str, Any]]:
        cleaned = []
        seen = set()
        for term in terms or []:
            text = str(term or "").strip()
            if len(text) < 2 or text in seen:
                continue
            seen.add(text)
            cleaned.append(text)
            if len(cleaned) >= 8:
                break
        if not cleaned:
            return []

        clauses = []
        params: List[Any] = []
        for term in cleaned:
            idx = len(params) + 1
            clauses.append(
                f"""(
                    name ILIKE ${idx}
                    OR brand ILIKE ${idx}
                    OR description ILIKE ${idx}
                    OR specifications->>'concerns' ILIKE ${idx}
                    OR specifications->>'positioning' ILIKE ${idx}
                    OR specifications->>'target_users' ILIKE ${idx}
                    OR specifications->>'suitable_skin_types' ILIKE ${idx}
                    OR specifications->>'key_ingredients' ILIKE ${idx}
                )"""
            )
            params.append(f"%{term}%")
        sql = (
            "SELECT * FROM products WHERE "
            + " OR ".join(clauses)
            + f" ORDER BY sales_count DESC NULLS LAST, rating DESC NULLS LAST LIMIT ${len(params) + 1}"
        )
        params.append(limit)
        products = await execute_query(sql, *params, fetch="all")
        return [self._normalize_product(p) for p in products]

    async def retrieve_similar_products(self, product_ids: List[int], exclude_ids: List[int] = None, limit: int = 8) -> List[Dict[str, Any]]:
        if not product_ids:
            return []
        exclude_ids = exclude_ids or []
        ref_products = await self.retrieve_by_ids(product_ids[:2])
        if not ref_products:
            return []

        ref = ref_products[0]
        cat = ref.get("category")
        brand = ref.get("brand")
        price = float(ref.get("price") or 0)

        return await self.retrieve_products(
            category=cat,
            budget_min=price * 0.5 if price > 0 else None,
            budget_max=price * 1.5 if price > 0 else None,
            limit=limit,
        )

    def _normalize_product(self, p: Dict[str, Any]) -> Dict[str, Any]:
        normalized = dict(p)
        normalized["display_name"] = clean_product_name(p.get("name", ""))
        normalized["price_val"] = float(p.get("price") or 0)
        normalized["original_price_val"] = float(p.get("original_price") or 0)

        specs = {}
        raw_specs = p.get("specifications")
        if isinstance(raw_specs, dict):
            specs = raw_specs
        elif isinstance(raw_specs, str):
            import json
            try:
                specs = json.loads(raw_specs)
            except:
                specs = {}
        normalized["_specs"] = specs

        normalized["target_users"] = specs.get("target_users") or specs.get("适合人群") or p.get("target_users") or ""
        normalized["concerns_list"] = self._extract_list(
            specs.get("concerns") or specs.get("主打功效") or specs.get("功效")
            or specs.get("OCR提取功效") or p.get("concerns")
        )
        normalized["key_ingredients_list"] = self._extract_list(
            specs.get("key_ingredients") or specs.get("核心成分")
            or specs.get("OCR提取核心成分") or specs.get("主要功效成分")
            or p.get("key_ingredients")
        )
        normalized["suitable_skin"] = (
            specs.get("suitable_skin_types")
            or specs.get("suitable_skin")
            or specs.get("适合肤质")
            or p.get("suitable_skin_types")
            or p.get("suitable_skin")
            or p.get("description", "")
        )
        normalized["positioning"] = specs.get("positioning") or specs.get("定位") or p.get("positioning") or ""
        raw_pitfall = specs.get("pitfalls") or specs.get("注意点") or specs.get("备注") or p.get("pitfalls") or ""
        clean_pitfall = self._clean_pitfall_text(raw_pitfall)
        if not clean_pitfall:
            clean_pitfall = self._derive_pitfall_from_facts(
                normalized["key_ingredients_list"], normalized["suitable_skin"], normalized.get("category") or p.get("category")
            )
        normalized["pitfalls"] = clean_pitfall

        skincare = specs.get("skincare_info") if isinstance(specs, dict) else None
        if not isinstance(skincare, dict):
            skincare = {}
        normalized["qa_facts"] = self._extract_list(skincare.get("qa_facts"))
        normalized["mechanism_notes"] = self._extract_list(skincare.get("mechanism_notes"))
        normalized["usage_steps"] = self._extract_list(skincare.get("usage_steps"))
        normalized["safety_notes"] = self._extract_list(skincare.get("safety_notes"))
        normalized["texture_notes"] = self._extract_list(skincare.get("texture_notes"))
        normalized["claim_notes"] = self._extract_list(skincare.get("claim_notes"))
        normalized["user_review_notes"] = self._extract_list(skincare.get("user_review_notes"))

        return normalized

    @staticmethod
    def _clean_pitfall_text(value: Any) -> str:
        """过滤掉 pitfalls 里的抓取残留(URL/搜索链接/纯乱码)，只保留真正像风险提示的文本。"""
        text = str(value or "").strip()
        if not text:
            return ""
        if re.search(r"https?://|www\.|\.com|\.cn|taobao|jd\.com|tmall|search\?", text, re.IGNORECASE):
            return ""
        if "%" in text and re.search(r"%[0-9A-Fa-f]{2}", text):  # URL 编码残留
            return ""
        # 至少要有中文，否则大概率是残留串
        if not re.search(r"[\u4e00-\u9fff]", text):
            return ""
        return text

    @staticmethod
    def _derive_pitfall_from_facts(ingredients: List[str], suitable_skin: str, category: Optional[str]) -> str:
        """无真实注意点时，基于成分/肤质事实派生风险提醒，避免留空或后续套话。"""
        blob = "、".join(ingredients or []) + " " + str(suitable_skin or "")
        parts = []
        if re.search(r"酒精|乙醇|变性酒精", blob):
            parts.append("含酒精类成分，敏感肌或屏障脆弱先小面积试用")
        if re.search(r"水杨酸|果酸|杏仁酸|A醇|视黄醇|视黄醛|壬二酸|高浓度", blob):
            parts.append("含酸类/高浓度活性，建议夜间低频建立耐受，白天严格防晒，别和其它猛料同晚叠加")
        if re.search(r"香精|香料", blob):
            parts.append("含香精，易敏人群留意")
        if not parts:
            if re.search(r"敏感|屏障|泛红|试用", str(suitable_skin or "")):
                parts.append("敏感肌先局部试用，确认无泛红刺痛再上脸")
        return "；".join(parts[:2])


    def _extract_list(self, value: Any) -> List[str]:
        if not value:
            return []
        if isinstance(value, list):
            return [str(x).strip() for x in value if x and str(x).strip()]
        if isinstance(value, str):
            parts = re.split(r"[、,，;；]\s*", value)
            return [p.strip() for p in parts if p.strip()]
        if isinstance(value, dict):
            return [str(v).strip() for v in value.values() if v and str(v).strip()]
        return []
