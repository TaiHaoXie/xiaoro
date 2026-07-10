import asyncio
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace

config_stub = types.ModuleType("app.config")
config_stub.settings = SimpleNamespace()
config_stub.get_settings = lambda: config_stub.settings
sys.modules.setdefault("app.config", config_stub)

from app.services.agent import ShoppingAgent
from app.services.conversation import TurnState


class FakeConversationService:
    def __init__(self):
        self.finalized = None

    async def get_context(self, session_id):
        return SimpleNamespace(
            session_id=session_id,
            messages=[],
            turn_state=TurnState(),
            get_profile_hint=lambda: "",
        )

    async def finalize_turn(self, **kwargs):
        self.finalized = kwargs


class FakeRagRetriever:
    async def retrieve_for_turn(self, turn):
        product = {
            "id": 1,
            "name": "薇诺娜清透防晒乳",
            "brand": "薇诺娜",
            "category": "护肤",
            "subcategory": "防晒",
            "price": 88.0,
            "description": "敏感肌可用的清透防晒。",
            "image_url": "",
            "detail_url": "",
            "relevance": 91,
        }
        return {
            "products": [product],
            "knowledge": [],
            "citations": [{"id": 1, "title": "薇诺娜清透防晒乳"}],
            "pitfalls": [{"title": "敏感肌先试用", "severity": "中", "description": "先做局部测试"}],
            "anchor_product": product if turn.active_anchor else None,
        }


def make_agent():
    agent = ShoppingAgent.__new__(ShoppingAgent)
    agent.conversation_service = FakeConversationService()
    agent.rag_retriever = FakeRagRetriever()
    agent._turn_handle_shopping = lambda *args, **kwargs: _async_tuple(
        "推荐薇诺娜清透防晒乳，价格88元，适合敏感肌日常通勤。",
        args[1],
    )
    agent._turn_handle_knowledge = lambda *args, **kwargs: _async_value("防晒卸妆要看是否防水。")
    agent._sanitize_recommendation_copy = lambda text: text
    agent._response_violates_candidate_guard = lambda response, products: (False, [])
    return agent


async def _async_tuple(*items):
    return items


async def _async_value(value):
    return value


def collect_events(agent, message, image_results=None):
    async def _collect():
        return [
            item
            async for item in agent.chat_turn_stream(
                message=message,
                session_id="contract-session",
                image_results=image_results,
            )
        ]

    return asyncio.run(_collect())


def event_names(events):
    return [item["event"] for item in events]


class UnifiedChatContractTest(unittest.TestCase):
    def test_chat_turn_stream_uses_legacy_sse_contract_for_recommendation(self):
        agent = make_agent()

        events = collect_events(agent, "推荐300以内适合敏感肌的防晒")

        names = event_names(events)
        self.assertEqual(names[0], "stage")
        self.assertIn("intent", names)
        self.assertIn("products", names)
        self.assertIn("citations", names)
        self.assertIn("pitfalls", names)
        self.assertIn("message", names)
        self.assertEqual(names[-1], "end")
        self.assertNotIn("turn_start", names)
        self.assertNotIn("thinking", names)
        self.assertNotIn("done", names)

        intent = next(item for item in events if item["event"] == "intent")["data"]
        self.assertEqual(intent["intent"], "product_recommend")
        self.assertEqual(intent["entities"]["budget"], 300)
        self.assertIn("敏感肌", intent["entities"]["skin_types"])
        self.assertTrue(intent["entities"]["category"].endswith("防晒"))

        message_event = next(item for item in events if item["event"] == "message")
        self.assertFalse(message_event["data"]["done"])
        self.assertNotIn("products", message_event["data"])
        self.assertIsNotNone(agent.conversation_service.finalized)

    def test_chat_turn_stream_resolves_image_followup_on_backend(self):
        agent = make_agent()
        image_results = [{
            "product_id": "1",
            "name": "薇诺娜清透防晒乳",
            "brand": "薇诺娜",
            "category": "护肤",
            "subcategory": "防晒",
            "price": 88,
            "similarity": 86,
        }]

        events = collect_events(agent, "这款适合敏感肌吗", image_results=image_results)

        intent = next(item for item in events if item["event"] == "intent")["data"]
        self.assertEqual(intent["intent"], "product_detail")
        self.assertEqual(intent["entities"]["brand"], "薇诺娜")
        self.assertEqual(intent["entities"]["product_name"], "薇诺娜清透防晒乳")
        self.assertTrue(next(item for item in events if item["event"] == "products")["data"]["products"])
        self.assertEqual(event_names(events)[-1], "end")

    def test_frontend_keeps_display_only_and_does_not_make_image_decisions(self):
        html = Path("app/static/chat.html").read_text(encoding="utf-8")

        self.assertNotIn("resolveImageTurnPolicy", html)
        self.assertNotIn("buildLocalImageIdentificationReply", html)
        self.assertNotIn("buildLocalImagePriceReply", html)
        self.assertNotIn("buildLocalImageJudgementReply", html)
        self.assertNotIn("composeImagePrompt", html)
        self.assertIn("image_results", html)


if __name__ == "__main__":
    unittest.main()
