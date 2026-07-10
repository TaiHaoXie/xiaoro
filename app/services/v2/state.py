from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from enum import Enum

from .models import FollowupScope


class StateVersion(str, Enum):
    V2_2 = "v2.2"


@dataclass
class ProductRef:
    product_id: int
    name: str = ""
    display_name: str = ""
    brand: str = ""
    category: str = ""
    price: Optional[float] = None

    def to_compact(self) -> Dict[str, Any]:
        return {
            "id": self.product_id,
            "name": self.name,
            "display_name": self.display_name,
            "brand": self.brand,
            "category": self.category,
            "price": self.price,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ProductRef":
        pid = d.get("id") or d.get("product_id")
        return cls(
            product_id=int(pid) if pid is not None else 0,
            name=d.get("name") or "",
            display_name=d.get("display_name") or "",
            brand=d.get("brand") or "",
            category=d.get("category") or "",
            price=d.get("price"),
        )


@dataclass
class SessionConstraints:
    category: Optional[str] = None
    skin_type: Optional[str] = None
    brand: Optional[str] = None
    budget_min: Optional[float] = None
    budget_max: Optional[float] = None
    concerns: List[str] = field(default_factory=list)
    exclude_terms: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "category": self.category,
            "skin_type": self.skin_type,
            "brand": self.brand,
            "budget_min": self.budget_min,
            "budget_max": self.budget_max,
            "concerns": list(self.concerns),
            "exclude_terms": list(self.exclude_terms),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SessionConstraints":
        return cls(
            category=d.get("category"),
            skin_type=d.get("skin_type"),
            brand=d.get("brand"),
            budget_min=d.get("budget_min"),
            budget_max=d.get("budget_max"),
            concerns=list(d.get("concerns") or []),
            exclude_terms=list(d.get("exclude_terms") or []),
        )


@dataclass
class ShoppingSessionState:
    version: str = StateVersion.V2_2.value
    current_candidate_pool: List[ProductRef] = field(default_factory=list)
    current_focus: Optional[ProductRef] = None
    comparison_set: List[ProductRef] = field(default_factory=list)
    constraints: SessionConstraints = field(default_factory=SessionConstraints)
    decision_target: Optional[str] = None
    last_scope: Optional[FollowupScope] = None
    last_answer_mode: Optional[str] = None
    last_decision_summary: Optional[str] = None
    last_winner_product_id: Optional[int] = None
    turn_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "current_candidate_pool": [p.to_compact() for p in self.current_candidate_pool],
            "current_focus": self.current_focus.to_compact() if self.current_focus else None,
            "comparison_set": [p.to_compact() for p in self.comparison_set],
            "constraints": self.constraints.to_dict(),
            "decision_target": self.decision_target,
            "last_scope": self.last_scope.value if self.last_scope else None,
            "last_answer_mode": self.last_answer_mode,
            "last_decision_summary": self.last_decision_summary,
            "last_winner_product_id": self.last_winner_product_id,
            "turn_count": self.turn_count,
        }

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "ShoppingSessionState":
        if not d:
            return cls()
        cands_raw = d.get("current_candidate_pool") or d.get("last_candidates") or []
        focus_raw = d.get("current_focus")
        comp_raw = d.get("comparison_set") or []
        constraints_raw = d.get("constraints") or {}
        if not focus_raw and cands_raw:
            focus_raw = cands_raw[0]
        winner = d.get("last_winner_product_id")
        if winner and cands_raw:
            for p in cands_raw:
                pid = p.get("id") or p.get("product_id")
                if pid == winner:
                    focus_raw = p
                    break
        focus = ProductRef.from_dict(focus_raw) if focus_raw else None
        return cls(
            version=d.get("version") or StateVersion.V2_2.value,
            current_candidate_pool=[ProductRef.from_dict(p) for p in cands_raw if p],
            current_focus=focus,
            comparison_set=[ProductRef.from_dict(p) for p in comp_raw if p],
            constraints=SessionConstraints.from_dict(constraints_raw),
            decision_target=d.get("decision_target"),
            last_scope=FollowupScope(d["last_scope"]) if d.get("last_scope") else None,
            last_answer_mode=d.get("last_answer_mode"),
            last_decision_summary=d.get("last_decision_summary"),
            last_winner_product_id=winner,
            turn_count=int(d.get("turn_count") or 0),
        )


def product_to_ref(product: Dict[str, Any]) -> Optional[ProductRef]:
    pid = product.get("id") or product.get("product_id")
    if pid is None:
        return None
    return ProductRef(
        product_id=int(pid),
        name=product.get("name") or "",
        display_name=product.get("display_name") or product.get("name") or "",
        brand=product.get("brand") or "",
        category=product.get("category") or "",
        price=product.get("price"),
    )


def build_session_state_payload(state: ShoppingSessionState) -> Dict[str, Any]:
    payload = state.to_dict()
    payload["last_candidates"] = [p.to_compact() for p in state.current_candidate_pool]
    if state.current_focus:
        payload["current_focus"] = [state.current_focus.to_compact()]
    return payload


def apply_answer_contract_to_state(
    state: ShoppingSessionState,
    products: List[Dict[str, Any]],
    answer_contract: Dict[str, Any],
    max_candidates: int = 4,
) -> ShoppingSessionState:
    compact_refs: List[ProductRef] = []
    for product in products[:max_candidates]:
        if not isinstance(product, dict):
            continue
        ref = product_to_ref(product)
        if ref:
            compact_refs.append(ref)

    if not compact_refs:
        return state

    followup_state = answer_contract.get("followup_state") if isinstance(answer_contract, dict) else {}
    if not isinstance(followup_state, dict):
        followup_state = {}

    answer_mode = str(answer_contract.get("answer_mode") or answer_contract.get("intent") or "")
    if answer_mode:
        state.last_answer_mode = answer_mode
    if len(compact_refs) >= 2 or answer_mode in {"recommendation", "compare"}:
        state.current_candidate_pool = compact_refs

    focus_ids = followup_state.get("current_focus_ids") or []
    focus_id_set = {int(pid) for pid in focus_ids if pid is not None}
    focus_ref = None
    for ref in compact_refs:
        if ref.product_id in focus_id_set:
            focus_ref = ref
            break
    if focus_ref is None and compact_refs:
        focus_ref = compact_refs[0]

    winner_id = followup_state.get("winner_product_id")
    if winner_id is not None:
        winner_int = int(winner_id)
        state.last_winner_product_id = winner_int
        state.last_decision_summary = followup_state.get("decision_summary")
        for ref in compact_refs:
            if ref.product_id == winner_int:
                focus_ref = ref
                break

    state.current_focus = focus_ref

    constraints_dict = followup_state.get("constraints") or {}
    if isinstance(constraints_dict, dict):
        for key, value in constraints_dict.items():
            if value in (None, "", [], {}):
                continue
            if key == "category":
                state.constraints.category = value
            elif key == "skin_type":
                state.constraints.skin_type = value
            elif key == "brand":
                state.constraints.brand = value
            elif key == "budget_min":
                state.constraints.budget_min = float(value)
            elif key == "budget_max":
                state.constraints.budget_max = float(value)
            elif key == "concerns" and isinstance(value, list):
                existing = set(state.constraints.concerns)
                for concern in value:
                    if concern and concern not in existing:
                        state.constraints.concerns.append(concern)
                        existing.add(concern)
            elif key == "exclude_terms" and isinstance(value, list):
                existing_excl = set(state.constraints.exclude_terms)
                for term in value:
                    if term and term not in existing_excl:
                        state.constraints.exclude_terms.append(term)
                        existing_excl.add(term)

    scope_str = followup_state.get("scope")
    if scope_str:
        try:
            state.last_scope = FollowupScope(scope_str)
        except ValueError:
            pass

    state.turn_count += 1
    return state
