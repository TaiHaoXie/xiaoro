from app.services.v2.models import FollowupScope
from app.services.v2.state import (
    ProductRef,
    ShoppingSessionState,
    build_session_state_payload,
    apply_answer_contract_to_state,
)


def product(pid, brand="品牌", category="面霜", price=100.0):
    return {
        "id": pid,
        "name": f"{brand}{pid}",
        "display_name": f"{brand}{pid}",
        "brand": brand,
        "category": category,
        "price": price,
    }


def test_build_session_state_payload_keeps_legacy_aliases():
    state = ShoppingSessionState(
        current_candidate_pool=[
            ProductRef(product_id=1, name="A"),
            ProductRef(product_id=2, name="B"),
        ],
        current_focus=ProductRef(product_id=2, name="B"),
    )

    payload = build_session_state_payload(state)

    assert [p["id"] for p in payload["current_candidate_pool"]] == [1, 2]
    assert [p["id"] for p in payload["last_candidates"]] == [1, 2]
    assert [p["id"] for p in payload["current_focus"]] == [2]


def test_apply_recommendation_contract_updates_pool_focus_and_constraints():
    state = ShoppingSessionState()
    contract = {
        "answer_mode": "recommendation",
        "followup_state": {
            "current_focus_ids": [2],
            "constraints": {"category": "面霜", "skin_type": "敏感肌", "budget_max": 300},
        },
    }

    updated = apply_answer_contract_to_state(state, [product(1), product(2), product(3)], contract)

    assert [p.product_id for p in updated.current_candidate_pool] == [1, 2, 3]
    assert updated.current_focus and updated.current_focus.product_id == 2
    assert updated.constraints.category == "面霜"
    assert updated.constraints.skin_type == "敏感肌"
    assert updated.constraints.budget_max == 300
    assert updated.last_answer_mode == "recommendation"
    assert updated.turn_count == 1


def test_apply_single_followup_keeps_previous_candidate_pool():
    state = ShoppingSessionState(
        current_candidate_pool=[ProductRef(product_id=1), ProductRef(product_id=2), ProductRef(product_id=3)]
    )
    contract = {
        "answer_mode": "followup",
        "followup_state": {"current_focus_ids": [2], "scope": "in_candidates"},
    }

    updated = apply_answer_contract_to_state(state, [product(2)], contract)

    assert [p.product_id for p in updated.current_candidate_pool] == [1, 2, 3]
    assert updated.current_focus and updated.current_focus.product_id == 2
    assert updated.last_scope == FollowupScope.IN_CANDIDATES
    assert updated.last_answer_mode == "followup"


def test_apply_winner_metadata_prefers_winner_focus():
    state = ShoppingSessionState(current_candidate_pool=[ProductRef(product_id=1), ProductRef(product_id=2)])
    contract = {
        "answer_mode": "compare",
        "followup_state": {
            "current_focus_ids": [1],
            "winner_product_id": 2,
            "decision_summary": "按油皮优先选B",
        },
    }

    updated = apply_answer_contract_to_state(state, [product(1), product(2)], contract)

    assert updated.current_focus and updated.current_focus.product_id == 2
    assert updated.last_winner_product_id == 2
    assert updated.last_decision_summary == "按油皮优先选B"
