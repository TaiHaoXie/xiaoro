import math
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .models import AnswerMode, FollowupType, SemanticIntent


@dataclass(frozen=True)
class IntentSample:
    text: str
    answer_mode: AnswerMode
    followup_type: Optional[FollowupType] = None
    usage_time_focus: Optional[str] = None
    needs_history: bool = False
    label: str = ""


@dataclass(frozen=True)
class RetrievalHit:
    intent: SemanticIntent
    sample: IntentSample
    score: float


class SemanticIntentRetriever:
    """In-memory vector retriever for low-confidence intent classification.

    It uses sparse character n-gram vectors. This is deliberately local and
    deterministic so backend tests do not depend on remote model availability.
    """

    def __init__(self, min_score: float = 0.46):
        self.min_score = min_score
        self.samples = self._build_samples()
        self._index = [(sample, self._vectorize(sample.text)) for sample in self.samples]

    def retrieve(self, message: str, has_history: bool, min_score: Optional[float] = None) -> Optional[RetrievalHit]:
        threshold = self.min_score if min_score is None else min_score
        query_vec = self._vectorize(message)
        if not query_vec:
            return None

        best: Optional[Tuple[IntentSample, float]] = None
        for sample, sample_vec in self._index:
            if sample.needs_history and not has_history:
                continue
            score = self._cosine(query_vec, sample_vec)
            if best is None or score > best[1]:
                best = (sample, score)

        if not best or best[1] < threshold:
            return None

        sample, score = best
        intent = SemanticIntent(
            answer_mode=sample.answer_mode,
            confidence=min(0.94, max(0.72, score)),
            reason=f"semantic_vector:{sample.label}",
            followup_type=sample.followup_type,
            usage_time_focus=sample.usage_time_focus,
            needs_history=sample.needs_history,
            matched_example=sample.text,
            vector_score=round(score, 4),
        )
        return RetrievalHit(intent=intent, sample=sample, score=score)

    def sample_stats(self) -> Dict[str, int]:
        stats: Dict[str, int] = {}
        for sample in self.samples:
            stats[sample.label] = stats.get(sample.label, 0) + 1
        return stats

    def _vectorize(self, text: str) -> Dict[str, float]:
        normalized = self._normalize_text(text)
        if not normalized:
            return {}

        features: Dict[str, float] = {}

        def add(feature: str, weight: float) -> None:
            features[feature] = features.get(feature, 0.0) + weight

        cjk_chars = re.findall(r"[\u4e00-\u9fff]", normalized)
        ascii_tokens = re.findall(r"[a-z0-9]+", normalized)

        for token in ascii_tokens:
            add(f"ascii:{token}", 1.0)

        for ch in cjk_chars:
            add(f"c1:{ch}", 0.35)
        for i in range(len(cjk_chars) - 1):
            add(f"c2:{''.join(cjk_chars[i:i + 2])}", 1.0)
        for i in range(len(cjk_chars) - 2):
            add(f"c3:{''.join(cjk_chars[i:i + 3])}", 1.25)

        for keyword in SEMANTIC_KEYWORDS:
            if keyword in normalized:
                add(f"kw:{keyword}", 2.0)

        norm = math.sqrt(sum(value * value for value in features.values()))
        if norm == 0:
            return features
        return {key: value / norm for key, value in features.items()}

    @staticmethod
    def _cosine(left: Dict[str, float], right: Dict[str, float]) -> float:
        if len(left) > len(right):
            left, right = right, left
        return sum(value * right.get(key, 0.0) for key, value in left.items())

    @staticmethod
    def _normalize_text(text: str) -> str:
        return re.sub(r"\s+", "", (text or "").lower().strip())

    def _build_samples(self) -> List[IntentSample]:
        samples: List[IntentSample] = []

        def add_group(label: str, phrases: List[str], mode: AnswerMode,
                      followup_type: Optional[FollowupType] = None,
                      usage_time_focus: Optional[str] = None,
                      needs_history: bool = False) -> None:
            for phrase in phrases[:50]:
                samples.append(IntentSample(
                    text=phrase,
                    answer_mode=mode,
                    followup_type=followup_type,
                    usage_time_focus=usage_time_focus,
                    needs_history=needs_history,
                    label=label,
                ))

        add_group("recommendation", _recommendation_samples(), AnswerMode.RECOMMENDATION)
        add_group("compare", _compare_samples(), AnswerMode.COMPARE)
        add_group("judgement", _judgement_samples(), AnswerMode.JUDGEMENT)
        add_group("knowledge", _knowledge_samples(), AnswerMode.KNOWLEDGE)
        add_group("followup_price", _followup_price_samples(), AnswerMode.FOLLOWUP, FollowupType.PRICE, needs_history=True)
        add_group("followup_ingredient", _followup_ingredient_samples(), AnswerMode.FOLLOWUP, FollowupType.INGREDIENT, needs_history=True)
        add_group("followup_efficacy", _followup_efficacy_samples(), AnswerMode.FOLLOWUP, FollowupType.EFFICACY, needs_history=True)
        add_group("followup_suitability", _followup_suitability_samples(), AnswerMode.FOLLOWUP, FollowupType.SUITABILITY, needs_history=True)
        add_group("followup_cheaper", _followup_cheaper_samples(), AnswerMode.FOLLOWUP, FollowupType.CHEAPER, needs_history=True)
        add_group("followup_usage_day", _followup_usage_day_samples(), AnswerMode.FOLLOWUP, FollowupType.USAGE_TIME, "day", True)
        add_group("followup_usage_night", _followup_usage_night_samples(), AnswerMode.FOLLOWUP, FollowupType.USAGE_TIME, "night", True)
        add_group("followup_usage_both", _followup_usage_both_samples(), AnswerMode.FOLLOWUP, FollowupType.USAGE_TIME, "both", True)
        return samples


SEMANTIC_KEYWORDS = [
    "白天", "日间", "早上", "出门", "上班", "通勤", "妆前",
    "晚上", "夜间", "睡前", "睡觉前", "晚间", "熬夜",
    "早晚", "日夜", "分别", "分开", "搭配",
    "便宜", "平价", "压预算", "省钱", "不想花太多", "预算低", "没那么贵", "少花点",
    "敏感", "敏皮", "泛红", "刺痛", "更稳", "不刺激",
    "成分", "配方", "含什么", "核心成分",
    "价格", "多少钱", "价位", "贵不贵",
    "对比", "比较", "区别", "差异", "哪个好",
    "推荐", "想买", "帮我选", "适合",
]


def _fill_templates(starts: List[str], middles: List[str], ends: List[str], limit: int = 50) -> List[str]:
    out: List[str] = []
    for start in starts:
        for middle in middles:
            for end in ends:
                phrase = f"{start}{middle}{end}"
                if phrase not in out:
                    out.append(phrase)
                if len(out) >= limit:
                    return out
    return out


def _recommendation_samples() -> List[str]:
    return _fill_templates(
        ["我想买", "帮我挑", "求推荐", "想找", "预算有限想买"],
        ["面霜", "防晒", "精华", "洗面奶", "眼霜"],
        ["有什么合适的", "哪款靠谱", "怎么选", "给我几款", "适合日常用的"],
    )


def _compare_samples() -> List[str]:
    return _fill_templates(
        ["理肤泉和玉泽", "小棕瓶和小黑瓶", "兰蔻小白管和安热沙", "这两个", "A和B"],
        ["哪个更好", "怎么选", "有什么区别", "帮我对比", "差别大吗"],
        ["", "一点", "适合我", "更值得买", "别太简单"],
    )


def _judgement_samples() -> List[str]:
    return _fill_templates(
        ["小棕瓶", "理肤泉B5", "珀莱雅防晒", "兰蔻小白管", "这个产品"],
        ["敏感肌能用吗", "油皮可以吗", "适合什么年龄", "怎么用", "会不会闷痘"],
        ["", "呀", "帮我判断", "说实话", "别只推荐"],
    )


def _knowledge_samples() -> List[str]:
    return _fill_templates(
        ["什么是", "解释一下", "科普一下", "为什么", "我想了解"],
        ["烟酰胺", "玻色因", "A醇", "防晒指数", "屏障修护"],
        ["", "是什么", "有什么用", "适合谁", "怎么理解"],
    )


def _followup_price_samples() -> List[str]:
    return _fill_templates(
        ["刚才那款", "这几个里面", "上面推荐的", "第一款", "它"],
        ["多少钱", "价格多少", "贵不贵", "价位大概多少", "活动价多少"],
        ["", "呀", "帮我看下", "能说清楚点吗", "预算压力大吗"],
    )


def _followup_ingredient_samples() -> List[str]:
    return _fill_templates(
        ["这款", "刚才那几个", "上面这些", "第一瓶", "它"],
        ["核心成分是什么", "主要靠什么成分", "配方有什么", "有没有刺激成分", "含什么"],
        ["", "呀", "帮我拆一下", "说人话", "重点讲"],
    )


def _followup_efficacy_samples() -> List[str]:
    manual = [
        "它主要功效是什么",
        "这款主打什么效果",
        "它能干嘛",
        "管什么用的",
        "主要作用是什么",
        "它是干什么用的",
        "有啥用",
        "有什么效果",
    ]
    generated = _fill_templates(
        ["这款", "它", "第三款", "第二个", "这瓶"],
        ["主要功效", "主打什么", "核心作用", "能解决什么问题", "什么效果", "有啥作用"],
        ["", "呀", "讲一下", "说说", "是什么"],
    )
    return (manual + [item for item in generated if item not in manual])[:50]


def _followup_suitability_samples() -> List[str]:
    return _fill_templates(
        ["如果我是敏皮", "我脸容易红", "屏障不太稳", "油皮通勤", "干敏肌"],
        ["这里面哪个更稳", "这几个谁更不刺激", "哪款更适合我", "哪个不容易翻车", "哪个风险低"],
        ["", "一点", "帮我选", "别太猛", "日常用"],
    )


def _followup_cheaper_samples() -> List[str]:
    manual = [
        "顺便有没有没那么贵的",
        "有没有不那么贵的选择",
        "想少花点从刚才里面选谁",
        "不想花太多有没有替代",
        "有没有价格没那么肉疼的",
    ]
    generated = _fill_templates(
        ["不想花太多的话", "想压预算的话", "预算再低一点", "学生党一点", "想省钱"],
        ["从刚才里面挑哪支", "这几款里哪个更平价", "有没有便宜点的", "哪个性价比更高", "有没有低价替代"],
        ["", "呀", "帮我选", "别太贵", "更划算"],
    )
    return (manual + [item for item in generated if item not in manual])[:50]


def _followup_usage_day_samples() -> List[str]:
    return _fill_templates(
        ["白天用的话", "早上出门前", "上班通勤", "妆前想用", "日间护肤"],
        ["这几款哪个更合适", "三款里谁更省心", "哪个不容易刺激", "哪瓶更适合", "留哪一瓶"],
        ["", "一点", "帮我选", "别厚重", "后面还要防晒"],
    )


def _followup_usage_night_samples() -> List[str]:
    manual = [
        "睡觉前只留一瓶该留谁",
        "睡觉前只留一瓶选谁",
        "晚上只想用一瓶留哪瓶",
        "夜间护肤只保留一个选哪个",
        "睡前只用一个三瓶里留谁",
    ]
    generated = _fill_templates(
        ["晚上用的话", "睡前护肤", "睡觉前只留一瓶", "夜间修护", "晚间想抗老"],
        ["这几款哪个更合适", "三瓶里留哪瓶", "选谁更稳", "哪瓶更适合", "哪个夜间效果更顺"],
        ["", "一点", "帮我选", "别刺激", "低频用"],
    )
    return (manual + [item for item in generated if item not in manual])[:50]


def _followup_usage_both_samples() -> List[str]:
    return _fill_templates(
        ["白天晚上", "早晚", "日间夜间", "白天和夜间", "早上和睡前"],
        ["怎么分开用", "分别怎么选", "怎么搭配", "各用哪一个", "怎么安排"],
        ["", "更合理", "不刺激", "适合干敏肌", "给个顺序"],
    )
