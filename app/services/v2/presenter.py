import re
from typing import List, Dict, Any, Optional, Tuple
from .models import CanonicalTurn, AnswerMode, FollowupType, FollowupScope
from .retriever import clean_product_name


FORBIDDEN_PHRASES = [
    "想换个侧重", "按需选择", "更好使用感", "性价比最稳",
    "根据个人需求", "根据自己的需求", "大家可以根据", "大家按需",
    "建议先", "最好是", "一定不要", "墙裂推荐", "yyds", "YYDS",
    "闭眼入", "踩雷", "不踩雷", "入手不亏", "不容错过",
    "是您的", "为您打造", "专为您", "是不二之选", "性价比极高",
    "强烈推荐", "非常值得入手", "功效卓越", "是理想选择", "值得拥有",
]

FOOTER_NOTE = "参考价为入库时价格，实时活动价以商品链接为准。"


class Presenter:

    def present_recommendation(
        self,
        turn: CanonicalTurn,
        products: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        if not products:
            return self.present_no_match(turn)
        ordered_products = self._order_recommendation_products(turn, products)
        text = self._build_diandian_recommendation(turn, ordered_products)
        return {
            "answer_mode": AnswerMode.RECOMMENDATION,
            "text": text,
            "products": ordered_products[:3],
            "comparison": None,
            "citations": [],
            "pitfalls": self._build_recommendation_pitfalls(turn, ordered_products[:3]),
        }

    def present_followup(
        self,
        turn: CanonicalTurn,
        products: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        if not products:
            return self.present_no_match(turn)
        if turn.followup_type == FollowupType.SUITABILITY:
            products = self._order_suitability_followup_products(turn, products)
        p = products[0]
        name = self._short_product_name(p)
        price = p.get("price_val") or p.get("price") or 0
        positioning = (p.get("positioning") or "").strip()
        brand = (p.get("brand") or "").strip()
        category_name, *_ = self._detect_category(turn, products)

        try:
            price_int = int(price) if float(price) == int(float(price)) else price
        except Exception:
            price_int = price
        price_text = f"约¥{price_int}" if price else "价格见详情页"
        spec = self._extract_product_spec(p)
        if spec and price:
            price_text = f"{price_text} / {spec}"

        lines = []
        response_products = products[:4]
        secondary_types = {
            item.get("followup_type")
            for item in (turn.secondary_intents or [])
            if isinstance(item, dict)
        }
        if turn.followup_type == FollowupType.PRICE:
            response_products = products[:1]
            lines.append(f"{name} 参考价 {price_text}。")
            if positioning:
                pos_price = positioning[:100].rstrip("，。；、,; ")
                if pos_price:
                    lines.append(pos_price + "。")
            if FollowupType.SUITABILITY.value in secondary_types:
                skin_text = self._clean_suitable_skin(p.get("suitable_skin") or "")
                direct_judgement = self._suitability_direct_judgement(turn, skin_text)
                if direct_judgement:
                    lines.append(direct_judgement)
                if skin_text:
                    lines.append(f"从适用肤质看：{skin_text}。")
            lines.append(FOOTER_NOTE)
        elif turn.followup_type == FollowupType.CHEAPER:
            cheaper = [x for x in products[1:4] if (x.get("price_val") or x.get("price") or 999999) < (price or 999999)]
            if cheaper:
                response_products = cheaper[:2]
                lines.append("相对平价的选择可以看这几款，价格更友好，但功效侧重略有不同。")
                lines.append("")
                for item in response_products:
                      lines.append(self._build_followup_product_line(item, category_name))
            else:
                response_products = products[:2]
                lines.append("相对平价的选择可以先看这几款，价格更友好，但功效侧重略有不同。")
                lines.append("")
                for item in response_products:
                      lines.append(self._build_followup_product_line(item, category_name))
            lines.append(FOOTER_NOTE)
        elif turn.followup_type == FollowupType.HIGHER_BUDGET:
            response_products = products[:3]
            names = "、".join(self._short_product_name(x) for x in response_products[:3])
            floor_text = f"¥{int(turn.budget_min)}以上" if turn.budget_min else "更高一档预算"
            lines.append(f"我按{floor_text}重新往上筛，并避开刚才列表里已经出现的款。")
            lines.append("")
            if names:
                lines.append(f"可以重点看：{names}。")
            for item in response_products[:3]:
                  lines.append(self._build_followup_product_line(item, category_name))
            lines.append("")
            lines.append(FOOTER_NOTE)
        elif turn.followup_type == FollowupType.MORE_OPTIONS:
            response_products = products[:3]
            if FollowupType.HIGHER_BUDGET.value in secondary_types:
                direction = "往预算更高、定位更进阶的方向"
            elif FollowupType.CHEAPER.value in secondary_types:
                direction = "往预算更低、价格更友好的方向"
            elif FollowupType.SUITABILITY.value in secondary_types:
                direction = "往更温和、更适合当前肤质的方向"
            else:
                direction = "换一批不重复的选择"
            lines.append(f"我理解你是想排除刚才那批，再{direction}找。")
            lines.append("")
            for item in response_products[:3]:
                  lines.append(self._build_followup_product_line(item, category_name))
            lines.append("")
            lines.append(FOOTER_NOTE)
        elif turn.followup_type == FollowupType.SUITABILITY:
            is_selection = any(cue in (turn.raw_message or "") for cue in ["哪款", "哪个", "哪一个", "哪一款", "更适合", "最适合", "优先"])
            has_explicit_anchor = bool(turn.referenced_products or turn.referenced_product_ids or turn.name_clues)
            if turn.followup_scope == FollowupScope.OUT_OF_CANDIDATES and not has_explicit_anchor and len(products) > 1:
                target = self._suitability_target_phrase(turn) or "这个新条件"
                if is_selection:
                    response_products = products[:1]
                    winner = response_products[0]
                    winner_name = self._short_product_name(winner)
                    lines.append(f"按{target}重新看，优先选{winner_name}。")
                    skin_text = self._clean_suitable_skin(winner.get("suitable_skin") or "")
                    direct_judgement = self._suitability_direct_judgement(turn, skin_text)
                    if direct_judgement:
                        lines.append(direct_judgement)
                    if skin_text:
                        lines.append(f"从适用肤质看：{skin_text}。")
                    winner_price = winner.get("price_val") or winner.get("price") or 0
                    try:
                        winner_price_value = int(winner_price) if float(winner_price) == int(float(winner_price)) else winner_price
                    except Exception:
                        winner_price_value = winner_price
                    winner_price_text = f"约¥{winner_price_value}" if winner_price else "价格见详情页"
                    winner_spec = self._extract_product_spec(winner)
                    if winner_spec and winner_price:
                        winner_price_text = f"{winner_price_text} / {winner_spec}"
                    lines.append(f"参考价 {winner_price_text}。")
                    lines.append(FOOTER_NOTE)
                    return {
                        "answer_mode": AnswerMode.FOLLOWUP,
                        "text": "\n".join(lines),
                        "products": response_products,
                        "comparison": None,
                        "citations": [],
                        "pitfalls": self._build_recommendation_pitfalls(turn, response_products),
                    }
                response_products = products[:3]
                lines.append(f"按{target}重新看，可以优先看这几款：")
                lines.append("")
                for item in response_products:
                    lines.append(self._build_followup_product_line(item, category_name))
                lines.append("")
                lines.append(FOOTER_NOTE)
                return {
                    "answer_mode": AnswerMode.FOLLOWUP,
                    "text": "\n".join(lines),
                    "products": response_products,
                    "comparison": None,
                    "citations": [],
                    "pitfalls": self._build_recommendation_pitfalls(turn, response_products),
                }
            if is_selection or has_explicit_anchor:
                response_products = products[:1]
            else:
                response_products = products[:3]
            skin_text = self._clean_suitable_skin(p.get("suitable_skin") or "")
            if is_selection and len(products) > 1:
                # "哪个更适合X"：读起来要像从多款里挑出的那一款，而不是孤零零描述一款
                lead = f"这几款里，{name} 更贴合"
                target = self._suitability_target_phrase(turn)
                lead += f"{target}。" if target else "你说的诉求。"
                lines.append(lead)
                if positioning:
                    lines.append(f" {positioning[:80]}。")
            else:
                pos_text = (positioning or "").strip()
                if pos_text and len(pos_text) >= 4:
                    lines.append(f"{name}，{pos_text[:80]}。")
                else:
                    lines.append(f"{name}。")
            if skin_text:
                direct_judgement = self._suitability_direct_judgement(turn, skin_text)
                if direct_judgement:
                    lines.append(f" {direct_judgement}")
                lines.append(f" 从适用肤质看：{skin_text}。")
            lines.append(f" 参考价 {price_text}。")
            lines.append(f" {FOOTER_NOTE}")
        elif turn.followup_type == FollowupType.INGREDIENT:
            response_products = products[:1]
            ingredients = p.get("key_ingredients_list") or []
            if not ingredients:
                parsed = self._parse_desc(p.get("suitable_skin") or "")
                ci = parsed.get("核心成分", "")
                if ci:
                    ingredients = [x.strip() for x in re.split(r"[、,，;；]", ci) if x.strip()][:5]
            if ingredients:
                lines.append(f"{name} 主要核心成分：")
                for ingredient in ingredients[:5]:
                    lines.append(f"- {ingredient}：{self._explain_ingredient_role(ingredient)}")
            else:
                lines.append(f"{name} 的完整成分表请以商品详情页/包装备案为准。")
            lines.append(f" 参考价 {price_text}。{FOOTER_NOTE}")
        elif turn.followup_type == FollowupType.EFFICACY:
            response_products = products[:1]
            parsed = self._parse_desc(p.get("suitable_skin") or "")
            parsed.update(self._parse_desc(p.get("description") or ""))

            lines.append(f"{name} 主打这几点：")
            efficacy_parts = []

            raw_concerns = p.get("concerns_list") or []
            if raw_concerns:
                efficacy_parts.append(f"针对{'、'.join(raw_concerns[:4])}")

            gongxiao = parsed.get("主打功效", "")
            if gongxiao:
                for seg in re.split(r"[；;、，,]", gongxiao):
                    seg = seg.strip()
                    if seg and len(seg) <= 20 and seg not in "".join(efficacy_parts):
                        efficacy_parts.append(seg)
                    if len(efficacy_parts) >= 4:
                        break

            if positioning:
                pos_short = positioning[:60].rstrip("，。；,; ")
                if pos_short and pos_short not in "".join(efficacy_parts):
                    efficacy_parts.append(pos_short)

            claim_notes = p.get("claim_notes") or []
            if claim_notes:
                for c in claim_notes[:2]:
                    c_str = str(c).strip()
                    if c_str and len(c_str) < 40 and c_str not in "".join(efficacy_parts):
                        efficacy_parts.append(f"品牌资料称{c_str}")

            mechanism_notes = p.get("mechanism_notes") or []
            if mechanism_notes and not efficacy_parts:
                m = str(mechanism_notes[0]).strip()
                if m and len(m) < 60:
                    efficacy_parts.append(m)

            if efficacy_parts:
                for part in efficacy_parts[:5]:
                    lines.append(f"- {part}")
            else:
                dingwei = parsed.get("定位", "")
                if dingwei:
                    lines.append(f"- {dingwei[:80]}")
                else:
                    lines.append("- 具体功效以商品详情页标注为准")

            core_ing = p.get("key_ingredients_list") or []
            if not core_ing:
                ci_text = parsed.get("核心成分", "")
                if ci_text:
                    core_ing = [x.strip() for x in re.split(r"[、,，;；]", ci_text) if x.strip()][:4]
            if core_ing:
                lines.append("")
                lines.append(f"核心成分：{'、'.join(core_ing[:4])}")
            lines.append("")
            lines.append(f"参考价 {price_text}。{FOOTER_NOTE}")
        elif turn.followup_type == FollowupType.USAGE_TIME:
            raw_msg = turn.raw_message or ""
            is_direct_usage_q = any(kw in raw_msg for kw in ["怎么用", "如何用", "使用方法", "怎么涂", "如何涂", "怎么抹"]) and not any(kw in raw_msg for kw in ["白天", "晚上", "夜间", "早上", "早晨", "晚间", "早晚"])
            raw_usage_steps = p.get("usage_steps") or []
            usage_steps = self._filter_real_usage_steps(raw_usage_steps)
            if is_direct_usage_q and usage_steps:
                response_products = [p]
                lines.append(f"{name} 的用法：")
                for step in usage_steps[:5]:
                    step_text = str(step).strip()
                    if step_text:
                        lines.append(f"- {step_text}")
                safety = [str(s).strip() for s in (p.get("safety_notes") or [])[:2] if str(s).strip()]
                safety = [s for s in safety if not self._looks_like_review(s)]
                if safety:
                    lines.append("")
                    lines.append("注意点：")
                    for s in safety:
                        lines.append(f"- {s}")
                texture = [str(t).strip() for t in (p.get("texture_notes") or [])[:2] if str(t).strip()]
                texture = [t for t in texture if not self._looks_like_review(t) and len(t) < 50]
                if texture:
                    lines.append("")
                    lines.append(f"质地：{'；'.join(texture)}")
                lines.append("")
                lines.append(f"参考价 {price_text}。{FOOTER_NOTE}")
            elif is_direct_usage_q:
                response_products = [p]
                lines.append(f"{name} 的用法：")
                lines.append(self._generic_usage_advice(p))
                lines.append("")
                lines.append(f"参考价 {price_text}。{FOOTER_NOTE}")
            else:
                # 日夜分工/早C晚A类追问
                def short_name(product: Dict[str, Any]) -> str:
                    return self._short_product_name(product)

                def product_text(product: Dict[str, Any]) -> str:
                    return " ".join([
                        product.get("name") or "",
                        product.get("display_name") or "",
                        product.get("category") or "",
                        product.get("positioning") or "",
                        product.get("suitable_skin") or "",
                        product.get("description") or "",
                        " ".join(product.get("concerns_list") or []),
                        " ".join(product.get("key_ingredients_list") or []),
                    ])

                def day_score(product: Dict[str, Any]) -> Tuple[int, str]:
                    text = product_text(product)
                    name_text = " ".join([
                        product.get("brand") or "",
                        product.get("name") or "",
                        product.get("display_name") or "",
                        product.get("positioning") or "",
                    ])
                    if product.get("category") == "防晒":
                        if any(w in name_text + text for w in ["小白管", "兰蔻", "妆前", "贴妆", "空气感", "抗光老"]):
                            return 96, "更适合日常通勤和妆前打底，重点看轻薄、贴妆和不搓泥"
                        if any(w in name_text + text for w in ["理肤泉", "UVMUNE", "大哥大", "轻盈", "无香"]):
                            return 94, "通勤防护更稳，兼顾超长波UVA防护和敏感肌可用，日常比户外防水款更顺手"
                        if any(w in name_text + text for w in ["薇诺娜", "敏感肌", "混敏", "干敏", "温和"]):
                            return 92, "敏感肌通勤更稳，先看是否熏眼、泛红和搓泥"
                        if any(w in name_text + text for w in ["怡思丁", "水感", "通勤"]):
                            return 88, "水感清爽，适合普通通勤，但敏感肌要先测试"
                        if any(w in name_text + text for w in ["安热沙", "安耐晒", "小金瓶", "蓝胖子", "防水", "防汗", "户外", "珀莱雅盾护"]):
                            return 78, "更偏户外、防水防汗或长时间防护，通勤不是第一优先"
                        return 82, "日常防护可用，按膜感和是否熏眼决定"
                    if any(w in name_text for w in ["绿宝瓶", "赫莲娜"]):
                        return 96, "白天通勤更合适，偏强韧维稳和抗氧，后续叠防晒压力小"
                    if any(w in name_text for w in ["修丽可", "紫米"]) or any(w in text for w in ["玻色因", "A醇", "视黄醇", "视黄醛"]):
                        return 70, "白天不是第一优先，干敏肌更建议放到夜间低频建立耐受"
                    if any(w in name_text for w in ["小棕瓶", "小黑瓶"]) or "二裂酵母" in text:
                        return 84, "白天也能用，但它更偏修护维稳，夜间发挥更顺"
                    if any(w in text for w in ["抗氧", "舒缓", "强韧", "保湿", "维稳"]):
                        return 88, "白天可做维稳打底，重点是后续防晒要跟上"
                    if any(w in text for w in ["烟酰胺", "美白", "淡斑", "提亮"]):
                        return 78, "白天可以用，但必须严格防晒，泛红刺痛时先停"
                    return 72, "白天可用，先按皮肤耐受和后续防晒决定频率"

                def night_score(product: Dict[str, Any]) -> Tuple[int, str]:
                    text = product_text(product)
                    name_text = " ".join([
                        product.get("brand") or "",
                        product.get("name") or "",
                        product.get("display_name") or "",
                        product.get("positioning") or "",
                    ])
                    if any(w in name_text for w in ["绿宝瓶", "赫莲娜"]):
                        return 82, "早晚都能用，夜间用偏强韧维稳，刺激风险比猛药型抗老低"
                    if any(w in name_text for w in ["小棕瓶", "小黑瓶"]) or "二裂酵母" in text:
                        return 95, "夜间修护逻辑更顺，干敏肌做抗初老更稳，先低频建立耐受"
                    if any(w in name_text for w in ["修丽可", "紫米"]) or any(w in text for w in ["玻色因", "A醇", "视黄醇", "视黄醛"]):
                        return 88, "更偏夜间集中抗老淡纹，但干敏肌要隔天夜间用，别和酸/A醇同晚叠加"
                    if any(w in text for w in ["绿宝瓶", "舒缓", "强韧", "保湿"]):
                        return 82, "早晚都能用，夜间用偏强韧维稳，刺激风险比猛药型抗老低"
                    if any(w in text for w in ["抗皱", "淡纹", "紧致"]):
                        return 80, "可以放在夜间做抗老，但干敏肌需要先看耐受"
                    if any(w in text for w in ["烟酰胺", "美白", "淡斑", "提亮"]):
                        return 72, "可以夜间用，但泛红刺痛时要停用观察"
                    return 60, "早晚都可，按皮肤耐受决定频率"

                focus = turn.usage_time_focus or "night"
                day_ranked = sorted(products[:4], key=lambda item: day_score(item)[0], reverse=True)
                night_ranked = sorted(products[:4], key=lambda item: night_score(item)[0], reverse=True)
                is_sunscreen_followup = turn.category == "防晒" or any((item.get("category") == "防晒") for item in products[:4])

                if focus == "day":
                    best = day_ranked[0]
                    if is_sunscreen_followup:
                        response_products = [best]
                        lines.append(f"通勤用优先选{short_name(best)}。")
                        lines.append("")
                        lines.append("判断理由：")
                        _, best_reason = day_score(best)
                        lines.append(f"- {short_name(best)}：{best_reason}。")
                        alternatives = [short_name(item) for item in day_ranked[1:3]]
                        if alternatives:
                            lines.append(f"- {'、'.join(alternatives)}更偏户外、防水或油皮清爽场景，不是这轮通勤首选。")
                        lines.append("")
                        lines.append("通勤用法：出门前15分钟足量涂，日常重点看不熏眼、不搓泥；长时间户外或出汗后再补涂。")
                    else:
                        response_products = day_ranked[:4]
                        lines.append(f"白天用优先选{short_name(best)}。")
                        lines.append("")
                        lines.append("判断理由：")
                        for item in day_ranked[:3]:
                            _, reason = day_score(item)
                            lines.append(f"- {short_name(item)}：{reason}。")
                        lines.append("")
                        lines.append("白天用法：精华后面一定叠防晒；干敏肌先隔天早上用，观察泛红、刺痛、闷闭口，稳定后再提高频次。")
                elif focus == "both":
                    day_best = day_ranked[0]
                    night_best = night_ranked[0]
                    response_products = []
                    seen = set()
                    for item in [day_best, night_best] + day_ranked + night_ranked:
                        key = str(item.get("id") or item.get("name") or id(item))
                        if key not in seen:
                            response_products.append(item)
                            seen.add(key)
                        if len(response_products) >= 4:
                            break
                    lines.append(f"日夜分工：白天优先选{short_name(day_best)}，夜间优先选{short_name(night_best)}。")
                    lines.append("")
                    lines.append("判断理由：")
                    _, day_reason = day_score(day_best)
                    _, night_reason = night_score(night_best)
                    lines.append(f"- 白天优先：{short_name(day_best)}，{day_reason}。")
                    lines.append(f"- 夜间优先：{short_name(night_best)}，{night_reason}。")
                    for item in response_products:
                        if item is day_best or item is night_best:
                            continue
                        _, reason = night_score(item)
                        lines.append(f"- 备选：{short_name(item)}，{reason}。")
                        break
                    lines.append("")
                    lines.append("日夜分工用法：白天用偏维稳抗氧的款并叠防晒；夜间再用偏修护/抗老的款，干敏肌不要同晚叠加酸类/A醇/去角质产品。")
                else:
                    ranked = night_ranked
                    response_products = ranked[:4]
                    best = ranked[0]
                    lines.append(f"夜间用优先选{short_name(best)}。")
                    lines.append("")
                    lines.append("判断理由：")
                    for item in ranked[:3]:
                        _, reason = night_score(item)
                        lines.append(f"- {short_name(item)}：{reason}。")
                    lines.append("")
                    lines.append("干敏肌夜间用法：先隔天晚上用，连续3-5天没有泛红、刺痛、闷闭口，再考虑提高频次；同一晚别叠加酸类/A醇/去角质产品。")
                lines.append(FOOTER_NOTE)
        else:
            lines.append(f"{name} 参考价 {price_text}。")
            if positioning:
                lines.append(f" {positioning[:80]}。")
            lines.append(f" {FOOTER_NOTE}")

        raw_text = "\n".join(lines)
        supplement = self._build_secondary_followup_supplement(turn, products)
        if supplement:
            if FOOTER_NOTE in raw_text:
                raw_text = raw_text.replace(FOOTER_NOTE, f"{supplement}\n{FOOTER_NOTE}", 1)
            else:
                raw_text = f"{raw_text}\n{supplement}"

        text = self._sanitize_plain(raw_text)
        return {
            "answer_mode": AnswerMode.FOLLOWUP,
            "text": text,
            "products": response_products,
            "comparison": None,
        }

    @staticmethod
    def _suitability_target_phrase(turn: CanonicalTurn) -> str:
        """从追问里提取"更适合谁/什么场景"，用于把选择型回答写成一句人话。"""
        msg = turn.raw_message or ""
        if "油肌" in msg:
            return "油皮"
        for skin in ["油痘肌", "干敏肌", "油敏肌", "混油皮", "混干皮", "敏感肌", "油皮", "干皮", "痘肌", "孕妇"]:
            if skin in msg:
                return skin
        for scene in ["通勤", "熬夜", "换季", "户外", "日常"]:
            if scene in msg:
                return f"{scene}场景"
        if turn.skin_type:
            return turn.skin_type
        return ""

    @staticmethod
    def _suitability_direct_judgement(turn: CanonicalTurn, skin_text: str) -> str:
        target = turn.skin_type or Presenter._suitability_target_phrase(turn)
        text = skin_text or ""
        if target == "油皮":
            if any(cue in text for cue in ["偏润", "厚重", "油皮夏季可能"]):
                return "对油皮来说不算第一优先，夏季或出油多时可能偏润。"
            if any(cue in text for cue in ["油皮", "混油", "清爽", "控油"]):
                return "对油皮来说可以参考，但还是要看实际肤感和闷痘风险。"
        if target and target in text:
            return f"对{target}来说可以参考，但先按耐受情况低频试用。"
        return ""

    def _order_suitability_followup_products(
        self,
        turn: CanonicalTurn,
        products: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        scored_products = [
            product for product in products
            if isinstance(product, dict) and isinstance(product.get("_in_candidate_score"), (int, float))
        ]
        if scored_products:
            scores = [float(product.get("_in_candidate_score") or 0) for product in products[:2]]
            if len(scores) == 1 or scores[0] - scores[1] >= 8:
                return products
        msg = turn.raw_message or ""
        if not any(cue in msg for cue in ["敏感肌", "敏感", "敏皮", "干敏", "混敏", "泛红", "屏障"]):
            return products

        def score(product: Dict[str, Any]) -> float:
            text = " ".join([
                product.get("name") or "",
                product.get("display_name") or "",
                product.get("brand") or "",
                product.get("positioning") or "",
                product.get("suitable_skin") or "",
                product.get("description") or "",
                " ".join(product.get("concerns_list") or []),
                " ".join(product.get("key_ingredients_list") or []),
            ])
            value = 0.0
            if any(w in text for w in ["敏感肌", "敏皮", "混敏", "干敏", "屏障", "医美术后", "舒缓"]):
                value += 30
            if any(w in text for w in ["温和", "低刺激", "不刺激", "无香精", "无酒精", "薇诺娜", "理肤泉"]):
                value += 12
            if any(w in text for w in ["薇诺娜", "理肤泉"]):
                value += 10
            if any(w in text for w in ["敏感肌可用", "敏感肌适用", "敏感肌友好"]):
                value += 12
            if any(w in text for w in ["敏感肌除外", "酒精感", "香精", "刺痛明显"]):
                value -= 30
            if any(w in text for w in ["怡思丁", "水感"]) and any(w in msg for w in ["敏感", "敏皮", "干敏", "混敏", "泛红", "屏障", "刺激"]):
                value -= 12
            if "油皮/混油/油痘肌" in text and not any(w in text for w in ["敏感", "温和", "低刺激"]):
                value -= 18
            return value

        return sorted(products, key=score, reverse=True)

    def _build_secondary_followup_supplement(
        self,
        turn: CanonicalTurn,
        products: List[Dict[str, Any]],
    ) -> str:
        if not turn.secondary_intents or not products:
            return ""

        parts = []
        seen = set()
        for item in turn.secondary_intents:
            ftype = item.get("followup_type")
            if not ftype or ftype in seen:
                continue
            seen.add(ftype)
            if ftype == FollowupType.CHEAPER.value:
                parts.append("如果想压预算，优先看当前列表里价格更低且诉求仍匹配的款；但不要只按低价牺牲肤质耐受。")
            elif ftype == FollowupType.HIGHER_BUDGET.value:
                parts.append("预算上探时要换到功效或肤感更明确的一档，不是把同一批商品换个说法。")
            elif ftype == FollowupType.MORE_OPTIONS.value:
                parts.append("这里会排除刚才已经给过的商品，再按你的新条件重找。")
            elif ftype == FollowupType.INGREDIENT.value:
                parts.append("完整成分以详情页和备案为准；敏感肌重点避开同晚叠加酸类、A醇或强去角质。")
            elif ftype == FollowupType.SUITABILITY.value:
                parts.append("敏感肌先局部试用，连续几天没有泛红、刺痛、闷闭口，再提高频次。")
            if len(parts) >= 2:
                break

        if not parts:
            return ""
        return "这里要注意：" + " ".join(parts)

    def present_compare(
        self,
        turn: CanonicalTurn,
        products: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        if len(products) < 2:
            return self.present_recommendation(turn, products)
        compare_products = products[:4]

        def price_label(p: Dict[str, Any]) -> str:
            price = p.get("price_val") or p.get("price") or 0
            if not price:
                return "价格见详情页"
            try:
                price_int = int(price) if float(price) == int(float(price)) else price
            except Exception:
                price_int = price
            return f"约¥{price_int}"

        def product_text(p: Dict[str, Any]) -> str:
            fields = [
                p.get("name") or "",
                p.get("display_name") or "",
                p.get("brand") or "",
                p.get("category") or "",
                p.get("positioning") or "",
                p.get("suitable_skin") or "",
                p.get("description") or "",
                " ".join(p.get("concerns_list") or []),
                " ".join(p.get("key_ingredients_list") or []),
            ]
            return " ".join(str(x) for x in fields if x)

        def focus_label(p: Dict[str, Any]) -> str:
            text = product_text(p)
            cat = p.get("category") or ""
            if cat == "防晒":
                if any(w in text for w in ["安热沙", "安耐晒", "防水", "防汗", "户外", "暴晒", "小金瓶"]):
                    return "户外防晒、防水防汗、长时间通勤"
                if any(w in text for w in ["小白管", "隔离", "妆前", "贴妆", "空气感", "轻透"]):
                    return "日常通勤、妆前打底、轻薄肤感"
                return "日常防晒和基础防护"
            if cat == "粉底液":
                if any(w in text for w in ["阿玛尼", "权力", "PRO", "轻薄"]):
                    return "持妆、遮瑕和相对轻薄妆效"
                if any(w in text for w in ["DW", "雅诗兰黛", "36小时", "15小时"]):
                    return "强持妆、控油和高遮瑕"
                return "持妆、遮瑕、控油和妆效"
            if any(w in text for w in ["B5", "泛醇", "积雪草", "急救", "理肤泉新B5", "B5万用霜"]):
                return "敏感期、泛红、屏障受损后的短期修护"
            if any(w in text for w in ["玉泽", "PBS", "仿生脂质", "神经酰胺"]):
                return "长期屏障维稳、干敏皮日常保湿修护"
            if any(w in text for w in ["理肤泉", "特安", "安心乳", "舒缓水乳"]):
                return "敏感肌舒缓、干敏维稳和基础保湿"
            if any(w in text for w in ["小白瓶", "烟酰胺", "淡斑", "美白"]):
                return "提亮、淡斑和均匀肤色"
            if any(w in text for w in ["小棕瓶", "抗老", "修护", "二裂酵母"]):
                return "抗初老、维稳和夜间修护"
            concerns = p.get("concerns_list") or []
            if concerns:
                return "、".join(concerns[:3])
            positioning = (p.get("positioning") or "").strip()
            return positioning[:36] if positioning else "基础护理"

        def risk_label(p: Dict[str, Any]) -> str:
            text = product_text(p)
            pitfalls = (p.get("pitfalls") or "").strip()
            if pitfalls:
                return pitfalls[:60]
            if p.get("category") == "防晒":
                return "防晒要足量涂抹并及时补涂，敏感肌先做局部测试"
            if p.get("category") == "粉底液":
                return "先确认色号、妆前保湿和成膜速度，干皮或起皮时不要直接厚涂"
            if any(w in text for w in ["B5", "厚敷", "乳木果油"]):
                return "油皮不建议全脸厚敷过夜，敏感期先薄涂"
            if any(w in text for w in ["玉泽", "面霜", "高保湿"]):
                return "偏滋润产品先少量试用，闷闭口肤质观察耐受"
            if p.get("category") == "精华":
                return "功效型产品先低频建立耐受，不要一次叠加多种猛料"
            return "首次使用先耳后/手腕内侧皮试"

        def contextual_score(p: Dict[str, Any]) -> float:
            text = product_text(p)
            target = self._suitability_target_phrase(turn)
            score = 0.0
            if target and target in text:
                score += 16
            if target in {"油皮", "油痘肌", "混油皮"}:
                if any(w in text for w in ["控油", "清爽", "哑光", "不闷", "油皮", "混油", "持妆", "水感"]):
                    score += 18
                if any(w in text for w in ["厚重", "滋润", "乳木果", "偏润", "闷痘", "油皮慎"]):
                    score -= 14
                if target == "油痘肌" and any(w in text for w in ["酒精", "香精", "刺痛"]):
                    score -= 8
            elif target in {"敏感肌", "干敏肌", "油敏肌"}:
                if any(w in text for w in ["敏感肌", "温和", "低刺激", "舒缓", "屏障", "无酒精", "无香精"]):
                    score += 20
                if any(w in text for w in ["酒精", "香精", "A醇", "酸类", "刺痛"]):
                    score -= 14
            elif target in {"干皮", "混干皮"}:
                if any(w in text for w in ["保湿", "滋润", "锁水", "角鲨烷", "乳木果", "干皮"]):
                    score += 18
                if any(w in text for w in ["拔干", "强控油", "强持妆", "哑光"]):
                    score -= 10

            for word in ["遮瑕", "持妆", "修护", "舒缓", "保湿", "控油", "提亮", "抗老", "防水", "防汗"]:
                if word in msg and word in text:
                    score += 8
            for term in getattr(turn, "exclude_terms", []) or []:
                if term and term in text and not any(marker in text for marker in [f"无{term}", f"不含{term}", f"未添加{term}"]):
                    score -= 40
            return score

        def contextual_reason(p: Dict[str, Any]) -> str:
            text = product_text(p)
            target = self._suitability_target_phrase(turn)
            if target in {"油皮", "油痘肌", "混油皮"}:
                if any(w in text for w in ["控油", "清爽", "哑光", "持妆", "水感"]):
                    return "它更贴近控油、清爽或持妆需求，油皮/油痘肌更容易用得住"
                return "它对油皮不是不能用，但还要重点观察闷痘和膜感"
            if target in {"敏感肌", "干敏肌", "油敏肌"}:
                if any(w in text for w in ["敏感肌", "温和", "低刺激", "舒缓", "屏障"]):
                    return "它的温和、舒缓或屏障修护信号更明确，敏感肌试错成本更低"
                return "它不是敏感肌第一优先，建议先小范围试用"
            if target in {"干皮", "混干皮"}:
                if any(w in text for w in ["保湿", "滋润", "锁水", "角鲨烷", "乳木果"]):
                    return "它的保湿锁水信号更强，干皮更不容易卡干"
                return "它更偏持妆或清爽，干皮要先做好妆前保湿"
            if any(w in msg for w in ["遮瑕", "持妆", "控油"]):
                return f"它在{focus_label(p)}这条线上更贴近你问的重点"
            return f"它的侧重点是{focus_label(p)}，和这轮问题更贴近"

        a, b = compare_products[:2]
        a_name = self._short_product_name(a)
        b_name = self._short_product_name(b)
        a_focus = focus_label(a)
        b_focus = focus_label(b)
        msg = turn.raw_message or ""
        a_text = product_text(a)
        b_text = product_text(b)
        b5 = a if "B5" in a_text else b if "B5" in b_text else None
        yuz = a if "玉泽" in a_text else b if "玉泽" in b_text else None
        selection_query = any(w in msg for w in ["哪个", "哪款", "哪一个", "哪一款", "谁", "更适合", "更好", "优先", "选"])
        context_winner = None
        context_runner = None
        context_target = ""
        decision_meta = None

        if selection_query:
            ranked_by_context = sorted(compare_products, key=contextual_score, reverse=True)
            winner = ranked_by_context[0]
            runner = ranked_by_context[1] if len(ranked_by_context) > 1 else None
            gap = contextual_score(winner) - (contextual_score(runner) if runner else 0)
            target = self._suitability_target_phrase(turn) or "你这轮诉求"
            if runner and gap >= 6:
                context_winner = winner
                context_runner = runner
                context_target = target
                decision_meta = {
                    "winner_product_id": winner.get("id"),
                    "runner_up_product_ids": [item.get("id") for item in ranked_by_context[1:4] if item.get("id") is not None],
                    "target": target,
                    "reason": contextual_reason(winner),
                    "summary": f"按{target}优先选{self._short_product_name(winner)}",
                }
                decision = (
                    f"按{target}，优先选{self._short_product_name(winner)}。"
                    f"{contextual_reason(winner)}；{self._short_product_name(runner)}更偏{focus_label(runner)}，"
                    "可以作为备选但不是这轮第一优先。"
                )
            else:
                focus_parts = [
                    f"{self._short_product_name(item)}偏{focus_label(item)}"
                    for item in compare_products
                ]
                decision = "；".join(focus_parts) + f"。按{target}看差距不算绝对，优先选肤感和耐受更稳的一款。"
        elif len(compare_products) == 2 and b5 and yuz:
            decision = (
                f"如果是泛红、刺痛、刷酸后或医美后的短期修护，优先选{self._short_product_name(b5)}；"
                f"如果是干敏皮长期保湿和屏障维稳，优先选{self._short_product_name(yuz)}。"
                "这两款不是单纯谁更好，而是急救修护和长期维稳的侧重点不同。"
            )
        elif len(compare_products) == 2 and any(w in msg for w in ["敏感", "屏障", "泛红", "修护"]) and any(w in a_text + b_text for w in ["B5", "玉泽", "屏障"]):
            decision = f"{a_name}偏{a_focus}；{b_name}偏{b_focus}。按当前诉求优先看更贴近敏感/屏障修护的一款。"
        elif len(compare_products) == 2 and any(w in msg for w in ["防晒", "小白管", "安热沙", "安耐晒"]):
            outdoor = a if any(w in a_text for w in ["安热沙", "安耐晒", "防水", "防汗", "小金瓶"]) else b
            daily = b if outdoor is a else a
            decision = (
                f"日常通勤、妆前贴妆更适合{self._short_product_name(daily)}；"
                f"户外暴晒、出汗多、长时间防护更适合{self._short_product_name(outdoor)}。"
                "如果不是户外场景，不必只追求更强防水防汗。"
            )
        else:
            focus_parts = [
                f"{self._short_product_name(item)}偏{focus_label(item)}"
                for item in compare_products
            ]
            decision = "；".join(focus_parts) + "。先按使用场景选，不建议只按价格拍板。"

        lines = ["## 对比结论", "", f"先说结论：{decision}", ""]
        lines.append("## 分项判断")
        lines.append("")
        for p in compare_products:
            short = self._short_product_name(p)
            positioning = (p.get("positioning") or "").strip()
            skin = (p.get("suitable_skin") or "").strip()
            ingredients = p.get("key_ingredients_list") or []
            concerns = p.get("concerns_list") or []
            lines.append(f"**{short}**")
            lines.append(f"- 价格：{price_label(p)}")
            if positioning:
                lines.append(f"- 定位：{positioning[:90]}。")
            lines.append(f"- 更适合：{focus_label(p)}")
            if skin:
                lines.append(f"- 适合肤质：{skin[:60]}")
            if ingredients:
                lines.append(f"- 关键成分：{'、'.join(ingredients[:4])}")
            elif concerns:
                lines.append(f"- 主要诉求：{'、'.join(concerns[:4])}")
            lines.append(f"- 风险点：{risk_label(p)}")
            lines.append("")

        cheaper = min(compare_products, key=lambda x: float(x.get("price_val") or x.get("price") or 999999))
        lines.append("## 怎么选")
        lines.append("")
        if context_winner:
            lines.append(f"- 按{context_target}：优先看{self._short_product_name(context_winner)}，{contextual_reason(context_winner)}。")
            if context_runner:
                lines.append(f"- {self._short_product_name(context_runner)}更适合{focus_label(context_runner)}，不是这轮第一优先。")
            lines.append("- 价格只作为第二判断，别为了便宜牺牲肤质耐受和使用场景。")
        else:
            lines.append(f"- 预算更紧：优先看{self._short_product_name(cheaper)}，但前提是它的使用场景符合你当前需求。")
            lines.append("- 想要更贴合场景：" + "；".join(
                f"{self._short_product_name(item)}看重{focus_label(item)}"
                for item in compare_products
            ) + "。")
            lines.append(f"- 如果这几款价格差不大，不要只按价格选，先看肤质耐受和使用场景。")
            pricier = max(compare_products, key=lambda x: float(x.get("price_val") or x.get("price") or 0))
            if pricier is not cheaper:
                lines.append(f"- 价格更高的{self._short_product_name(pricier)}只有在它的核心场景刚好命中你时才值得加预算。")
        lines.append("")
        lines.append(FOOTER_NOTE)
        text = self._sanitize_plain("\n".join(lines))
        return {
            "answer_mode": AnswerMode.COMPARE,
            "text": text,
            "products": compare_products,
            "comparison": {"products": compare_products},
            "decision": decision_meta,
        }

    def present_knowledge(
        self,
        turn: CanonicalTurn,
        knowledge_text: str = "",
        related_products: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        if knowledge_text:
            text = knowledge_text.strip()
        elif related_products:
            text = self._build_product_knowledge_answer(turn, related_products)
        else:
            text = self._build_knowledge_fallback(turn)
        text = self._sanitize_plain(text)
        if related_products:
            text += f"\n\n{FOOTER_NOTE}"
        return {
            "answer_mode": AnswerMode.KNOWLEDGE,
            "text": text,
            "products": (related_products or [])[:3],
            "comparison": None,
        }

    def present_judgement(
        self,
        turn: CanonicalTurn,
        products: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        if not products:
            # 找不到该单品，fallback到no_match
            return self.present_no_match(turn)

        p = products[0]
        name = p.get("display_name") or clean_product_name(p.get("name") or "")
        brand = (p.get("brand") or "").strip()
        cat = (p.get("category") or "").strip()
        positioning = (p.get("positioning") or "").strip()
        suitable_skin_raw = (p.get("suitable_skin") or "").strip()
        parsed = self._parse_desc(suitable_skin_raw)
        suitable_skin_label = self._clean_suitable_skin(parsed.get("适合肤质") or suitable_skin_raw)
        target_people = (parsed.get("适合人群") or "").strip()
        core_effect = (parsed.get("主打功效") or "").strip()
        core_ingredients_from_desc = (parsed.get("核心成分") or "").strip()
        concerns_list = p.get("concerns_list") or []
        key_ingredients = p.get("key_ingredients_list") or []
        pitfalls = (p.get("pitfalls") or "").strip()
        description = (p.get("description") or "").strip()
        price = p.get("price_val") or p.get("price") or 0

        try:
            price_int = int(price) if float(price) == int(float(price)) else price
        except Exception:
            price_int = price
        price_text = f"约¥{price_int}" if price else "价格见详情页"

        msg = turn.raw_message or ""

        # 判断用户问的是什么维度
        ask_suitability = any(k in msg for k in ["能用吗", "可以用吗", "可以吗", "适合吗", "我能用", "适不适合", "能不能用", "可不可以用"])
        ask_skin = any(k in msg for k in ["敏感肌", "油皮", "干皮", "混油", "混干", "痘肌", "孕妇", "玫瑰痤疮"])
        ask_age = any(k in msg for k in ["年龄", "多大", "几岁", "年轻", "熟龄"])
        ask_usage = any(k in msg for k in ["怎么用", "如何用", "使用方法", "用法", "白天用", "晚上用", "什么时候用", "天天用", "每天用"])
        ask_risk = any(k in msg for k in ["过敏", "爆痘", "闷痘", "刺激", "刺痛", "泛红", "副作用"])

        if self._is_image_identification_query(turn) and not any([ask_suitability, ask_skin, ask_age, ask_usage, ask_risk]):
            return self._present_image_identification(turn, p)

        # 提取用户提到的肤质/人群
        user_skin = turn.skin_type or ""
        if not user_skin:
            for sk in ["敏感肌", "油皮", "干皮", "混油皮", "混干皮", "痘肌", "孕妇"]:
                if sk in msg:
                    user_skin = sk
                    break

        lines = []
        # 识图场景：加锚点引子
        if turn.image_context:
            ctx_results = turn.image_context.get("results") or []
            anchor_prod = None
            if ctx_results and isinstance(ctx_results[0], dict):
                anchor_prod = ctx_results[0]
            pids = turn.image_context.get("product_ids") or []
            if not anchor_prod and pids:
                for cp in products:
                    if cp.get("id") == pids[0]:
                        anchor_prod = cp
                        break
            if anchor_prod:
                abrand = anchor_prod.get("brand") or ""
                ashort = self._short_product_name(anchor_prod)
                if abrand and abrand not in ashort:
                    ashort = abrand + ashort
                lines.append(f"看了你发的图片，匹配度最高的是{ashort}。")
                lines.append("")

        # 标题
        title_name = name
        if brand and brand in name:
            title_name = name
        short_title = self._short_product_name(p)
        lines.append(f"## {short_title} 使用判断")
        lines.append("")

        # 一句话定位
        pos_short = ""
        if positioning:
            pos_short = positioning.split("。")[0]
        elif parsed.get("定位"):
            pos_short = parsed["定位"].split("。")[0]
        if pos_short:
            lines.append(pos_short + "。")
        lines.append("")

        # 能不能用/结论
        lines.append("**能不能用**")
        can_use = True
        reason_parts = []

        skin_for_judge = suitable_skin_label or suitable_skin_raw
        if user_skin and skin_for_judge:
            if user_skin in skin_for_judge or any(k in skin_for_judge for k in ["全肤质", "所有肤质", "多种肤质"]):
                reason_parts.append(f"{user_skin}在适配范围内")
            else:
                bad_signals = ["酒精", "香精", "高浓度酸", "皂基"]
                if any(b in (description + " " + core_ingredients_from_desc + " " + str(key_ingredients)) and user_skin in ["敏感肌", "屏障受损"] for b in bad_signals):
                    can_use = False
                    reason_parts.append(f"{user_skin}需要注意：含潜在刺激性成分，建议先耳后/手腕内侧测试24小时")
                else:
                    reason_parts.append(f"标注主适{suitable_skin_label or '多种肤质'}，{user_skin}建议先在耳后做皮试")
        elif user_skin and not skin_for_judge:
            reason_parts.append(f"商品库未明确标注{user_skin}适用性，建议耳后测试后再全脸使用")
        else:
            reason_parts.append(f"主适{suitable_skin_label or '多种肤质'}，首次使用建议先皮试")

        if ask_age:
            reason_parts.append("护肤以肤质和诉求为主，年龄仅作参考")

        conclusion = "可以用" if can_use else "需要谨慎"
        lines.append(f"结论：{conclusion}，" + "；".join(reason_parts) + "。")
        lines.append("")

        # 适合谁（结构化展示，避免整段dump）
        lines.append("**适合谁**")
        if target_people:
            short_people = target_people[:60]
            lines.append(f"- {short_people}")
        elif suitable_skin_label:
            lines.append(f"- {suitable_skin_label}")
        else:
            lines.append("- 商品详情页标注的适用人群")
        if core_effect:
            eff_short = core_effect.replace(";", "、").replace(";", "、")[:80]
            lines.append(f"- 主打功效：{eff_short}")
        if concerns_list:
            top_concerns = concerns_list[:3]
            lines.append(f"- 诉求匹配：{'、'.join(top_concerns)}")
        if core_ingredients_from_desc:
            ing_short = core_ingredients_from_desc[:60]
            lines.append(f"- 核心成分：{ing_short}")
        elif key_ingredients:
            lines.append(f"- 核心成分：{'、'.join(key_ingredients[:3])}")
        lines.append("")

        # 不适合谁
        lines.append("**不适合谁**")
        unsuitable = []
        if "酒精" in description or "酒精" in str(key_ingredients):
            unsuitable.append("对酒精敏感/屏障极薄的肌肤")
        if "香精" in description or "香精" in str(key_ingredients):
            unsuitable.append("对香精过敏的肤质")
        if "酸" in str(key_ingredients) and cat in ["精华", "面霜"]:
            unsuitable.append("屏障受损/敏感期不建议使用高浓度酸类产品")
        if "孕妇" in msg:
            unsuitable.append("孕期/哺乳期建议咨询医生后使用含A醇/水杨酸/高浓度维A类产品")
        if not unsuitable:
            unsuitable.append("对产品任一成分过敏者")
        for u in unsuitable[:4]:
            lines.append(f"- {u}")
        lines.append("")

        # 怎么用
        lines.append("**怎么用**")
        usage_tips = []
        if cat in ["精华", "面霜"]:
            usage_tips.append("洁面-爽肤水后取适量，均匀涂抹于面部和颈部，轻轻按摩至吸收")
            if cat == "精华":
                usage_tips.append("后续叠加乳液/面霜锁水")
        elif cat == "防晒":
            usage_tips.append("日常通勤/户外活动前15分钟涂抹，每2-3小时补涂一次；用量约硬币大小")
        elif cat == "面膜":
            usage_tips.append("洁面后敷10-15分钟，取下后轻拍剩余精华至吸收，无需清洗（按产品说明）")
        elif cat == "洁面":
            usage_tips.append("早晚取适量于掌心，加水揉出泡沫后轻柔按摩面部30秒-1分钟，温水洗净")
        elif cat == "眼霜":
            usage_tips.append("取米粒大小轻点于眼周，无名指腹轻拍至吸收，避免拉扯")
        elif cat == "爽肤水":
            usage_tips.append("洁面后用化妆棉或手取适量，轻拍于面部至吸收")
        else:
            usage_tips.append("按产品说明书使用，首次使用建议先在耳后/手腕内侧做皮试")
        if "晚上用" in msg or "夜间" in msg:
            usage_tips.append("你提到想晚上使用，按夜间护肤流程使用即可")
        if "白天用" in msg or "日间" in msg:
            usage_tips.append("你提到想白天使用，注意白天使用后叠加防晒")
        if "天天用" in msg or "每天用" in msg:
            usage_tips.append("这类产品日常可每天使用，但如果出现刺痛/泛红/脱皮应立即停用并降低频次")
        for tip in usage_tips[:4]:
            lines.append(f"- {tip}")
        lines.append("")

        # 风险点
        lines.append("**风险点**")
        risks = []
        if pitfalls:
            for pw in pitfalls.split("；"):
                pw = pw.strip()
                if pw and len(pw) > 3:
                    risks.append(pw)
        if not risks:
            if cat in ["精华"] and key_ingredients:
                risks.append("功效型产品建议从低频次（隔天用）开始，建立耐受后再每天使用")
            else:
                risks.append("首次使用先在耳后/手腕内侧皮试24小时，无不适再上脸")
            risks.append("如出现持续泛红/刺痛/瘙痒/肿疹，立即停用并冷敷，必要时就医")
        for r in risks[:3]:
            lines.append(f"- {r}")
        lines.append("")

        pitfall_items = []
        for r in risks[:3]:
            if "皮试" in r:
                title = "先做皮试"
            elif any(w in r for w in ["泛红", "刺痛", "瘙痒", "肿疹", "停用"]):
                title = "不适立即停用"
            elif any(w in r for w in ["酒精", "香精", "酸", "A醇", "视黄醇"]):
                title = "刺激风险"
            else:
                title = "使用提醒"
            severity = "高" if any(w in r for w in ["立即停用", "泛红", "刺痛", "过敏", "屏障极薄"]) else "中"
            pitfall_items.append({"title": title, "description": r, "severity": severity})

        # 价格与购买
        lines.append(f"参考价 {price_text}。{FOOTER_NOTE}")

        text = "\n".join(lines)
        response_products = products[:1] if turn.image_context else products[:3]
        return {
            "answer_mode": AnswerMode.JUDGEMENT,
            "text": text,
            "products": response_products,
            "comparison": None,
            "citations": [],
            "pitfalls": pitfall_items,
        }

    @staticmethod
    def _is_image_identification_query(turn: CanonicalTurn) -> bool:
        if not turn.image_context:
            return False
        msg = turn.raw_message or ""
        return any(w in msg for w in [
            "这是什么", "这款是什么", "这个是什么", "图里是什么", "图片里是什么",
            "帮我看看这是什么", "识别一下", "认一下", "是什么产品",
        ])

    def _present_image_identification(self, turn: CanonicalTurn, product: Dict[str, Any]) -> Dict[str, Any]:
        name = self._short_product_name(product)
        brand = (product.get("brand") or "").strip()
        category = (product.get("category") or "").strip() or "商品"
        positioning = (product.get("positioning") or "").strip()
        suitable_skin = self._clean_suitable_skin(product.get("suitable_skin") or "")
        concerns = product.get("concerns_list") or []
        ingredients = product.get("key_ingredients_list") or []
        price = product.get("price_val") or product.get("price") or 0
        similarity = None
        ctx_results = (turn.image_context or {}).get("results") or []
        if ctx_results and isinstance(ctx_results[0], dict):
            similarity = ctx_results[0].get("similarity")

        try:
            price_text = f"约¥{int(price)}" if price else "价格见详情页"
        except Exception:
            price_text = f"约¥{price}" if price else "价格见详情页"

        match_text = ""
        try:
            if similarity is not None:
                match_text = f"匹配度约{float(similarity):.1f}%。"
        except Exception:
            match_text = ""

        lines = [
            f"看了你发的图片，匹配度最高的是{name}。{match_text}",
            "",
            "## 图片识别结果",
            "",
            f"- 品类：{category}",
        ]
        if brand:
            lines.append(f"- 品牌：{brand}")
        lines.append(f"- 参考价：{price_text}")
        if positioning:
            lines.append(f"- 定位：{positioning[:90]}。")
        if concerns:
            lines.append(f"- 主要诉求：{'、'.join(concerns[:4])}")
        if ingredients:
            lines.append(f"- 关键成分：{'、'.join(ingredients[:4])}")
        if suitable_skin:
            lines.append(f"- 适合肤质：{suitable_skin}")

        lines.extend([
            "",
            "## 注意点",
            "",
        ])
        caution_items = self._build_recommendation_pitfalls(turn, [product])
        if caution_items:
            for item in caution_items[:2]:
                lines.append(f"- {item.get('description')}")
        else:
            lines.append("- 识图结果按商品库候选匹配，包装版本、规格和实时价格以下单页为准。")
        lines.extend([
            "",
            "后面你可以直接追问它适合什么肤质、怎么用、有没有平替，或者拿它和另一款对比。",
            FOOTER_NOTE,
        ])

        return {
            "answer_mode": AnswerMode.JUDGEMENT,
            "text": self._sanitize_plain("\n".join(lines)),
            "products": [product],
            "comparison": None,
            "citations": [],
            "pitfalls": caution_items,
        }

    def present_no_match(self, turn: CanonicalTurn) -> Dict[str, Any]:
        msg = turn.raw_message or ""
        if any(w in msg for w in ["你好", "您好", "hi", "hello", "在吗"]):
            text = (
                "你好！我是护肤美妆导购助手，可以帮你推荐适合的护肤品或化妆品。\n\n"
                "你可以告诉我：\n"
                "1. 你的肤质（干皮/油皮/敏感肌等）\n"
                "2. 想买什么品类（精华/面霜/防晒等）\n"
                "3. 主要诉求（抗初老/美白/保湿等）\n"
                "4. 大概预算"
            )
        elif turn.skin_type:
            text = (
                f"已记住你是{turn.skin_type}。接下来你直接说想看什么品类、预算或诉求，"
                "比如防晒、精华、抗初老、300 内，我会按这个肤质优先筛。"
            )
        else:
            text = (
                "我先帮你缩小范围～你可以补充一下肤质、预算、主要诉求"
                "（比如油皮/想抗老/300 内），我会结合商品库和护肤知识库给你筛 2-3 款更合适的。"
            )
        return {
            "answer_mode": AnswerMode.NO_MATCH,
            "text": text,
            "products": [],
            "comparison": None,
        }

    # ==================== 点点式推荐（v1 _build_local_recommendation_fallback 迁移） ====================

    def _order_recommendation_products(
        self,
        turn: CanonicalTurn,
        products: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        budget = turn.budget_max
        budget_min = turn.budget_min
        category_name, *_ = self._detect_category(turn, products)
        top = self._select_top3(products, category_name, budget, budget_min=budget_min)

        seen = set()
        ordered = []
        for p in top:
            key = str(p.get("id") or id(p))
            if key not in seen:
                ordered.append(p)
                seen.add(key)
        for p in products:
            key = str(p.get("id") or id(p))
            if key not in seen:
                ordered.append(p)
                seen.add(key)
        return ordered

    def _build_recommendation_pitfalls(
        self,
        turn: CanonicalTurn,
        products: List[Dict[str, Any]],
    ) -> List[Dict[str, str]]:
        if not products:
            return []

        category_name, *_ = self._detect_category(turn, products)
        skin_type = turn.skin_type or self._extract_skin_type(turn.raw_message or "")

        def product_text(product: Dict[str, Any]) -> str:
            return " ".join([
                product.get("name") or "",
                product.get("display_name") or "",
                product.get("positioning") or "",
                product.get("description") or "",
                product.get("suitable_skin") or "",
                  self._product_data_value(product, "target_users", "适合人群"),
                  self._product_data_value(product, "suitable_skin_types", "适合肤质"),
                  self._product_data_value(product, "key_ingredients", "核心成分"),
                  self._product_data_value(product, "concerns", "主打功效"),
                " ".join(product.get("concerns_list") or []),
                " ".join(product.get("key_ingredients_list") or []),
            ])

        def clean(value: str, limit: int = 86) -> str:
            text = re.sub(r"\s+", " ", str(value or "")).strip(" ；;。")
            if not text or text.startswith("http"):
                return ""
            if any(bad in text for bad in ["产地参数", "生产企业", "备案号"]):
                text = re.sub(r"；?[^；。]*(?:产地参数|生产企业|备案号)[^；。]*", "", text).strip(" ；;。")
            if len(text) > limit:
                text = text[:limit].rstrip("，。、；;") + "…"
            return text

        def product_specific_caution(product: Dict[str, Any]) -> str:
            text = product_text(product)
            if category_name == "防晒":
                if any(k in text for k in ["安热沙", "安耐晒", "小金瓶", "防水", "防汗", "户外"]):
                    return "更偏户外防护，通勤薄涂不够；出汗、游泳后要补涂，晚上认真清洁。"
                if any(k in text for k in ["理肤泉", "薇诺娜", "敏感肌", "无香", "温和"]):
                    return "敏感肌友好也不等于零风险，先试熏眼、泛红、搓泥和闷闭口。"
                if any(k in text for k in ["小白管", "妆前", "隔离", "贴妆"]):
                    return "妆前型防晒要观察和底妆是否搓泥，后续上妆前留足成膜时间。"
                return "防晒看场景和补涂，别只按SPF数字或价格判断。"
            if category_name == "精华":
                if any(k in text for k in ["A醇", "视黄醇", "视黄醛", "水杨酸", "果酸", "杏仁酸"]):
                    return "强功效线先低频夜间用，不要和酸类、去角质或其他猛料同晚叠加。"
                if any(k in text for k in ["烟酰胺", "美白", "淡斑", "377", "VC", "维C"]):
                    return "提亮淡斑线白天要叠防晒，泛红刺痛时先停用观察。"
                if any(k in text for k in ["小棕瓶", "小黑瓶", "二裂酵母", "修护", "维稳"]):
                    return "修护维稳线不要和多款新精华同时上脸，否则很难判断刺激来源。"
                if any(k in text for k in ["玻色因", "胜肽", "抗皱", "淡纹", "紧致"]):
                    return "抗老线看耐受和保湿承接，干敏肌先隔天用，别急着每天叠加。"
                return "功效型精华先单品测试，第一周不要同时新增多种精华。"
            if category_name in ("面霜", "乳液"):
                if any(k in text for k in ["B5", "厚敷", "急救", "乳木果油"]):
                    return "B5/厚润修护适合局部急救，油皮和闭口期别全脸厚敷过夜。"
                if any(k in text for k in ["玉泽", "PBS", "仿生脂质", "屏障"]):
                    return "屏障修护线适合长期维稳，但敏感期仍建议先薄涂观察。"
                if any(k in text for k in ["清爽", "油皮", "混油"]):
                    return "清爽型也要看保湿够不够，换季或屏障弱时不要只追求轻薄。"
                return "面霜先看质地和闷闭口风险，不要一开始就厚涂全脸。"
            if category_name == "面膜":
                if any(k in text for k in ["清洁", "泥膜"]):
                    return "清洁面膜不要频繁用，一周1-2次即可，敏感期先暂停。"
                return "涂抹/贴片面膜别敷超时，10-15分钟即可，敷后用面霜锁水。"
            if category_name == "底妆":
                if any(k in text for k in ["NC", "NW", "色号", "冷调", "暖调", "黄皮", "粉调"]):
                    return "底妆先确认色号和氧化情况，最好试半天再决定，避免上脸后发灰或暗沉。"
                if any(k in text for k in ["持妆", "不脱妆", "防汗", "防水", "长效"]):
                    return "持妆型底妆先试半天，重点看卡粉、斑驳、闷痘和卸妆是否干净。"
                if any(k in text for k in ["遮瑕", "无瑕", "高遮瑕"]):
                    return "遮瑕强的粉底容易妆感偏厚，干皮先看卡粉，油痘肌先看闷闭口。"
                return "底妆不要只看色号图，先试妆效、服帖度和卸妆后是否闷闭口。"
            return ""

        items: List[Dict[str, str]] = []
        seen_titles = set()
        product_level_count = 0

        def add(title: str, description: str, severity: str = "中") -> None:
            title = clean(title, 24)
            description = clean(description, 100)
            if not title or not description or title in seen_titles:
                return
            items.append({"title": title, "description": description, "severity": severity})
            seen_titles.add(title)

        def add_product_tip(title: str, description: str, severity: str = "中") -> None:
            nonlocal product_level_count
            before = len(items)
            add(title, description, severity)
            if len(items) > before:
                product_level_count += 1

        combined_text = " ".join(product_text(p) for p in products)
        product_raw_pitfall_ids = set()

        for p in products[:3]:
            short = self._short_product_name(p)
            raw = clean(p.get("pitfalls") or "", 90)
            if raw:
                product_raw_pitfall_ids.add(p.get("id"))
                add_product_tip(f"{short}使用提醒", raw, "中")
            if len(items) >= 3:
                break

        knowledge_pitfalls = list(getattr(turn, "knowledge_pitfalls", None) or [])
        if turn.image_context and isinstance(turn.image_context, dict):
            raw_knowledge_pitfalls = turn.image_context.get("knowledge_pitfalls") or []
            if isinstance(raw_knowledge_pitfalls, list):
                knowledge_pitfalls.extend(raw_knowledge_pitfalls)

        for item in knowledge_pitfalls[:3]:
            if not isinstance(item, dict):
                continue
            title = item.get("title") or item.get("category") or "知识库使用提醒"
            description = item.get("description") or item.get("content") or ""
            severity = item.get("severity") or "中"
            add(title, description, severity)
            if len(items) >= 3:
                break

        authoritative_tip_count = len(items)
        for p in products[:3]:
            if p.get("id") in product_raw_pitfall_ids:
                continue
            short = self._short_product_name(p)
            parsed = self._parse_desc(p.get("description") or "")
            caution = (
                self._extract_product_data_warning(p)
                if authoritative_tip_count
                else self._extract_note(p, parsed, category_name) or product_specific_caution(p)
            )
            if caution:
                add_product_tip(f"{short}使用提醒", caution, "中")
            if len(items) >= 3:
                break

        if authoritative_tip_count and items:
            return self._merge_medium_pitfalls(items)
        if product_level_count and len(items) >= 2:
            return self._merge_medium_pitfalls(items)

        if category_name == "精华" and len(items) < 2:
            if skin_type in ("敏感肌", "干敏肌", "油敏肌"):
                add("干敏肌先低频试用", "先隔天夜间单独用，连续3-5天没有泛红、刺痛、闷闭口，再提高频次。", "高")
            if any(k in combined_text for k in ["玻色因", "A醇", "视黄醇", "视黄醛", "水杨酸", "果酸", "杏仁酸"]):
                add("别同晚叠猛料", "玻色因、A醇、酸类等功效成分不要和去角质产品同晚叠加，敏感期先停用。", "中")
            else:
                add("功效精华看耐受", "抗老、提亮或修护类精华都建议先单品测试，不要第一周就叠加多种新产品。", "中")
        elif category_name == "防晒" and len(items) < 2:
            add("防晒要涂够量", "脸部用量接近一元硬币大小，户外或出汗后需要及时补涂。", "中")
            if skin_type in ("敏感肌", "干敏肌", "油敏肌"):
                add("敏感肌先试膜感", "先局部试用，重点观察酒精感、熏眼、泛红和闷闭口。", "高")
        elif category_name in ("面霜", "乳液") and len(items) < 2:
            add("先看闷闭口风险", "修护保湿类产品不要一开始就厚敷全脸，油皮或闭口期先薄涂观察。", "中")
            if skin_type in ("敏感肌", "干敏肌", "油敏肌"):
                add("敏感期少叠加", "屏障不稳时先把清洁、防晒和保湿做简单，不要同时新增多款功效产品。", "高")
        elif category_name == "底妆" and len(items) < 2:
            add("底妆先试色号", "色号、氧化和妆效受肤色影响很大，先靠柜或小样试半天，别只看商品图下单。", "中")
            add("卸妆要确认干净", "持妆或高遮瑕底妆要认真卸妆，油痘肌重点观察闷痘、闭口和卡粉。", "中")
        elif len(items) < 2:
            add("确认使用场景", "下单前确认规格、实时价格和使用场景，敏感肌第一次上脸先局部测试。", "中")

        if len(items) < 2:
            add("下单前看实时价", "商品库价格是入库参考价，活动价、规格和赠品组合需要以详情页为准。", "中")
        return self._merge_medium_pitfalls(items)

    @staticmethod
    def _merge_medium_pitfalls(items: List[Dict[str, str]]) -> List[Dict[str, str]]:
        if not items:
            return []
        high_items = [item for item in items if item.get("severity") == "高"]
        medium_items = [item for item in items if item.get("severity") != "高"]
        if len(medium_items) <= 1:
            if high_items and medium_items:
                item = dict(medium_items[0])
                title = str(item.get("title") or "使用提醒").replace("使用提醒", "").strip(" ：:")
                desc = str(item.get("description") or "").strip(" 。；;")
                if title and desc and title not in desc:
                    item["description"] = f"{title}：{desc}"
                return (high_items + [item])[:3]
            return (high_items + medium_items)[:3]

        parts = []
        seen = set()
        for item in medium_items:
            title = str(item.get("title") or "使用提醒").replace("使用提醒", "").strip(" ：:")
            desc = str(item.get("description") or "").strip(" 。；;")
            if not desc:
                continue
            phrase = f"{title}：{desc}" if title else desc
            if phrase in seen:
                continue
            seen.add(phrase)
            parts.append(phrase)
            if len(parts) >= 3:
                break
        if not parts:
            return high_items[:3]

        merged_items = []
        for idx, part in enumerate(parts[:3]):
            merged_items.append({
                "title": "使用提醒" if idx == 0 else f"使用提醒{idx + 1}",
                "description": part,
                "severity": "中",
            })
        return (high_items + merged_items)[:3]

    def _build_followup_product_line(self, product: Dict[str, Any], category_name: str) -> str:
        name = self._short_product_name(product)
        price = product.get("price_val") or product.get("price") or 0
        try:
            price_part = f"约¥{int(price)}，" if price else ""
        except Exception:
            price_part = f"约¥{price}，" if price else ""

        ingredients = product.get("key_ingredients_list") or self._split_field(
            self._product_data_value(product, "key_ingredients", "核心成分")
        )
        concerns = product.get("concerns_list") or self._split_field(
            self._product_data_value(product, "concerns", "主打功效")
        )
        target_users = self._product_data_value(product, "target_users", "适合人群")
        suitable_skin = self._clean_suitable_skin(
            self._product_data_value(product, "suitable_skin", "suitable_skin_types", "适合肤质")
            or product.get("suitable_skin")
            or ""
        )
        positioning = (self._product_data_value(product, "positioning", "定位") or product.get("positioning") or "").strip()
        warning = self._extract_product_data_warning(product)

        parts: List[str] = []
        if ingredients:
            parts.append(f"核心看{'、'.join(ingredients[:2])}")
        if target_users:
            parts.append(self._trim_reason_phrase(target_users, 34))
        elif concerns:
            parts.append(f"主打{'、'.join(concerns[:3])}")
        elif positioning:
            parts.append(self._trim_reason_phrase(positioning, 44))
        if warning:
            parts.append(f"注意{self._trim_reason_phrase(warning, 38)}")
        elif suitable_skin and any(signal in suitable_skin for signal in ["敏感", "泛红", "屏障", "油皮", "干皮"]):
            parts.append(f"适配人群看{self._trim_reason_phrase(suitable_skin, 28)}")

        if not parts:
            parts.append("和上一轮产品侧重点不同，可以作为备选")
        return f"- {name}：{price_part}{'；'.join(parts[:3])}。"

    @staticmethod
    def _split_field(value: Any) -> List[str]:
        if not value:
            return []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item or "").strip()]
        return [part.strip() for part in re.split(r"[、,，;；]\s*", str(value)) if part.strip()]

    @classmethod
    def _product_data_value(cls, product: Dict[str, Any], *keys: str) -> str:
        specs = product.get("_specs") or product.get("specifications") or product.get("specs") or {}
        if not isinstance(specs, dict):
            specs = {}
        for key in keys:
            value = product.get(key)
            if value:
                return "、".join(str(item).strip() for item in value if str(item).strip()) if isinstance(value, list) else str(value).strip()
            value = specs.get(key)
            if value:
                return "、".join(str(item).strip() for item in value if str(item).strip()) if isinstance(value, list) else str(value).strip()
        return ""

    @staticmethod
    def _trim_reason_phrase(value: str, limit: int = 42) -> str:
        text = re.sub(r"\s+", " ", str(value or "")).strip(" ；;。")
        if len(text) > limit:
            text = text[:limit].rstrip("，。、；;") + "…"
        return text

    def _extract_product_data_warning(self, product: Dict[str, Any]) -> str:
        fields = [
            self._product_data_value(product, "pitfalls", "注意点", "备注"),
            self._product_data_value(product, "suitable_skin", "suitable_skin_types", "适合肤质"),
            self._product_data_value(product, "target_users", "适合人群"),
            self._product_data_value(product, "key_ingredients", "核心成分"),
            product.get("description") or "",
        ]
        text = "；".join(str(item).strip() for item in fields if str(item or "").strip())
        if not text:
            return ""

        for raw in re.split(r"[；;\n。]", text):
            item = re.sub(r"\s+", " ", raw).strip(" ；;。")
            if not item or item.startswith("http"):
                continue
            if "敏感肌除外" in item or "敏感肌需谨慎" in item:
                return "商品资料提示敏感肌需谨慎或除外，敏感肌先别只按功效词下单，需核成分并局部试用"
            if "敏感肌需先" in item or "敏感肌先试" in item:
                return "商品资料提示敏感肌需先局部试用，先少量观察泛红、刺痛和闷闭口"
            if "个体耐受" in item:
                return self._trim_reason_phrase(item, 70)

        for raw in re.split(r"[；;\n。]", text):
            item = re.sub(r"\s+", " ", raw).strip(" ；;。")
            if not item or item.startswith("http") or len(item) < 10:
                continue
            if any(signal in item for signal in ["泛红", "刺痛", "闷闭口", "闭口", "屏障受损", "脆弱肌", "干皮需叠加保湿"]):
                return self._trim_reason_phrase(item, 70)

        ingredients = product.get("key_ingredients_list") or self._split_field(self._product_data_value(product, "key_ingredients"))
        concerns = product.get("concerns_list") or self._split_field(self._product_data_value(product, "concerns"))
        suitable_skin = self._product_data_value(product, "suitable_skin", "suitable_skin_types")
        if any("B5" in item or "泛醇" in item for item in ingredients) and any(
            signal in " ".join(concerns + [suitable_skin])
            for signal in ["屏障受损", "泛红", "敏感", "脆弱"]
        ):
            return "商品资料主打泛醇/B5和屏障修护，敏感肌先按个体耐受少量试用"
        return ""


    def _build_diandian_recommendation(self, turn: CanonicalTurn, products: List[Dict[str, Any]]) -> str:
        budget = turn.budget_max
        budget_min = turn.budget_min
        skin_type = turn.skin_type or self._extract_skin_type(turn.raw_message or "")

        category_name, core_need, avoid_point, benefit, param_label = self._detect_category(turn, products)
        price_range = self._price_range_label(budget)
        skin_type_text = skin_type if skin_type else "适合的"

        top = self._select_top3(products, category_name, budget, budget_min=budget_min)
        count = len(top)

        # 识图场景：加锚点引子
        image_anchor = ""
        if turn.image_context:
            # 锚点商品：优先用 image_context 里的原图匹配结果（而不是推荐TOP——平替场景下TOP不是原图）
            anchor_prod = None
            ctx_results = turn.image_context.get("results") or []
            if ctx_results and isinstance(ctx_results[0], dict):
                anchor_prod = ctx_results[0]
            pids = turn.image_context.get("product_ids") or []
            if not anchor_prod and pids:
                for p in top:
                    if p.get("id") == pids[0]:
                        anchor_prod = p
                        break
            if not anchor_prod and top:
                anchor_prod = top[0]

            anchor_brand = turn.image_anchor_brand or (anchor_prod.get("brand") if anchor_prod else "") or ""
            if anchor_prod:
                name = anchor_prod.get("name") or anchor_prod.get("display_name") or ""
                short_name = name
                for skip in [anchor_brand, "全新", "新一代", "官方", "旗舰", "正品",
                            "（黄子弘凡同款）", "护肤品", "化妆品", "套装", "礼盒",
                            "生日礼物", "送女友", "送男友", "男女"]:
                    if skip and short_name.startswith(skip):
                        short_name = short_name[len(skip):].lstrip()
                for j in ["护肤品", "化妆品", "套装", "礼盒", "生日礼物", "送女友", "送男友",
                          "男女", "官方", "旗舰", "同款", "升级版", "第二代", "第三代",
                          "第四代", "新一代", "黄子弘凡同款", "正品"]:
                    short_name = short_name.replace(j, "")
                # 截断到第一个品类词
                for stop in ["霜", "乳", "液", "瓶", "膏", "蜜", "精华", "水", "油", "防晒"]:
                    si = short_name.find(stop)
                    if si >= 2:
                        short_name = short_name[:si + len(stop)]
                        break
                if len(short_name) > 16:
                    short_name = short_name[:16]
                if anchor_brand and anchor_brand not in short_name:
                    image_anchor = f"看了你发的图片，匹配度最高的是{anchor_brand}{short_name}。"
                else:
                    image_anchor = f"看了你发的图片，匹配度最高的是{short_name}。"
                msg = (turn.raw_message or "")
                if any(w in msg for w in ["平替", "替代", "相似", "同款", "类似", "同类", "还有什么", "别的"]):
                    image_anchor += f"以它为锚点，我帮你挑几款同{category_name}里的其他品牌选择：\n\n"
                else:
                    image_anchor += "\n\n"

        if skin_type:
            variant_idx = hash(turn.raw_message or "") % 3
            intros = [
                f"给{skin_type_text}挑{price_range}{category_name}，我会优先看「{core_need}」，尽量避开「{avoid_point}」，这样{benefit}。",
                f"{skin_type_text}选{price_range}{category_name}，关键是「{core_need}」，别踩「{avoid_point}」的坑，选对了{benefit}。",
                f"按{skin_type_text}来筛{price_range}{category_name}，先看「{core_need}」，再避「{avoid_point}」，{benefit}。",
            ]
            intro = intros[variant_idx]
        else:
            variant_idx = hash(turn.raw_message or "") % 3
            intros = [
                f"挑{price_range}{category_name}，优先看「{core_need}」，避开「{avoid_point}」，{benefit}。",
                f"选{price_range}{category_name}，重点抓「{core_need}」，别踩「{avoid_point}」的坑，选对了{benefit}。",
                f"筛{price_range}{category_name}，先看「{core_need}」，再避「{avoid_point}」，这样{benefit}。",
            ]
            intro = intros[variant_idx]
        if count == 1:
            intro += "这轮先给你留一款最贴近的。"
        elif count == 2:
            intro += "这轮给你两款：一款走性价比路线，一款进阶一点。"
        else:
            intro += "这轮给你三款：一款性价比、一款进阶、一款特殊场景兜底。"

        title_prefix = f"{skin_type_text}{price_range}" if skin_type else price_range
        title = f"## {title_prefix}{category_name}指南"

        sections = []
        for i, p in enumerate(top):
            parsed = self._parse_desc(p.get("description") or "")
            label = self._label_for(i, p, skin_type)
            sections.append(self._build_product_block(p, label, parsed, param_label, category_name))

        summary = self._build_summary(top, budget, skin_type, category_name, budget_min=budget_min)

        parts = [
            image_anchor + intro if image_anchor else intro,
            "",
            title,
            "",
            "\n\n".join(sections),
            "",
            "## 综合建议",
            "",
            summary,
        ]
        return self._sanitize_structured("\n".join(parts))

    @classmethod
    def _extract_product_spec(cls, product: Dict[str, Any]) -> str:
        specs = product.get("_specs") or product.get("specifications") or product.get("specs") or {}
        if isinstance(specs, str):
            specs = {"规格": specs}

        candidates: List[Any] = []
        for key in ("规格", "spec", "specification", "规格/容量", "容量", "净含量", "净含量/规格", "volume", "size", "net_content"):
            candidates.append(product.get(key))
            if isinstance(specs, dict):
                candidates.append(specs.get(key))
        candidates.extend([
            product.get("display_name"),
            product.get("name"),
        ])

        for value in candidates:
            raw = str(value or "").strip()
            if not raw:
                continue
            spec = cls._normalize_spec_text(raw)
            if spec:
                return spec
        return ""

    @staticmethod
    def _normalize_spec_text(raw: str) -> str:
        raw = str(raw or "").strip()
        if not raw:
            return ""

        # Keep combo specs intact; otherwise "50g×2" would be displayed as "50g".
        combo_patterns = [
            r"(\d+(?:\.\d+)?\s*(?:ml|mL|ML|g|G|克|毫升|片|枚|支|瓶|盒)\s*[xX*×]\s*\d+)",
            r"(\d+\s*[xX*×]\s*\d+(?:\.\d+)?\s*(?:ml|mL|ML|g|G|克|毫升|片|枚|支|瓶|盒))",
            r"(\d+\s*盒\s*\d+\s*(?:片|贴))",
        ]
        for pattern in combo_patterns:
            match = re.search(pattern, raw)
            if match:
                spec = re.sub(r"\s+", "", match.group(1))
                return (
                    spec.replace("*", "×")
                    .replace("x", "×")
                    .replace("X", "×")
                    .replace("贴", "片")
                    .replace("毫升", "ml")
                    .replace("ML", "ml")
                    .replace("mL", "ml")
                    .replace("克", "g")
                    .replace("G", "g")
                )

        match = re.search(r"(\d+(?:\.\d+)?\s*(?:ml|mL|ML|g|G|克|毫升|片|枚|支|瓶|盒))", raw)
        if match:
            spec = re.sub(r"\s+", "", match.group(1))
            return spec.replace("贴", "片").replace("毫升", "ml").replace("ML", "ml").replace("mL", "ml").replace("克", "g").replace("G", "g")
        return ""

    @staticmethod
    def _short_product_name(p: Dict[str, Any]) -> str:
        raw = (p.get("display_name") or p.get("name") or "").strip()
        brand = (p.get("brand") or "").strip()
        # 去掉前缀的"官方""黄子弘凡同款""孙颖莎推荐"等营销词
        import re
        skip_words = ["现货", "官方", "同款", "推荐", "生日礼物", "送女友", "送老婆", "送妈妈",
                      "化妆品", "护肤品", "护肤", "化妆品礼盒", "礼盒", "套装",
                      "第二代", "第三代", "全新", "新款", "升级"]
        for sk in skip_words:
            if raw.startswith(sk):
                raw = raw[len(sk):].lstrip("【《（(")
        # 按品类词截断：找到品类关键词后保留到该词
        cat_cutoffs = ["面霜", "精华", "防晒", "眼霜", "面膜", "洁面", "乳液", "爽肤水", "化妆水",
                       "洗面奶", "散粉", "蜜粉", "粉饼", "口红", "唇膏", "粉底", "洁面霜",
                       "B5霜", "小白瓶", "小棕瓶", "小黑瓶", "小白管", "神仙水", "大红瓶"]
        stop_idx = len(raw)
        for cc in cat_cutoffs:
            idx = raw.find(cc)
            if idx >= 0:
                stop_idx = min(stop_idx, idx + len(cc))
        raw = raw[:stop_idx]
        if brand and brand not in raw:
            raw = brand + raw
        # 去冗余分隔符
        raw = re.sub(r"^[\s·\-|【\(\[\{]+", "", raw).strip()
        # 已经按品类词截到“面霜/精华/防晒”等结尾时，不再硬切到半个词；
        # 否则会出现“海蓝之谜...60ml修”这类不完整短名。
        if len(raw) > 32:
            raw = raw[:32].rstrip("修舒抗保滋紧淡亮控油补水防晒面霜精华乳液")
        return raw or clean_product_name(p.get("name", ""))[:18]

    @staticmethod
    def _extract_skin_type(msg: str) -> str:
        skin_keywords = [
            ("干敏肌", ["干敏", "干敏肌"]),
            ("油敏肌", ["油敏", "油敏肌"]),
            ("敏感肌", ["敏感", "泛红", "刺痛", "屏障受损", "敏皮"]),
            ("干皮", ["干皮", "干性皮肤"]),
            ("油皮", ["油皮", "油性皮肤", "容易出油", "大油皮"]),
            ("混油皮", ["混油", "混合皮", "T区油"]),
            ("混干皮", ["混干"]),
            ("中性皮", ["中性皮", "中性皮肤"]),
        ]
        for st, kws in skin_keywords:
            if any(kw in msg for kw in kws):
                return st
        return ""

    def _detect_category(self, turn: CanonicalTurn, products: List[Dict[str, Any]]):
        explicit_cat = (turn.category or "").strip()
        if explicit_cat and "护肤-" in explicit_cat:
            explicit_cat = explicit_cat.replace("护肤-", "").replace("彩妆-", "")

        PROFILES = [
            ("防晒", ["防晒", "防晒霜", "防晒乳", "防晒喷雾"],
             "肤感轻薄好坚持", "盲目追高SPF导致闷痘", "日常通勤愿意天天涂", "防晒指数"),
            ("底妆", ["素颜霜", "粉底", "底妆", "遮瑕", "粉底液"],
             "服帖自然不卡粉", "只追高遮瑕导致妆感厚重", "日常妆感自然不假面", "妆效特点"),
            ("爽肤水", ["爽肤水", "化妆水", "柔肤水", "精萃水", "精粹水", "精华水", "美肤水", "菌菇水", "流金水", "金盏花水"],
             "温和不刺激好吸收", "盲目追求高浓度导致刺激", "日常维稳补水更靠谱", "核心功效"),
            ("面霜", ["面霜", "乳霜", "保湿霜", "修护霜", "滋润霜"],
             "保湿修护不闷痘", "只看厚重感导致闷闭口", "秋冬保湿稳定不翻车", "核心功效"),
            ("眼霜", ["眼霜", "眼部精华"],
             "温和好吸收不长脂肪粒", "盲目追求速效导致刺激眼周", "长期用温和有效", "核心成分"),
            ("面膜", ["面膜"],
             "补水修护急救稳", "频繁敷面膜导致过度水合", "关键时候真有用", "核心功效"),
            ("精华", ["精华", "精华液", "精华露", "精华素", "肌底液"],
             "成分适配能建立耐受", "堆猛料导致泛红刺痛", "长期用真的有效果", "核心成分"),
            ("洁面", ["洁面", "洗面奶", "洁颜", "洁面乳", "洁面泡沫"],
             "温和清洁不紧绷", "过度清洁导致屏障受损", "日常用更稳得住", "核心成分"),
            ("乳液", ["乳液", "润肤乳"],
             "保湿滋润不黏腻", "盲目追求厚重导致闷痘", "日常保湿更舒服", "核心功效"),
        ]

        if explicit_cat:
            for name, _kws, cn, ap, ben, pl in PROFILES:
                if (
                    name == explicit_cat
                    or explicit_cat in name
                    or name in explicit_cat
                    or any(kw == explicit_cat or kw in explicit_cat for kw in _kws)
                ):
                    return name, cn, ap, ben, pl
            return explicit_cat, "匹配核心诉求", "被附加卖点带跑", "买了才不会闲置", "核心特点"

        msg = turn.raw_message or ""

        # 先看msg里有没有明确品类关键词
        msg_cat_score = {}
        for name, kws, cn, ap, ben, pl in PROFILES:
            msg_cat_score[name] = sum(3 for kw in kws if kw in msg)
        msg_best = max(msg_cat_score.items(), key=lambda x: x[1]) if msg_cat_score else None

        # 再看TOP1产品的category（ranker已把最相关排第一）
        top1_cat = None
        if products:
            top1_cat = (products[0].get("category") or "").strip() or None

        # 如果msg里明确说了某品类，优先用
        if msg_best and msg_best[1] > 0:
            for name, kws, cn, ap, ben, pl in PROFILES:
                if name == msg_best[0]:
                    return name, cn, ap, ben, pl

        # 如果TOP1产品的品类在已知PROFILES里，用它
        if top1_cat:
            for name, kws, cn, ap, ben, pl in PROFILES:
                if name == top1_cat or top1_cat in name:
                    return name, cn, ap, ben, pl

        # 最后按products词频兜底
        prod_cat_text = " ".join(
            ((p.get("category") or "") + " " +
             (
                 (p.get("_specs") or {}).get("subcategory", "")
                 if isinstance(p.get("_specs"), dict)
                 else (p.get("subcategory") or "")
             ))
            for p in products[:8]
        )

        best = None
        best_score = 0
        for name, kws, cn, ap, ben, pl in PROFILES:
            score = sum(2 for kw in kws if kw in prod_cat_text)
            if score > best_score:
                best_score = score
                best = (name, cn, ap, ben, pl)
        if best:
            return best
        return "好物", "匹配核心诉求", "被附加卖点带跑", "买了才不会闲置", "核心特点"

    @staticmethod
    def _price_range_label(budget: Optional[float]) -> str:
        if not budget or budget <= 0:
            return ""
        if budget >= 800:
            return "进阶"
        if budget <= 200:
            return "高性价比"
        return "性价比"

    def _select_top3(
        self,
        products: List[Dict[str, Any]],
        category_name: str,
        budget: Optional[float],
        budget_min: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        def _mismatch(p: Dict[str, Any]) -> int:
            name = (p.get("name", "") or "") + " " + (p.get("display_name", "") or "")
            specs = p.get("_specs") or {}
            subcat = (specs.get("subcategory") or p.get("subcategory") or "").strip()
            if category_name == "好物":
                return 0
            bad_words = {
                "面霜": ["洁面", "洗面奶", "洗面霜", "洁面霜", "眼霜", "面膜", "粉底液", "口红", "眼影", "眉笔",
                          "散粉", "蜜粉", "卸妆", "精华水", "爽肤水", "防晒", "卸妆油", "卸妆水"],
            }
            if category_name in bad_words:
                for b in bad_words[category_name]:
                    if b in name:
                        return 3
            if subcat and subcat == category_name:
                return 0
            return 1

        scored = []
        has_budget_range = budget_min is not None and budget is not None
        budget_mid = ((budget_min or 0) + (budget or 0)) / 2 if has_budget_range else None
        for idx, p in enumerate(products):
            mm = _mismatch(p)
            try:
                price = float(p.get("price_val") or p.get("price") or 999999)
            except Exception:
                price = 999999
            if has_budget_range and budget_mid:
                if budget_min <= price <= budget:
                    budget_penalty = abs(price - budget_mid) / budget_mid
                elif price < budget_min:
                    budget_penalty = 3 + (budget_min - price) / budget_mid
                else:
                    budget_penalty = 2 + (price - budget) / budget_mid
                price_key = abs(price - budget_mid)
            else:
                budget_penalty = 0
                if budget and price > budget * 1.35:
                    budget_penalty = 5
                if budget and budget >= 300 and price <= budget:
                    floor = budget * 0.3
                    target = budget * 0.55
                    if price < floor:
                        budget_penalty = 2 + (floor - price) / floor
                    else:
                        budget_penalty = abs(price - target) / target
                    price_key = abs(price - target)
                else:
                    price_key = price
            scored.append((mm, budget_penalty, price_key, idx, p))
        scored.sort(key=lambda x: (x[0], x[1], x[2], x[3]))
        selected = [x[4] for x in scored[:3]]
        if category_name == "面霜" and len(selected) >= 3:
            selected_ids = {p.get("id") for p in selected}
            special_scene = None
            for _, _, _, _, product in scored:
                if product.get("id") in selected_ids:
                    continue
                text = " ".join([
                    product.get("brand") or "",
                    product.get("name") or "",
                    product.get("display_name") or "",
                    product.get("description") or "",
                    product.get("suitable_skin") or "",
                ])
                price = self._safe_price(product)
                within_budget = budget is None or not price or price <= budget
                if within_budget and any(w in text for w in ["特护", "舒敏", "退红", "泛红", "医美", "薇诺娜"]):
                    special_scene = product
                    break
            if special_scene is not None:
                selected[-1] = special_scene
        return selected

    @staticmethod
    def _safe_price(product: Dict[str, Any]) -> float:
        try:
            return float(product.get("price_val") or product.get("price") or 0)
        except Exception:
            return 0.0

    @staticmethod
    def _looks_like_review(text: str) -> bool:
        t = str(text or "")
        if not t:
            return False
        review_signals = ["***", "回购", "赞不绝口", "买家", "品质好", "送货快", "客服", "物流",
                          "我肤质", "适合我", "收到货", "包装好", "下次还", "用了一段", "上脸后",
                          "权威第三方", "机构认证"]
        if any(s in t for s in review_signals):
            return True
        if re.search(r"\d+\s*[。.]\s*\d", t):
            return True
        if len(t) > 60 and any(s in t for s in ["吸收", "肤感", "清爽", "黏腻", "舒服"]):
            return True
        return False

    @classmethod
    def _filter_real_usage_steps(cls, raw_steps: list) -> list:
        real = []
        for s in raw_steps or []:
            t = str(s or "").strip()
            if not t:
                continue
            if cls._looks_like_review(t):
                continue
            if len(t) > 80 and any(w in t for w in ["回购", "舒服", "超棒", "明显", "认证", "适用"]):
                continue
            if re.search(r"\d+[。.]\s*0", t):
                continue
            if len(re.findall(r"[\u4e00-\u9fff]", t)) < 4:
                continue
            real.append(t)
        return real

    @staticmethod
    def _generic_usage_advice(p: Dict[str, Any]) -> str:
        cat = (p.get("category") or "").strip()
        name = p.get("name") or p.get("display_name") or ""
        has_sunscreen = any(k in name + cat for k in ["防晒", "隔离"])
        has_mask = any(k in name + cat for k in ["面膜"])
        has_makeup = any(k in name + cat for k in ["粉底", "气垫", "妆前", "BB霜", "CC霜"])
        has_cleanser = any(k in name + cat for k in ["洁面", "卸妆", "洗面奶", "洁颜"])
        has_makeup_remover = any(k in name + cat for k in ["卸妆"])
        has_toner = any(k in name + cat for k in ["水", "爽肤水", "柔肤水", "化妆水"])
        has_essence = any(k in name + cat for k in ["精华"])
        has_cream = any(k in name + cat for k in ["霜", "乳液"])

        suitable = (p.get("suitable_skin") or "")
        need_sunscreen = any(k in suitable + (p.get("description") or "") for k in ["烟酰胺", "美白", "酸", "A醇", "视黄醇", "VC", "维c", "抗氧"])

        lines = []
        if has_cleanser or has_makeup_remover:
            lines.append("- 干手干脸取适量，轻柔打圈按摩30-60秒后用温水冲净，避免用力拉扯。")
            if has_makeup_remover:
                lines.append("- 卸完建议再用洁面二次清洁，避免残留闷痘。")
        elif has_mask:
            lines.append("- 洁面后敷10-15分钟即可，不要敷到面膜纸干透，后续拍打吸收。")
            lines.append("- 贴片面膜不用洗的话也要把剩余精华按摩吸收，避免闷痘。")
        elif has_sunscreen:
            lines.append("- 出门前15-20分钟足量涂抹（脸部约一枚硬币大小），成膜后再上妆。")
            lines.append("- 长时间户外或出汗后2-3小时补涂一次。")
        elif has_makeup:
            lines.append("- 做好妆前保湿+防晒，取适量从面中向外轻拍推开，鼻翼、嘴角处用余粉带过。")
        elif has_toner:
            lines.append("- 洁面后取适量于化妆棉或手心，轻拍至吸收，再进行后续护肤。")
        elif has_essence or has_cream:
            lines.append("- 洁面-爽肤水后使用，取适量均匀点涂面部，由内向外轻轻按摩至吸收。")
            if need_sunscreen:
                lines.append("- 这款含功效性成分，首次用建议先低频建立耐受，白天务必配合防晒。")
            if cat == "精华" or "精华" in name:
                lines.append("- 后续可以叠加乳液/面霜锁水。")
        else:
            lines.append("- 洁面爽肤后取适量均匀涂抹于面部，轻轻按摩至吸收即可。")
            if need_sunscreen:
                lines.append("- 含功效性成分，首次使用建议低频建立耐受，白天配合防晒。")

        suitable_skin = suitable or (p.get("description") or "")
        if "烟酰胺不耐受" in suitable_skin or "慎用" in suitable_skin:
            lines.append("- 初次使用建议耳后或下颌小范围测试，出现泛红刺痛立即停用。")

        return "\n".join(lines)

    @staticmethod
    def _parse_desc(desc: str) -> Dict[str, str]:
        info: Dict[str, str] = {}
        if not desc:
            return info
        cleaned = []
        for raw in desc.split("\n"):
            line = raw.strip()
            if not line:
                continue
            if line.startswith("备注") and any(b in line for b in ["产地参数", "非特殊用途", "生产企业", "备案号"]):
                continue
            if line.startswith("核心成分") and any(b in line for b in ["未展示", "未核", "具体以官方"]):
                continue
            if any(line.startswith(p) for p in ["【数据来源】", "【商品】", "【规格】", "【详情页】", "【功效/成分"]):
                continue
            cleaned.append(line)
        text = "\n".join(cleaned)
        for field in ["定位", "适合人群", "适合肤质", "核心成分", "主打功效", "规格", "备注"]:
            m = re.search(rf"{field}[：:]\s*([^\n]+)", text)
            if m:
                info[field] = m.group(1).strip()
        if "适合肤质" not in info:
            sm = re.search(r"适合([^，。；\n]{2,12}?(?:肌|肤质|皮肤))", text)
            if sm:
                info["适合肤质"] = sm.group(1).strip()
        return info

    def _build_product_block(
        self, p: Dict[str, Any], label: str, parsed: Dict[str, str], param_label: str, category_name: str
    ) -> str:
        name = self._short_product_name(p)
        price = p.get("price_val") or p.get("price") or 0
        spec = parsed.get("规格", "") or self._extract_product_spec(p)
        try:
            pn = int(price) if float(price) == int(float(price)) else price
            price_text = f"约¥{pn}" + (f" / {spec}" if spec else "") if price else "价格见详情页"
        except Exception:
            price_text = "价格见详情页"

        reason = self._extract_reason(p, parsed, category_name)
        param = self._extract_param(p, parsed, category_name)
        ingredients = p.get("key_ingredients_list") or []
        core_ingredients = "、".join(ingredients[:3]) if ingredients else (parsed.get("核心成分") or param)
        note = self._extract_note(p, parsed, category_name)

        def _clean(t: str, n: int = 60) -> str:
            t = str(t or "").replace("\n", " ").replace("\r", " ").strip()
            t = re.sub(r"\s+", " ", t)
            if len(t) > n:
                cut = t[:n]
                for end_char in ["。", "，", "；", ";", "、", " "]:
                    pos = cut.rfind(end_char)
                    if pos >= n * 0.6:
                        cut = cut[:pos]
                        break
                t = cut.rstrip("，。、；;，（(、,") + "…"
            return t

        return (
            f"**{label}：{name}**\n\n"
            f"{_clean(reason, 90)}\n\n"
            f"- 参考价：{_clean(price_text, 40)}\n"
            f"- 核心成分：{_clean(core_ingredients, 50)}\n"
            f"- 注意点：{_clean(note, 50)}"
        )

    def _extract_param(self, p: Dict[str, Any], parsed: Dict[str, str], category_name: str) -> str:
        concerns = p.get("concerns_list") or []
        ingredients = p.get("key_ingredients_list") or []
        desc = p.get("description") or ""
        if category_name == "防晒":
            m = re.search(r"SPF(\d+\+?)\s*(?:PA?(\+*))?", desc, re.IGNORECASE)
            if m:
                return f"SPF{m.group(1)}" + (f" / PA{m.group(2)}" if m.group(2) else "")
            m2 = re.search(r"(?:spf|防晒指数|防晒系数)[：: ]?\s*(\d+\+?)", str(p.get("specs") or p.get("_specs") or {}), re.IGNORECASE)
            if m2:
                return f"SPF{m2.group(1)}"
            if concerns:
                return "、".join(concerns[:2])
            return "高倍防晒"
        if category_name == "精华":
            if ingredients:
                return "、".join(ingredients[:3])
            return (parsed.get("核心成分") or "核心修护成分")[:30]
        if category_name == "底妆":
            if concerns:
                return "、".join(concerns[:3])
            return "自然服帖款"
        if category_name in ("面霜", "眼霜", "面膜", "乳液"):
            if concerns:
                return "、".join(concerns[:3])
            kws = ["保湿", "修护", "滋润", "抗老", "紧致", "提亮", "补水", "舒缓"]
            hit = [k for k in kws if k in desc]
            if hit:
                return "、".join(hit[:3])
            return (parsed.get("主打功效") or "保湿修护")[:20]
        if category_name == "爽肤水":
            if concerns:
                return "、".join(concerns[:3])
            return (parsed.get("主打功效") or "保湿调理")[:20]
        if category_name == "洁面":
            if concerns:
                return "、".join(concerns[:3])
            return "温和清洁"
        if concerns:
            return "、".join(concerns[:3])
        return (parsed.get("主打功效") or "匹配本轮诉求")[:20]

    def _extract_suitable(self, p: Dict[str, Any], parsed: Dict[str, str]) -> str:
        skin = self._clean_suitable_skin(p.get("suitable_skin") or "")
        if skin:
            return skin
        if parsed.get("适合肤质"):
            s = self._clean_suitable_skin(parsed["适合肤质"])
            s = re.sub(r"多种肤质[；;，,]*", "", s).strip("；;，, ")
            if s:
                return s
        return "大部分肤质可用，敏感肌建议先试"

    @staticmethod
    def _clean_suitable_skin(value: str) -> str:
        text = re.sub(r"\s+", " ", str(value or "")).strip(" ；;。")
        if not text:
            return ""
        parts = [part.strip(" ；;。") for part in re.split(r"[；;]", text) if part.strip(" ；;。")]
        filtered = []
        for part in parts:
            if any(noise in part for noise in ["国货线", "国货", "标杆", "品牌线"]):
                continue
            filtered.append(part)
        return "；".join(filtered) if filtered else text

    def _extract_note(self, p: Dict[str, Any], parsed: Dict[str, str], category_name: str) -> str:
        pitfalls = (p.get("pitfalls") or "").strip()
        caution_signals = [
            "先", "不要", "避免", "注意", "测试", "试用", "停用", "泛红", "刺痛",
            "闷", "熏眼", "补涂", "清洁", "超时", "敏感期", "酒精", "香精",
            "酸类", "A醇", "建立耐受", "厚敷", "局部",
        ]
        if (
            pitfalls
            and "产地参数" not in pitfalls
            and "非特殊用途" not in pitfalls
            and any(signal in pitfalls for signal in caution_signals)
            and len(pitfalls) >= 8
            and (pitfalls.endswith("。") or pitfalls.endswith("；") or pitfalls.endswith("，") or len(pitfalls) >= 14)
        ):
            return pitfalls
        if parsed.get("备注"):
            n = parsed["备注"]
            if any(b in n for b in ["产地参数", "非特殊用途", "生产企业", "备案号", "联合研发品牌", "品牌", "规格", "初体验"]):
                n = ""
            if n and len(n) < 50 and any(signal in n for signal in caution_signals):
                return n
        data_warning = self._extract_product_data_warning(p)
        if data_warning:
            return data_warning
        desc = p.get("description") or ""
        if category_name == "防晒":
            text = " ".join([
                desc,
                p.get("name") or "",
                p.get("display_name") or "",
                p.get("positioning") or "",
                p.get("suitable_skin") or "",
                " ".join(p.get("concerns_list") or []),
            ])
            if any(w in text for w in ["敏感肌", "混敏", "干敏", "医美术后", "温和"]):
                return "敏感肌先试耳后和眼周，重点观察熏眼、泛红、刺痛和搓泥"
            if any(w in text for w in ["防水", "防汗", "户外", "军训", "安热沙", "安耐晒", "小金瓶", "蓝胖子"]):
                return "户外或出汗场景要足量涂抹并补涂，防水防汗款建议认真清洁"
            if any(w in text for w in ["妆前", "贴妆", "隔离", "小白管", "空气感"]):
                return "妆前使用先等成膜再上底妆，容易搓泥时减少前序护肤叠加"
            if any(w in text for w in ["水感", "清爽", "油皮", "混油", "油痘"]):
                return "清爽型也要涂够量，油皮重点观察是否闷闭口和熏眼"
            return "涂够量才有效（约一元硬币大小），记得补涂"
        if category_name == "精华":
            text = " ".join([
                desc,
                p.get("positioning") or "",
                p.get("suitable_skin") or "",
                " ".join(p.get("concerns_list") or []),
                " ".join(p.get("key_ingredients_list") or []),
            ])
            if any(k in text for k in ["玻色因", "A醇", "视黄醇", "视黄醛"]):
                return "抗老活性成分存在刺激概率，干敏肌先隔天夜间用，别叠加酸类"
            if any(k in text for k in ["水杨酸", "果酸", "杏仁酸"]):
                return "酸类产品有刺激性，敏感期停用，白天必须严格防晒"
            if any(k in text for k in ["烟酰胺", "小白瓶", "淡斑", "美白", "377"]):
                return "烟酰胺/提亮类先低频用，泛红或刺痛时先停用观察"
            if any(k in text for k in ["玉泽", "PBS", "仿生脂质", "屏障"]):
                return "屏障修护线先单独用3-5天，确认不闷闭口和不泛红再叠加其他功效"
            if any(k in text for k in ["二裂酵母", "小棕瓶", "小黑瓶", "维稳", "修护"]):
                return "维稳修护型相对温和，但干敏肌仍建议先单独用3-5天观察"
            if any(k in text for k in ["蓝铜胜肽", "胜肽", "蓝金能量"]):
                return "胜肽抗老类先隔天使用，敏感期不要和酸/A醇同晚叠加"
            if any(k in text for k in ["油痘肌先测试", "油痘肌", "闭口"]):
                return "油痘肌或易闭口肤质先少量试用，确认不闷再全脸"
            return "先低频少量使用，确认不泛红刺痛后再提高频次"
        if category_name == "底妆":
            return "建议先试色号，不同肤色上脸效果有差异"
        if category_name == "爽肤水":
            if "敏感" in desc or "温和" in desc:
                return "温和调理型，敏感肌可用，建议用手轻拍促进吸收"
            if "控油" in desc:
                return "偏清爽控油型，油皮/混油皮夏季可做二次清洁"
            return "洁面后使用，帮助后续护肤品吸收"
        if category_name == "面霜":
            texture = ((p.get("_specs") or {}).get("texture") or "")
            skin = (p.get("suitable_skin") or "")
            concerns_text = " ".join(p.get("concerns_list") or [])
            if "B5" in desc or "厚敷" in desc or "急救" in concerns_text:
                return "厚敷可做急救，但日常建议薄涂，油皮避免全脸厚敷过夜"
            if "清爽" in texture or ("油皮" in skin and "干皮" not in skin and "干敏" not in skin):
                return "质地偏清爽，混油/油皮春秋可用，干皮冬天可能需叠加"
            if "敏感" in desc or "屏障" in desc or "修护" in concerns_text:
                return "修护类相对温和，但换季/敏感期仍建议先在耳后试用"
            return "掌心乳化后按压上脸更易吸收，干皮秋冬用更安心"
        if category_name == "眼霜":
            return "取米粒大小用无名指轻点眼周，不要拉扯眼周肌肤"
        if category_name == "面膜":
            if "清洁" in desc:
                return "清洁类面膜不要频繁使用，一周1-2次即可"
            return "敷10-15分钟即可，不要超时，敷后记得后续保湿"
        if category_name == "洁面":
            return "早晚清洁即可，不要过度清洁导致屏障受损"
        if category_name == "乳液":
            return "质地相对轻薄，油皮夏季可单独用，干皮建议叠加面霜"
        return "价格为入库参考价，非实时，点商品链接查天猫/京东实时价"

    def _extract_reason(self, p: Dict[str, Any], parsed: Dict[str, str], category_name: str) -> str:
        brand = (p.get("brand") or "").strip()
        name = p.get("name", "") or p.get("display_name", "")
        positioning = (p.get("positioning") or "").strip().replace("\n", " ")
        concerns = p.get("concerns_list") or []
        if positioning and 0 < len(positioning) < 70:
            reason = positioning.rstrip("。") + "。"
        elif concerns:
            top_gx = "、".join(concerns[:2])
            b = brand or name[:6]
            reason = f"{b}这款{category_name}主打的是{top_gx}。"
        elif parsed.get("主打功效"):
            gx_parts = [x.strip() for x in re.split(r"[；;、，,]", parsed["主打功效"]) if x.strip()]
            b = brand or ""
            if not b:
                for sep in ["爽肤水", "精华液", "精华露", "防晒霜", "防晒乳", "面霜", "眼霜", "面膜", "粉底液", "洁面", "乳液"]:
                    if sep in name:
                        b = name.split(sep)[0].strip()
                        break
                if not b:
                    b = name[:6].strip()
            reason = f"{b}这款{category_name}主打{('、'.join(gx_parts[:2]) or parsed['主打功效'][:15])}。"
        elif brand and brand in name:
            reason = f"{brand}经典{category_name}，口碑比较稳。"
        else:
            reason = "这款匹配本轮需求。"
        for turd in FORBIDDEN_PHRASES:
            reason = reason.replace(turd, "")
        reason = re.sub(r"定位[：:]", "", reason)
        return reason.strip("，。、；; ") + "。"

    @staticmethod
    def _label_for(i: int, p: Dict[str, Any], skin_type: str) -> str:
        try:
            price = float(p.get("price_val") or p.get("price") or 0)
        except Exception:
            price = 0
        if i == 0:
            if price >= 800:
                return "更贴合诉求"
            return "优先看这款"
        if i == 1:
            if price >= 800:
                return "进阶之选"
            return "可以对比这款"
        text = " ".join(p.get("concerns_list") or []) + " " + (p.get("suitable_skin") or "") + " " + (p.get("description") or "")
        if skin_type in ("敏感肌", "干敏肌", "油敏肌") and ("敏感" in text or "屏障" in text or "温和" in text):
            return "敏感肌可参考"
        return "备选"

    def _build_summary(
        self,
        top: List[Dict[str, Any]],
        budget: Optional[float],
        skin_type: str,
        category_name: str,
        budget_min: Optional[float] = None,
    ) -> str:
        def _short(p: Dict[str, Any]) -> str:
            compact = self._short_product_name(p)
            if compact:
                return compact
            brand = (p.get("brand") or "").strip()
            nm = (p.get("display_name") or p.get("name") or "").strip()
            junk_words = ["护肤品", "化妆品", "套装", "礼盒", "生日礼物", "送女友", "送男友", "送老婆",
                          "送老公", "送妈妈", "送爸爸", "男女", "官方", "旗舰", "同款", "升级版",
                          "第二代", "第三代", "第四代", "新一代", "黄子弘凡同款", "（黄子弘凡同款）",
                          "正品", "补水保湿", "保湿补水", "秋冬", "夏季", "春夏", "干敏肌", "油敏肌",
                          "敏感肌", "干皮", "油皮", "混干", "混油", "医美术后", "屏障受损"]
            for j in junk_words:
                nm = nm.replace(j, "")
            # 去掉SPF/PA/数字ml等规格噪声
            nm = re.sub(r"SPF\d+\+?", "", nm)
            nm = re.sub(r"PA\+{0,4}", "", nm)
            nm = re.sub(r"\d+\s*ml", "", nm, flags=re.IGNORECASE)
            nm = re.sub(r"\d+\s*g", "", nm, flags=re.IGNORECASE)
            nm = re.sub(r"\s+", " ", nm).strip(" ，,。、")
            if brand and nm.startswith(brand):
                nm = nm[len(brand):].strip(" ，,")
            cat_kw = {
                "面霜": ["面霜", "保湿霜", "霜"],
                "防晒": ["防晒霜", "防晒乳", "防晒", "隔离"],
                "精华": ["精华液", "精华", "小白瓶", "小棕瓶", "绿宝瓶"],
                "洁面": ["洗面奶", "洗面霜", "洁颜霜", "洁颜", "洁面"],
                "爽肤水": ["爽肤水", "化妆水", "精粹水", "精华水", "水"],
                "眼霜": ["眼霜"],
                "面膜": ["面膜"],
                "乳液": ["乳液", "乳", "天才黄油", "黄油"],
                "底妆": ["粉底液", "气垫", "粉底"],
            }
            core_seg = nm
            kws_list = cat_kw.get(category_name, [])
            # 优先匹配最长的关键词（如"防晒霜"优先于"防晒"）
            for kws in sorted(kws_list, key=len, reverse=True):
                idx = nm.find(kws)
                if idx >= 0:
                    start = max(0, idx - 8)
                    end = idx + len(kws)
                    core_seg = nm[start:end].strip(" ，,")
                    break
            out = (brand + core_seg) if brand else core_seg
            if len(out) > 14:
                cut = out[:14]
                for stop in ["霜", "乳", "液", "瓶", "膏", "蜜", "精华", "水", "油", "黄油"]:
                    si = cut.rfind(stop)
                    if si >= 6:
                        out = cut[:si + len(stop)]
                        break
                else:
                    out = cut
            return out or brand or "这款"

        def _price(p: Dict[str, Any]) -> float:
            try:
                return float(p.get("price_val") or p.get("price") or 0)
            except Exception:
                return 0.0

        def _focus(p: Dict[str, Any]) -> str:
            text = " ".join([
                p.get("name") or "",
                p.get("display_name") or "",
                p.get("brand") or "",
                p.get("positioning") or "",
                p.get("suitable_skin") or "",
                " ".join(p.get("concerns_list") or []),
                " ".join(p.get("key_ingredients_list") or []),
                p.get("description") or "",
            ])
            if category_name == "防晒":
                sensitive_friendly = (
                    any(w in text for w in ["敏感肌可用", "敏感肌适用", "敏感肌友好", "混敏", "干敏", "医美术后", "温和", "薇诺娜", "理肤泉"])
                    and "敏感肌需测试" not in text
                )
                if sensitive_friendly:
                    return "敏感肌通勤防护"
                if any(w in text for w in ["安热沙", "安耐晒", "小金瓶", "蓝胖子", "防水", "防汗", "户外", "军训"]):
                    return "户外防水防汗"
                if any(w in text for w in ["小白管", "隔离", "妆前", "贴妆", "空气感", "抗光老"]):
                    return "通勤妆前防晒"
                if any(w in text for w in ["水感", "清爽", "油皮", "混油", "油痘"]):
                    return "清爽通勤防晒"
                return "日常防护"
            if any(w in text for w in ["玻色因", "A醇", "视黄醇", "抗皱", "淡纹", "紧致"]):
                return "抗老淡纹"
            if any(w in text for w in ["蓝铜胜肽", "胜肽", "抗老"]):
                return "抗老维稳"
            if any(w in text for w in ["二裂酵母", "小黑瓶", "小棕瓶", "维稳", "修护"]):
                return "维稳修护"
            if any(w in text for w in ["美白", "淡斑", "提亮", "烟酰胺"]):
                return "提亮淡斑"
            if any(w in text for w in ["保湿", "补水", "舒缓"]):
                return "保湿舒缓"
            return "本轮诉求"

        def _product_text(p: Dict[str, Any]) -> str:
            return " ".join([
                p.get("name") or "",
                p.get("display_name") or "",
                p.get("brand") or "",
                p.get("category") or "",
                p.get("positioning") or "",
                p.get("suitable_skin") or "",
                p.get("description") or "",
                " ".join(p.get("concerns_list") or []),
                " ".join(p.get("key_ingredients_list") or []),
            ])

        def _decision_sentence(p: Dict[str, Any], rank: int) -> str:
            short = _short(p)
            price = int(_price(p)) if _price(p) else 0
            price_part = f"约¥{price}，" if price else ""
            text = _product_text(p)
            if category_name == "面霜":
                if any(w in text for w in ["薇诺娜", "马齿苋", "舒敏", "玫瑰痤疮"]):
                    if "薇诺娜" in text and ("特护" in text or "舒敏" in text):
                        short = "薇诺娜特护霜"
                    else:
                        short = short.replace(" 面霜", "")
                    return f"{short}更偏敏感泛红、油敏肌或医美后舒缓，{price_part}适合把预算留给敏感期稳定，不是单纯追求厚润。"
                if any(w in text for w in ["玉泽", "PBS", "仿生脂质", "高保湿", "皮肤屏障修护"]):
                    return f"{short}更适合干敏皮长期屏障维稳和日常保湿，{price_part}比B5更偏日常面霜，不是急救厚敷路线。"
                if any(w in text for w in ["B5", "理肤泉", "厚敷", "修护痘印"]):
                    return f"优先看{short}，{price_part}更适合屏障不稳、泛红或刷酸后局部修护；日常薄涂就够，油皮别全脸厚敷过夜。"
                if any(w in text for w in ["清爽", "油皮", "混油"]):
                    return f"{short}更适合想要清爽肤感的人，{price_part}重点观察是否保湿够、不闷闭口。"
                return f"{short}主打{_focus(p)}，{price_part}可以作为同价位补充选择，先看质地和肤质耐受。"
            if category_name == "防晒":
                sensitive_friendly = (
                    any(w in text for w in ["敏感肌可用", "敏感肌适用", "敏感肌友好", "混敏", "干敏", "医美术后", "温和", "薇诺娜", "理肤泉"])
                    and "敏感肌需测试" not in text
                )
                if sensitive_friendly:
                    return f"{short}更偏敏感肌日常通勤，{price_part}先看是否熏眼、泛红和搓泥，适合把预算放在温和防护上。"
                if any(w in text for w in ["安热沙", "安耐晒", "小金瓶", "蓝胖子", "防水", "防汗", "户外", "军训"]):
                    return f"{short}更适合户外、出汗或长时间防护，{price_part}优势在防水防汗，不是只追求便宜。"
                if any(w in text for w in ["小白管", "隔离", "妆前", "贴妆", "空气感", "抗光老"]):
                    return f"{short}更适合通勤和妆前打底，{price_part}重点看贴妆、轻薄和抗光老。"
                return f"{short}主打{_focus(p)}，{price_part}可以作为同预算内的防晒备选，先看肤感和使用场景。"
            if rank == 0:
                return f"优先看{short}，{price_part}更贴近本轮诉求，主打{_focus(p)}。"
            if rank == 1:
                return f"{short}适合把预算放在{_focus(p)}上，和首选款形成侧重差异。"
            return f"{short}偏{_focus(p)}，适合作为同预算内的备选。"

        lines = []
        if budget_min is not None and budget is not None:
            budget_mid = int((budget_min + budget) / 2)
            skin_label = skin_type or "当前肤质"
            lines.append(f"按{budget_mid}元左右来看，这几款都在预算附近；选的时候主要看功效侧重和{skin_label}能不能建立耐受。")
        else:
            if budget is not None:
                if category_name == "防晒":
                    lines.append(f"这几款都在¥{int(budget)}以内，别只盯着最低价，通勤还是户外、膜感舒不舒服、敏不敏感比价格更重要。")
                else:
                    lines.append(f"这几款都压在¥{int(budget)}以内，别只看谁便宜，修护逻辑和质地厚薄才是选对的关键。")

        for idx, product in enumerate(top[:3]):
            lines.append(_decision_sentence(product, idx))

        extra = ""
        if category_name == "面霜":
            if skin_type in ("敏感肌", "干敏肌", "油敏肌"):
                extra = "敏感肌第一次用建议先在耳后做皮试，没问题再上脸。"
            else:
                extra = "面霜选对质地很关键：油皮挑清爽乳霜、干皮挑厚润霜，换季尤其要注意。"
        elif category_name == "精华":
            extra = "功效型精华建议从低频次开始，建立耐受后再逐步增加，不要一次叠加多种猛料。"
        elif category_name == "防晒":
            extra = "防晒涂够量才有效（约一元硬币大小），户外每2小时补涂一次。"
        elif category_name == "底妆":
            extra = "底妆建议靠柜试色或买小样试，色号和肤感每个人差异都很大。"
        elif category_name == "爽肤水":
            extra = "爽肤水用手轻拍即可，不建议天天用化妆棉擦拭，容易过度摩擦角质。"
        elif category_name == "眼霜":
            extra = "眼霜用无名指轻点，不要来回拉扯眼周肌肤。"
        elif category_name == "面膜":
            extra = "不管什么面膜都别敷超时（10-15分钟即可），敷完记得后续保湿锁水。"
        if extra:
            lines.append(extra)
        lines.append("")
        lines.append(f"_{FOOTER_NOTE}_")
        return "\n".join(lines)

    @staticmethod
    def _sanitize_plain(text: str) -> str:
        for t in FORBIDDEN_PHRASES:
            text = text.replace(t, "")
        text = re.sub(r"(?:这里要注意[：:])?这里展示的是入库参考价，实时活动价和规格组合以下单页为准。?", "", text)
        text = re.sub(r"(?:参考价为入库时价格，实时活动价以商品链接为准。\s*){2,}", FOOTER_NOTE, text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    @staticmethod
    def _sanitize_structured(text: str) -> str:
        for t in FORBIDDEN_PHRASES:
            text = text.replace(t, "")
        text = re.sub(r"(?:这里要注意[：:])?这里展示的是入库参考价，实时活动价和规格组合以下单页为准。?", "", text)
        text = re.sub(r"(?:参考价为入库时价格，实时活动价以商品链接为准。\s*){2,}", FOOTER_NOTE, text)
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def _build_product_knowledge_answer(self, turn: CanonicalTurn, products: List[Dict[str, Any]]) -> str:
        product = products[0]
        name = self._short_product_name(product)
        ingredients = product.get("key_ingredients_list") or self._split_field(
            self._product_data_value(product, "key_ingredients", "核心成分")
        )
        concerns = product.get("concerns_list") or self._split_field(
            self._product_data_value(product, "concerns", "主打功效")
        )
        target_users = self._product_data_value(product, "target_users", "适合人群")
        suitable_skin = self._clean_suitable_skin(
            self._product_data_value(product, "suitable_skin", "suitable_skin_types", "适合肤质")
            or product.get("suitable_skin")
            or ""
        )
        warning = self._extract_product_data_warning(product)
        asked = turn.raw_message or ""
        selected = [
            item for item in ingredients
            if any(part and part in asked for part in re.split(r"[（(、/，,™\s]+", item))
        ] or ingredients[:5]

        lines = [f"你问的是 {name} 里这组核心成分，按商品资料可以这样理解：", ""]
        for item in selected[:5]:
            lines.append(f"- {item}：{self._explain_ingredient_role(item)}")
        if concerns:
            lines.append(f"- 这款整体诉求：{'、'.join(concerns[:4])}。")
        if target_users:
            lines.append(f"- 更适合：{self._trim_reason_phrase(target_users, 58)}。")
        if suitable_skin:
            lines.append(f"- 肤质参考：{self._trim_reason_phrase(suitable_skin, 58)}。")
        if warning:
            lines.append(f"- 注意点：{self._trim_reason_phrase(warning, 70)}。")
        else:
            lines.append("- 注意点：这类功效精华先低频试用，别和酸类、A醇或强去角质同晚叠加。")
        return "\n".join(lines)

    @staticmethod
    def _explain_ingredient_role(ingredient: str) -> str:
        text = str(ingredient or "")
        if "海茴香" in text or "植物干细胞" in text:
            return "主要是这条产品线的强韧、抗氧和维稳卖点，不等同于医学意义上的“干细胞治疗”。"
        if "益生菌" in text or "菌" in text:
            return "偏向屏障防御和微生态维稳的表达，重点看复配和长期耐受，不是越高浓度越好。"
        if "咖啡因" in text:
            return "常见于抗氧、改善倦容和辅助紧致类配方，更多是辅助角色。"
        if "PITERA" in text or "酵母" in text or "发酵" in text:
            return "偏肤质调理、细腻肤感和维稳提亮，敏感肌仍建议先局部试。"
        if "烟酰胺" in text:
            return "常用于提亮、均匀肤色和辅助屏障，敏感肌要看浓度和耐受。"
        if "泛醇" in text or "B5" in text:
            return "偏舒缓、保湿和辅助屏障修护，泛红或屏障弱时更有参考价值。"
        if "玻色因" in text:
            return "偏抗老、紧致和充盈感方向，干敏肌先低频建立耐受。"
        if "胜肽" in text or "肽" in text:
            return "常用于抗老、淡纹和修护协同，实际效果看复配和使用周期。"
        return "属于这款商品资料里的核心卖点，具体功效要结合浓度、复配和肤质耐受看。"

    @staticmethod
    def _build_knowledge_fallback(turn: CanonicalTurn) -> str:
        msg = turn.raw_message or ""
        topic = msg[:30]
        if "早C晚A" in msg or "早c晚a" in msg or ("早C" in msg and "晚A" in msg):
            return (
                "早C晚A是一种功效护肤搭配思路：早上用维C或抗氧化类产品，晚上用A醇/视黄醇类产品。"
                "它的核心不是固定公式，而是把更适合白天抗氧化的成分和更适合夜间建立耐受的抗老成分错开。"
                "敏感肌、屏障不稳或新手不要一开始就早晚都上猛料，先从低频晚A开始，白天必须做好防晒。"
            )
        if any(term in msg for term in ["玻色因", "A醇", "视黄醇"]):
            if "区别" in msg or "差异" in msg or "不同" in msg:
                return (
                    "玻色因和A醇都常见于抗老产品，但方向不一样。"
                    "玻色因更偏紧致、充盈感和淡纹思路，刺激感通常比A醇低一些，适合想稳妥做抗老的人。"
                    "A醇/视黄醇更偏促进更新、改善粗糙和细纹，但刺激概率更高，敏感肌要从低频夜间开始，并严格防晒。"
                    "如果屏障不稳，先别急着叠A醇，优先把保湿修护和防晒做稳。"
                )
        if "烟酰胺" in msg:
            return (
                "烟酰胺是护肤里很常见的功效成分，主要看浓度和配方搭配。"
                "它常见作用是提亮肤色、帮助均匀肤色、辅助控油和屏障维稳。"
                "敏感肌使用时不要一开始就高频叠加酸类或强功效精华，先低频观察泛红、刺痛和闷闭口。"
            )
        if "玻色因" in msg:
            return (
                "玻色因是抗老产品里常见的成分卖点，核心方向是围绕紧致、淡纹和皮肤充盈感。"
                "它通常不会像强酸类那样直接剥脱角质，但功效型抗老产品仍可能因为复配成分带来刺激。"
                "干敏肌更适合先低频夜间用，稳定后再提高频次。"
            )
        if "A醇" in msg or "视黄醇" in msg:
            return (
                "A醇/视黄醇是经典抗老成分，主要用于改善细纹、粗糙和皮肤更新节奏。"
                "它的刺激概率比普通保湿修护成分更高，新手和敏感肌要从低频夜间开始，并注意白天防晒。"
            )
        return f"关于「{topic}」，先按成分作用、适合肤质和使用风险三个维度判断；功效型成分尤其要看浓度、复配和自己的耐受。"
