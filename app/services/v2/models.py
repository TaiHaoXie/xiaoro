from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from enum import Enum


class AnswerMode(str, Enum):
    RECOMMENDATION = "recommendation"
    FOLLOWUP = "followup"
    COMPARE = "compare"
    KNOWLEDGE = "knowledge"
    JUDGEMENT = "judgement"
    NO_MATCH = "no_match"


class FollowupType(str, Enum):
    PRICE = "price"
    INGREDIENT = "ingredient"
    EFFICACY = "efficacy"
    SUITABILITY = "suitability"
    USAGE_TIME = "usage_time"
    CHEAPER = "cheaper"
    HIGHER_BUDGET = "higher_budget"
    MORE_OPTIONS = "more_options"
    OTHER = "other"


class FollowupScope(str, Enum):
    IN_CANDIDATES = "in_candidates"
    OUT_OF_CANDIDATES = "out_of_candidates"
    AMBIGUOUS = "ambiguous"


@dataclass
class SemanticIntent:
    answer_mode: AnswerMode
    confidence: float = 0.8
    reason: str = ""
    followup_type: Optional[FollowupType] = None
    usage_time_focus: Optional[str] = None
    needs_history: bool = False
    matched_example: Optional[str] = None
    vector_score: Optional[float] = None
    secondary_intents: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class CanonicalTurn:
    raw_message: str
    session_id: Optional[str] = None
    conversation_history: List[Dict[str, Any]] = field(default_factory=list)
    image_context: Optional[Dict[str, Any]] = None
    session_state: Dict[str, Any] = field(default_factory=dict)

    intent: Optional[AnswerMode] = None
    category: Optional[str] = None
    brand: Optional[str] = None
    image_anchor_brand: Optional[str] = None
    concerns: List[str] = field(default_factory=list)
    exclude_terms: List[str] = field(default_factory=list)
    skin_type: Optional[str] = None
    budget_min: Optional[float] = None
    budget_max: Optional[float] = None
    budget_flexible: bool = False
    compare_targets: List[str] = field(default_factory=list)
    followup_type: Optional[FollowupType] = None
    followup_scope: Optional[FollowupScope] = None
    followup_scope_reason: str = ""
    followup_judge_source: str = "rule"
    followup_judge_confidence: float = 0.0
    followup_shadow_judge: Optional[Dict[str, Any]] = None
    usage_time_focus: Optional[str] = None
    referenced_products: List[str] = field(default_factory=list)
    referenced_product_ids: List[int] = field(default_factory=list)
    name_clues: List[str] = field(default_factory=list)
    is_followup: bool = False

    knowledge_query: Optional[str] = None
    knowledge_pitfalls: List[Dict[str, Any]] = field(default_factory=list)

    user_anxiety: bool = False
    budget_drift: bool = False
    intent_confidence: float = 0.0
    intent_reason: str = ""
    matched_intent_example: Optional[str] = None
    intent_vector_score: Optional[float] = None
    secondary_intents: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class RouteDecision:
    answer_mode: AnswerMode
    confidence: float = 1.0
    reason: str = ""
    followup_type: Optional[FollowupType] = None
