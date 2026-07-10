"""
RAG检索服务模块

整合向量检索和知识库搜索
提供增强的检索能力
"""

from typing import List, Dict, Any, Optional, Tuple
import logging
import asyncio

from app.services.embedding import get_embedding_service
from app.services.knowledge_base import get_knowledge_service
from app.services.llm import get_llm_service
from app.services.image_vector import get_image_vector_service
from app.database.postgres import execute_query
from app.models.domain import Citation, SourceType, PitfallWarning

logger = logging.getLogger(__name__)


class RAGRetriever:
    """
    RAG检索器

    结合关键词搜索、向量检索和知识库
    提供多路召回和重排序
    """

    def __init__(self):
        """初始化RAG检索器"""
        self.embedding_service = get_embedding_service()
        self.knowledge_service = get_knowledge_service()
        self.llm_service = get_llm_service()
        self._image_vector_service = None
        logger.info("✅ RAG检索器初始化成功")

    def _get_image_vector_service(self):
        if self._image_vector_service is None:
            try:
                self._image_vector_service = get_image_vector_service()
            except Exception as e:
                logger.warning(f"⚠️ 图片向量服务不可用，语义召回降级为纯关键词: {e}")
                self._image_vector_service = False
        return self._image_vector_service or None

    async def retrieve(
        self,
        query: str,
        filters: Optional[Dict] = None,
        top_k: int = 10,
        search_products: bool = True,
        search_knowledge: bool = True
    ) -> Dict[str, Any]:
        """
        多路召回检索

        Args:
            query: 查询文本
            filters: 过滤条件 (category, brand, price_range等)
            top_k: 返回数量
            search_products: 是否搜索商品
            search_knowledge: 是否搜索知识库

        Returns:
            检索结果（包含增强的来源引用）
        """
        results = {
            "query": query,
            "products": [],
            "knowledge": [],
            "citations": [],  # 增强的来源引用
            "pitfalls": []    # 避坑提示
        }

        # 1. 商品向量搜索
        if search_products:
            products = await self._search_products(query, filters, top_k)
            results["products"] = products

            # 创建商品来源引用
            for p in products:
                results["citations"].append(Citation(
                    type=SourceType.PRODUCT,
                    id=p["id"],
                    title=p["name"],
                    snippet=f"{p['brand']} - ¥{p['price']}" if p.get('brand') else f"¥{p['price']}",
                    url=p.get("detail_url") or f"/api/v1/search/products/{p['id']}",
                    confidence=p.get("relevance", 80) / 100,
                    metadata={"brand": p.get("brand"), "price": p.get("price"), "detail_url": p.get("detail_url", "")}
                ).model_dump())

        # 2. 知识库搜索
        if search_knowledge:
            knowledge = await self._search_knowledge(query, top_k)
            results["knowledge"] = knowledge

            # 创建知识来源引用
            for k in knowledge:
                citation_type = self._map_knowledge_type(k.get("type", "knowledge"))
                results["citations"].append(Citation(
                    type=citation_type,
                    id=k["id"],
                    title=k["title"],
                    snippet=k.get("content", "")[:100] + "..." if len(k.get("content", "")) > 100 else k.get("content", ""),
                    confidence=k.get("score", 0.8),
                    metadata={"type": k.get("type")}
                ).model_dump())

        # 3. 查找避坑信息
        pitfalls = await self._search_pitfalls(query, results.get("products", []))
        results["pitfalls"] = pitfalls

        logger.info(
            f"🔍 RAG检索完成: "
            f"{len(results['products'])}个商品, "
            f"{len(results['knowledge'])}条知识, "
            f"{len(results['pitfalls'])}条避坑提示"
        )

        return results

    async def _search_products(
        self,
        query: str,
        filters: Optional[Dict],
        top_k: int
    ) -> List[Dict]:
        """商品搜索（关键词SQL + CLIP语义向量混合召回）。"""
        products = []

        try:
            def _build_base_sql(include_price_limit: bool = True) -> Tuple[str, List[Any]]:
                sql = "SELECT * FROM products WHERE 1=1"
                params: List[Any] = []

                if filters:
                    if "category" in filters and filters["category"]:
                        cat = str(filters["category"])
                        if "-" in cat:
                            head, tail = cat.split("-", 1)
                            sql += (
                                " AND (category = $%d OR category = $%d"
                                " OR (category ILIKE $%d AND ("
                                " specifications->>'subcategory' IS NOT NULL AND specifications->>'subcategory' ILIKE $%d"
                                " OR (specifications->>'subcategory' IS NULL AND name ILIKE $%d)"
                                ")))"
                                % (len(params)+1, len(params)+2, len(params)+3, len(params)+4, len(params)+5)
                            )
                            params.extend([cat, tail, f"%{head}%", f"%{tail}%", f"%{tail}%"])
                            exclude_terms = self._get_subcategory_exclusions(tail)
                            for ex in exclude_terms:
                                sql += f" AND name NOT ILIKE ${len(params)+1}"
                                params.append(f"%{ex}%")
                        else:
                            sql += f" AND (category ILIKE ${len(params)+1} OR category = ${len(params)+2})"
                            params.extend([f"%{cat}%", cat])
                    if "brand" in filters:
                        sql += f" AND brand = ${len(params) + 1}"
                        params.append(filters["brand"])
                    if include_price_limit:
                        if "price_min" in filters:
                            sql += f" AND price >= ${len(params) + 1}"
                            params.append(filters["price_min"])
                        if "price_max" in filters:
                            sql += f" AND price <= ${len(params) + 1}"
                            params.append(filters["price_max"])

                return sql, params

            async def _keyword_search() -> List[Dict]:
                sql, params = _build_base_sql()
                if query:
                    sql += f" AND (name ILIKE ${len(params) + 1} OR description ILIKE ${len(params) + 2})"
                    params.extend([f"%{query}%", f"%{query}%"])
                sql += f" LIMIT {top_k * 2}"
                results = await execute_query(sql, *params, fetch="all")
                if not results:
                    terms = self._extract_query_terms(query)
                    if terms:
                        or_sql, or_params = _build_base_sql()
                        clauses = []
                        for term in terms:
                            idx = len(or_params) + 1
                            clauses.append(
                                f"(name ILIKE ${idx} OR description ILIKE ${idx} "
                                f"OR specifications::text ILIKE ${idx})"
                            )
                            or_params.append(f"%{term}%")
                        or_sql += " AND (" + " OR ".join(clauses) + ")"
                        or_sql += f" LIMIT {top_k * 4}"
                        results = await execute_query(or_sql, *or_params, fetch="all")
                return results

            async def _vector_recall() -> Dict[int, float]:
                try:
                    vec_svc = self._get_image_vector_service()
                    if not vec_svc:
                        return {}
                    has_cat = bool(filters and filters.get("category"))
                    vec_filters = {}
                    if has_cat:
                        cat = str(filters["category"])
                        vec_filters["category"] = cat.split("-", 1)[0] if "-" in cat else cat
                    min_score = 0.24 if has_cat else 0.27
                    vec_results = await vec_svc.search_by_text(
                        text_query=query,
                        top_k=top_k * 2,
                        min_score=min_score,
                        filters=vec_filters or None,
                    )
                    return {r["product_id"]: float(r.get("similarity", 0))
                            for r in vec_results if r.get("product_id")}
                except Exception as e:
                    logger.warning(f"⚠️ 向量召回失败，降级为纯关键词: {e}")
                    return {}

            keyword_results, vector_hits = await asyncio.gather(
                _keyword_search(), _vector_recall()
            )

            if not keyword_results and vector_hits:
                vec_min = 30 if not (filters and filters.get("category")) else 27
                strong_vec = {pid: sim for pid, sim in vector_hits.items() if sim >= vec_min}
                vec_pids = list(strong_vec.keys())[:max(3, top_k)]
                if vec_pids:
                    extra_sql, extra_params = _build_base_sql(include_price_limit=False)
                    extra_sql += f" AND id = ANY(${len(extra_params)+1})"
                    extra_params.append(vec_pids)
                    keyword_results = await execute_query(extra_sql, *extra_params, fetch="all")
                    if filters and filters.get("price_max"):
                        pmax = filters["price_max"]
                        keyword_results = [p for p in keyword_results if float(p.get("price", 0)) <= pmax]

            if not keyword_results:
                return []

            query_terms = self._extract_query_terms(query)
            wanted_subcat = ""
            if filters and filters.get("category"):
                wanted_subcat = str(filters["category"]).split("-")[-1]
            want_skin = filters.get("skin_types") if filters else None
            want_concern = filters.get("skin_concerns") if filters else None
            budget = filters.get("budget") if filters else None

            for product in keyword_results:
                specs = product.get("specifications") or {}
                if isinstance(specs, str):
                    try:
                        import json as _json
                        specs = _json.loads(specs)
                    except Exception:
                        specs = {}
                subcat = (specs.get("subcategory") or "") if isinstance(specs, dict) else ""

                skincare = product.get("skincare_info") or {}
                if isinstance(skincare, str):
                    try:
                        import json as _json
                        skincare = _json.loads(skincare)
                    except Exception:
                        skincare = {}
                prod_skin = skincare.get("skin_types") or [] if isinstance(skincare, dict) else []
                prod_concern = skincare.get("concerns") or [] if isinstance(skincare, dict) else []
                haystack = f"{product['name']} {product.get('description', '')} {subcat}"

                relevance = 35.0

                if wanted_subcat:
                    if subcat:
                        if wanted_subcat in subcat or subcat in wanted_subcat:
                            relevance += 6
                        else:
                            relevance -= 22
                    else:
                        other_in_name = any(
                            o in product["name"] for o in self._SUBCAT_WORDS if o != wanted_subcat
                        )
                        if other_in_name:
                            relevance -= 22
                        elif wanted_subcat in product["name"]:
                            relevance += 6

                relevance += sum(4 for t in query_terms if t in haystack)

                if want_skin:
                    relevance += sum(12 for s in want_skin if s in prod_skin)

                if want_concern:
                    for qc in want_concern:
                        if any(qc in pc or pc in qc for pc in prod_concern):
                            relevance += 14

                if budget and product.get("price"):
                    price = float(product["price"])
                    proximity = max(0.0, 1 - abs(price - budget) / budget)
                    relevance += round(proximity * 18, 2)

                if product["id"] in vector_hits:
                    sim = vector_hits[product["id"]]
                    if sim >= 32:
                        relevance += 8.0
                    elif sim >= 28:
                        relevance += 4.0
                    elif sim >= 25:
                        relevance += 1.5

                relevance = round(min(relevance, 99.0), 2)

                price_band = ""
                _specs = product.get("specifications") or {}
                if isinstance(_specs, str):
                    try:
                        import json as _json
                        _specs = _json.loads(_specs)
                    except Exception:
                        _specs = {}
                if isinstance(_specs, dict):
                    price_band = _specs.get("price_band") or ""

                products.append({
                    "id": product["id"],
                    "name": product["name"],
                    "brand": product["brand"],
                    "category": product["category"],
                    "price": float(product["price"]),
                    "description": product.get("description", ""),
                    "detail_url": product.get("detail_url") or "",
                    "image_url": product.get("image_url") or "",
                    "price_band": price_band,
                    "relevance": relevance,
                    "specifications": specs if isinstance(specs, dict) else {},
                    "skincare_info": skincare if isinstance(skincare, dict) else {},
                    "original_price": product.get("original_price"),
                    "tags": product.get("tags") or [],
                    "stock": product.get("stock"),
                })

            products.sort(key=lambda x: (-x["relevance"], x["price"]))

            return products[:top_k]

        except Exception as e:
            logger.error(f"❌ 商品搜索失败: {e}")
            return []

    # 子类目互斥词：用于「用户要A品类却出现B品类」时降权
    _SUBCAT_WORDS = [
        "眼霜", "面霜", "乳液", "眼膜", "面膜", "洁面", "洗面奶",
        "卸妆", "爽肤水", "化妆水", "防晒", "粉底", "气垫", "散粉",
        "口红", "唇釉", "遮瑕", "隔离", "眼部精华",
    ]

    # 子类目混淆词映射：目标子类 -> name中包含这些词时会导致LIKE '%子类目%'误匹配（如"洗面霜"包含"面霜"）
    _SUBCAT_NAME_CONFLICTS = {
        "面霜": ["洗面霜", "剃须霜"],
        "乳液": ["洗面乳", "洁面乳", "卸妆乳"],
    }

    @classmethod
    def _get_subcategory_exclusions(cls, subcat: str) -> List[str]:
        """获取name中需要排除的混淆词，避免name ILIKE '%子类目%'误匹配。"""
        if not subcat:
            return []
        return list(cls._SUBCAT_NAME_CONFLICTS.get(subcat, []))

    # 护肤美妆领域关键词词典，用于从整句问题里召回商品
    _DOMAIN_TERMS = [
        "洁面", "卸妆", "爽肤水", "精华水", "化妆水", "精华", "面霜", "乳液",
        "眼霜", "面膜", "防晒", "粉底", "粉底液", "气垫", "散粉", "蜜粉",
        "口红", "唇釉", "唇膏", "遮瑕", "隔离", "妆前",
        "美白", "提亮", "淡斑", "抗氧化", "抗氧", "抗老", "抗皱", "紧致",
        "保湿", "补水", "修护", "舒缓", "控油", "祛痘", "去黑头", "淡纹",
        "烟酰胺", "视黄醇", "玻色因", "胜肽", "水杨酸", "果酸", "维C", "玻尿酸",
        "敏感肌", "油皮", "干皮", "混油", "痘肌", "屏障",
    ]

    def _extract_query_terms(self, query: str) -> List[str]:
        """从整句问题里提取护肤美妆领域关键词，用于分词召回商品。"""
        if not query:
            return []
        terms = [t for t in self._DOMAIN_TERMS if t in query]
        # 去重保序
        seen = set()
        result = []
        for t in terms:
            if t not in seen:
                seen.add(t)
                result.append(t)
        return result[:6]

    async def _search_knowledge(
        self,
        query: str,
        top_k: int
    ) -> List[Dict]:
        """知识库搜索"""
        try:
            return await self.knowledge_service.search_knowledge(
                query=query,
                top_k=top_k
            )
        except Exception as e:
            logger.error(f"❌ 知识搜索失败: {e}")
            return []

    def _map_knowledge_type(self, knowledge_type: str) -> SourceType:
        """映射知识类型到来源类型"""
        type_mapping = {
            "faq": SourceType.FAQ,
            "review": SourceType.REVIEW,
            "user_guide": SourceType.GUIDE,
            "pitfall": SourceType.PITFALL,
            "product_desc": SourceType.KNOWLEDGE,
        }
        return type_mapping.get(knowledge_type, SourceType.KNOWLEDGE)

    async def _search_pitfalls(
        self,
        query: str,
        products: List[Dict]
    ) -> List[Dict]:
        """
        搜索避坑信息

        Args:
            query: 用户查询
            products: 检索到的商品列表

        Returns:
            避坑提示列表
        """
        pitfalls = []

        try:
            # 1. 优先：从知识库文档的「避雷与注意」段落抽取真实可用的提示
            kb_pitfalls = await self._extract_pitfalls_from_knowledge(query)
            pitfalls.extend(kb_pitfalls)

            # 2. 其次：知识库中 type='pitfall' 的专门条目（若有）
            if len(pitfalls) < 3:
                pitfall_knowledge = await execute_query(
                    """SELECT id, title, content, type, metadata
                       FROM knowledge_base
                       WHERE type = 'pitfall'
                       AND (title ILIKE $1 OR content ILIKE $2)
                       LIMIT 3""",
                    f"%{query}%", f"%{query}%",
                    fetch="all"
                )
                for pk in pitfall_knowledge:
                    metadata = pk.get("metadata") or {}
                    pitfalls.append({
                        "title": pk["title"],
                        "category": metadata.get("category", "通用"),
                        "severity": metadata.get("severity", "中"),
                        "description": pk["content"][:200],
                        "recommendation": metadata.get("recommendation")
                    })

        except Exception as e:
            logger.warning(f"⚠️ 避坑搜索失败: {e}")

        return pitfalls[:4]

    async def _extract_pitfalls_from_knowledge(self, query: str) -> List[Dict]:
        """从知识库文档的「避雷与注意」段落里抽取条目级避坑提示（真实内容，非模板）。"""
        results: List[Dict] = []
        try:
            terms = self._extract_query_terms(query)
            # 用领域词召回相关文档；无命中词则回退到整句
            patterns = terms or [query]
            clauses = []
            params: List[Any] = []
            for term in patterns:
                idx = len(params) + 1
                clauses.append(f"(title ILIKE ${idx} OR content ILIKE ${idx})")
                params.append(f"%{term}%")
            where = " OR ".join(clauses) if clauses else "TRUE"
            rows = await execute_query(
                f"""SELECT id, title, content FROM knowledge_base
                    WHERE type = 'user_guide'
                    AND content ILIKE '%避雷%'
                    AND ({where})
                    LIMIT 4""",
                *params,
                fetch="all"
            )

            seen = set()
            for row in rows:
                bullets = self._parse_avoid_bullets(row.get("content", ""))
                for bullet in bullets:
                    key = bullet["title"]
                    if key in seen:
                        continue
                    seen.add(key)
                    results.append(bullet)
                    if len(results) >= 4:
                        return results
        except Exception as e:
            logger.warning(f"⚠️ 知识库避坑抽取失败: {e}")
        return results

    @staticmethod
    def _parse_avoid_bullets(content: str) -> List[Dict]:
        """从一段文档里截取「避雷与注意」小节下的要点，拆成结构化避坑条目。"""
        if not content or "避雷" not in content:
            return []
        # 取「避雷与注意」之后、下一个二级标题之前的内容
        section = content.split("避雷", 1)[1]
        section = section.split("\n##", 1)[0]
        bullets: List[Dict] = []
        for raw in section.split("\n"):
            line = raw.strip()
            if not line.startswith("-"):
                continue
            line = line.lstrip("-").strip()
            if not line:
                continue
            # 形如「**警惕香精和酒精**：变性酒精和香精是常见刺激源」→ 标题 + 描述
            title, _, desc = line.partition("：")
            title = title.replace("*", "").strip()
            desc = desc.strip() or title
            if not title:
                continue
            bullets.append({
                "title": title[:24],
                "category": "护肤",
                "severity": "高" if any(w in line for w in ["刺激", "致敏", "受损", "屏障", "医", "处方", "禁用"]) else "中",
                "description": desc[:140],
                "recommendation": None
            })
            if len(bullets) >= 3:
                break
        return bullets

    async def _get_category_pitfalls(self, category: str) -> List[Dict]:
        """获取类别相关的避坑提示（扩展版）"""
        # 预定义的类别避坑指南
        category_pitfalls = {
            "护肤": [
                {
                    "title": "护肤品过敏风险提示",
                    "category": "安全性",
                    "severity": "高",
                    "description": "新产品使用前建议在耳后或手腕内侧做皮试，观察24小时无过敏反应后再全脸使用。",
                    "recommendation": "敏感肌建议选择成分简单的产品，避免酒精、香精等刺激成分。"
                },
                {
                    "title": "护肤效果期望管理",
                    "category": "效果",
                    "severity": "中",
                    "description": "护肤品需要持续使用才能见效，一般需要28天（皮肤代谢周期）以上。",
                    "recommendation": "保持耐心，按说明坚持使用，不要频繁更换产品。"
                },
                {
                    "title": "护肤叠加使用注意事项",
                    "category": "使用",
                    "severity": "中",
                    "description": "多种精华叠加使用可能导致成分冲突或过度刺激，不是越多越好。",
                    "recommendation": "建议每次护肤步骤不超过3层，注意成分相容性。"
                }
            ],
            "手机": [
                {
                    "title": "手机续航注意事项",
                    "category": "使用",
                    "severity": "中",
                    "description": "实际续航时间会因使用场景（游戏/视频/待机）和信号强度有较大差异。",
                    "recommendation": "参考实际评测数据，不要只看官方标称值。"
                },
                {
                    "title": "5G网络覆盖差异",
                    "category": "网络",
                    "severity": "低",
                    "description": "不同地区5G网络覆盖情况不同，实际网速可能受限于所在位置。",
                    "recommendation": "购买前确认所在城市/小区的5G覆盖情况。"
                },
                {
                    "title": "手机保值率提醒",
                    "category": "价格",
                    "severity": "中",
                    "description": "手机贬值较快，新品发布后旧款价格可能大幅下降。",
                    "recommendation": "非必要不建议抢首发，等待3-6个月通常有更好价格。"
                }
            ],
            "耳机": [
                {
                    "title": "蓝牙耳机场景限制",
                    "category": "兼容性",
                    "severity": "低",
                    "description": "部分蓝牙耳机在游戏场景下可能有延迟，通话降噪效果因环境而异。",
                    "recommendation": "游戏玩家建议选择低延迟模式，通话时注意环境噪音。"
                },
                {
                    "title": "入耳式耳机听力风险",
                    "category": "健康",
                    "severity": "高",
                    "description": "长时间使用入耳式耳机可能损伤听力，尤其是高音量情况下。",
                    "recommendation": "遵循60-60原则：音量不超过60%，连续使用不超过60分钟。"
                },
                {
                    "title": "降噪耳机安全提醒",
                    "category": "安全",
                    "severity": "中",
                    "description": "强降噪可能让你听不到周围环境声音，户外使用需注意安全。",
                    "recommendation": "在马路、车站等环境建议开启环境音模式或降低降噪强度。"
                }
            ],
            "电脑": [
                {
                    "title": "笔记本性能释放差异",
                    "category": "性能",
                    "severity": "中",
                    "description": "轻薄本由于散热限制，实际性能可能低于同配置的游戏本。",
                    "recommendation": "根据使用场景选择，重度办公/游戏建议选性能释放好的型号。"
                },
                {
                    "title": "Mac双系统注意事项",
                    "category": "兼容性",
                    "severity": "中",
                    "description": "M系列芯片Mac安装Windows需要虚拟机，性能和兼容性有损失。",
                    "recommendation": "如果必须用Windows专业软件，建议直接选购Windows笔记本。"
                },
                {
                    "title": "屏幕素质误区",
                    "category": "显示",
                    "severity": "低",
                    "description": "高分辨率不等于高色准，2K/4K屏幕的色域覆盖更重要。",
                    "recommendation": "关注色域覆盖（sRGB/DCI-P3）和色准ΔE值。"
                }
            ],
            "平板": [
                {
                    "title": "生产力工具局限性",
                    "category": "使用",
                    "severity": "中",
                    "description": "平板电脑在专业软件支持上仍有局限，不适合作为主力生产力设备。",
                    "recommendation": "明确需求：娱乐为主选iPad/安卓，轻办公选Surface类平板。"
                },
                {
                    "title": "存储容量不可扩展",
                    "category": "配置",
                    "severity": "中",
                    "description": "大多数平板存储不可升级，买小了后续无法扩容。",
                    "recommendation": "建议至少256GB起步，有大量下载需求建议512GB。"
                }
            ],
            "相机": [
                {
                    "title": "镜头群投入提醒",
                    "category": "成本",
                    "severity": "高",
                    "description": "相机只是开始，镜头群才是持续投入，总成本可能远超机身。",
                    "recommendation": "新手建议从套机头开始，明确需求后再添置定焦/长焦镜头。"
                },
                {
                    "title": "画幅权衡建议",
                    "category": "选购",
                    "severity": "中",
                    "description": "全画幅画质更好但体积重量大，APS-C更便携易携带。",
                    "recommendation": "经常旅行/外出建议选APS-C，工作室/棚拍可选全画幅。"
                }
            ],
            "手表": [
                {
                    "title": "智能手表续航焦虑",
                    "category": "续航",
                    "severity": "中",
                    "description": "功能越丰富的智能手表续航越短，通常需要每天充电。",
                    "recommendation": "如果不喜欢频繁充电，考虑长续航模式或传统智能手表。"
                },
                {
                    "title": "健康监测仅供参考",
                    "category": "健康",
                    "severity": "高",
                    "description": "智能手表的健康数据不能替代医疗设备，异常情况请就医。",
                    "recommendation": "将数据作为参考，身体不适请及时就医。"
                }
            ],
            "音箱": [
                {
                    "title": "无线音质妥协",
                    "category": "音质",
                    "severity": "中",
                    "description": "蓝牙传输会有一定音质损失，发烧友建议选择有线连接。",
                    "recommendation": "日常使用蓝牙足够，专业聆听建议选支持有线/AirPlay的型号。"
                },
                {
                    "title": "房间声学影响",
                    "category": "使用",
                    "severity": "低",
                    "description": "房间大小、装修材料会影响听感，同一音箱在不同环境效果差异大。",
                    "recommendation": "有条件建议试听，或选择支持EQ调节的型号。"
                }
            ],
            "家电": [
                {
                    "title": "容量规划建议",
                    "category": "选购",
                    "severity": "中",
                    "description": "大家电一旦购买使用周期长，容量买小了后期难以升级。",
                    "recommendation": "冰箱/洗衣机建议在预算内选大不选小，预留成长空间。"
                },
                {
                    "title": "智能功能溢价",
                    "category": "性价比",
                    "severity": "低",
                    "description": "智能功能通常有溢价，但很多功能实际使用频率不高。",
                    "recommendation": "评估自己是否会真的使用这些功能，避免为不用的功能买单。"
                }
            ],
            "美妆": [
                {
                    "title": "色号试色建议",
                    "category": "选购",
                    "severity": "高",
                    "description": "不同肤色、光线条件下口红色号效果差异很大，网图仅供参考。",
                    "recommendation": "建议专柜试色或先购买小样/试用装确认效果。"
                },
                {
                    "title": "化妆品保质期提醒",
                    "category": "安全",
                    "severity": "中",
                    "description": "开封后化妆品保质期缩短，过期使用可能引起皮肤问题。",
                    "recommendation": "注意包装上的开盖后使用期限标识（6M/12M等）。"
                }
            ],
            "食品": [
                {
                    "title": "保质期确认",
                    "category": "安全",
                    "severity": "高",
                    "description": "购买食品务必确认保质期，临期商品价格虽好但要考虑食用时间。",
                    "recommendation": "计算从收货到吃完的时间，确保在保质期内能吃完。"
                },
                {
                    "title": "储存条件注意",
                    "category": "储存",
                    "severity": "中",
                    "description": "部分食品需要特定储存条件（冷藏/避光/干燥），不符条件易变质。",
                    "recommendation": "购买前确认自己能否满足储存要求。"
                }
            ],
            "运动": [
                {
                    "title": "运动装备适配性",
                    "category": "选购",
                    "severity": "中",
                    "description": "专业运动装备针对特定运动设计，通用型可能在专业运动中表现不佳。",
                    "recommendation": "明确主要运动类型，针对性选购装备。"
                },
                {
                    "title": "运动强度循序渐进",
                    "category": "使用",
                    "severity": "高",
                    "description": "新装备到手不要立即高强度使用，身体需要适应期。",
                    "recommendation": "逐步增加运动强度，避免运动损伤。"
                }
            ]
        }

        return category_pitfalls.get(category, [])

    async def rerank(
        self,
        results: Dict[str, Any],
        query: str,
        user_context: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """
        重排序（使用LLM）

        Args:
            results: 检索结果
            query: 原始查询
            user_context: 用户上下文（肤质、预算等）

        Returns:
            重排序后的结果
        """
        try:
            if not results.get("products") and not results.get("knowledge"):
                return results

            if not self.llm_service.is_available():
                results["reranked"] = False
                results["rerank_error"] = "no_llm_provider"
                return results

            # 构建精排Prompt
            rerank_prompt = self._build_rerank_prompt(
                query=query,
                products=results.get("products", []),
                knowledge=results.get("knowledge", []),
                user_context=user_context or {}
            )

            # 调用LLM进行精排
            messages = [
                {
                    "role": "system",
                    "content": """你是一个电商导购专家，擅长根据用户需求对商品进行精准排序。

请按照以下标准对商品进行评分（1-10分）：
1. 相关性：商品是否满足用户的基本需求
2. 匹配度：商品是否符合用户的特定要求（肤质、预算等）
3. 性价比：在同类商品中的价格优势
4. 品质感：品牌口碑和产品质量

输出格式（JSON）：
{
    "product_scores": [
        {"id": "商品ID或序号", "score": 评分, "reason": "评分理由"}
    ],
    "knowledge_scores": [
        {"id": "知识ID或序号", "score": 评分, "reason": "评分理由"}
    ]
}"""
                },
                {
                    "role": "user",
                    "content": rerank_prompt
                }
            ]

            response = await self.llm_service.chat(
                messages,
                temperature=0.3  # 低温度保证稳定性
            )

            # 解析LLM响应
            scores = self._parse_rerank_response(response)

            # 应用评分重新排序
            if "product_scores" in scores:
                results["products"] = self._apply_scores(
                    results["products"],
                    scores["product_scores"]
                )

            if "knowledge_scores" in scores:
                results["knowledge"] = self._apply_scores(
                    results["knowledge"],
                    scores["knowledge_scores"]
                )

            results["reranked"] = True

            logger.info(f"✅ LLM精排完成: {len(results.get('products', []))}个商品, {len(results.get('knowledge', []))}条知识")

            return results

        except Exception as e:
            logger.warning(f"⚠️ LLM精排失败，使用原始排序: {e}")
            results["reranked"] = False
            results["rerank_error"] = str(e)
            return results

    def _build_rerank_prompt(
        self,
        query: str,
        products: List[Dict],
        knowledge: List[Dict],
        user_context: Dict
    ) -> str:
        """构建精排Prompt"""

        prompt_parts = [
            f"用户需求：{query}"
        ]

        # 添加用户上下文
        if user_context:
            context_str = ", ".join([f"{k}={v}" for k, v in user_context.items() if v])
            if context_str:
                prompt_parts.append(f"用户画像：{context_str}")

        # 添加商品列表
        if products:
            prompt_parts.append("\n待排序商品：")
            for i, p in enumerate(products[:10]):  # 最多10个
                prompt_parts.append(
                    f"{i+1}. {p.get('name', 'Unknown')} - {p.get('brand', '')} "
                    f"¥{p.get('price', 0)} - {p.get('description', '')[:80]}"
                )

        # 添加知识列表
        if knowledge:
            prompt_parts.append("\n待排序知识：")
            for i, k in enumerate(knowledge[:5]):
                prompt_parts.append(
                    f"{i+1}. {k.get('title', 'Unknown')} - {k.get('content', '')[:100]}"
                )

        prompt_parts.append("\n请对上述商品和知识进行评分并排序。")

        return "\n".join(prompt_parts)

    def _parse_rerank_response(self, response: str) -> Dict[str, Any]:
        """解析LLM精排响应"""
        import json
        import re

        try:
            # 尝试直接解析JSON
            return json.loads(response)
        except json.JSONDecodeError:
            # 尝试提取JSON块
            json_match = re.search(r'\{[\s\S]*\}', response)
            if json_match:
                try:
                    return json.loads(json_match.group())
                except:
                    pass

            # 解析失败，返回空
            logger.warning(f"无法解析LLM精排响应: {response[:200]}")
            return {}

    def _apply_scores(self, items: List[Dict], scores: List[Dict]) -> List[Dict]:
        """应用评分并重新排序"""
        # 创建评分映射
        score_map = {}
        for score_item in scores:
            # 尝试匹配ID或索引
            key = score_item.get("id")
            if key:
                score_map[key] = score_item

        # 为每个项目添加评分
        for i, item in enumerate(items):
            item_id = item.get("id") or str(i + 1)

            if item_id in score_map:
                item["rerank_score"] = score_map[item_id].get("score", 0)
                item["rerank_reason"] = score_map[item_id].get("reason", "")
            else:
                # 没有评分的项目，使用原有相关性分数
                item["rerank_score"] = item.get("relevance", 50)
                item["rerank_reason"] = "保持原排序"

        # 按评分降序排序
        sorted_items = sorted(items, key=lambda x: x.get("rerank_score", 0), reverse=True)

        return sorted_items

    async def generate_response(
        self,
        query: str,
        retrieval_results: Dict[str, Any],
        context_limit: int = 2000
    ) -> str:
        """
        基于检索结果生成回答（增强版：包含来源引用和避坑提示）

        Args:
            query: 用户问题
            retrieval_results: 检索结果（包含citations和pitfalls）
            context_limit: 上下文长度限制

        Returns:
            生成的回答（含来源引用和避坑提示）
        """
        # 1. 构建上下文
        context_parts = []

        # 添加商品信息
        if retrieval_results["products"]:
            context_parts.append("【相关商品】")
            for p in retrieval_results["products"][:3]:
                context_parts.append(
                    f"- {p['name']} ({p.get('brand', '')}) ¥{p.get('price', 0)}: {p.get('description', '')[:100]}"
                )

        # 添加知识库信息
        if retrieval_results["knowledge"]:
            context_parts.append("\n【相关知识】")
            for k in retrieval_results["knowledge"][:2]:
                context_parts.append(f"- {k['title']}: {k.get('content', '')[:100]}")

        context = "\n".join(context_parts)

        # 2. 构建避坑提示上下文
        pitfall_context = ""
        if retrieval_results.get("pitfalls"):
            pitfall_context = "\n\n【避坑提示】\n"
            for p in retrieval_results["pitfalls"][:2]:
                pitfall_context += f"- ⚠️ {p['title']}: {p['description']}\n"

        # 3. 构建提示词
        prompt = f"""你是一个专业、客观的电商导购助手。请根据以下信息回答用户问题。

用户问题：{query}

参考信息：
{context}
{pitfall_context}

回答要求：
1. 准确回答用户问题
2. 正文里不要输出[来源1]、[来源2]、信息来源、参考资料等引用标记
3. 不要在正文里重复输出“避坑提示”或“信息来源”板块，这些会在界面单独展示
4. 如果参考信息不足，请诚实告知
5. 优先使用简洁自然的中文，可用短标题和要点，但不要堆砌格式

请开始回答："""

        # 4. 调用LLM生成回答
        try:
            response = await self.llm_service.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=800
            )

            return response

        except Exception as e:
            logger.error(f"❌ 回答生成失败: {e}")
            return "抱歉，我暂时无法回答这个问题。请尝试其他问题或联系人工客服。"

    async def rag_query(
        self,
        query: str,
        filters: Optional[Dict] = None,
        top_k: int = 5
    ) -> Dict[str, Any]:
        """
        完整的RAG查询流程（增强版）

        Args:
            query: 用户查询
            filters: 过滤条件
            top_k: 召回数量

        Returns:
            检索结果 + 生成的回答 + 来源引用 + 避坑提示
        """
        # 1. 检索
        retrieval_results = await self.retrieve(
            query=query,
            filters=filters,
            top_k=top_k
        )

        # 2. 重排序（可选）
        # retrieval_results = await self.rerank(retrieval_results, query)

        # 3. 生成回答
        response = await self.generate_response(
            query=query,
            retrieval_results=retrieval_results
        )

        return {
            "query": query,
            "response": response,
            "citations": retrieval_results.get("citations", []),      # 增强的来源引用
            "pitfalls": retrieval_results.get("pitfalls", []),         # 避坑提示
            "products": retrieval_results["products"],
            "knowledge": retrieval_results["knowledge"],
            "metadata": {
                "total_citations": len(retrieval_results.get("citations", [])),
                "total_pitfalls": len(retrieval_results.get("pitfalls", [])),
                "has_warnings": len(retrieval_results.get("pitfalls", [])) > 0
            }
        }


# ==================== 全局实例 ====================

_rag_retriever: Optional[RAGRetriever] = None


def get_rag_retriever() -> RAGRetriever:
    """
    获取RAG检索器单例

    Returns:
        RAGRetriever实例
    """
    global _rag_retriever
    if _rag_retriever is None:
        _rag_retriever = RAGRetriever()
    return _rag_retriever


# ==================== 使用示例 ====================

"""
from app.services.rag import get_rag_retriever

retriever = get_rag_retriever()

# 完整RAG查询
result = await retriever.rag_query(
    query="iPhone 15 和华为 Mate 60 哪个更好？",
    top_k=5
)

print(result["response"])
print("来源:", result["sources"])
"""
