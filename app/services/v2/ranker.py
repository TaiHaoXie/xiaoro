from typing import List, Dict, Any, Optional
from .models import CanonicalTurn


class Ranker:
    def rank(
        self,
        candidates: List[Dict[str, Any]],
        turn: CanonicalTurn,
        top_n: int = 8,
    ) -> List[Dict[str, Any]]:
        if not candidates:
            return []

        scored = []
        for product in candidates:
            score = self._score_product(product, turn)
            scored.append((score, product))

        scored.sort(key=lambda x: x[0], reverse=True)

        selected = [p for s, p in scored[:top_n]]

        for i, p in enumerate(selected):
            p["_rank_position"] = i
            p["_match_reason"] = self._build_match_reason(p, turn, i)

        return selected

    def _score_product(self, product: Dict[str, Any], turn: CanonicalTurn) -> float:
        score = 0.0
        price = product.get("price_val", 0)
        specs = product.get("_specs", {})
        concerns_list = product.get("concerns_list", [])
        suitable_skin = product.get("suitable_skin", "")
        positioning = product.get("positioning", "")
        sales = product.get("sales_count", 0) or 0
        rating = float(product.get("rating") or 0)

        score += min(sales / 1000, 10) * 0.5
        score += rating * 2

        if turn.brand:
            brand = product.get("brand", "") or ""
            if turn.brand.lower() in brand.lower():
                score += 20

        # 产品名关键词匹配（如"B5"、"小白瓶"、"小棕瓶"、"大哥大"等系列名）
        msg = turn.raw_message or ""
        product_name = (product.get("name") or "") + " " + (product.get("display_name") or "") + " " + (positioning or "")
        # 去掉已在brand/品类/疑问词中处理过的部分，只看剩下的关键词
        stop_parts = ["敏感肌", "油皮", "干皮", "混油", "混干", "中性皮", "全肤质",
                      "面霜", "精华", "防晒", "眼霜", "洁面", "面膜", "乳液", "爽肤水", "散粉", "口红", "粉底",
                      "推荐", "哪个好", "哪个更好", "对比", "比较", "选哪个", "能用吗", "可以吗", "适合吗",
                      "什么年龄", "适合什么", "怎么", "如何", "平替", "替代", "有没有", "吗", "呢", "啊"]
        if turn.brand:
            stop_parts.append(turn.brand)
        remainder = msg
        for sp in stop_parts:
            remainder = remainder.replace(sp, " ")
        # 剩下的2-6个字符的中文/英文片段作为产品线索
        import re as _re
        clues = [w for w in _re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fa5]{2,6}", remainder) if len(w) >= 2]
        name_hits = 0
        for clue in clues:
            if clue in product_name and clue not in ("能用", "适合", "可以"):
                name_hits += 1
        if name_hits > 0:
            score += name_hits * 25

        # 别名表命中的name_clue（强引导，权重更高）
        if turn.name_clues:
            alias_hits = 0
            for clue in turn.name_clues:
                if clue in product_name:
                    alias_hits += 1
            if alias_hits > 0:
                score += alias_hits * 40

        if turn.category:
            cat = product.get("category", "") or ""
            subcat = specs.get("subcategory", "") or ""
            if turn.category in cat or turn.category in subcat:
                score += 15

        if turn.concerns:
            matched_concerns = 0
            for c in turn.concerns:
                concern_text = " ".join(concerns_list) + " " + positioning + " " + (product.get("description", "") or "")
                if c in concern_text:
                    matched_concerns += 1
            score += matched_concerns * 8

        if turn.skin_type:
            skin_type_text = suitable_skin + " " + (product.get("description", "") or "")
            if turn.skin_type in skin_type_text:
                score += 12
            if "敏感肌" in turn.skin_type:
                if any(w in skin_type_text for w in ["温和", "无刺激", "敏感肌可用", "屏障修护", "低刺激"]):
                    score += 6
                if any(w in skin_type_text for w in ["酒精", "香精", "高浓度酸"]):
                    score -= 8

        if turn.budget_min is not None and turn.budget_max is not None:
            mid_budget = (turn.budget_min + turn.budget_max) / 2
            if turn.budget_flexible:
                if turn.budget_min * 0.9 <= price <= turn.budget_max * 1.1:
                    score += 15
                    distance = abs(price - mid_budget) / mid_budget
                    score += (1 - min(distance, 1)) * 10
                elif price < turn.budget_min * 0.9:
                    score -= (turn.budget_min - price) / turn.budget_min * 20
                else:
                    score -= (price - turn.budget_max) / turn.budget_max * 25
            else:
                if turn.budget_min <= price <= turn.budget_max:
                    score += 20
                    distance = abs(price - mid_budget) / mid_budget
                    score += (1 - min(distance, 1)) * 10
                else:
                    score -= min(abs(price - mid_budget) / mid_budget * 30, 50)
        elif turn.budget_max is not None:
            if price <= turn.budget_max:
                score += 10
            else:
                score -= (price - turn.budget_max) / turn.budget_max * 30

        return score

    def _build_match_reason(self, product: Dict[str, Any], turn: CanonicalTurn, position: int) -> Dict[str, Any]:
        reasons = []
        tags = []

        price = product.get("price_val", 0)
        concerns_list = product.get("concerns_list", [])
        key_ingredients = product.get("key_ingredients_list", [])
        suitable_skin = product.get("suitable_skin", "")
        positioning = product.get("positioning", "")
        pitfalls = product.get("pitfalls", "")

        if turn.budget_min and turn.budget_max:
            mid = (turn.budget_min + turn.budget_max) / 2
            if abs(price - mid) / mid < 0.15:
                reasons.append(f"价格贴近预算区间")
                tags.append("budget_fit")

        if turn.skin_type and turn.skin_type in suitable_skin:
            reasons.append(f"标注{turn.skin_type}适用")
            tags.append("skin_fit")

        if turn.concerns:
            matched = [c for c in turn.concerns if any(c in cl for cl in concerns_list) or c in positioning or c in (product.get("description") or "")]
            if matched:
                reasons.append(f"主打{ '、'.join(matched[:2]) }")
                tags.append("concern_fit")

        if turn.brand and turn.brand.lower() in (product.get("brand") or "").lower():
            reasons.append(f"{turn.brand}出品")
            tags.append("brand_fit")

        if not reasons and positioning:
            short_pos = positioning[:40]
            reasons.append(short_pos)
            tags.append("positioning")

        return {
            "reasons": reasons[:3],
            "tags": tags,
            "key_ingredients": key_ingredients[:4],
            "pitfalls": pitfalls,
        }
