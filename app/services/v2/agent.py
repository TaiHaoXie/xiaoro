import json
import logging
import os
import re
from typing import AsyncGenerator, Dict, Any, Optional, List, Tuple
from datetime import datetime

from .models import CanonicalTurn, AnswerMode, FollowupType, FollowupScope
from .turn_parser import TurnParser
from .router import Router
from .retriever import Retriever
from .ranker import Ranker
from .presenter import Presenter
from .state import ShoppingSessionState, build_session_state_payload, apply_answer_contract_to_state
from app.config import settings

logger = logging.getLogger(__name__)


class V2ShoppingAgent:
    def __init__(self):
        self.parser = TurnParser()
        self.router = Router()
        self.retriever = Retriever()
        self.ranker = Ranker()
        self.presenter = Presenter()
        self._session_states: Dict[str, ShoppingSessionState] = {}

    async def chat_stream_events(
        self,
        message: str,
        session_id: Optional[str] = None,
        context: Optional[Dict] = None,
        image_context: Optional[Dict[str, Any]] = None,
        conversation_history: Optional[List[Dict[str, Any]]] = None,
        session_state: Optional[Dict[str, Any]] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        if not session_id:
            session_id = f"session_{datetime.now().timestamp()}"

        yield {"event": "start", "data": {"session_id": session_id}}

        decision_steps: List[Dict[str, Any]] = []

        def record_step(title: str, description: str, step_type: str = "pipeline",
                        score: Optional[float] = None, data: Optional[Dict[str, Any]] = None,
                        reason: Optional[str] = None) -> None:
            decision_steps.append({
                "title": title,
                "description": description,
                "step_type": step_type,
                "score": score,
                "data": data or {},
                "reason": reason or description,
            })

        try:
            cached_state = self._session_states.get(session_id)
            if cached_state is not None:
                loaded_state_dict = build_session_state_payload(cached_state)
            else:
                loaded_state_dict = session_state or {}

            yield {"event": "stage", "data": {"message": "理解你的需求", "status": "active"}}
            turn = await self.parser.parse_async(
                raw_message=message,
                session_id=session_id,
                conversation_history=conversation_history or [],
                image_context=image_context,
            )
            turn.session_state = loaded_state_dict
            await self._apply_followup_state(turn)

            yield {"event": "stage", "data": {"message": "判断任务类型", "status": "active"}}
            route = self.router.route(turn)
            budget_label = self._format_budget_label(turn)
            record_step(
                "理解你的需求",
                f"品类={turn.category or '未指定'}，品牌={turn.brand or '未指定'}，"
                f"预算={budget_label}，肤质={turn.skin_type or '未限定'}。",
                step_type="parse",
                data={
                    "category": turn.category,
                    "brand": turn.brand,
                    "budget": budget_label,
                    "skin_type": turn.skin_type,
                },
            )
            record_step(
                "判断任务类型",
                f"路由到{route.answer_mode.value}模式，置信度{route.confidence or 0:.0%}。",
                step_type="route",
                score=route.confidence,
                data={"mode": route.answer_mode.value},
            )

            yield {
                "event": "intent",
                "data": {
                    "intent": route.answer_mode.value,
                    "confidence": route.confidence,
                    "entities": {
                        "category": turn.category,
                        "brand": turn.brand,
                        "budget_max": turn.budget_max,
                        "budget_min": turn.budget_min,
                        "skin_type": turn.skin_type,
                        "concerns": turn.concerns,
                        "exclude_terms": turn.exclude_terms,
                        "followup_type": turn.followup_type.value if turn.followup_type else None,
                        "followup_scope": turn.followup_scope.value if turn.followup_scope else None,
                        "followup_scope_reason": turn.followup_scope_reason,
                        "followup_judge_source": turn.followup_judge_source,
                        "followup_judge_confidence": turn.followup_judge_confidence,
                        "followup_shadow_judge": turn.followup_shadow_judge,
                        "referenced_product_ids": turn.referenced_product_ids,
                        "usage_time_focus": turn.usage_time_focus,
                        "intent_reason": turn.intent_reason,
                        "matched_intent_example": turn.matched_intent_example,
                        "intent_vector_score": turn.intent_vector_score,
                        "secondary_intents": turn.secondary_intents,
                    },
                    "scenario_intent": route.answer_mode.value,
                    "v2": True,
                }
            }

            if route.answer_mode in {AnswerMode.RECOMMENDATION, AnswerMode.FOLLOWUP, AnswerMode.JUDGEMENT} or turn.image_context:
                yield {"event": "stage", "data": {"message": "补充商品注意点", "status": "active"}}
                await self._enrich_turn_with_knowledge_pitfalls(turn)

            products = []
            result = None
            products_emitted = False

            if route.answer_mode == AnswerMode.NO_MATCH:
                yield {"event": "stage", "data": {"message": "整理回答", "status": "active"}}
                result = self.presenter.present_no_match(turn)

            elif route.answer_mode == AnswerMode.KNOWLEDGE:
                yield {"event": "stage", "data": {"message": "检索相关信息", "status": "active"}}
                related_products = await self._retrieve_for_knowledge(turn)
                if related_products:
                    yield {"event": "products", "data": {"products": self._serialize_products(related_products)}}
                    products_emitted = True
                result = self.presenter.present_knowledge(turn, related_products=related_products)

            elif route.answer_mode == AnswerMode.COMPARE:
                yield {"event": "stage", "data": {"message": "检索对比商品", "status": "active"}}

                products = []
                targets = list(turn.compare_targets or [])
                anchored_products = []
                if turn.referenced_product_ids:
                    anchored_products = await self._retrieve_products_by_ids_ordered(turn.referenced_product_ids[:4])
                # 追问场景下只有1个新目标时，从历史锚点补第一个对比项
                if len(targets) == 1 and turn.referenced_products:
                    for anchor in turn.referenced_products:
                        if anchor not in targets:
                            targets.insert(0, anchor)
                        if len(targets) >= 4:
                            break
                if not targets and turn.referenced_products:
                    targets = list(turn.referenced_products[:4])
                max_compare = min(max(len(targets), len(anchored_products), 2), 4)
                seen_ids = set()
                for p in anchored_products[:max_compare]:
                    pid = p.get("id") or id(p)
                    if pid not in seen_ids:
                        products.append(p)
                        seen_ids.add(pid)
                if targets:
                    # 每个对比目标取top1，保证"从一堆里挑"时每个目标都有机会出现。
                    for target in targets[:max_compare]:
                        if len(products) >= max_compare:
                            break
                        matches = await self.retriever.retrieve_by_name_fuzzy(target, limit=3)
                        if matches:
                            ranked_matches = self.ranker.rank(matches, turn, top_n=3)
                            for p in ranked_matches:
                                pid = p.get("id") or id(p)
                                if pid not in seen_ids:
                                    products.append(p)
                                    seen_ids.add(pid)
                                    break
                    # 不足2个时尝试更多候选
                    if len(products) < 2:
                        for target in targets[:max_compare]:
                            matches = await self.retriever.retrieve_by_name_fuzzy(target, limit=8)
                            if matches:
                                ranked_matches = self.ranker.rank(matches, turn, top_n=8)
                                for p in ranked_matches:
                                    pid = p.get("id") or id(p)
                                    if pid not in seen_ids:
                                        products.append(p)
                                        seen_ids.add(pid)
                                        break
                            if len(products) >= max_compare:
                                break
                if len(products) < 2:
                    candidates = await self._retrieve_for_turn(turn)
                    extra = self.ranker.rank(candidates, turn, top_n=4)
                    for p in extra:
                        pid = p.get("id") or id(p)
                        if pid not in seen_ids:
                            products.append(p)
                            seen_ids.add(pid)
                            if len(products) >= max_compare:
                                break

                yield {"event": "stage", "data": {"message": "生成对比", "status": "active"}}
                result = self.presenter.present_compare(turn, products[:max_compare])

            elif route.answer_mode == AnswerMode.FOLLOWUP:
                yield {"event": "stage", "data": {"message": "查找相关商品", "status": "active"}}
                candidates = await self._retrieve_for_followup(turn)
                followup_type_value = (
                    turn.followup_type.value
                    if hasattr(turn.followup_type, "value")
                    else str(turn.followup_type or "")
                )
                if turn.followup_scope == FollowupScope.IN_CANDIDATES or followup_type_value == FollowupType.SUITABILITY.value:
                    products = self._rank_in_candidates(candidates, turn)[:4]
                else:
                    products = self.ranker.rank(candidates, turn, top_n=4)
                yield {"event": "stage", "data": {"message": "整理回答", "status": "active"}}
                result = self.presenter.present_followup(turn, products)

            elif route.answer_mode == AnswerMode.JUDGEMENT:
                yield {"event": "stage", "data": {"message": "检索商品信息", "status": "active"}}
                candidates = await self._retrieve_for_turn(turn)
                products = self.ranker.rank(candidates, turn, top_n=3)
                # 如果有单品名线索但没搜到，逐个name_clue fuzzy
                if not products:
                    name_clues = list(getattr(turn, "name_clues", None) or [])
                    if turn.brand:
                        name_clues.insert(0, turn.brand)
                    for clue in name_clues[:3]:
                        try:
                            matches = await self.retriever.retrieve_by_name_fuzzy(clue, limit=5)
                            if matches:
                                products = self.ranker.rank(matches, turn, top_n=3)
                                if products:
                                    break
                        except Exception:
                            pass
                yield {"event": "stage", "data": {"message": "给出使用判断", "status": "active"}}
                result = self.presenter.present_judgement(turn, products)

            else:
                yield {"event": "stage", "data": {"message": "检索商品库", "status": "active"}}
                candidates = await self._retrieve_for_turn(turn)
                products = self.ranker.rank(candidates, turn, top_n=8)

                if not products and turn.category and turn.budget_min is None and turn.budget_max is None:
                    candidates_fallback = await self.retriever.retrieve_products(
                        category=turn.category,
                        limit=20,
                    )
                    products = self.ranker.rank(candidates_fallback, turn, top_n=8)

                yield {"event": "stage", "data": {"message": "整理推荐", "status": "active"}}
                result = self.presenter.present_recommendation(turn, products)

            if result:
                result = self._apply_output_guard(turn, route.answer_mode, result)
                result_products = result.get("products") or []
                record_step(
                    "检索商品与知识库",
                    f"从商品库召回候选，结合知识库规则，共命中{len(result_products)}个候选商品。"
                    + (f" 包含图片识别候选。" if turn.image_context else ""),
                    step_type="retrieve",
                    data={"candidate_count": len(result_products)},
                )
                record_step(
                    "匹配与排序",
                    f"按肤质、预算、诉求做确定性打分排序，筛出Top{min(len(result_products), 4)}给你。",
                    step_type="rank",
                    data={"top_n": min(len(result_products), 4)},
                )
                yield {"event": "stage", "data": {"message": "整理回答", "status": "active"}}
                llm_text = await self._try_generate_llm_text(turn, result)
                if llm_text:
                    result["text"] = llm_text
                    result["generation_source"] = "llm"
                else:
                    result["generation_source"] = "local_fallback"
                # 无论LLM还是本地兜底，都过一遍禁词清洗
                if result.get("text"):
                    result["text"] = self._clean_llm_text(result["text"])
                final_name = None
                final_price = None
                if result_products:
                    first = result_products[0]
                    final_name = (first.get("display_name") or first.get("name") or "")[:30]
                    final_price = self._safe_float(first.get("price") or first.get("price_val"))
                pitfalls_count = len(result.get("pitfalls") or [])
                generation_source = "LLM" if result.get("generation_source") == "llm" else "本地兜底"
                record_step(
                    "生成回答与使用提醒",
                    f"用{generation_source}组织正文内容，并附{pitfalls_count}条使用提醒。",
                    step_type="present",
                    data={"pitfalls": pitfalls_count, "generation": result.get("generation_source")},
                )

                decision_process_payload = {
                    "steps": decision_steps,
                    "duration_ms": None,
                    "final_recommendation": (
                        {"name": final_name, "price": final_price}
                        if final_name else None
                    ),
                }

                answer_contract = self._build_answer_contract(turn, result)
                answer_contract["generation_source"] = result.get("generation_source")
                inline_images = self._build_inline_image_products(result_products)
                has_inline_images = bool(answer_contract.get("primary_product_ids") and inline_images)
                if has_inline_images:
                    answer_contract.setdefault("display_sections", []).append("inline_images")
                answer_contract["display_sections"].append("decision_process")
                answer_contract["inline_images"] = inline_images
                yield {"event": "decision_process", "data": {"decision_process": decision_process_payload}}
                yield {"event": "answer_contract", "data": {"answer_contract": answer_contract}}
                if result.get("products") and not products_emitted:
                    yield {"event": "products", "data": {"products": self._serialize_products(result["products"])}}
                if result.get("comparison"):
                    yield {"event": "comparison", "data": result["comparison"]}
                if result.get("citations"):
                    yield {"event": "citations", "data": {"citations": result["citations"]}}
                if result.get("pitfalls"):
                    yield {"event": "pitfalls", "data": {"pitfalls": result["pitfalls"]}}

                prev_state = self._session_states.get(session_id) or ShoppingSessionState()
                updated_state = apply_answer_contract_to_state(
                    prev_state, result_products, answer_contract
                )
                self._session_states[session_id] = updated_state

                text = result.get("text", "")
                chunks = self._split_into_chunks(text)
                for chunk in chunks:
                    yield {"event": "message", "data": {"content": chunk, "done": False}}

            yield {"event": "end", "data": {}}

        except Exception as e:
            logger.exception(f"V2 agent error: {e}")
            yield {
                "event": "error",
                "data": {"message": f"处理请求时出错：{str(e)}", "code": "AGENT_ERROR"}
            }
            yield {"event": "end", "data": {}}

    def _build_answer_contract(self, turn: CanonicalTurn, result: Dict[str, Any]) -> Dict[str, Any]:
        products = result.get("products") or []
        product_ids = []
        for product in products:
            pid = product.get("id")
            if pid is not None and pid not in product_ids:
                product_ids.append(pid)

        sections = ["answer"]
        if products:
            sections.append("products")
        if result.get("pitfalls"):
            sections.append("pitfalls")
        if result.get("comparison"):
            sections.append("comparison")
        if turn.followup_scope:
            sections.append("followup_state")

        answer_mode = result.get("answer_mode")
        if hasattr(answer_mode, "value"):
            answer_mode = answer_mode.value

        if turn.followup_type == FollowupType.USAGE_TIME:
            order_reason = f"usage_time:{turn.usage_time_focus or 'general'}"
        elif turn.followup_type:
            order_reason = f"followup:{turn.followup_type.value}"
        elif turn.intent:
            order_reason = turn.intent.value
        else:
            order_reason = "default"

        decision_meta = result.get("decision") or {}

        return {
            "answer_mode": answer_mode,
            "intent": turn.intent.value if turn.intent else answer_mode,
            "decision": decision_meta,
            "followup_type": turn.followup_type.value if turn.followup_type else None,
            "followup_scope": turn.followup_scope.value if turn.followup_scope else None,
            "followup_scope_reason": turn.followup_scope_reason,
            "followup_judge_source": turn.followup_judge_source,
            "followup_judge_confidence": turn.followup_judge_confidence,
            "followup_shadow_judge": turn.followup_shadow_judge,
            "usage_time_focus": turn.usage_time_focus,
            "primary_product_ids": product_ids[:8],
            "followup_state": self._build_followup_state_contract(turn, product_ids, decision_meta),
            "display_sections": sections,
            "products_order_reason": order_reason,
            "secondary_intents": turn.secondary_intents,
            "intent_reason": turn.intent_reason,
            "matched_intent_example": turn.matched_intent_example,
            "intent_vector_score": turn.intent_vector_score,
        }

    def _build_followup_state_contract(
        self,
        turn: CanonicalTurn,
        product_ids: List[int],
        decision_meta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        decision_meta = decision_meta or {}
        winner_id = decision_meta.get("winner_product_id")
        current_focus_ids = [winner_id] if winner_id is not None else product_ids[:1]
        return {
            "version": "v2.1",
            "scope": turn.followup_scope.value if turn.followup_scope else None,
            "scope_reason": turn.followup_scope_reason,
            "judge_source": turn.followup_judge_source,
            "judge_confidence": turn.followup_judge_confidence,
            "shadow_judge": turn.followup_shadow_judge,
            "winner_product_id": winner_id,
            "decision_summary": decision_meta.get("summary"),
            "referenced_product_ids": list(turn.referenced_product_ids or []),
            "current_focus_ids": current_focus_ids,
            "last_candidate_ids": product_ids[:4],
            "constraints": {
                "category": turn.category,
                "skin_type": turn.skin_type,
                "budget_min": turn.budget_min,
                "budget_max": turn.budget_max,
                "concerns": list(turn.concerns or []),
                "exclude_terms": list(turn.exclude_terms or []),
            },
        }

    async def _apply_followup_state(self, turn: CanonicalTurn) -> None:
        """Resolve shallow multi-turn state before routing.

        The session state is the source of truth for "第几款/这款/这几款里".
        Text history remains a fallback, but product IDs win whenever present.
        """
        state = turn.session_state or {}
        last_candidates = state.get("last_candidates") or []
        current_focus = state.get("current_focus") or []
        if not last_candidates and not current_focus:
            return

        prev_constraints = dict(state.get("constraints") or {})

        msg = turn.raw_message or ""
        ordinal_indices = self._extract_ordinal_indices(msg)
        has_pronoun = self._has_candidate_pronoun(msg)
        has_collection_ref = self._has_candidate_collection_reference(msg)
        new_exclude_terms = self._extract_exclusion_terms(msg)
        prev_exclude = list(prev_constraints.get("exclude_terms") or [])
        exclude_terms = []
        seen_excl = set()
        for term in (new_exclude_terms + prev_exclude):
            if term and term not in seen_excl:
                exclude_terms.append(term)
                seen_excl.add(term)
        if exclude_terms:
            turn.exclude_terms = exclude_terms

        if not turn.skin_type and prev_constraints.get("skin_type"):
            turn.skin_type = prev_constraints.get("skin_type")
        if not turn.category and prev_constraints.get("category"):
            turn.category = prev_constraints.get("category")

        if not turn.followup_type or turn.followup_type == FollowupType.OTHER:
            detected_type = self.parser._detect_followup_type(msg)
            if detected_type != FollowupType.OTHER:
                turn.followup_type = detected_type

        wants_out = self._looks_like_out_of_candidates(turn, msg)
        wants_budget_shift = turn.followup_type in {FollowupType.CHEAPER, FollowupType.HIGHER_BUDGET}
        has_new_exclusion = bool(new_exclude_terms)
        has_selection_cue = self._has_followup_selection_cue(msg)
        has_new_skin_condition = bool(turn.skin_type and turn.skin_type != prev_constraints.get("skin_type"))
        has_followup_dimension = self._has_followup_dimension_cue(msg)
        has_new_topic_signal = self._has_new_topic_signal(turn, prev_constraints)
        is_short_contextual = len(msg.strip()) <= 15 and not has_new_topic_signal

        scope = None
        reason = ""
        ref_ids: List[int] = []
        ref_names: List[str] = []

        if ordinal_indices:
            for index in ordinal_indices:
                if 0 <= index < len(last_candidates):
                    product = last_candidates[index]
                    pid = self._coerce_product_id(product)
                    if pid is not None and pid not in ref_ids:
                        ref_ids.append(pid)
                        ref_names.append(self._state_product_label(product))
            scope = FollowupScope.IN_CANDIDATES if ref_ids else FollowupScope.AMBIGUOUS
            reason = "ordinal_reference" if ref_ids else "ordinal_out_of_range"
        elif has_pronoun:
            anchor_products = current_focus or last_candidates[:1]
            for product in anchor_products[:1]:
                pid = self._coerce_product_id(product)
                if pid is not None:
                    ref_ids.append(pid)
                    ref_names.append(self._state_product_label(product))
            scope = FollowupScope.IN_CANDIDATES if ref_ids else FollowupScope.AMBIGUOUS
            reason = "pronoun_reference" if ref_ids else "pronoun_without_focus"
        elif has_new_exclusion:
            scope = FollowupScope.OUT_OF_CANDIDATES
            reason = "exclusion_terms"
        elif wants_budget_shift:
            scope = FollowupScope.OUT_OF_CANDIDATES
            reason = "budget_shift"
        elif wants_out:
            scope = FollowupScope.OUT_OF_CANDIDATES
            reason = "condition_switch_or_more_options"
        elif has_collection_ref:
            ref_ids = self._state_product_ids(last_candidates)
            ref_names = [self._state_product_label(p) for p in last_candidates[:len(ref_ids)]]
            scope = FollowupScope.IN_CANDIDATES if ref_ids else FollowupScope.AMBIGUOUS
            reason = "candidate_collection_reference"
        elif has_selection_cue and has_new_skin_condition:
            scope = FollowupScope.OUT_OF_CANDIDATES
            reason = "new_condition_selection"
        elif has_selection_cue:
            if has_new_topic_signal:
                pass
            else:
                ref_ids = self._state_product_ids(last_candidates)
                ref_names = [self._state_product_label(p) for p in last_candidates[:len(ref_ids)]]
                scope = FollowupScope.IN_CANDIDATES if ref_ids else FollowupScope.AMBIGUOUS
                reason = "selection_cue_within_candidates"
        elif has_followup_dimension and last_candidates and (is_short_contextual or has_pronoun or has_collection_ref) and not has_new_topic_signal:
            anchor = current_focus or last_candidates[:1]
            ref_ids = self._state_product_ids(anchor)
            ref_names = [self._state_product_label(p) for p in anchor[:len(ref_ids)]]
            scope = FollowupScope.IN_CANDIDATES if ref_ids else FollowupScope.AMBIGUOUS
            reason = "dimension_followup_on_candidates"
        elif turn.is_followup:
            scope = FollowupScope.AMBIGUOUS
            reason = "contextual_followup_without_anchor"

        llm_decision = await self._maybe_llm_followup_scope(turn, scope, reason, last_candidates, current_focus)
        used_llm_decision = False
        if llm_decision:
            decided_scope, decided_reason, decided_confidence, decided_ids = llm_decision
            scope = decided_scope
            reason = decided_reason
            turn.followup_judge_source = "llm"
            turn.followup_judge_confidence = decided_confidence
            used_llm_decision = True
            if decided_ids:
                ref_ids = decided_ids
                ref_names = [
                    self._state_product_label(p)
                    for p in (last_candidates or current_focus)
                    if self._coerce_product_id(p) in set(decided_ids)
                ]
        elif scope is not None:
            turn.followup_judge_source = "rule"
            turn.followup_judge_confidence = self._rule_followup_confidence(scope, reason)

        if (
            not used_llm_decision
            and not getattr(settings, "V2_FOLLOWUP_LLM_JUDGE_ENABLED", False)
            and getattr(settings, "V2_FOLLOWUP_LLM_JUDGE_SHADOW_ENABLED", False)
        ):
            shadow_decision = await self._maybe_llm_followup_scope(
                turn,
                scope,
                reason,
                last_candidates,
                current_focus,
                shadow_mode=True,
            )
            if shadow_decision:
                shadow_scope, shadow_reason, shadow_confidence, shadow_ids = shadow_decision
                turn.followup_shadow_judge = {
                    "scope": shadow_scope.value,
                    "reason": shadow_reason,
                    "confidence": shadow_confidence,
                    "referenced_product_ids": shadow_ids,
                    "rule_scope": scope.value if scope else None,
                    "rule_reason": reason,
                    "applied": False,
                }
                logger.info(
                    "V2 followup LLM shadow judge: rule=%s/%s llm=%s conf=%.2f ids=%s",
                    scope.value if scope else None,
                    reason,
                    shadow_scope.value,
                    shadow_confidence,
                    shadow_ids,
                )

        if scope is None:
            return

        turn.followup_scope = scope
        turn.followup_scope_reason = reason
        if ref_ids:
            turn.referenced_product_ids = ref_ids
            turn.referenced_products = [name for name in ref_names if name]
        if scope == FollowupScope.IN_CANDIDATES and not ref_ids and last_candidates:
            turn.referenced_product_ids = self._state_product_ids(last_candidates)

        if scope == FollowupScope.OUT_OF_CANDIDATES:
            turn.referenced_product_ids = []
            turn.referenced_products = []
            if not turn.category:
                turn.category = self._infer_single_candidate_category(last_candidates)
            self._apply_budget_shift_from_state(turn, last_candidates, current_focus)

        # Detect "anchor + new target" compare (e.g. "这个和紫米呢"):
        # pronoun/ordinal anchor + connector + new product clue → supplement compare_targets
        # so router's _is_explicit_compare (>=2 targets) fires instead of generic followup.
        if scope == FollowupScope.IN_CANDIDATES and ref_names and len(turn.compare_targets) < 2:
            compare_cues = ["和", "与", "跟", "还是", "vs", "VS", "比", "对比", "比较",
                            "哪个", "哪款", "谁更", "怎么选", "区别", "更好", "更适合"]
            has_compare_cue = any(c in msg for c in compare_cues)
            new_clues = []
            for nc in (list(turn.name_clues or []) + list(turn.compare_targets or [])):
                nc_s = str(nc).strip()
                if nc_s and nc_s not in ref_names and nc_s not in new_clues:
                    new_clues.append(nc_s)
            if turn.brand:
                b = str(turn.brand).strip()
                if b and b not in ref_names and b not in new_clues:
                    new_clues.append(b)
            if has_compare_cue and new_clues:
                merged = []
                seen_t = set()
                for t in (ref_names[:1] + new_clues + list(turn.compare_targets or [])):
                    t_s = str(t).strip()
                    if t_s and t_s not in seen_t:
                        merged.append(t_s)
                        seen_t.add(t_s)
                    if len(merged) >= 4:
                        break
                if len(merged) >= 2:
                    turn.compare_targets = merged

        # Any scoped state means this is part of the dialogue, even if the first
        # classifier called it knowledge because the wording was "这玩意有什么用".
        turn.is_followup = True
        if len(turn.compare_targets) < 2 and turn.intent in {AnswerMode.KNOWLEDGE, AnswerMode.RECOMMENDATION, AnswerMode.NO_MATCH}:
            turn.intent = AnswerMode.FOLLOWUP
            turn.intent_reason = f"stateful_followup:{reason}"
            turn.intent_confidence = max(turn.intent_confidence or 0.0, 0.88)
        if not turn.followup_type or turn.followup_type == FollowupType.OTHER:
            turn.followup_type = self.parser._detect_followup_type(msg)
        if exclude_terms and turn.followup_type in {None, FollowupType.OTHER, FollowupType.INGREDIENT}:
            turn.followup_type = FollowupType.SUITABILITY

    @staticmethod
    def _extract_ordinal_indices(text: str) -> List[int]:
        mapping = {
            "一": 0, "二": 1, "两": 1, "三": 2, "四": 3, "五": 4, "六": 5,
            "七": 6, "八": 7, "九": 8,
            "1": 0, "2": 1, "3": 2, "4": 3, "5": 4, "6": 5, "7": 6, "8": 7, "9": 8,
        }
        indices = []
        for match in re.finditer(r"第\s*([一二两三四五六七八九1-9])\s*(?:款|个|支|瓶|种)?", text or ""):
            index = mapping.get(match.group(1))
            if index is not None and index not in indices:
                indices.append(index)
        return indices

    @staticmethod
    def _has_candidate_pronoun(text: str) -> bool:
        return any(cue in (text or "") for cue in [
            "这款", "那款", "这个", "那个", "它", "它们", "这个产品", "那个产品",
            "这玩意", "这东西", "这个东西", "刚才那个", "你说的那个",
            "刚刚推荐的", "你推荐的", "你刚推荐的", "上面那款", "上面那个",
        ])

    @staticmethod
    def _has_candidate_collection_reference(text: str) -> bool:
        return any(cue in (text or "") for cue in [
            "这几款", "这几个", "这三款", "这些", "这里面", "这几款里",
            "这几个里面", "里面哪个", "里面哪款", "上面这些", "其中",
            "上面", "刚才", "刚刚", "你推荐的",
            "除了", "除此之外", "上面这些以外", "这几款以外", "这几个以外",
        ])

    @staticmethod
    def _has_followup_selection_cue(text: str) -> bool:
        return any(cue in (text or "") for cue in [
            "哪个", "哪款", "哪一个", "哪一款", "怎么选", "选哪个", "选哪款",
            "更合适", "更适合", "更稳", "更好", "更好的", "更推荐", "优先",
            "谁更", "选哪", "拿哪",
        ])

    @staticmethod
    def _has_followup_dimension_cue(text: str) -> bool:
        msg = text or ""
        cues = [
            "适合", "能用吗", "可以用吗", "能不能用", "友好吗",
            "白天", "晚上", "夜间", "夜里", "晚间", "睡前", "早上", "日间",
            "怎么用", "用法", "使用方法", "什么时候用",
            "成分", "含什么", "配方", "刺激", "酒精", "香精",
            "功效", "作用", "效果", "主打",
            "多少钱", "价格", "价位", "贵不贵",
            "怎么样", "好不好", "好用吗", "值得买吗", "靠谱吗",
        ]
        return any(cue in msg for cue in cues)

    @staticmethod
    def _has_new_topic_signal(turn: CanonicalTurn, prev_constraints: Dict[str, Any]) -> bool:
        new_brand = bool(turn.brand and turn.brand != (prev_constraints.get("brand") or ""))
        new_category = bool(turn.category and turn.category != (prev_constraints.get("category") or ""))
        new_name_clues = bool(turn.name_clues)
        return new_brand or new_category or new_name_clues

    def _looks_like_out_of_candidates(self, turn: CanonicalTurn, text: str) -> bool:
        msg = text or ""
        if any(cue in msg for cue in [
            "换一批", "再来几款", "还有别的", "还有没有别的", "还有没有其他",
            "还有其他", "别的选择", "重新推荐", "换个", "换成", "这几款以外",
            "上面这些以外", "除了上面这些", "排除刚才", "不看刚才", "不要上面这些",
            "除此之外",
        ]):
            return True
        if self._has_candidate_pronoun(msg) or self._extract_ordinal_indices(msg):
            return False
        condition_switch_skin = [
            "油皮", "干皮", "敏感肌", "敏皮", "油痘肌", "混油", "混干", "中性皮",
        ]
        if msg.startswith(("那", "那么", "如果是")) and "呢" in msg and any(skin in msg for skin in condition_switch_skin):
            return True
        if re.search(r"^(那|那么|如果是)?\s*[^，。？！]{0,8}(预算|价格|价位|功效|诉求).*呢[？?]?$", msg):
            return bool(turn.skin_type or turn.category or turn.concerns or turn.budget_min is not None or turn.budget_max is not None)
        return False

    @staticmethod
    def _extract_exclusion_terms(text: str) -> List[str]:
        msg = text or ""
        if not any(cue in msg for cue in ["不要", "不想要", "别有", "别太", "不要太", "避开", "不含", "无", "没有"]):
            return []
        terms = []
        for term in ["酒精", "香精", "酸", "A醇", "视黄醇", "烟酰胺", "厚重", "油腻", "闷痘", "搓泥", "拔干", "假白"]:
            if term in msg and term not in terms:
                terms.append(term)
        return terms

    @staticmethod
    def _state_prices(products: List[Dict[str, Any]]) -> List[float]:
        prices = []
        for product in products or []:
            raw = product.get("price") or product.get("price_val")
            try:
                price = float(raw)
            except (TypeError, ValueError):
                continue
            if price > 0:
                prices.append(price)
        return prices

    def _apply_budget_shift_from_state(
        self,
        turn: CanonicalTurn,
        last_candidates: List[Dict[str, Any]],
        current_focus: List[Dict[str, Any]],
    ) -> None:
        prices = self._state_prices(current_focus) or self._state_prices(last_candidates)
        if not prices:
            return
        secondary_types = {
            item.get("followup_type")
            for item in (turn.secondary_intents or [])
            if isinstance(item, dict)
        }
        wants_cheaper = (
            turn.followup_type == FollowupType.CHEAPER
            or FollowupType.CHEAPER.value in secondary_types
        )
        wants_higher_budget = (
            turn.followup_type == FollowupType.HIGHER_BUDGET
            or FollowupType.HIGHER_BUDGET.value in secondary_types
        )
        constraints = (turn.session_state or {}).get("constraints") or {}
        previous_budget_max = self._safe_float(constraints.get("budget_max"))
        previous_budget_min = self._safe_float(constraints.get("budget_min"))

        if wants_cheaper and turn.budget_max is None:
            list_prices = self._state_prices(last_candidates)
            last_mode = str((turn.session_state or {}).get("last_answer_mode") or "")
            if last_mode in {"recommendation", "compare"} and len(list_prices) >= 2:
                ceiling = max(list_prices) * 0.95
            else:
                ceiling = min(prices) * 0.95
            if previous_budget_max is not None:
                ceiling = min(ceiling, previous_budget_max)
            turn.budget_max = max(1.0, ceiling)
            turn.budget_min = None
            turn.budget_flexible = True
        elif wants_higher_budget and turn.budget_min is None:
            floor = max(prices)
            if previous_budget_max is not None:
                floor = max(floor, previous_budget_max)
            elif previous_budget_min is not None:
                floor = max(floor, previous_budget_min)
            turn.budget_min = floor * 1.01
            turn.budget_max = None
            turn.budget_flexible = True

    @staticmethod
    def _rule_followup_confidence(scope: FollowupScope, reason: str) -> float:
        if reason in {"ordinal_reference", "pronoun_reference"}:
            return 0.96
        if reason in {"exclusion_terms", "budget_shift", "condition_switch_or_more_options"}:
            return 0.9
        if reason == "candidate_collection_reference":
            return 0.88
        if scope == FollowupScope.AMBIGUOUS:
            return 0.45
        return 0.8

    async def _maybe_llm_followup_scope(
        self,
        turn: CanonicalTurn,
        rule_scope: Optional[FollowupScope],
        rule_reason: str,
        last_candidates: List[Dict[str, Any]],
        current_focus: List[Dict[str, Any]],
        shadow_mode: bool = False,
    ) -> Optional[Tuple[FollowupScope, str, float, List[int]]]:
        """Optional LLM judge for ambiguous followup scope.

        It only judges the scope. Product retrieval and ranking stay local.
        """
        enabled_flag = (
            "V2_FOLLOWUP_LLM_JUDGE_SHADOW_ENABLED"
            if shadow_mode
            else "V2_FOLLOWUP_LLM_JUDGE_ENABLED"
        )
        if not getattr(settings, enabled_flag, False):
            return None
        if rule_scope not in {None, FollowupScope.AMBIGUOUS}:
            return None
        if not (last_candidates or current_focus):
            return None

        def compact_product(product: Dict[str, Any]) -> Dict[str, Any]:
            return {
                "id": self._coerce_product_id(product),
                "name": product.get("display_name") or product.get("name"),
                "brand": product.get("brand"),
                "category": product.get("category"),
                "price": product.get("price") or product.get("price_val"),
            }

        valid_ids = {
            pid for pid in (self._coerce_product_id(p) for p in (last_candidates or current_focus))
            if pid is not None
        }
        payload = {
            "user_message": turn.raw_message,
            "rule_scope": rule_scope.value if rule_scope else None,
            "rule_reason": rule_reason,
            "last_candidates": [compact_product(p) for p in (last_candidates or [])[:4]],
            "current_focus": [compact_product(p) for p in (current_focus or [])[:2]],
            "slots": {
                "category": turn.category,
                "skin_type": turn.skin_type,
                "budget_min": turn.budget_min,
                "budget_max": turn.budget_max,
                "exclude_terms": turn.exclude_terms,
                "followup_type": turn.followup_type.value if turn.followup_type else None,
            },
        }
        messages = [
            {
                "role": "system",
                "content": (
                    "你是导购系统的追问范围裁判。只输出JSON，不要解释。"
                    "scope只能是in_candidates、out_of_candidates、ambiguous。"
                    "如果用户问第几款/这款/它，通常是in_candidates；"
                    "如果用户换肤质、预算、排除成分、换一批，通常是out_of_candidates。"
                    "referenced_product_ids只能从给定商品id里选。"
                ),
            },
            {
                "role": "user",
                "content": (
                    "请判断这句追问的范围，输出形如："
                    '{"scope":"in_candidates","confidence":0.8,"reason":"...","referenced_product_ids":[1]}'
                    "\n输入：\n"
                    + json.dumps(payload, ensure_ascii=False)
                ),
            },
        ]
        try:
            from app.services.llm import get_llm_service

            llm_service = get_llm_service()
            raw = await llm_service.chat(
                messages,
                model=getattr(settings, "V2_FOLLOWUP_LLM_JUDGE_MODEL", None),
                temperature=0,
                max_tokens=int(getattr(settings, "V2_FOLLOWUP_LLM_JUDGE_MAX_TOKENS", 160)),
                provider=getattr(settings, "V2_FOLLOWUP_LLM_JUDGE_PROVIDER", None),
                use_cache=False,
            )
            data = self._extract_json_object(raw)
        except Exception as exc:
            logger.warning("V2 followup LLM %s judge skipped: %s", "shadow" if shadow_mode else "active", exc)
            return None

        if not isinstance(data, dict):
            return None
        scope_raw = str(data.get("scope") or "").strip()
        scope = {
            FollowupScope.IN_CANDIDATES.value: FollowupScope.IN_CANDIDATES,
            FollowupScope.OUT_OF_CANDIDATES.value: FollowupScope.OUT_OF_CANDIDATES,
            FollowupScope.AMBIGUOUS.value: FollowupScope.AMBIGUOUS,
        }.get(scope_raw)
        if scope is None:
            return None
        try:
            confidence = float(data.get("confidence", 0.6))
        except (TypeError, ValueError):
            confidence = 0.6
        confidence = max(0.0, min(confidence, 1.0))
        reason = "llm_judge:" + str(data.get("reason") or scope.value)[:80]
        ids = []
        for raw_id in data.get("referenced_product_ids") or []:
            try:
                pid = int(raw_id)
            except (TypeError, ValueError):
                continue
            if pid in valid_ids and pid not in ids:
                ids.append(pid)
        return scope, reason, confidence, ids

    @staticmethod
    def _extract_json_object(text: str) -> Optional[Dict[str, Any]]:
        if not text:
            return None
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            data = json.loads(text[start:end + 1])
        except Exception:
            return None
        return data if isinstance(data, dict) else None

    @staticmethod
    def _coerce_product_id(product: Dict[str, Any]) -> Optional[int]:
        raw = product.get("id") or product.get("product_id")
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    @classmethod
    def _state_product_ids(cls, products: List[Dict[str, Any]]) -> List[int]:
        ids = []
        for product in products or []:
            pid = cls._coerce_product_id(product)
            if pid is not None and pid not in ids:
                ids.append(pid)
        return ids

    @staticmethod
    def _state_product_label(product: Dict[str, Any]) -> str:
        return str(product.get("display_name") or product.get("name") or product.get("brand") or "").strip()

    @classmethod
    def _infer_single_candidate_category(cls, products: List[Dict[str, Any]]) -> Optional[str]:
        categories = []
        for product in products or []:
            category = product.get("category")
            if category and category not in categories:
                categories.append(category)
        return categories[0] if len(categories) == 1 else None

    async def _retrieve_products_by_ids_ordered(self, product_ids: List[int]) -> List[Dict[str, Any]]:
        ids = []
        for pid in product_ids or []:
            try:
                int_pid = int(pid)
            except (TypeError, ValueError):
                continue
            if int_pid not in ids:
                ids.append(int_pid)
        if not ids:
            return []
        products = await self.retriever.retrieve_by_ids(ids)
        by_id = {}
        for product in products:
            pid = self._coerce_product_id(product)
            if pid is not None:
                by_id[pid] = product
        return [by_id[pid] for pid in ids if pid in by_id]

    async def _retrieve_for_turn(self, turn: CanonicalTurn) -> List[Dict[str, Any]]:
        image_product_ids = []
        if turn.image_context and turn.image_context.get("product_ids"):
            image_product_ids = turn.image_context["product_ids"]

        candidates = []
        seen_ids = set()

        def add_many(items: List[Dict[str, Any]]) -> None:
            for product in items or []:
                pid = product.get("id")
                key = pid if pid is not None else id(product)
                if key in seen_ids:
                    continue
                seen_ids.add(key)
                candidates.append(product)

        image_alternative_request = (
            bool(turn.image_context)
            and turn.followup_type in {FollowupType.CHEAPER, FollowupType.HIGHER_BUDGET, FollowupType.MORE_OPTIONS}
        )
        if image_alternative_request and image_product_ids:
            anchor_products = await self.retriever.retrieve_by_ids(image_product_ids[:1])
            anchor = anchor_products[0] if anchor_products else None
            category = turn.category or (anchor.get("category") if anchor else None)
            anchor_price = self._safe_float(anchor.get("price") if anchor else None)
            budget_max = turn.budget_max
            budget_min = turn.budget_min
            if turn.followup_type == FollowupType.CHEAPER and budget_max is None and anchor_price:
                budget_max = anchor_price * 0.9
            elif turn.followup_type == FollowupType.HIGHER_BUDGET and budget_min is None and anchor_price:
                budget_min = anchor_price * 1.05
            alternatives = await self.retriever.retrieve_products(
                category=category,
                skin_type=turn.skin_type,
                budget_min=budget_min,
                budget_max=budget_max,
                limit=20,
            )
            excluded = {int(pid) for pid in image_product_ids if pid is not None}
            add_many([p for p in alternatives if self._coerce_product_id(p) not in excluded])
            if candidates:
                return candidates

        if image_product_ids:
            add_many(await self.retriever.retrieve_by_ids(image_product_ids))

        # 单品名线索优先：当用户明确提了某款产品昵称/别名（绿宝瓶、小棕瓶、珂润面霜等），
        # 先按名字精确找单品，避免被concerns/预算过滤掉
        name_clues = list(getattr(turn, "name_clues", None) or [])
        if turn.brand:
            name_clues.insert(0, turn.brand)
        if name_clues:
            for clue in name_clues[:2]:
                try:
                    matches = await self.retriever.retrieve_by_name_fuzzy(clue, limit=5)
                    if matches:
                        add_many(matches)
                        break
                except Exception:
                    pass

        db_products = await self.retriever.retrieve_products(
            category=turn.category,
            brand=turn.brand,
            concerns=turn.concerns if turn.concerns else None,
            skin_type=turn.skin_type,
            budget_min=turn.budget_min,
            budget_max=turn.budget_max,
            limit=20,
        )
        if not db_products and turn.category and turn.concerns and (turn.budget_min is not None or turn.budget_max is not None):
            db_products = await self.retriever.retrieve_products(
                category=turn.category,
                brand=turn.brand,
                budget_min=turn.budget_min,
                budget_max=turn.budget_max,
                limit=20,
            )
        elif len(db_products) < 3 and turn.category and turn.concerns and (turn.budget_min is not None or turn.budget_max is not None):
            supplements = await self.retriever.retrieve_products(
                category=turn.category,
                brand=turn.brand,
                budget_min=turn.budget_min,
                budget_max=turn.budget_max,
                limit=20,
            )
            add_many(supplements)
        if not db_products and turn.category and turn.budget_min is not None and turn.budget_max is not None:
            mid_budget = (turn.budget_min + turn.budget_max) / 2
            db_products = await self.retriever.retrieve_products(
                category=turn.category,
                brand=turn.brand,
                budget_min=mid_budget * 0.65,
                budget_max=mid_budget * 1.35,
                limit=20,
            )
        # 如果按concerns过滤为空且没有单品名线索，放宽到只按category/brand
        if not candidates and not db_products and not name_clues:
            db_products = await self.retriever.retrieve_products(
                category=turn.category,
                brand=turn.brand,
                skin_type=turn.skin_type,
                limit=20,
            )
        add_many(db_products)

        return candidates

    async def _retrieve_for_knowledge(self, turn: CanonicalTurn) -> List[Dict[str, Any]]:
        if self._is_general_knowledge_without_product_context(turn.raw_message):
            return []

        candidates: List[Dict[str, Any]] = []
        seen_ids = set()

        def add_many(items: List[Dict[str, Any]]) -> None:
            for product in items or []:
                pid = product.get("id")
                key = pid if pid is not None else id(product)
                if key in seen_ids:
                    continue
                seen_ids.add(key)
                candidates.append(product)

        terms = self._extract_knowledge_query_terms(turn.raw_message)
        if terms and hasattr(self.retriever, "retrieve_by_text_terms"):
            add_many(await self.retriever.retrieve_by_text_terms(terms, limit=8))

        if turn.category or turn.brand:
            add_many(await self.retriever.retrieve_products(
                category=turn.category,
                brand=turn.brand,
                limit=5,
            ))

        if turn.referenced_products:
            for ref in turn.referenced_products[:6]:
                add_many(await self.retriever.retrieve_products(
                    category=turn.category,
                    brand=ref,
                    limit=3,
                ))

        if not candidates:
            return []

        ranked = self.ranker.rank(candidates, turn, top_n=8)
        return self._select_best_knowledge_products(ranked, turn.raw_message)

    @staticmethod
    def _is_general_knowledge_without_product_context(query: str) -> bool:
        text = str(query or "")
        if any(term in text for term in ["早C晚A", "早c晚a", "早C", "晚A"]):
            return True
        if any(term in text for term in ["是什么", "什么是", "有什么用", "有什么作用", "什么作用", "什么意思", "科普", "解释"]):
            if any(term in text for term in ["玻色因", "A醇", "视黄醇", "视黄醛", "烟酰胺", "酸类", "水杨酸", "果酸", "维C", "早C晚A", "神经酰胺", "二裂酵母"]):
                return True
        if any(term in text for term in ["玻色因", "A醇", "视黄醇", "视黄醛", "烟酰胺", "酸类", "水杨酸", "果酸", "维C"]) and any(
            cue in text for cue in ["区别", "差异", "一起用", "同时用", "混用", "搭配", "白天", "晚上", "能用吗", "区别", "哪个好"]
        ):
            return True
        return False

    async def _retrieve_for_followup(self, turn: CanonicalTurn) -> List[Dict[str, Any]]:
        if turn.followup_scope == FollowupScope.IN_CANDIDATES:
            product_ids = list(turn.referenced_product_ids or [])
            if not product_ids:
                product_ids = self._state_product_ids((turn.session_state or {}).get("last_candidates") or [])
            scoped_products = await self._retrieve_products_by_ids_ordered(product_ids[:4])
            if scoped_products:
                return self._rank_in_candidates(scoped_products, turn)

        if (
            turn.followup_scope == FollowupScope.OUT_OF_CANDIDATES
            and turn.followup_type not in {FollowupType.MORE_OPTIONS, FollowupType.CHEAPER, FollowupType.HIGHER_BUDGET}
        ):
            scoped = await self.retriever.retrieve_products(
                category=turn.category,
                brand=turn.brand,
                concerns=turn.concerns if turn.concerns else None,
                skin_type=turn.skin_type,
                budget_min=turn.budget_min,
                budget_max=turn.budget_max,
                limit=20,
            )
            scoped = self._filter_excluded_products(scoped, turn.exclude_terms)
            if scoped:
                if turn.followup_type == FollowupType.SUITABILITY:
                    return self._rank_in_candidates(scoped, turn)
                return scoped

        if turn.followup_type == FollowupType.MORE_OPTIONS:
            secondary_types = {
                item.get("followup_type")
                for item in (turn.secondary_intents or [])
                if isinstance(item, dict)
            }
            candidates = await self.retriever.retrieve_products(
                category=turn.category,
                skin_type=turn.skin_type,
                budget_min=turn.budget_min if FollowupType.HIGHER_BUDGET.value in secondary_types else None,
                budget_max=turn.budget_max if FollowupType.CHEAPER.value in secondary_types else None,
                limit=30,
            )
            filtered = [p for p in candidates if not self._seen_in_history(p, turn.conversation_history)]
            filtered = self._filter_excluded_products(filtered, turn.exclude_terms)
            if filtered:
                return filtered
            fallback = await self.retriever.retrieve_products(
                category=turn.category,
                skin_type=turn.skin_type,
                limit=30,
            )
            fallback_filtered = [p for p in fallback if not self._seen_in_history(p, turn.conversation_history)]
            fallback_filtered = self._filter_excluded_products(fallback_filtered, turn.exclude_terms)
            return fallback_filtered or self._filter_excluded_products(fallback, turn.exclude_terms) or fallback

        if turn.followup_type == FollowupType.HIGHER_BUDGET:
            candidates = await self.retriever.retrieve_products(
                category=turn.category,
                skin_type=turn.skin_type,
                budget_min=turn.budget_min,
                limit=20,
            )
            filtered = [
                p for p in candidates
                if not self._seen_in_history(p, turn.conversation_history)
                and (
                    turn.budget_min is None
                    or float(p.get("price_val") or p.get("price") or 0) >= float(turn.budget_min)
                )
            ]
            filtered = self._filter_excluded_products(filtered, turn.exclude_terms)
            return filtered

        if turn.followup_type == FollowupType.CHEAPER:
            candidates = await self.retriever.retrieve_products(
                category=turn.category,
                skin_type=turn.skin_type,
                budget_max=turn.budget_max,
                limit=20,
            )
            filtered = [
                p for p in candidates
                if not self._seen_in_history(p, turn.conversation_history)
                and (
                    turn.budget_max is None
                    or float(p.get("price_val") or p.get("price") or 0) <= float(turn.budget_max)
                )
            ]
            filtered = self._filter_excluded_products(filtered, turn.exclude_terms)
            if filtered:
                return filtered
            fallback = await self.retriever.retrieve_products(
                category=turn.category,
                skin_type=turn.skin_type,
                limit=20,
            )
            fallback = self._filter_excluded_products(fallback, turn.exclude_terms)
            if fallback:
                unseen = [p for p in fallback if not self._seen_in_history(p, turn.conversation_history)]
                affordable_pool = unseen or fallback
                return sorted(
                    affordable_pool,
                    key=lambda p: float(p.get("price_val") or p.get("price") or 999999),
                )

        if turn.image_context and turn.followup_type == FollowupType.CHEAPER:
            image_ids = turn.image_context.get("product_ids") or []
            anchor_products = await self.retriever.retrieve_by_ids(image_ids[:1]) if image_ids else []
            anchor = anchor_products[0] if anchor_products else None
            category = turn.category or (anchor.get("category") if anchor else None)
            anchor_price = float(anchor.get("price") or 0) if anchor else 0
            budget_candidates = []
            if turn.budget_max is not None:
                budget_candidates.append(turn.budget_max)
            if anchor_price > 0:
                budget_candidates.append(anchor_price * 0.85)
            budget_max = min(budget_candidates) if budget_candidates else None
            candidates = await self.retriever.retrieve_products(
                category=category,
                budget_max=budget_max,
                limit=20,
            )
            excluded = set(image_ids)
            filtered = [p for p in candidates if p.get("id") not in excluded]
            filtered = self._filter_excluded_products(filtered, turn.exclude_terms)
            if filtered:
                return filtered

        if turn.referenced_products:
            collected = []
            seen_ids = set()
            # 指代锚点(如"第N款"/点名品牌)优先于预算：用户点名的那款即便超预算也要返回，
            # 不能因预算过滤空了就 fall through 到通用检索，换成一个便宜的无关商品。
            is_ordinal_anchor = bool(re.search(r"第\s*[一二三四五六1-6]\s*(?:款|个|支|瓶|种)", turn.raw_message or ""))
            for brand in turn.referenced_products[:4]:
                matches = []
                if turn.category:
                    matches = await self.retriever.retrieve_products(
                        category=turn.category,
                        brand=brand,
                        budget_min=None if is_ordinal_anchor else turn.budget_min,
                        budget_max=None if is_ordinal_anchor else turn.budget_max,
                        limit=3,
                    )
                if not matches:
                    matches = await self.retriever.retrieve_by_name_fuzzy(brand, limit=3)
                for p in matches:
                    if p["id"] not in seen_ids:
                        collected.append(p)
                        seen_ids.add(p["id"])
            if collected:
                return self._rank_in_candidates(self._filter_excluded_products(collected, turn.exclude_terms), turn) or collected

        if turn.brand:
            matches = await self.retriever.retrieve_by_name_fuzzy(turn.brand, limit=5)
            if matches:
                return self._filter_excluded_products(matches, turn.exclude_terms) or matches

        if turn.category:
            products = await self.retriever.retrieve_products(
                category=turn.category,
                budget_max=turn.budget_max,
                limit=10,
            )
            return self._filter_excluded_products(products, turn.exclude_terms) or products

        products = await self.retriever.retrieve_products(limit=10)
        return self._filter_excluded_products(products, turn.exclude_terms) or products

    def _rank_in_candidates(self, products: List[Dict[str, Any]], turn: CanonicalTurn) -> List[Dict[str, Any]]:
        if not products or len(products) <= 1:
            return products
        scored = []
        for index, product in enumerate(products):
            score = self._score_in_candidate(product, turn)
            scored.append((score, -index, product))
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        ranked = [product for _, _, product in scored]
        for idx, product in enumerate(ranked):
            product["_in_candidate_score"] = scored[idx][0]
        return ranked

    def _score_in_candidate(self, product: Dict[str, Any], turn: CanonicalTurn) -> float:
        text = self._product_context_text(product)
        msg = turn.raw_message or ""
        score = 0.0

        targets = []
        if turn.skin_type:
            targets.append(turn.skin_type)
        for skin in ["油痘肌", "油敏肌", "干敏肌", "敏感肌", "油皮", "干皮", "混油", "混干", "痘肌"]:
            if skin in msg and skin not in targets:
                targets.append(skin)

        for target in targets:
            if target in text:
                score += 18
            if target in {"油皮", "混油", "油痘肌"}:
                if any(w in text for w in ["控油", "清爽", "哑光", "不闷", "油皮", "混油", "持妆", "水感"]):
                    score += 16
                if any(w in text for w in ["厚重", "滋润", "乳木果", "偏润", "油皮慎", "闷痘"]):
                    score -= 14
            if target in {"敏感肌", "干敏肌", "油敏肌"}:
                if any(w in text for w in ["敏感肌", "温和", "低刺激", "舒缓", "屏障", "无酒精", "无香精"]):
                    score += 18
                if any(w in text for w in ["PBS", "仿生脂质"]):
                    score += 48
                elif any(w in text for w in ["屏障修护", "神经酰胺"]):
                    score += 8
                if any(w in text for w in ["急救", "医美术后", "刷酸"]) and not any(w in msg for w in ["急救", "医美", "刷酸", "泛红刺痛"]):
                    score -= 10
                if any(w in text for w in ["酒精", "香精", "酸", "A醇", "刺痛"]):
                    score -= 12
            if target in {"干皮", "混干", "干敏肌"}:
                if any(w in text for w in ["保湿", "滋润", "锁水", "角鲨烷", "乳木果", "干皮"]):
                    score += 16
                if any(w in text for w in ["控油", "拔干", "哑光", "强持妆"]):
                    score -= 10

        for concern in turn.concerns or []:
            if concern and concern in text:
                score += 10
        if any(w in msg for w in ["控油", "持妆", "遮瑕", "修护", "舒缓", "保湿", "提亮", "抗老"]):
            for word in ["控油", "持妆", "遮瑕", "修护", "舒缓", "保湿", "提亮", "抗老"]:
                if word in msg and word in text:
                    score += 8
        for term in turn.exclude_terms or []:
            if self._product_violates_exclusion(product, term):
                score -= 50

        price = self._safe_float(product.get("price_val") or product.get("price")) or 0
        if turn.followup_type == FollowupType.CHEAPER and price:
            score += max(0, 20 - price / 20)
        elif turn.followup_type == FollowupType.HIGHER_BUDGET and price:
            score += min(price / 50, 12)
        return score

    @staticmethod
    def _product_context_text(product: Dict[str, Any]) -> str:
        values = [
            product.get("name") or "",
            product.get("display_name") or "",
            product.get("brand") or "",
            product.get("category") or "",
            product.get("positioning") or "",
            product.get("suitable_skin") or "",
            product.get("description") or "",
            " ".join(product.get("concerns_list") or []),
            " ".join(product.get("key_ingredients_list") or []),
            " ".join(str(x) for x in product.get("safety_notes") or []),
            " ".join(str(x) for x in product.get("texture_notes") or []),
        ]
        return " ".join(str(value) for value in values if value)

    @classmethod
    def _filter_excluded_products(cls, products: List[Dict[str, Any]], terms: List[str]) -> List[Dict[str, Any]]:
        if not products or not terms:
            return products
        return [
            product for product in products
            if not any(cls._product_violates_exclusion(product, term) for term in terms)
        ]

    @classmethod
    def _product_violates_exclusion(cls, product: Dict[str, Any], term: str) -> bool:
        text = cls._product_context_text(product)
        if not term or term not in text:
            return False
        safe_markers = [f"无{term}", f"不含{term}", f"未添加{term}", f"0{term}", f"零{term}"]
        if any(marker in text for marker in safe_markers):
            return False
        return True

    @staticmethod
    def _seen_in_history(product: Dict[str, Any], history: List[Dict[str, Any]]) -> bool:
        if not history:
            return False
        assistant_text = "\n".join(
            (item.get("content") or "")
            for item in history[-6:]
            if item.get("role") == "assistant"
        )
        if not assistant_text:
            return False
        candidates = [
            product.get("display_name") or "",
            product.get("name") or "",
            product.get("brand") or "",
        ]
        for text in candidates:
            text = str(text or "").strip()
            if len(text) >= 2 and text in assistant_text:
                return True
        return False

    def _split_into_chunks(self, text: str, chunk_size: int = 25) -> List[str]:
        chunks = []
        current = ""
        for char in text:
            current += char
            if len(current) >= chunk_size and char in "\n。！？； ":
                chunks.append(current)
                current = ""
        if current:
            chunks.append(current)
        return chunks

    async def _try_generate_llm_text(self, turn: CanonicalTurn, result: Dict[str, Any]) -> Optional[str]:
        # V2 生成式大模型润色开关：默认关闭（保护基线）。
        # 读取优先级：os.environ > settings.V2_DISABLE_LLM（方便命令行临时覆盖）
        env_val = os.environ.get("V2_DISABLE_LLM", "").strip().lower()
        if env_val in ("1", "true", "yes"):
            return None
        if env_val in ("0", "false", "no"):
            llm_disabled = False
        else:
            llm_disabled = getattr(settings, "V2_DISABLE_LLM", True)
        if llm_disabled:
            return None

        products = result.get("products") or []
        answer_mode = result.get("answer_mode")
        if hasattr(answer_mode, "value"):
            answer_mode = answer_mode.value

        if answer_mode == AnswerMode.NO_MATCH.value or not products:
            return None

        try:
            from app.services.llm import get_llm_service
        except Exception as exc:
            logger.warning("V2 LLM service unavailable, use local presenter: %s", exc)
            return None

        local_draft = (result.get("text") or "").strip()
        product_lines = []
        for idx, product in enumerate(products[:4], start=1):
            facts = self._build_llm_product_facts(product, idx)
            product_lines.append(json.dumps(facts, ensure_ascii=False))

        pitfalls = result.get("pitfalls") or []
        pitfalls_text = "\n".join(
            f"- {item.get('title', '使用提醒')}：{item.get('description', '')}"
            for item in pitfalls[:4]
            if isinstance(item, dict)
        )
        output_requirements = self._build_llm_output_requirements(answer_mode, turn)

        prompt = f"""你是一个有经验的护肤/美妆导购。请基于下面提供的商品事实，给用户一段自然、具体、像真人说话的中文回答。

用户原话：{turn.raw_message}
识别条件：品类={turn.category or '未指定'}；肤质={turn.skin_type or '未限定'}；预算={self._format_budget_label(turn)}；诉求={'、'.join(turn.concerns) or '未限定'}
回答模式：{answer_mode}

候选商品事实（只能基于这些事实回答，不能编造商品、成分、价格、评价）：
{chr(10).join(product_lines)}

使用提醒：
{pitfalls_text or '无'}

本地结构草稿（只参考结构与骨架，内容要用你自己的话改写）：
{local_draft[:1200]}

写作要求（必须遵守）：
1. 开头先说一句人话结论（针对用户肤质/诉求直接给判断），不要套话铺垫。
2. 当商品事实里有"质地肤感""作用原理""使用方法""常见问答""用户体验反馈""品牌资料""注意事项"时，要自然地揉进介绍里，像在和朋友说：
   - 可以说"这款质地偏…"、"它主打…"、"用法上…"、"很多人反馈…"、"官方资料称…"。
   - "品牌资料"里的实验数据/专利/榜单属于品牌方说法，必须带"根据品牌资料"或"官方称"这样的降级口吻，不要当硬事实。
   - "用户体验反馈"是用户真实评价摘要，可以引用但要说是"不少买过的人反馈"。
3. 绝对不要出现以下任何话术：
   - "没抓到/未查到/没有足够评价/数据不足/资料有限/详情页没有提到"等暴露数据缺口的话。
   - 如果某类事实缺失，就自然跳过那部分，转而介绍质地/用法/成分/适合肤质即可，不要向用户解释缺失原因。
4. 禁止模板腔：不要说"闭眼入""不容错过""强烈推荐""性价比之王""完美选择""根据个人需求选择"。
5. 禁止暴露内部词：不要说"后端""候选商品""系统筛选""AI""模型"。
6. 价格保留 ¥ 符号和规格，例如"约¥108 / 50ml"，不要四舍五入或乱改数字。
7. 输出用 Markdown：商品名加粗，下面用 "- 参考价：..." "- 核心成分：..." "- 注意点：..." 的形式，和本地草稿骨架一致。
8. 推荐模式写3款以内；对比模式按给定商品事实写2-4款；判断/追问模式聚焦用户点名的那1-2款。
9. 总长度控制在 650 字以内，不要用表格、不要用emoji。
{output_requirements}
"""

        messages = [
            {
                "role": "system",
                "content": (
                    "你是线下专柜做了5年的资深护肤导购，说话直接、克制、不营销。"
                    "只用给定商品事实回答，不能编造。遇到事实缺失就跳过那一段，不要告诉用户数据不足。"
                    "会解释为什么、怎么用、适合谁、有什么体感，但绝不吹牛逼。"
                    "如果用户点名的具体商品不在候选里，不要承认你没有这个商品的信息，"
                    "而是自然转向你确实了解的同类品推荐，或者反问用户想要什么功效的。"
                    "绝对禁止使用'我手头''我手里''我这边''目前我库里'这类暴露你在看一个数据列表的表述，"
                    "直接说商品名字，不要说'我手头这几款'。"
                ),
            },
            {"role": "user", "content": prompt},
        ]

        try:
            llm_service = get_llm_service()
            text = await llm_service.chat(messages, temperature=0.35, max_tokens=1200, use_cache=False)
        except Exception as exc:
            logger.warning("V2 LLM generation failed, use local presenter: %s", exc)
            return None

        text = self._clean_llm_text(text)
        if not self._is_usable_llm_text(text, products, answer_mode):
            logger.warning("V2 LLM text rejected, use local presenter")
            return None
        if not self._llm_keeps_presenter_skeleton(text, local_draft):
            logger.warning("V2 LLM text dropped presenter skeleton, use local presenter")
            return None
        return text

    @staticmethod
    def _extract_skeleton_markers(draft: str) -> List[str]:
        """从本地 presenter 草稿提取结构骨架标记：## 标题、**加粗标签**、- 字段标签：。

        这是"格式底线"：LLM 只能在这些骨架内润色扩写，不能删掉它们。
        """
        markers: List[str] = []
        seen = set()

        def add(marker: str) -> None:
            marker = marker.strip()
            if marker and marker not in seen:
                seen.add(marker)
                markers.append(marker)

        for raw in (draft or "").split("\n"):
            line = raw.strip()
            if not line:
                continue
            if line.startswith("## "):
                add(line)
                continue
            bold = re.fullmatch(r"\*\*([^*]+)\*\*", line)
            if bold:
                add(f"**{bold.group(1).strip()}**")
                continue
            field = re.match(r"-\s*([^：:]{1,10})[：:]", line)
            if field:
                add(f"- {field.group(1).strip()}：")
        return markers

    @classmethod
    def _llm_keeps_presenter_skeleton(cls, llm_text: str, local_draft: str) -> bool:
        """LLM 输出必须包含本地草稿中的关键事实（商品价格数字、商品短名），允许自由组织语言。"""
        if not llm_text or not local_draft:
            return True
        normalized_llm = re.sub(r"\s+", "", llm_text)
        prices = re.findall(r"[¥￥]\s*(\d{2,5})", local_draft)
        price_hits = sum(1 for p in prices[:3] if p in normalized_llm)
        if prices and price_hits == 0:
            return False
        return True

    def _build_llm_output_requirements(self, answer_mode: Optional[str], turn: CanonicalTurn) -> str:
        common = [
            "不要说“筛选了一圈”“口碑比较稳”“闭眼入”“性价比最稳”“根据个人需求”等模板话术；不要暴露“后端”“候选商品”“系统”等内部词。",
            f"不要把系统推导出的预算区间说成用户原话；如果预算是区间，只说“按{self._format_budget_label(turn)}理解”。",
            "不要编造商品库没有的成分、价格、链接、评价。",
            "输出 Markdown，但不要使用表格，不要使用 emoji，控制在650字以内。",
        ]
        if answer_mode == AnswerMode.RECOMMENDATION.value:
            return "\n".join([
                "0. 第一段必须给出有信息量的结论，至少说清：适合谁、为什么这组候选适合、用户应该怎么选；不要只写“以下几款可以参考”。",
                "1. 必须使用下面格式输出推荐类回答，不要把商品信息揉成一段：",
                "   第一段摘要",
                "   **商品名称**",
                "   - 参考价：约¥价格 / 规格",
                "   - 核心成分：只写商品事实里已有的关键成分，缺失时写“以商品详情页为准”",
                "   - 注意点：写1条和当前肤质、诉求或使用场景相关的具体提醒",
                "   ## 综合建议",
                "   用1段话说明哪款更适合预算/肤质/诉求，不能省略。",
                "2. 推荐类回答最多写3款；商品标题必须原样使用“名称”字段给出的商品名，不要自己加规格、营销词或补全成长句，方便前端把商品图插在商品名下方。",
                *[f"{idx + 3}. {item}" for idx, item in enumerate(common)],
            ])
        if answer_mode == AnswerMode.COMPARE.value:
            return "\n".join([
                "0. 第一段必须先给明确的对比结论：给定产品的核心差异是什么、各自更适合谁，不能每款都说一样的话。",
                "1. 对比必须从质地、适合肤质、核心成分/定位、价格四个维度指出差异，不要重复描述。",
                "2. 用下面格式输出：",
                "   第一段结论",
                "   **商品名称**",
                "   - 参考价/规格、核心差异点、适合人群",
                "   每个给定商品都按这个格式写一段",
                "   ## 综合建议",
                "   明确说谁更适合用户的肤质/场景，不要说“根据个人需求选择”。",
                "3. 商品标题必须原样使用“名称”字段给出的商品名。",
                *[f"{idx + 4}. {item}" for idx, item in enumerate(common)],
            ])
        if answer_mode == AnswerMode.FOLLOWUP.value:
            focus_hint = ""
            ftype = turn.followup_type.value if turn.followup_type else ""
            if ftype == FollowupType.USAGE_TIME.value:
                focus_hint = "用户在追问用法/时间，请优先使用“使用方法”字段作答；问能不能早晚用/需不需要卸时，直接按事实回答，不要重复推荐其他商品。"
            elif ftype == FollowupType.INGREDIENT.value:
                focus_hint = "用户在追问成分，优先用“关键成分”和“注意事项”字段回答；事实里没提到的成分不要瞎编。"
            elif ftype == FollowupType.EFFICACY.value:
                focus_hint = "用户在追问主打功效/作用，优先用“诉求”“品牌资料”“作用机制”字段，重点讲能解决什么问题、靠什么成分起效，不要复述价格。"
            elif ftype == FollowupType.SUITABILITY.value:
                focus_hint = "用户在追问是否适合自己（敏感肌/孕妇/油皮/干皮/能不能天天用等），优先用“适合肤质”“注意事项”“用户体验反馈”，结合肤质/诉求给明确判断。"
            elif ftype == FollowupType.PRICE.value:
                focus_hint = "用户在追问价格，直接给参考价和规格，不用再复述产品介绍。"
            return "\n".join([
                "0. 第一段必须先给明确判断或结论，针对用户问的那个点直接回答，不要重新写一整段产品介绍。",
                "1. 商品名必须原样使用“名称”字段给出的商品名。",
                f"2. {focus_hint or '围绕用户的追问点作答，优先使用对应字段，不要答非所问。'}",
                "3. 追问场景下最多回答1款核心商品；回答控制在250字以内。",
                *[f"{idx + 4}. {item}" for idx, item in enumerate(common)],
            ])
        return "\n".join([
            "0. 第一段必须先给明确判断或结论，不要只写铺垫。",
            "1. 商品名必须原样使用“名称”字段给出的商品名，不要自己加规格、营销词或补全成长句，方便前端把商品图插在商品名下方。",
            "2. 每款商品必须包含：为什么适合/不适合当前肤质或诉求、核心成分/成分侧重、参考价（保留规格，例如“约¥968 / 50ml”）、1条具体注意点。",
            *[f"{idx + 3}. {item}" for idx, item in enumerate(common)],
        ])

    def _build_llm_product_facts(self, product: Dict[str, Any], index: int) -> Dict[str, Any]:
        name = self._compact_product_name(product) or f"候选{index}"
        spec = self._extract_product_spec(product)

        def _join_list(v, max_items: int = 5) -> str:
            items = []
            if isinstance(v, list):
                items = [str(x).strip() for x in v if x and str(x).strip()]
            elif isinstance(v, str):
                items = [s.strip() for s in re.split(r"[、,，;；]\s*", v) if s.strip()]
            return "；".join(items[:max_items])

        qa_text = _join_list(product.get("qa_facts"), max_items=3)
        mechanism_text = _join_list(product.get("mechanism_notes"), max_items=3)
        usage_text = _join_list(product.get("usage_steps"), max_items=3)
        safety_text = _join_list(product.get("safety_notes"), max_items=3)
        texture_text = _join_list(product.get("texture_notes"), max_items=3)
        claim_text = _join_list(product.get("claim_notes"), max_items=2)
        review_text = _join_list(product.get("user_review_notes"), max_items=3)

        facts = {
            "序号": index,
            "名称": name,
            "品牌": product.get("brand") or "",
            "品类": product.get("category") or "",
            "参考价": self._format_price_with_spec(product, spec),
            "规格": spec,
            "定位": product.get("positioning") or "",
            "适合肤质": product.get("suitable_skin") or "",
            "适合人群": product.get("target_users") or "",
            "主要诉求": "、".join(product.get("concerns_list") or []),
            "关键成分": "、".join(product.get("key_ingredients_list") or []),
            "商品详情摘要": self._build_product_detail_excerpt(product),
            "商品扩展信息": self._build_product_extra_facts(product),
            "商品资料提醒": getattr(self, "presenter", Presenter())._extract_product_data_warning(product),
            "使用提醒": product.get("pitfalls") or "",
        }
        if texture_text:
            facts["质地肤感"] = texture_text
        if usage_text:
            facts["使用方法"] = usage_text
        if mechanism_text:
            facts["作用原理"] = mechanism_text
        if qa_text:
            facts["常见问答"] = qa_text
        if safety_text:
            facts["注意事项"] = safety_text
        if review_text:
            facts["用户体验反馈"] = review_text
        if claim_text:
            facts["品牌资料"] = claim_text
        return facts

    @staticmethod
    def _coerce_specs(product: Dict[str, Any]) -> Dict[str, Any]:
        specs = product.get("_specs") or product.get("specifications") or product.get("specs") or {}
        if isinstance(specs, str):
            try:
                specs = json.loads(specs)
            except Exception:
                specs = {}
        return specs if isinstance(specs, dict) else {}

    @classmethod
    def _build_product_detail_excerpt(cls, product: Dict[str, Any], limit: int = 520) -> str:
        text = str(product.get("description") or "").strip()
        if not text:
            return ""
        text = re.sub(r"https?://\S+", "", text)
        text = re.sub(r"\s+", " ", text).strip(" ；;。")
        if len(text) > limit:
            text = text[:limit].rstrip("，。、；;") + "…"
        return text

    @classmethod
    def _build_product_extra_facts(cls, product: Dict[str, Any]) -> Dict[str, str]:
        specs = cls._coerce_specs(product)
        allow_keys = [
            "volume", "规格", "净含量",
            "texture", "质地",
            "usage_time", "用法", "使用方法",
            "shop_name", "店铺",
            "source_type", "数据来源",
            "detail_url", "详情页",
            "保质期", "产品产地", "包装清单", "商品编号",
            "适合肤质", "适合人群", "核心成分", "主打功效", "备注",
        ]
        facts: Dict[str, str] = {}
        for key in allow_keys:
            value = specs.get(key) or product.get(key)
            if value is None or value == "":
                continue
            if isinstance(value, (list, tuple)):
                text = "、".join(str(item).strip() for item in value if str(item).strip())
            elif isinstance(value, dict):
                text = "；".join(f"{k}:{v}" for k, v in value.items() if v)
            else:
                text = str(value)
            text = re.sub(r"\s+", " ", text).strip(" ；;。")
            if not text:
                continue
            facts[key] = text[:160]
            if len(facts) >= 12:
                break
        return facts

    def _compact_product_name(self, product: Dict[str, Any]) -> str:
        """喂给 LLM 的商品名必须收敛：复用 presenter 的短名逻辑，去掉营销词/规格堆叠。

        前端 inline 图片匹配已把收敛后的短名作为候选，所以这里收敛不会破坏配图。
        """
        try:
            compact = Presenter._short_product_name(product)
        except Exception:
            compact = ""
        if compact:
            return compact
        return (product.get("display_name") or product.get("name") or "").strip()

    def _format_price_with_spec(self, product: Dict[str, Any], spec: str = "") -> str:
        price = self._safe_float(product.get("price") or product.get("price_val"))
        if not price:
            return "价格见详情页"
        price_value = int(price) if price.is_integer() else price
        price_text = f"约¥{price_value}"
        return f"{price_text} / {spec}" if spec else price_text

    @classmethod
    def _extract_product_spec(cls, product: Dict[str, Any]) -> str:
        raw_specs = product.get("_specs") or product.get("specifications") or product.get("specs") or {}
        specs = cls._coerce_specs(product)
        if isinstance(raw_specs, str) and not specs:
            specs = {"规格": raw_specs}

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
            spec = cls._normalize_product_spec(value)
            if spec:
                return spec
        return ""

    @staticmethod
    def _normalize_product_spec(value: Any) -> str:
        raw = str(value or "").strip()
        if not raw:
            return ""
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
        if not match:
            return ""
        spec = re.sub(r"\s+", "", match.group(1))
        return spec.replace("贴", "片").replace("毫升", "ml").replace("ML", "ml").replace("mL", "ml").replace("克", "g").replace("G", "g")

    @staticmethod
    def _clean_llm_text(text: str) -> str:
        text = str(text or "").strip()
        if text.startswith("```"):
            text = text.strip("`").strip()
            if text.lower().startswith("markdown"):
                text = text[len("markdown"):].strip()
        # 清掉淘宝客服腔的开头称呼
        text = re.sub(r"^(亲[，,~！!。\s]*)+", "", text)
        text = re.sub(r"[，,\s]*亲(?=[，,！!。\s]|$)", "", text)
        # 营销黑话翻译成客观导购话（保留肤质/人群指向信息，纯吹牛逼的降级或删除）
        marketing_translate = [
            ("油皮亲妈", "适合油性肤质"),
            ("混油亲妈", "适合混合偏油肤质"),
            ("混干亲妈", "适合混合偏干肤质"),
            ("干皮亲妈", "适合干性肤质"),
            ("敏感肌亲妈", "适合敏感肌"),
            ("痘肌亲妈", "适合痘肌"),
            ("沙漠皮亲妈", "适合极干肤质"),
            ("YYDS", "口碑很好"),
            ("yyds", "口碑很好"),
            ("性价比之王", "性价比不错"),
            ("性价比天花板", "性价比不错"),
            ("闭眼入", ""),
            ("入手不亏", ""),
            ("不容错过", ""),
            ("强烈推荐", ""),
            ("非常值得入手", ""),
            ("是理想选择", ""),
            ("完美选择", ""),
            ("必入", ""),
            ("封神", "口碑很好"),
            ("绝绝子", "表现不错"),
        ]
        for mkt, rep in marketing_translate:
            text = text.replace(mkt, rep)
        forbidden = [
            "筛选了一圈", "口碑比较稳", "性价比最稳", "根据个人需求",
            "根据自己的需求", "大家可以根据",
            "后端筛选出的", "后端已经确定的", "后端提供的", "候选商品",
            "系统筛选出的", "系统推导出的",
            "没有足够的用户评价", "未抓取到足够评价", "评价数据不足",
            "资料有限", "数据有限", "详情页没有提到", "没有查到", "未查到",
            "暂时没有", "暂无评价", "暂无相关",
            "这款产品的评价不多", "评价较少", "评价很少",
            "手头没有", "数据库没有", "库里没有", "没有收录",
            "我没有相关", "没有它的具体信息", "没有它的资料", "不太熟",
            "我掌握的信息", "我的信息有限", "信息我这里不全", "我这里不全",
            "我手头", "手上这几款", "我手里", "目前我手头", "我这里有",
            "目前我库里", "我库里的", "我这边",
        ]
        for item in forbidden:
            text = text.replace(item, "")
        text = text.replace("- 参考价格：", "- 参考价：")
        text = text.replace("- 注意事项：", "- 注意点：")
        text = text.replace("- 关键成分：", "- 核心成分：")
        text = re.sub(r"(?:这里要注意[：:])?这里展示的是入库参考价，实时活动价和规格组合以下单页为准。?", "", text)
        text = re.sub(r"(?:参考价为入库时价格，实时活动价以商品链接为准。\s*){2,}", "参考价为入库时价格，实时活动价以商品链接为准。", text)
        return text.strip()

    @staticmethod
    def _is_usable_llm_text(text: str, products: List[Dict[str, Any]], answer_mode: Optional[str] = None) -> bool:
        if not text or len(text) < 30:
            return False
        if any(marker in text for marker in ["模型服务刚刚有点不稳定", "我刚刚没有稳定连上模型服务", "抱歉", "作为AI"]):
            return False
        if products:
            aliases = set()
            for p in products[:4]:
                for raw in [p.get("display_name"), p.get("name"), p.get("brand")]:
                    s = str(raw or "").strip()
                    if s:
                        aliases.add(s)
                        if len(s) >= 4:
                            aliases.add(s[:10])
                # 提取2-4字连续中文片段作为简称，覆盖"小棕瓶""小黑瓶""白泥""大白饼"等
                name_str = str(p.get("name") or "")
                cn_segs = re.findall(r"[\u4e00-\u9fa5]{2,}", name_str)
                for seg in cn_segs:
                    # 滑动窗口取所有2、3、4字片段
                    for L in (2, 3, 4):
                        for i in range(len(seg) - L + 1):
                            sub = seg[i:i+L]
                            # 过滤太通用的词（品类词/品牌通用词）
                            if sub in {"面膜", "面霜", "精华", "防晒", "乳液", "爽肤水", "化妆水",
                                      "洁面", "粉底", "气垫", "散粉", "粉饼", "妆前", "隔离",
                                      "遮瑕", "腮红", "口红", "唇膏", "卸妆", "香水", "套装",
                                      "补水", "保湿", "抗老", "修护", "控油", "舒缓", "美白",
                                      "第三代", "新版", "升级版", "经典", "持久", "滋润", "清爽"}:
                                continue
                            aliases.add(sub)
            hit = sum(1 for a in aliases if a and a in text)
            if hit < min(1, len(products)):
                return False
        return True

    def _apply_output_guard(
        self,
        turn: CanonicalTurn,
        answer_mode: AnswerMode,
        result: Dict[str, Any],
    ) -> Dict[str, Any]:
        try:
            products = result.get("products") or []
            original_count = len(products)
            mode_value = answer_mode.value if hasattr(answer_mode, "value") else str(answer_mode)
            max_expected = None
            force_single = False

            if mode_value == AnswerMode.FOLLOWUP.value:
                ftype = turn.followup_type.value if hasattr(turn.followup_type, "value") else str(turn.followup_type or "")
                anchor_count = (
                    len(turn.referenced_products or [])
                    + len(turn.referenced_product_ids or [])
                    + len(turn.name_clues or [])
                )
                has_anchor = anchor_count > 0
                single_types = {
                    FollowupType.PRICE.value,
                    FollowupType.INGREDIENT.value,
                    FollowupType.EFFICACY.value,
                }
                if ftype in single_types or anchor_count == 1:
                    force_single = True
                if ftype == FollowupType.SUITABILITY.value and anchor_count == 1:
                    force_single = True
                max_expected = 1 if force_single else 3
            elif mode_value == AnswerMode.COMPARE.value:
                max_expected = min(max(len(turn.compare_targets or []), len(turn.referenced_product_ids or []), 2), 4)
            elif mode_value == AnswerMode.RECOMMENDATION.value:
                max_expected = 3
            elif mode_value == AnswerMode.JUDGEMENT.value:
                # 判断模式：用户点名了具体商品时聚焦1个；只有泛问"油皮适合什么"才返回3个
                has_specific_target = bool(turn.name_clues or turn.brand or turn.compare_targets)
                if turn.image_context or has_specific_target:
                    max_expected = 1
                else:
                    max_expected = 3

            adjusted = False
            if max_expected is not None and len(products) > max_expected:
                result["products"] = products[:max_expected]
                adjusted = True
                logger.warning(
                    "OutputGuard: trimmed products from %d to %d (mode=%s, followup=%s, anchor=%s)",
                    original_count, max_expected, mode_value,
                    turn.followup_type, bool(turn.referenced_products or turn.name_clues),
                )

            if adjusted and "text" in result:
                result["text"] = result["text"]

            return result
        except Exception as guard_err:
            logger.warning("OutputGuard exception (non-fatal, returning original result): %s", guard_err)
            return result

    @staticmethod
    def _safe_float(value: Any) -> Optional[float]:
        if value is None or value == "":
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _format_budget_label(turn: CanonicalTurn) -> str:
        if turn.budget_min is not None and turn.budget_max is not None:
            mid = int((turn.budget_min + turn.budget_max) / 2)
            return f"约¥{mid}"
        if turn.budget_max is not None:
            return f"¥{int(turn.budget_max)}以内"
        if turn.budget_min is not None:
            return f"¥{int(turn.budget_min)}以上"
        return "未限定"

    async def _enrich_turn_with_knowledge_pitfalls(self, turn: CanonicalTurn) -> None:
        if turn.knowledge_pitfalls:
            return

        image_context = turn.image_context if isinstance(turn.image_context, dict) else {}
        results = image_context.get("results") or []
        top = results[0] if results and isinstance(results[0], dict) else {}
        query_parts = [
            turn.category,
            turn.brand,
            " ".join(turn.concerns or []),
            " ".join(turn.name_clues or []),
            top.get("category"),
            top.get("brand"),
            top.get("display_name"),
            top.get("name"),
            "避雷",
            "注意",
            "使用风险",
        ]
        query = " ".join(str(item).strip() for item in query_parts if item and str(item).strip())
        if not query:
            return

        try:
            from app.services.rag import get_rag_retriever

            rag_retriever = get_rag_retriever()
            pitfalls = await rag_retriever._search_pitfalls(query, [])
        except Exception as exc:
            logger.warning("V2 image knowledge pitfalls fallback skipped: %s", exc)
            return

        cleaned = []
        seen = set()
        for item in pitfalls or []:
            if not isinstance(item, dict):
                continue
            description = str(item.get("description") or item.get("content") or "").strip()
            if not description or description in seen:
                continue
            seen.add(description)
            cleaned.append(item)
            if len(cleaned) >= 3:
                break

        if cleaned:
            turn.knowledge_pitfalls = cleaned
            if image_context:
                image_context["knowledge_pitfalls"] = cleaned

    async def _enrich_image_context_with_knowledge_pitfalls(self, turn: CanonicalTurn) -> None:
        await self._enrich_turn_with_knowledge_pitfalls(turn)

    def _serialize_products(self, products: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        result = []
        for p in products:
            result.append({
                "id": p.get("id"),
                "name": p.get("name"),
                "display_name": p.get("display_name"),
                "brand": p.get("brand"),
                "category": p.get("category"),
                "price": self._safe_float(p.get("price")) or 0,
                "original_price": self._safe_float(p.get("original_price")) or 0,
                "spec": self._extract_product_spec(p),
                "image_url": p.get("image_url"),
                "detail_url": p.get("detail_url"),
                "description": p.get("description", "")[:100],
                "rating": self._safe_float(p.get("rating")) or 0,
                "sales_count": p.get("sales_count", 0),
                "_match_reason": p.get("_match_reason", {}),
                "positioning": p.get("positioning", ""),
                "suitable_skin": p.get("suitable_skin", ""),
                "concerns_list": p.get("concerns_list", []),
                "key_ingredients_list": p.get("key_ingredients_list", []),
                "pitfalls": p.get("pitfalls", ""),
            })
        return result

    def _build_inline_image_products(self, products: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        inline_products = self._serialize_products(products[:4])
        for product in inline_products:
            product["product_id"] = product.get("id")
        return inline_products

    @classmethod
    def _rank_products_for_knowledge_query(
        cls,
        products: List[Dict[str, Any]],
        query: str,
    ) -> List[Dict[str, Any]]:
        if not products:
            return []
        terms = cls._extract_knowledge_query_terms(query)
        if not terms:
            return products

        scored = []
        for index, product in enumerate(products):
            score = cls._score_product_for_knowledge_terms(product, terms)
            scored.append((score, -index, product))
        if max(score for score, _, _ in scored) <= 0:
            return products
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [product for _, _, product in scored]

    @classmethod
    def _select_best_knowledge_products(
        cls,
        products: List[Dict[str, Any]],
        query: str,
    ) -> List[Dict[str, Any]]:
        ranked = cls._rank_products_for_knowledge_query(products, query)
        terms = cls._extract_knowledge_query_terms(query)
        if not ranked or not terms:
            return ranked[:3]
        scores = [cls._score_product_for_knowledge_terms(product, terms) for product in ranked]
        top_score = max(scores) if scores else 0
        if top_score <= 0:
            return ranked[:3]
        return [
            product for product, score in zip(ranked, scores)
            if score == top_score
        ][:3]

    @classmethod
    def _score_product_for_knowledge_terms(cls, product: Dict[str, Any], terms: List[str]) -> int:
        specs = cls._coerce_specs(product)
        values: List[str] = [
            product.get("name") or "",
            product.get("display_name") or "",
            product.get("brand") or "",
            product.get("category") or "",
            product.get("positioning") or "",
            product.get("suitable_skin") or "",
            product.get("target_users") or "",
            product.get("pitfalls") or "",
            product.get("description") or "",
            " ".join(product.get("key_ingredients_list") or []),
            " ".join(product.get("concerns_list") or []),
        ]
        for value in specs.values():
            if isinstance(value, (str, int, float)):
                values.append(str(value))
            elif isinstance(value, list):
                values.extend(str(item) for item in value)
        haystack = " ".join(values)
        score = 0
        for term in terms:
            if term and term in haystack:
                score += max(1, min(len(term), 8))
        return score

    @staticmethod
    def _extract_knowledge_query_terms(query: str) -> List[str]:
        text = str(query or "")
        for phrase in ["有什么用", "有啥用", "什么用", "干嘛的", "是干什么的", "作用", "是什么", "成分", "配方"]:
            text = text.replace(phrase, " ")
        raw_terms = re.split(r"[、,，/；;。！？?（）()\s]+", text)
        terms = []
        seen = set()
        for raw in raw_terms:
            term = raw.strip()
            if len(term) < 2:
                continue
            if term in {"提取物", "防御因子", "植物", "细胞"}:
                continue
            if term not in seen:
                seen.add(term)
                terms.append(term)
        return terms[:8]


_v2_agent_instance: Optional[V2ShoppingAgent] = None


def get_v2_shopping_agent() -> V2ShoppingAgent:
    global _v2_agent_instance
    if _v2_agent_instance is None:
        _v2_agent_instance = V2ShoppingAgent()
    return _v2_agent_instance
