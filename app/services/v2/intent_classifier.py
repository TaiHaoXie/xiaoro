from typing import Any, Dict, List, Optional

from .models import AnswerMode, FollowupType, SemanticIntent
from .semantic_intent_retriever import SemanticIntentRetriever


DAY_TIME_KEYWORDS = ["白天", "日间", "早上", "早晨", "上午", "通勤", "妆前"]
NIGHT_TIME_KEYWORDS = ["晚上", "夜间", "夜里", "晚间", "睡前", "夜晚"]
BOTH_TIME_KEYWORDS = ["早晚", "白天和晚上", "白天晚上", "日间夜间", "日夜", "分别怎么用", "什么时候用"]

CONTEXT_REFERENCE_CUES = [
    "这几款", "这几个", "这三款", "这些", "这里面", "其中", "上面", "刚才",
    "刚刚", "你推荐的", "这几个里面", "这几款里", "里面哪个", "里面哪",
    "除了", "除此之外", "上面这些以外", "这几款以外", "这几个以外",
    "第一款", "第一个", "第一支", "第一瓶", "第二款", "第三款", "这款", "那款", "它",
    "这玩意", "这东西", "这个东西", "这个产品", "那个产品", "它们",
]

FOLLOWUP_SELECTION_CUES = [
    "哪个", "哪款", "哪一个", "哪一款", "怎么选", "选哪个", "更合适",
    "更适合", "更稳", "更好", "更好的", "更推荐", "优先", "分别怎么用",
]

PRICE_CUES = ["多少钱", "价格", "价位", "贵不贵", "预算"]
INGREDIENT_CUES = ["成分", "含什么", "有什么成分", "配方", "刺激风险", "会不会刺激", "有没有酒精", "有没有香精", "含不含"]
EFFICACY_CUES = ["功效", "作用", "效果", "主打什么", "管什么", "干嘛用", "干吗用", "干什么用", "有什么用", "有什么效果", "什么功效", "主要功效", "主要作用", "能干嘛", "能干啥", "能做什么"]
CHEAPER_CUES = ["便宜", "更便宜", "便宜点", "平价", "更平价", "平替", "性价比", "压预算", "预算更低", "学生党", "没那么贵", "不那么贵", "少花点"]
HIGHER_BUDGET_CUES = [
    "预算更高", "高预算", "预算高一点", "贵一点", "更贵", "进阶一点", "进阶款", "升级款", "加预算", "往上", "更高价位", "高一档",
    "价格高些", "价格高一点", "价位高些", "价位高一点", "高些", "贵些", "更高价格", "更高价",
]
MORE_OPTIONS_CUES = ["除了", "除此之外", "上面这些以外", "这几款以外", "这几个以外", "还有别的吗", "还有没有别的", "还有没有其他", "还有其他", "换一批", "再来几款", "别的选择"]
SUITABILITY_CUES = [
    "适合", "能用吗", "可以用吗", "能不能用", "友好", "友好吗", "敏感肌", "敏皮", "油皮", "油肌",
    "干皮", "孕妇", "更稳", "闷痘", "爆痘", "过敏", "翻车", "别翻车",
    "温和", "更温和", "低刺激", "刺激小",
]
GENERIC_REVIEW_CUES = [
    "怎么样", "好不好", "好用吗", "好用不", "值得买吗", "值得不", "行不行",
    "靠谱吗", "推荐吗", "能买吗", "香不香", "油不油", "干不干", "闷不闷",
]

COMPARE_CUES = ["对比", "比较", "哪个好", "哪个更好", "哪个更适合", "怎么选", "选哪个", "区别", "差异", "vs", "VS"]
KNOWLEDGE_CUES = ["是什么", "干嘛的", "有什么用", "有啥用", "作用", "为什么", "怎么理解", "到底是"]
KNOWLEDGE_PREFIXES = ["什么是", "请问", "科普", "解释一下", "了解一下"]
GREETING_CUES = ["你好", "您好", "hi", "hello", "在吗", "在不在"]
RECOMMEND_CUES = ["推荐", "买什么", "选什么", "用什么", "求推荐", "想买", "帮我选"]


class IntentClassifier:
    """Hybrid semantic intent classifier.

    This layer normalizes user intent. Slot extraction remains in TurnParser.
    It is deliberately deterministic for backend regression stability, but it
    reasons over context-reference cues and intent families instead of using a
    single short-message regex gate.
    """

    def __init__(self):
        self.semantic_retriever = SemanticIntentRetriever()
        self.semantic_matcher = None
        try:
            from app.config import settings
            self.model_fallback_enabled = bool(getattr(settings, "INTENT_LLM_ENABLED", False))
            self.semantic_embedding_enabled = bool(getattr(settings, "INTENT_SEMANTIC_EMBEDDING_ENABLED", False))
            self._semantic_min_score = float(getattr(settings, "INTENT_SEMANTIC_MIN_SCORE", 0.55))
            self._semantic_model = str(getattr(settings, "INTENT_SEMANTIC_EMBEDDING_MODEL", "BAAI/bge-large-zh-v1.5"))
        except Exception:
            self.model_fallback_enabled = False
            self.semantic_embedding_enabled = False
            self._semantic_min_score = 0.55
            self._semantic_model = "BAAI/bge-large-zh-v1.5"

    def classify(self, message: str, history: List[Dict[str, Any]], slots: Dict[str, Any]) -> SemanticIntent:
        rule_intent = self._classify_rules(message, history, slots)
        if not self.model_fallback_enabled:
            return rule_intent
        if not self._should_use_model_fallback((message or "").strip(), rule_intent, slots):
            return rule_intent
        try:
            model_intent = self._model_intent_fallback(message, history, slots, rule_intent)
        except Exception:
            return rule_intent
        if model_intent is None:
            return rule_intent
        model_intent.secondary_intents = self._secondary_intents((message or "").strip(), model_intent)
        return model_intent

    async def classify_async(
        self, message: str, history: List[Dict[str, Any]], slots: Dict[str, Any]
    ) -> SemanticIntent:
        """异步意图分类：规则→(字符向量在规则内)→语义向量→模型兜底。

        规则没拦住时，先用真语义向量层接住绕话；语义层没把握才落大模型。
        语义层/模型层任何异常都回退规则结果，不让请求崩。
        """
        rule_intent = self._classify_rules(message, history, slots)
        msg = (message or "").strip()
        if not self._should_use_model_fallback(msg, rule_intent, slots):
            return rule_intent

        # 语义向量层：绕话靠语义命中，命中就不必惊动大模型
        if self.semantic_embedding_enabled:
            try:
                semantic_intent = await self._semantic_intent_match(msg, bool(history))
            except Exception:
                semantic_intent = None
            if semantic_intent is not None:
                semantic_intent.secondary_intents = self._secondary_intents(msg, semantic_intent)
                return semantic_intent

        if not self.model_fallback_enabled:
            return rule_intent
        try:
            model_intent = await self._model_intent_fallback_async(message, history, slots, rule_intent)
        except Exception:
            return rule_intent
        if model_intent is None:
            return rule_intent
        model_intent.secondary_intents = self._secondary_intents(msg, model_intent)
        return model_intent

    async def _semantic_intent_match(self, message: str, has_history: bool) -> Optional[SemanticIntent]:
        """真语义向量匹配；懒建 matcher，embedding 不可用时静默返回 None。"""
        matcher = self._get_semantic_matcher()
        if matcher is None:
            return None
        return await matcher.match(message, has_history)

    def _get_semantic_matcher(self):
        if self.semantic_matcher is not None:
            return self.semantic_matcher
        try:
            from .semantic_embedding_intent import SemanticEmbeddingIntentMatcher

            async def _encode_batch(texts: List[str]) -> List[List[float]]:
                from app.services.embedding import get_embedding_service
                service = get_embedding_service()
                if service is None:
                    return []
                return await service.encode_semantic_batch(texts, model=self._semantic_model)

            self.semantic_matcher = SemanticEmbeddingIntentMatcher(
                self.semantic_retriever.samples,
                encode_batch=_encode_batch,
                min_score=self._semantic_min_score,
            )
        except Exception:
            self.semantic_matcher = None
        return self.semantic_matcher


    def _classify_rules(self, message: str, history: List[Dict[str, Any]], slots: Dict[str, Any]) -> SemanticIntent:
        msg = (message or "").strip()
        has_history = bool(history)
        has_category = bool(slots.get("category"))
        has_brand = bool(slots.get("brand"))
        has_concern = bool(slots.get("concerns"))
        has_name_clue = bool(slots.get("name_clues"))
        compare_targets = slots.get("compare_targets") or []
        has_image_context = bool(slots.get("image_context"))

        if self._is_knowledge_query(msg, has_category, has_brand, has_concern) and not (
            has_history and self._has_contextual_reference(msg)
        ):
            intent = SemanticIntent(AnswerMode.KNOWLEDGE, 0.75, "knowledge_query")
            intent.secondary_intents = self._secondary_intents(msg, intent)
            return intent

        if self._is_compare(msg, compare_targets):
            intent = SemanticIntent(AnswerMode.COMPARE, 0.95, "explicit_compare")
            intent.secondary_intents = self._secondary_intents(msg, intent)
            return intent

        if has_image_context:
            image_followup = self._classify_image_context_followup(msg)
            if image_followup:
                image_followup.secondary_intents = self._secondary_intents(msg, image_followup)
                return image_followup
            if self._is_image_context_judgement_query(msg):
                intent = SemanticIntent(AnswerMode.JUDGEMENT, 0.9, "image_context_judgement")
                intent.secondary_intents = self._secondary_intents(msg, intent)
                return intent

        followup = self._classify_followup(msg, has_history, has_category, has_brand, has_concern)
        if followup:
            vector_followup = self._vector_retrieve(msg, has_history)
            if (
                followup.followup_type == FollowupType.OTHER
                and vector_followup
                and vector_followup.answer_mode == AnswerMode.FOLLOWUP
            ):
                vector_followup.secondary_intents = self._secondary_intents(msg, vector_followup)
                return vector_followup
            followup.secondary_intents = self._secondary_intents(msg, followup)
            return followup

        if self._is_product_judgement(msg, has_brand, has_category, has_name_clue):
            intent = SemanticIntent(AnswerMode.JUDGEMENT, 0.9, "product_judgement")
            intent.secondary_intents = self._secondary_intents(msg, intent)
            return intent

        if has_image_context and self._is_image_identification_query(msg):
            intent = SemanticIntent(AnswerMode.JUDGEMENT, 0.92, "image_identification")
            intent.secondary_intents = self._secondary_intents(msg, intent)
            return intent

        if self._is_no_match(msg, has_category, has_brand, has_concern, has_image_context):
            return SemanticIntent(AnswerMode.NO_MATCH, 0.8, "no_match")

        vector_intent = self._vector_retrieve(msg, has_history)
        if vector_intent:
            vector_intent.secondary_intents = self._secondary_intents(msg, vector_intent)
            return vector_intent

        # 走到这里 = 前面所有具名判定都没命中。这是"我没认出来"的兜底，不是"我确定是推荐"。
        # 必须背低置信度，让上层门控（_should_use_model_fallback）天然会把它升级到语义/模型层，
        # 而不是伪装成 0.85 的高分把裁判挡在门外。
        intent = SemanticIntent(AnswerMode.RECOMMENDATION, 0.3, "default_recommendation")
        intent.secondary_intents = self._secondary_intents(msg, intent)
        return intent

    # ----- 意图层模型兜底：不是裁判，只在规则/向量都没拦住时补充理解 -----
    # 阈值：规则给出的置信度低于此值，或落到 OTHER/NO_MATCH，才允许调用模型兜底。
    MODEL_FALLBACK_CONFIDENCE = 0.8
    _MODEL_MODE_MAP = {
        "recommendation": AnswerMode.RECOMMENDATION,
        "followup": AnswerMode.FOLLOWUP,
        "compare": AnswerMode.COMPARE,
        "judgement": AnswerMode.JUDGEMENT,
        "knowledge": AnswerMode.KNOWLEDGE,
        "no_match": AnswerMode.NO_MATCH,
    }

    def _should_use_model_fallback(self, msg: str, rule_intent: Any, slots: Optional[Dict[str, Any]] = None) -> bool:
        """决定要不要把这句话升级给语义/模型层当"裁判"。

        核心原则改成：**只要出现"分歧/没认出来"就升级，而不是"槽位强就跳过"。**
        旧逻辑把"槽位强"当成"意图已明确"，导致最强的对比证据(两个可比目标)反而
        触发跳过，把裁判挡在门外——这正是"换个问法就崩"的根。
        """
        if rule_intent is None:
            return True
        confidence = getattr(rule_intent, "confidence", 1.0) or 0.0
        reason = getattr(rule_intent, "reason", "") or ""
        followup_type = getattr(rule_intent, "followup_type", None)
        if getattr(followup_type, "value", followup_type) == FollowupType.OTHER.value:
            return True
        # 分歧优先：解析出的结构与裁决意图矛盾时，无论置信多高、槽位多强，都必须找裁判。
        if self._structure_contradicts(rule_intent, slots):
            return True
        if reason == "default_recommendation":
            # default 是"没认出来"的兜底动作。商品槽位清楚且无结构矛盾，就走推荐这个
            # 安全默认，不必每条正常推荐都烧一次模型；否则(槽位也不清楚)升级。
            if self._has_strong_slots(slots):
                return False
            return True
        # 规则给出了具名判定(如 knowledge_query/explicit_compare/product_judgement)就信任它，
        # 即便置信度低于阈值也不再烧模型；只有完全没有具名理由时才按置信度兜底。
        if reason:
            return False
        return confidence < self.MODEL_FALLBACK_CONFIDENCE

    def _structure_contradicts(self, rule_intent: Any, slots: Optional[Dict[str, Any]]) -> bool:
        """解析器抽到的结构证据与裁决意图相矛盾 → 该升级找裁判。

        目前覆盖最主要一类：句子里出现了 >=2 个可比目标(一堆可挑的候选)，
        裁决却不是 compare。这几乎一定是"换了个说法"漏判的对比/挑选。
        结构证据便宜、稳、抗改写，用它来兜住关键词小抄接不住的说法。
        """
        if not slots:
            return False
        mode = getattr(rule_intent, "answer_mode", None)
        mode_val = getattr(mode, "value", mode)
        targets = slots.get("compare_targets") or []
        if len(targets) >= 2 and mode_val != AnswerMode.COMPARE.value:
            return True
        return False

    @staticmethod
    def _has_strong_slots(slots: Optional[Dict[str, Any]]) -> bool:
        if not slots:
            return False
        return bool(
            slots.get("category")
            or slots.get("brand")
            or slots.get("concerns")
            or slots.get("compare_targets")
            or slots.get("name_clues")
        )


    def _coerce_model_intent(self, payload: Any) -> Optional[SemanticIntent]:
        """把模型返回的结构化意图收敛成已知 mode；不认识就丢弃，绝不让模型编造新类型。"""
        if not isinstance(payload, dict):
            return None
        mode_raw = str(payload.get("mode") or "").strip().lower()
        answer_mode = self._MODEL_MODE_MAP.get(mode_raw)
        if answer_mode is None:
            return None
        reason = str(payload.get("reason") or "model_fallback")[:80]
        return SemanticIntent(answer_mode, 0.7, f"model_fallback:{reason}")

    def _build_intent_messages(
        self, message: str, slots: Dict[str, Any]
    ) -> List[Dict[str, str]]:
        allowed = "、".join(self._MODEL_MODE_MAP.keys())
        sys_prompt = (
            "你是导购系统的意图理解兜底模块。只输出 JSON，不要解释。"
            "字段 mode 只能取以下之一：" + allowed + "。"
            "recommendation=想要买/求推荐；followup=在已有推荐上追问；compare=对比两款；"
            "judgement=问某款能不能用/适不适合；knowledge=问概念/原理/是什么；no_match=不属于导购。"
        )
        user_prompt = (
            f"用户这句话：{(message or '').strip()}\n"
            f"已知槽位：品类={slots.get('category') or '无'}；品牌={slots.get('brand') or '无'}；"
            f"诉求={slots.get('concerns') or '无'}\n"
            '只返回形如 {"mode": "...", "reason": "..."} 的 JSON。'
        )
        return [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_prompt},
        ]

    def _model_intent_fallback(
        self, message: str, history: List[Dict[str, Any]], slots: Dict[str, Any], rule_intent: Any
    ) -> Optional[SemanticIntent]:
        """同步意图兜底：仅供无事件循环的场景（如单测/脚本）。

        在 FastAPI 请求线程里已有事件循环，asyncio.run 会抛错并回退规则；
        真正的线上路径请走 _model_intent_fallback_async。
        """
        import asyncio

        try:
            from app.services.llm import get_llm_service
        except Exception:
            return None

        messages = self._build_intent_messages(message, slots)

        async def _run() -> Optional[str]:
            llm_service = get_llm_service()
            return await llm_service.chat(messages, temperature=0.0, max_tokens=120, use_cache=False)

        try:
            raw = asyncio.run(_run())
        except RuntimeError:
            return None
        except Exception:
            return None

        payload = self._parse_intent_json(raw)
        return self._coerce_model_intent(payload)

    async def _model_intent_fallback_async(
        self, message: str, history: List[Dict[str, Any]], slots: Dict[str, Any], rule_intent: Any
    ) -> Optional[SemanticIntent]:
        """异步意图兜底：直接 await 模型，能在运行中的事件循环里安全生效。

        模型不是裁判：只在规则没拦住时补充理解，且结果必须落进已知 mode 才采用。
        """
        try:
            from app.services.llm import get_llm_service
        except Exception:
            return None

        messages = self._build_intent_messages(message, slots)
        try:
            llm_service = get_llm_service()
            raw = await llm_service.chat(messages, temperature=0.0, max_tokens=120, use_cache=False)
        except Exception:
            return None

        payload = self._parse_intent_json(raw)
        return self._coerce_model_intent(payload)


    @staticmethod
    def _parse_intent_json(raw: Any) -> Optional[Dict[str, Any]]:
        import json

        text = str(raw or "").strip()
        if not text:
            return None
        if text.startswith("```"):
            text = text.strip("`").strip()
            if text.lower().startswith("json"):
                text = text[4:].strip()
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            data = json.loads(text[start:end + 1])
        except Exception:
            return None
        return data if isinstance(data, dict) else None

    def _vector_retrieve(self, msg: str, has_history: bool) -> Optional[SemanticIntent]:
        hit = self.semantic_retriever.retrieve(msg, has_history=has_history)
        return hit.intent if hit else None

    def _secondary_intents(self, msg: str, primary: SemanticIntent) -> List[Dict[str, Any]]:
        candidates: List[Dict[str, Any]] = []

        def add(ftype: FollowupType, reason: str, usage_focus: Optional[str] = None) -> None:
            if primary.answer_mode == AnswerMode.FOLLOWUP and primary.followup_type == ftype:
                if ftype != FollowupType.USAGE_TIME or primary.usage_time_focus == usage_focus:
                    return
            if primary.answer_mode == AnswerMode.FOLLOWUP and primary.followup_type == FollowupType.USAGE_TIME and ftype == FollowupType.SUITABILITY:
                return
            if any(item.get("followup_type") == ftype.value and item.get("usage_time_focus") == usage_focus for item in candidates):
                return
            candidates.append({
                "answer_mode": AnswerMode.FOLLOWUP.value,
                "followup_type": ftype.value,
                "usage_time_focus": usage_focus,
                "reason": reason,
            })

        if any(cue in msg for cue in CHEAPER_CUES):
            add(FollowupType.CHEAPER, "secondary_cheaper")
        if any(cue in msg for cue in HIGHER_BUDGET_CUES):
            add(FollowupType.HIGHER_BUDGET, "secondary_higher_budget")
        if any(cue in msg for cue in MORE_OPTIONS_CUES):
            add(FollowupType.MORE_OPTIONS, "secondary_more_options")
        if any(cue in msg for cue in INGREDIENT_CUES + ["刺激成分", "刺激风险", "会不会刺激"]):
            add(FollowupType.INGREDIENT, "secondary_ingredient")
        if any(cue in msg for cue in EFFICACY_CUES):
            add(FollowupType.EFFICACY, "secondary_efficacy")
        if any(cue in msg for cue in SUITABILITY_CUES) and (
            primary.followup_type != FollowupType.CHEAPER or self._has_strong_suitability_signal(msg)
        ):
            add(FollowupType.SUITABILITY, "secondary_suitability")
        if any(cue in msg for cue in DAY_TIME_KEYWORDS + NIGHT_TIME_KEYWORDS + BOTH_TIME_KEYWORDS):
            add(FollowupType.USAGE_TIME, "secondary_usage_time", self._usage_time_focus(msg))
        if any(cue in msg for cue in PRICE_CUES):
            add(FollowupType.PRICE, "secondary_price")

        return candidates[:3]

    def _classify_image_context_followup(self, msg: str) -> Optional[SemanticIntent]:
        if any(cue in msg for cue in CHEAPER_CUES):
            return SemanticIntent(AnswerMode.RECOMMENDATION, 0.9, "image_context_recommendation", FollowupType.CHEAPER)
        if any(cue in msg for cue in HIGHER_BUDGET_CUES):
            return SemanticIntent(AnswerMode.RECOMMENDATION, 0.9, "image_context_recommendation", FollowupType.HIGHER_BUDGET)
        if any(cue in msg for cue in MORE_OPTIONS_CUES):
            return SemanticIntent(AnswerMode.RECOMMENDATION, 0.9, "image_context_recommendation", FollowupType.MORE_OPTIONS)
        if any(cue in msg for cue in PRICE_CUES):
            return SemanticIntent(AnswerMode.FOLLOWUP, 0.88, "image_context_followup", FollowupType.PRICE)
        if any(cue in msg for cue in INGREDIENT_CUES):
            return SemanticIntent(AnswerMode.FOLLOWUP, 0.88, "image_context_followup", FollowupType.INGREDIENT)
        if any(cue in msg for cue in EFFICACY_CUES):
            return SemanticIntent(AnswerMode.FOLLOWUP, 0.88, "image_context_followup", FollowupType.EFFICACY)
        return None

    @staticmethod
    def _is_image_context_judgement_query(msg: str) -> bool:
        return any(cue in msg for cue in [
            "这款", "这个", "能用", "可以吗", "可不可以", "适合", "通勤可以", "敏感肌", "油皮", "干皮", "屏障",
            "是什么", "这是什么", "是哪款",
        ])

    def _classify_followup(
        self,
        msg: str,
        has_history: bool,
        has_category: bool,
        has_brand: bool,
        has_concern: bool,
    ) -> Optional[SemanticIntent]:
        if not has_history:
            return None

        has_context_reference = any(cue in msg for cue in CONTEXT_REFERENCE_CUES)
        has_selection_cue = any(cue in msg for cue in FOLLOWUP_SELECTION_CUES)
        has_followup_family_cue = any(
            cue in msg
            for cue in PRICE_CUES + INGREDIENT_CUES + EFFICACY_CUES + CHEAPER_CUES + HIGHER_BUDGET_CUES + MORE_OPTIONS_CUES + SUITABILITY_CUES
            + DAY_TIME_KEYWORDS + NIGHT_TIME_KEYWORDS + BOTH_TIME_KEYWORDS
        )
        short_contextual = len(msg) <= 18 and not has_category and not has_brand and not has_concern

        if not (has_context_reference or has_selection_cue or has_followup_family_cue or short_contextual):
            return None

        ftype = self._followup_type(msg)
        usage_focus = self._usage_time_focus(msg) if ftype == FollowupType.USAGE_TIME else None
        confidence = 0.92 if has_context_reference else 0.86
        return SemanticIntent(
            AnswerMode.FOLLOWUP,
            confidence,
            "contextual_followup",
            followup_type=ftype,
            usage_time_focus=usage_focus,
            needs_history=True,
        )

    def _followup_type(self, msg: str) -> FollowupType:
        USAGE_CUES = ["怎么用", "如何用", "用法", "使用方法", "怎么涂", "怎么抹", "怎么擦",
                      "怎么洗", "要洗吗", "要卸吗", "需要卸妆", "需要洗", "要卸妆吗", "直接涂"]
        has_usage = any(cue in msg for cue in DAY_TIME_KEYWORDS + NIGHT_TIME_KEYWORDS + BOTH_TIME_KEYWORDS + USAGE_CUES)
        has_more_options = any(cue in msg for cue in MORE_OPTIONS_CUES)
        has_higher_budget = any(cue in msg for cue in HIGHER_BUDGET_CUES)
        has_cheaper = any(cue in msg for cue in CHEAPER_CUES)
        has_suitability_risk = any(cue in msg for cue in SUITABILITY_CUES)
        has_sensitive_skin_risk = any(cue in msg for cue in [
            "敏感肌", "敏感", "敏皮", "干敏", "混敏", "泛红", "屏障",
            "翻车", "过敏", "闷痘", "爆痘",
        ])

        if has_usage and has_sensitive_skin_risk:
            return FollowupType.SUITABILITY
        if has_usage:
            return FollowupType.USAGE_TIME
        if has_more_options:
            return FollowupType.MORE_OPTIONS
        if has_higher_budget:
            return FollowupType.HIGHER_BUDGET
        if has_cheaper and self._has_strong_suitability_signal(msg):
            return FollowupType.SUITABILITY
        if has_cheaper:
            return FollowupType.CHEAPER
        if any(cue in msg for cue in PRICE_CUES):
            return FollowupType.PRICE
        if any(cue in msg for cue in INGREDIENT_CUES) or (
            any(cue in msg for cue in KNOWLEDGE_CUES) and self._mentions_ingredient_term(msg)
        ):
            return FollowupType.INGREDIENT
        if any(cue in msg for cue in EFFICACY_CUES):
            return FollowupType.EFFICACY
        if any(cue in msg for cue in SUITABILITY_CUES + FOLLOWUP_SELECTION_CUES):
            return FollowupType.SUITABILITY
        # 泛评价追问：指代某款("第N款/这款/它")问"怎么样/好吗/值得吗"，没有具体维度。
        # 归到 SUITABILITY(判断这款行不行)，避免落 OTHER 后被升级层瞎猜成价格。
        if any(cue in msg for cue in GENERIC_REVIEW_CUES):
            return FollowupType.SUITABILITY
        return FollowupType.OTHER

    @staticmethod
    def _mentions_ingredient_term(msg: str) -> bool:
        return any(term in msg for term in [
            "咖啡因", "烟酰胺", "A醇", "视黄醇", "视黄醛", "玻色因", "酵母", "二裂",
            "PITERA", "泛醇", "B5", "神经酰胺", "胜肽", "肽", "酸", "维C", "VC",
            "海茴香", "益生菌", "角鲨烷", "透明质酸",
        ])

    def _usage_time_focus(self, msg: str) -> str:
        if any(cue in msg for cue in BOTH_TIME_KEYWORDS):
            return "both"
        has_day = any(cue in msg for cue in DAY_TIME_KEYWORDS)
        has_night = any(cue in msg for cue in NIGHT_TIME_KEYWORDS)
        if has_day and has_night:
            return "both"
        if has_day:
            return "day"
        if has_night:
            return "night"
        return "general"

    def _is_compare(self, msg: str, compare_targets: List[str]) -> bool:
        # "早晚/日夜分别怎么用"是用法追问，不是对比，且没有>=2个具名目标时排除。
        if any(cue in msg for cue in BOTH_TIME_KEYWORDS) and len(compare_targets) < 2:
            return False
        # 结构证据优先：抽到 >=2 个可比目标就是"从一堆里挑"，直接判 compare，
        # 不再强制命中具体比较词(COMPARE_CUES)——那是"换个问法就崩"的根。
        if len(compare_targets) >= 2:
            return True
        # 兜底：没抽到具名目标，但句子是明显的 A和B + 比较词结构。
        has_compare_cue = any(cue in msg for cue in COMPARE_CUES)
        has_ab = ("和" in msg or "与" in msg or "跟" in msg or "vs" in msg.lower())
        return has_compare_cue and has_ab

    def _is_product_judgement(self, msg: str, has_brand: bool, has_category: bool, has_name_clue: bool) -> bool:
        if not (has_name_clue or (has_brand and has_category)):
            return False
        return any(cue in msg for cue in SUITABILITY_CUES + ["怎么用", "用法", "适合什么年龄", "适合什么肤质"])

    @staticmethod
    def _is_image_identification_query(msg: str) -> bool:
        return any(cue in msg for cue in [
            "这是什么", "这款是什么", "这个是什么", "图里是什么", "图片里是什么",
            "帮我看看这是什么", "识别一下", "认一下", "是什么产品",
        ])

    def _is_no_match(self, msg: str, has_category: bool, has_brand: bool, has_concern: bool, has_image_context: bool) -> bool:
        if has_image_context:
            return False
        if any(cue in msg for cue in GREETING_CUES) and len(msg) < 10:
            return True
        has_recommend = any(cue in msg for cue in RECOMMEND_CUES)
        if not has_category and not has_brand and not has_concern and not has_recommend:
            if any(cue in msg for cue in ["我是", "我属于", "我的肤质", "预算", "以内", "以下", "左右"]):
                return True
        if not has_category and not has_brand and not has_concern and not has_recommend:
            return len(msg) < 8 and not any(prefix in msg for prefix in KNOWLEDGE_PREFIXES)
        return False

    @staticmethod
    def _has_contextual_reference(msg: str) -> bool:
        return any(cue in msg for cue in CONTEXT_REFERENCE_CUES + [
            "第一款", "第一个", "第一支", "第一瓶", "第二款", "第三款", "这款", "那款", "它",
            "这玩意", "这东西", "这个东西", "这个产品", "那个产品", "它们",
        ])

    def _is_knowledge_query(self, msg: str, has_category: bool, has_brand: bool, has_concern: bool) -> bool:
        if any(term in msg for term in ["早C晚A", "早c晚a", "早C", "晚A"]) and any(
            cue in msg for cue in ["是什么", "什么意思", "怎么理解", "怎么用", "能用", "可以"]
        ):
            return True
        if any(term in msg for term in ["玻色因", "A醇", "视黄醇", "视黄醛", "烟酰胺", "酸类", "水杨酸", "果酸", "维C"]) and any(
            cue in msg for cue in ["区别", "差异", "有什么不同", "哪个更刺激", "哪个更温和"]
        ):
            return True
        if any(cue in msg for cue in ["A醇", "视黄醇", "视黄醛", "烟酰胺", "酸类", "水杨酸", "果酸", "维C"]) and any(
            cue in msg for cue in ["白天", "晚上", "早上", "夜间", "能用", "可以用", "怎么用", "一起用", "叠加", "混用"]
        ):
            return True
        if any(cue in msg for cue in INGREDIENT_CUES + ["烟酰胺", "酸类", "A醇", "视黄醇", "维C"]) and any(
            cue in msg for cue in ["一起用", "同时用", "叠加", "同用", "混用", "搭配", "能不能一起", "可以一起"]
        ):
            return True
        if any(msg.startswith(prefix) for prefix in KNOWLEDGE_PREFIXES):
            return not has_category and not has_brand and not has_concern
        if any(cue in msg for cue in KNOWLEDGE_CUES):
            return not has_category and not has_brand
        if "怎么" in msg and not any(cue in msg for cue in ["选", "买", "推荐"]):
            return not has_category and not has_brand
        if "是什么" in msg and not has_concern:
            return True
        return False

    @staticmethod
    def _has_strong_suitability_signal(msg: str) -> bool:
        return any(cue in msg for cue in [
            "敏感肌", "敏感", "敏皮", "油皮", "干皮", "混油", "混干", "孕妇",
            "泛红", "屏障", "闷痘", "爆痘", "过敏", "翻车", "别翻车",
            "温和", "更温和", "低刺激", "刺激小",
            "能用吗", "可以用吗", "能不能用", "适不适合",
        ])
