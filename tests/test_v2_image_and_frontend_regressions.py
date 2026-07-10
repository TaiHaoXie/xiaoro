import unittest
import re
import sys
import types
import asyncio
from pathlib import Path
from types import SimpleNamespace

openai_stub = types.ModuleType("openai")
openai_stub.AsyncOpenAI = lambda *args, **kwargs: SimpleNamespace()
openai_stub.OpenAI = lambda *args, **kwargs: SimpleNamespace()
sys.modules.setdefault("openai", openai_stub)

config_stub = types.ModuleType("app.config")
config_stub.settings = SimpleNamespace()
config_stub.get_settings = lambda: config_stub.settings
sys.modules.setdefault("app.config", config_stub)

llm_stub = types.ModuleType("app.services.llm")
llm_stub.LLMService = object
llm_stub.get_llm_service = lambda: None
sys.modules.setdefault("app.services.llm", llm_stub)

embedding_stub = types.ModuleType("app.services.embedding")
embedding_stub.EmbeddingService = object
embedding_stub.get_embedding_service = lambda: None
sys.modules.setdefault("app.services.embedding", embedding_stub)

intent_stub = types.ModuleType("app.services.intent")
intent_stub.IntentService = object
intent_stub.IntentResult = object
intent_stub.IntentType = object
intent_stub.get_intent_service = lambda: None
sys.modules.setdefault("app.services.intent", intent_stub)

postgres_stub = types.ModuleType("app.database.postgres")
async def _execute_query_stub(*args, **kwargs):
    return []
postgres_stub.execute_query = _execute_query_stub
sys.modules.setdefault("app.database.postgres", postgres_stub)

from app.services.v2.models import AnswerMode, CanonicalTurn, FollowupType
from app.services.v2.agent import V2ShoppingAgent
from app.services.v2.presenter import Presenter
from app.services.v2.retriever import Retriever
from app.services.v2.turn_parser import TurnParser


class V2ImageAndFrontendRegressionTest(unittest.TestCase):
    def test_image_identification_base_makeup_uses_feature_caution_not_generic(self):
        turn = CanonicalTurn(
            raw_message="这款是什么",
            session_id="test-image-base-makeup",
            image_context={
                "results": [{
                    "id": 101,
                    "brand": "MAC",
                    "category": "粉底液",
                    "similarity": 57.6,
                }],
                "brand": "MAC",
                "category": "粉底液",
                "policy": "image_identification",
            },
        )
        turn.intent = AnswerMode.JUDGEMENT
        turn.category = "粉底液"
        turn.brand = "MAC"

        product = {
            "id": 101,
            "name": "M.A.C魅可全新升级无瑕粉底液持妆不脱妆#NC15",
            "display_name": "MAC无瑕粉底液",
            "brand": "MAC",
            "category": "粉底液",
            "price_val": 360,
            "positioning": "持妆遮瑕粉底液，偏自然服帖妆效。",
            "description": "持妆不脱妆，遮瑕，防汗水，NC15色号。",
            "suitable_skin": "多种肤质适用",
            "concerns_list": ["遮瑕", "防汗水"],
            "key_ingredients_list": [],
            "pitfalls": "",
        }

        result = Presenter()._present_image_identification(turn, product)
        text = result["text"]
        pitfall_text = " ".join(item["description"] for item in result["pitfalls"])

        self.assertNotIn("下单前确认规格、实时价格和使用场景", text)
        self.assertNotIn("商品库价格是入库参考价", text)
        self.assertRegex(text + pitfall_text, r"色号|试妆|持妆|卡粉|闷痘|卸妆")

    def test_image_judgement_anchor_uses_compact_name_without_dangling_spec_digit(self):
        raw_name = "玉兰油（OLAY）第4代淡斑小白瓶40ml面部精华液烟酰胺美白淡斑"
        turn = CanonicalTurn(
            raw_message="这款是什么，敏感肌能用吗",
            session_id="test-image-anchor-short-name",
            image_context={
                "results": [{
                    "id": 201,
                    "name": raw_name,
                    "display_name": raw_name,
                    "brand": "玉兰油",
                    "category": "精华",
                    "similarity": 88.0,
                }],
                "policy": "image_identification",
            },
        )
        turn.intent = AnswerMode.JUDGEMENT
        product = {
            "id": 201,
            "name": raw_name,
            "display_name": raw_name,
            "brand": "玉兰油",
            "category": "精华",
            "price_val": 350,
            "positioning": "OLAY淡斑小白瓶，5%烟酰胺主打淡斑美白。",
            "suitable_skin": "油皮/混油/暗沉肌；烟酰胺不耐受需先建立耐受",
            "concerns_list": ["美白淡斑", "提亮肤色"],
            "key_ingredients_list": ["烟酰胺", "酰本胺"],
        }

        text = Presenter().present_judgement(turn, [product])["text"]

        self.assertIn("匹配度最高的是玉兰油（OLAY）第4代淡斑小白瓶。", text)
        self.assertNotIn("匹配度最高的是玉兰油（OLAY）第4代淡斑小白瓶4。", text)

    def test_frontend_has_send_guard_and_typewriter_rendering(self):
        html = Path("app/static/chat.html").read_text(encoding="utf-8")

        self.assertIn("let isSendingMessage = false", html)
        self.assertIn("function setSendingState", html)
        self.assertIn("if (isSendingMessage) return", html)
        self.assertIn("sendBtn.disabled = isSendingMessage", html)
        self.assertIn("function renderAssistantTextWithTypewriter", html)
        self.assertIn("await renderAssistantTextWithTypewriter", html)

    def test_frontend_product_cards_use_match_reason_not_raw_description(self):
        html = Path("app/static/chat.html").read_text(encoding="utf-8")

        self.assertIn("function getProductCardReason", html)
        self.assertIn("product?._match_reason?.reasons", html)
        self.assertIn("const reason = getProductCardReason(p)", html)
        self.assertNotIn("const reason = p.rerank_reason || p.description", html)

    def test_frontend_product_shelf_uses_compact_name(self):
        html = Path("app/static/chat.html").read_text(encoding="utf-8")

        self.assertIn("display_name: product.display_name", html)
        self.assertIn("getCompactProductDisplayName(item)", html)
        self.assertNotIn("product-mini-title\">${escapeHtml(item.name || '推荐商品')}</div>", html)

    def test_frontend_product_price_uses_spec_when_available(self):
        html = Path("app/static/chat.html").read_text(encoding="utf-8")

        self.assertIn("const spec = String(product?.spec || '')", html)
        self.assertIn("return spec ? `${priceText} / ${spec}` : priceText", html)

    def test_llm_product_facts_keep_price_spec_together(self):
        agent = V2ShoppingAgent.__new__(V2ShoppingAgent)

        facts = agent._build_llm_product_facts(
            {
                "name": "雅诗兰黛小棕瓶精华露50ml抗老紧致护肤品套装",
                "brand": "雅诗兰黛",
                "category": "精华",
                "price": 968,
                "_specs": {"规格": "50ml"},
            },
            1,
        )

        self.assertEqual(facts["参考价"], "约¥968 / 50ml")
        self.assertEqual(facts["规格"], "50ml")

    def test_llm_product_facts_use_compact_name_not_raw_marketplace_title(self):
        agent = V2ShoppingAgent.__new__(V2ShoppingAgent)

        facts = agent._build_llm_product_facts(
            {
                "name": "玉泽（Dr.Yu）精华乳皮肤屏障修护精华乳50ml补水保湿舒缓修护护肤品敏感肌可用 精华乳25ml",
                "display_name": "玉泽（Dr.Yu）精华乳皮肤屏障修护精华乳50ml补水保湿舒缓修护护肤品敏感肌可用 精华乳25ml",
                "brand": "玉泽",
                "category": "精华",
                "price": 88,
            },
            1,
        )

        # 喂给 LLM 的名称必须收敛，不能是一长串带营销词/规格堆叠的原始标题
        self.assertLessEqual(len(facts["名称"]), 22)
        self.assertNotIn("补水保湿", facts["名称"])
        self.assertNotIn("敏感肌可用", facts["名称"])
        self.assertNotIn("护肤品", facts["名称"])

    def test_llm_product_facts_extract_spec_from_name_when_specs_missing(self):
        agent = V2ShoppingAgent.__new__(V2ShoppingAgent)

        facts = agent._build_llm_product_facts(
            {
                "name": "修丽可紫米精华30ml 护肤品玻色因抗皱抗老淡纹紧致",
                "brand": "修丽可",
                "category": "精华",
                "price_val": 1050,
                "_specs": {},
            },
            2,
        )

        self.assertEqual(facts["参考价"], "约¥1050 / 30ml")
        self.assertEqual(facts["规格"], "30ml")

    def test_presenter_recommendation_uses_locked_product_field_labels(self):
        turn = CanonicalTurn(
            raw_message="300以内面霜推荐",
            category="面霜",
            budget_max=300,
        )
        products = [
            {
                "name": "玉泽皮肤屏障修护保湿霜50g",
                "display_name": "玉泽皮肤屏障修护面霜保湿霜干敏肌保湿改善泛红补水缓解干燥舒缓",
                "brand": "玉泽",
                "category": "面霜",
                "price": 139,
                "description": "定位：屏障修护面霜\n适合肤质：干敏皮\n核心成分：神经酰胺、角鲨烷",
                "key_ingredients_list": ["神经酰胺", "角鲨烷"],
                "concerns_list": ["屏障修护"],
            }
        ]

        text = Presenter().present_recommendation(turn, products)["text"]

        self.assertIn("- 参考价：", text)
        self.assertIn("- 核心成分：", text)
        self.assertIn("- 注意点：", text)
        self.assertIn("## 综合建议", text)
        self.assertNotIn("参考价格", text)
        self.assertNotIn("核心功效", text)
        self.assertNotIn("注意事项", text)

    def test_presenter_recommendation_returns_three_product_cards(self):
        turn = CanonicalTurn(raw_message="干敏肌抗初老精华，预算1000左右", category="精华")
        products = [
            {"id": i, "name": f"测试精华{i}", "brand": "测试", "category": "精华", "price": 900 + i}
            for i in range(1, 5)
        ]

        result = Presenter().present_recommendation(turn, products)

        self.assertEqual(len(result["products"]), 3)
        self.assertEqual([item["id"] for item in result["products"]], [1, 2, 3])

    def test_presenter_recommendation_price_keeps_spec_from_product_name(self):
        turn = CanonicalTurn(
            raw_message="干敏肌1000左右抗初老精华",
            category="精华",
            skin_type="干敏肌",
            budget_min=800,
            budget_max=1200,
        )
        products = [
            {
                "name": "雅诗兰黛小棕瓶精华露50ml抗老紧致护肤品套装",
                "display_name": "雅诗兰黛小棕瓶精华露50ml抗老紧致护肤品套装",
                "brand": "雅诗兰黛",
                "category": "精华",
                "price": 968,
                "description": "定位：第七代小棕瓶特润修护肌活精华露",
                "key_ingredients_list": ["二裂酵母", "三肽-32"],
                "concerns_list": ["抗老淡纹"],
            }
        ]

        text = Presenter().present_recommendation(turn, products)["text"]

        self.assertIn("- 参考价：约¥968 / 50ml", text)

    def test_presenter_recommendation_uses_compact_title_not_raw_marketplace_title(self):
        turn = CanonicalTurn(
            raw_message="300以内精华推荐",
            category="精华",
            budget_max=300,
        )
        raw_title = "玉泽（Dr.Yu）精华乳皮肤屏障修护精华乳50ml补水保湿舒缓修护护肤品敏感肌可用 精华乳25ml"
        raw_title_2 = "珀莱雅双抗焕白净亮精华液3.0双抗精华30ml补水保湿提亮护肤品"
        products = [
            {
                "name": raw_title,
                "display_name": raw_title,
                "brand": "玉泽",
                "category": "精华",
                "price": 88,
                "description": "定位：屏障修护精华乳\n适合肤质：敏感肌\n核心成分：神经酰胺、角鲨烷",
                "key_ingredients_list": ["神经酰胺", "角鲨烷"],
                "concerns_list": ["屏障修护"],
            },
            {
                "name": raw_title_2,
                "display_name": raw_title_2,
                "brand": "珀莱雅",
                "category": "精华",
                "price": 99,
                "description": "定位：提亮抗氧精华\n适合肤质：多数肤质\n核心成分：麦角硫因、虾青素",
                "key_ingredients_list": ["麦角硫因", "虾青素"],
                "concerns_list": ["提亮", "抗氧"],
            }
        ]

        text = Presenter().present_recommendation(turn, products)["text"]

        self.assertNotIn("补水保湿舒缓修护护肤品", text)
        self.assertNotIn("敏感肌可用 精华乳25ml", text)
        self.assertNotIn("补水保湿提亮护肤品", text)
        self.assertIn("玉泽（Dr.Yu）精华", text)

    def test_followup_secondary_supplement_does_not_repeat_supplement_label(self):
        turn = CanonicalTurn(
            raw_message="除了上面这些，有没有价格高些的",
            followup_type=FollowupType.MORE_OPTIONS,
            secondary_intents=[
                {"followup_type": FollowupType.HIGHER_BUDGET.value},
                {"followup_type": FollowupType.PRICE.value},
            ],
        )
        products = [{"name": "兰蔻轻透水漾防晒", "brand": "兰蔻", "price": 329}]

        supplement = Presenter()._build_secondary_followup_supplement(turn, products)

        self.assertNotIn("补充：", supplement)
        self.assertNotIn("补充：预算补充：", supplement)
        self.assertNotIn("价格补充：", supplement)
        self.assertIn("预算上探", supplement)

    def test_no_match_skin_profile_statement_acknowledges_skin_not_ask_for_skin(self):
        turn = CanonicalTurn(
            raw_message="我是油敏肌",
            skin_type="油敏肌",
        )

        text = Presenter().present_no_match(turn)["text"]

        self.assertIn("油敏肌", text)
        self.assertNotIn("补充一下肤质", text)

    def test_skin_and_budget_profile_statement_is_not_recommendation(self):
        turn = TurnParser().parse("我是干敏肌，预算300以内")

        self.assertEqual(turn.intent, AnswerMode.NO_MATCH)
        self.assertEqual(turn.skin_type, "干敏肌")
        self.assertEqual(turn.budget_max, 300)

    def test_recommendation_inherits_budget_from_profile_statement(self):
        history = [{"role": "user", "content": "我是干敏肌，预算300以内"}]

        turn = TurnParser().parse("想要修护面霜", conversation_history=history)

        self.assertEqual(turn.intent, AnswerMode.RECOMMENDATION)
        self.assertEqual(turn.skin_type, "干敏肌")
        self.assertEqual(turn.budget_max, 300)
        self.assertIsNone(turn.budget_min)

    def test_redness_recommendation_infers_sensitive_skin_and_chinese_budget(self):
        turn = TurnParser().parse("我就是换季容易红，预算三百以内，想找个修护面霜")

        self.assertEqual(turn.intent, AnswerMode.RECOMMENDATION)
        self.assertEqual(turn.category, "面霜")
        self.assertEqual(turn.skin_type, "敏感肌")
        self.assertEqual(turn.budget_max, 300)

    def test_ingredient_name_usage_followup_is_ingredient(self):
        history = [{
            "role": "assistant",
            "content": "雅诗兰黛小棕瓶\n赫莲娜绿宝瓶",
        }]

        turn = TurnParser().parse("第一款咖啡因有什么用", conversation_history=history)

        self.assertEqual(turn.intent, AnswerMode.FOLLOWUP)
        self.assertEqual(turn.followup_type, FollowupType.INGREDIENT)
        self.assertEqual(turn.referenced_products, ["小棕瓶"])

    def test_summary_keeps_ct50_product_short_name_readable(self):
        presenter = Presenter()
        products = [
            {
                "name": "玉泽皮肤屏障修护精华乳",
                "display_name": "玉泽（Dr.Yu）精华乳",
                "brand": "玉泽",
                "category": "精华",
                "price": 88,
                "concerns_list": ["屏障修护"],
            },
            {
                "name": "夸迪蓝金能量炮CT50悬油次抛2.0精华液",
                "display_name": "夸迪蓝金能量炮CT50悬油次抛2.0精华液",
                "brand": "夸迪",
                "category": "精华",
                "price": 383.74,
                "concerns_list": ["抗老淡纹"],
                "key_ingredients_list": ["CT50蓝金能量", "蓝铜胜肽"],
            },
        ]

        summary = presenter._build_summary(products, budget=None, skin_type="油敏肌", category_name="精华")

        self.assertIn("夸迪蓝金能量炮", summary)
        self.assertNotIn("夸迪0悬油", summary)

    def test_dw_foundation_alias_targets_estee_lauder_for_compare(self):
        turn = TurnParser().parse("DW粉底液和阿玛尼权力粉底哪个好")

        self.assertEqual(turn.intent, AnswerMode.COMPARE)
        self.assertIn("DW", turn.name_clues)
        self.assertEqual(turn.compare_targets[:2], ["DW", "权力"])

    def test_product_alias_overrides_contextual_category_keyword(self):
        turn = TurnParser().parse("贝德玛粉水能卸防晒吗")

        self.assertEqual(turn.brand, "贝德玛")
        self.assertEqual(turn.category, "卸妆")
        self.assertIn("粉水", turn.name_clues)

    def test_common_odd_product_names_are_canonicalized(self):
        cases = [
            ("ANR适合干敏肌吗", "雅诗兰黛", "精华", "小棕瓶"),
            ("SK2神仙水适合油皮吗", "SK-II", "精华", "神仙水"),
            ("安耐晒小金瓶适合油皮吗", "安热沙", "防晒", "小金瓶"),
            ("菌菇水适合闭口吗", "悦木之源", "爽肤水", "菌菇水"),
            ("金盏花水适合油皮吗", "科颜氏", "爽肤水", "金盏花"),
        ]

        for query, brand, category, clue in cases:
            with self.subTest(query=query):
                turn = TurnParser().parse(query)
                self.assertEqual(turn.brand, brand)
                self.assertEqual(turn.category, category)
                self.assertIn(clue, turn.name_clues)

    def test_foundation_alias_compare_without_brand_name_has_two_targets(self):
        turn = TurnParser().parse("权力粉底和DW哪个好")

        self.assertEqual(turn.intent, AnswerMode.COMPARE)
        self.assertEqual(turn.compare_targets[:2], ["权力", "DW"])

    def test_skinceuticals_product_line_compare_has_two_targets(self):
        turn = TurnParser().parse("CE精华和紫米精华哪个好")

        self.assertEqual(turn.intent, AnswerMode.COMPARE)
        self.assertEqual(turn.compare_targets[:2], ["CE", "紫米"])

    def test_followup_single_choice_returns_one_product_card(self):
        turn = CanonicalTurn(
            raw_message="哪款更适合敏感肌",
            followup_type=FollowupType.SUITABILITY,
        )
        products = [
            {"id": 1, "name": "玉泽精华", "brand": "玉泽", "price": 88, "suitable_skin": "敏感肌"},
            {"id": 2, "name": "珀莱雅双抗精华", "brand": "珀莱雅", "price": 99, "suitable_skin": "多数肤质"},
            {"id": 3, "name": "资生堂红腰子", "brand": "资生堂", "price": 288, "suitable_skin": "多数肤质"},
        ]

        result = Presenter().present_followup(turn, products)

        self.assertEqual(len(result["products"]), 1)
        self.assertEqual(result["products"][0]["id"], 1)

    def test_referenced_suitability_followup_returns_only_anchor_product_and_keeps_spec(self):
        turn = CanonicalTurn(
            raw_message="第二款对油肌友好吗",
            followup_type=FollowupType.SUITABILITY,
            referenced_products=["修丽可"],
        )
        products = [
            {
                "id": 35,
                "name": "修丽可紫米精华30ml 护肤品玻色因抗皱抗老淡纹紧致",
                "display_name": "修丽可紫米精华30ml 护肤品玻色因抗皱抗老淡纹紧致",
                "brand": "修丽可",
                "category": "精华",
                "price": 1050,
                "positioning": "12%玻色因溶液主打丰盈抗皱。",
                "suitable_skin": "干皮/混干/熟龄肌；油皮夏季可能偏润。",
            },
            {
                "id": 34,
                "name": "修丽可CE精华30ml维生素C+E紧致修护抗氧化",
                "display_name": "修丽可CE精华30ml维生素C+E紧致修护抗氧化",
                "brand": "修丽可",
                "category": "精华",
                "price": 1630,
            },
        ]

        result = Presenter().present_followup(turn, products)

        self.assertEqual([item["id"] for item in result["products"]], [35])
        self.assertIn("对油皮来说", result["text"])
        self.assertIn("参考价 约¥1050 / 30ml", result["text"])
        self.assertNotIn("修丽可CE精华", result["text"])

    def test_oily_skin_friendly_ordinal_question_is_suitability_followup(self):
        history = [{
            "role": "assistant",
            "content": "预算贴合款：雅诗兰黛小棕瓶\n进阶功效款：修丽可紫米精华\n敏感肌友好款：赫莲娜绿宝瓶",
        }]

        turn = TurnParser().parse("第二款对油肌友好吗", conversation_history=history)

        self.assertEqual(turn.intent, AnswerMode.FOLLOWUP)
        self.assertEqual(turn.followup_type, FollowupType.SUITABILITY)
        self.assertEqual(turn.skin_type, "油皮")
        self.assertEqual(turn.referenced_products, ["紫米"])

    def test_serialized_product_keeps_spec_for_frontend_card_price(self):
        agent = V2ShoppingAgent.__new__(V2ShoppingAgent)

        products = agent._serialize_products([
            {
                "id": 35,
                "name": "修丽可紫米精华30ml 护肤品玻色因抗皱抗老淡纹紧致",
                "display_name": "修丽可紫米精华30ml 护肤品玻色因抗皱抗老淡纹紧致",
                "brand": "修丽可",
                "category": "精华",
                "price": 1050,
            }
        ])

        self.assertEqual(products[0]["spec"], "30ml")

    def test_cheaper_followup_cards_match_cheaper_items_in_answer(self):
        turn = CanonicalTurn(
            raw_message="有没有平替",
            followup_type=FollowupType.CHEAPER,
        )
        products = [
            {"id": 1, "name": "贵价精华", "brand": "A", "price": 500, "display_name": "贵价精华"},
            {"id": 2, "name": "平替精华一号", "brand": "B", "price": 120, "display_name": "平替精华一号"},
            {"id": 3, "name": "平替精华二号", "brand": "C", "price": 180, "display_name": "平替精华二号"},
            {"id": 4, "name": "无关高价精华", "brand": "D", "price": 450, "display_name": "无关高价精华"},
        ]

        result = Presenter().present_followup(turn, products)

        self.assertEqual([item["id"] for item in result["products"]], [2, 3])
        self.assertIn("- B平替精华", result["text"])
        self.assertIn("- C平替精华", result["text"])
        self.assertNotIn("无关高价精华", result["text"])

    def test_cheaper_followup_reason_uses_product_data_not_only_price(self):
        turn = CanonicalTurn(
            raw_message="有没有平替",
            followup_type=FollowupType.CHEAPER,
            category="面霜",
        )
        products = [
            {"id": 1, "name": "贵价面霜", "brand": "A", "category": "面霜", "price": 1200},
            {
                "id": 2,
                "name": "理肤泉新B5面霜",
                "brand": "理肤泉",
                "category": "面霜",
                "price": 88,
                "target_users": "干燥泛红、屏障受损、需要保湿修护舒缓的人群",
                "key_ingredients_list": ["泛醇/B5", "积雪草苷"],
                "concerns_list": ["修护", "舒缓", "保湿", "干燥泛红"],
                "suitable_skin": "干燥、泛红、屏障受损肌；敏感肌/脆弱肌可参考，需按个体耐受试用",
            },
        ]

        text = Presenter().present_followup(turn, products)["text"]

        self.assertIn("泛醇", text)
        self.assertIn("屏障受损", text)
        self.assertRegex(text, r"个体耐受|敏感肌")
        self.assertNotIn("预算更低，可以作为平替备选", text)

    def test_suitability_selection_followup_picks_best_and_reads_as_choice(self):
        # 用户问"这几款里哪个更适合敏感肌"：必须在候选里挑最贴合的那款，并读起来像"从多款里选"
        turn = CanonicalTurn(
            raw_message="刚才那几个里面哪个更适合敏感肌",
            followup_type=FollowupType.SUITABILITY,
            category="精华",
        )
        products = [
            {
                "id": 1,
                "name": "雅诗兰黛小棕瓶精华",
                "brand": "雅诗兰黛",
                "category": "精华",
                "price": 968,
                "suitable_skin": "全肤质通用",
            },
            {
                "id": 2,
                "name": "薇诺娜舒敏精华",
                "brand": "薇诺娜",
                "category": "精华",
                "price": 320,
                "suitable_skin": "敏感肌、屏障受损，敏感肌可用、温和低刺激",
                "concerns_list": ["舒缓", "维稳", "屏障"],
            },
            {
                "id": 3,
                "name": "赫莲娜绿宝瓶精华",
                "brand": "赫莲娜",
                "category": "精华",
                "price": 1580,
                "suitable_skin": "熟龄、抗老",
            },
        ]

        result = Presenter().present_followup(turn, products)
        text = result["text"]

        # 必须挑出敏感肌最贴合的薇诺娜作为首选，而不是默认第一款小棕瓶
        self.assertEqual(result["products"][0]["id"], 2)
        self.assertIn("薇诺娜", text)
        # 读起来像"从多款里挑"，而不是孤零零描述一款
        self.assertRegex(text, r"这几款|这几个|相比|更贴合|更适合|里更")

    def test_cheaper_followup_single_candidate_is_presented_as_alternative(self):
        turn = CanonicalTurn(
            raw_message="有没有便宜一点的平替",
            followup_type=FollowupType.CHEAPER,
            category="精华",
        )
        products = [{
            "id": 2,
            "name": "玉泽屏障精华",
            "brand": "玉泽",
            "category": "精华",
            "price": 88,
            "key_ingredients_list": ["神经酰胺", "角鲨烷"],
            "concerns_list": ["屏障修护", "舒缓"],
        }]

        result = Presenter().present_followup(turn, products)

        self.assertEqual([item["id"] for item in result["products"]], [2])
        self.assertIn("平价", result["text"])
        self.assertIn("玉泽", result["text"])
        self.assertNotIn("已经是相对平价", result["text"])

    def test_ingredient_followup_returns_one_product_card(self):
        turn = CanonicalTurn(
            raw_message="第一款核心成分有什么用",
            followup_type=FollowupType.INGREDIENT,
        )
        products = [
            {"id": 1, "name": "A精华", "brand": "A", "price": 100, "key_ingredients_list": ["烟酰胺"]},
            {"id": 2, "name": "B精华", "brand": "B", "price": 200, "key_ingredients_list": ["泛醇"]},
        ]

        result = Presenter().present_followup(turn, products)

        self.assertEqual([item["id"] for item in result["products"]], [1])
        self.assertIn("烟酰胺：", result["text"])
        self.assertIn("常用于提亮", result["text"])

    def test_foundation_compare_uses_makeup_focus_not_skincare_brightening(self):
        turn = CanonicalTurn(
            raw_message="DW粉底液和阿玛尼权力粉底哪个好",
            category="粉底液",
        )
        products = [
            {
                "id": 79,
                "name": "雅诗兰黛第二代DW持妆粉底液",
                "brand": "雅诗兰黛",
                "category": "粉底液",
                "price": 436,
                "positioning": "油皮亲妈高遮瑕持妆粉底经典",
                "suitable_skin": "油皮、混油",
                "concerns_list": ["持妆", "高遮瑕", "控油"],
                "key_ingredients_list": ["粉底粉体", "透明质酸", "烟酰胺"],
            },
            {
                "id": 80,
                "name": "阿玛尼权力持妆PRO粉底液",
                "brand": "阿玛尼",
                "category": "粉底液",
                "price": 296,
                "positioning": "高遮瑕控油持妆油皮混油底妆",
                "suitable_skin": "油皮、混油、瑕疵皮",
                "concerns_list": ["持妆", "遮瑕", "控油"],
                "key_ingredients_list": ["粉底粉体", "特殊成膜剂", "甘油"],
            },
        ]

        text = Presenter().present_compare(turn, products)["text"]

        self.assertIn("持妆", text)
        self.assertIn("遮瑕", text)
        self.assertIn("控油", text)
        self.assertIn("相对轻薄妆效", text)
        self.assertNotIn("提亮、淡斑和均匀肤色", text)

    def test_first_product_followup_keeps_first_product_order_from_history(self):
        history = [{
            "role": "assistant",
            "content": "玉泽屏障精华\n珀莱雅双抗精华\n资生堂红腰子精华",
        }]

        turn = TurnParser().parse("第一款核心成分有什么用", conversation_history=history)

        self.assertEqual(turn.intent, AnswerMode.FOLLOWUP)
        self.assertEqual(turn.followup_type, FollowupType.INGREDIENT)
        self.assertEqual(turn.referenced_products[:1], ["玉泽"])

    def test_second_product_followup_anchors_to_second_from_last_answer(self):
        history = [{
            "role": "assistant",
            "content": "雅诗兰黛小棕瓶精华\n修丽可紫米精华\n赫莲娜绿宝瓶精华",
        }]

        turn = TurnParser().parse("第二款是什么成分，具体有什么用", conversation_history=history)

        self.assertEqual(turn.intent, AnswerMode.FOLLOWUP)
        self.assertEqual(turn.followup_type, FollowupType.INGREDIENT)
        self.assertEqual(turn.referenced_products, ["紫米"])

    def test_third_product_followup_anchors_to_third_from_last_answer(self):
        history = [{
            "role": "assistant",
            "content": "雅诗兰黛小棕瓶精华\n修丽可紫米精华\n赫莲娜绿宝瓶精华",
        }]

        turn = TurnParser().parse("第三款适合油皮吗", conversation_history=history)

        self.assertEqual(turn.followup_type, FollowupType.SUITABILITY)
        self.assertEqual(turn.referenced_products, ["绿宝瓶"])

    def test_ordinal_reference_uses_only_latest_answer_not_earlier_turns(self):
        history = [
            {"role": "user", "content": "油皮精华推荐"},
            {"role": "assistant", "content": "雅诗兰黛小棕瓶精华\n修丽可紫米精华\n赫莲娜绿宝瓶精华"},
            {"role": "user", "content": "换面霜呢"},
            {"role": "assistant", "content": "玉泽屏障面霜\n理肤泉B5面霜\n薇诺娜特护霜"},
        ]

        turn = TurnParser().parse("第二款多少钱", conversation_history=history)

        self.assertEqual(turn.followup_type, FollowupType.PRICE)
        self.assertEqual(turn.referenced_products, ["B5"])

    def test_recommendation_pitfalls_use_product_specific_pitfalls_first(self):
        turn = CanonicalTurn(raw_message="300以内精华推荐", category="精华")
        products = [
            {
                "name": "A醇精华",
                "brand": "测试",
                "category": "精华",
                "pitfalls": "A醇新手先隔天夜间用，孕期和敏感爆发期先避开。",
                "description": "定位：A醇抗老精华",
            }
        ]

        pitfalls = Presenter()._build_recommendation_pitfalls(turn, products)

        self.assertTrue(pitfalls)
        self.assertIn("孕期", pitfalls[0]["description"])
        self.assertNotIn("功效型精华先单品测试", pitfalls[0]["description"])

    def test_recommendation_pitfalls_fallback_to_same_note_as_product_block(self):
        turn = CanonicalTurn(raw_message="300以内精华推荐", category="精华")
        products = [
            {
                "name": "玉泽皮肤屏障修护精华乳",
                "brand": "玉泽",
                "category": "精华",
                "description": "定位：屏障修护精华乳，PBS仿生脂质技术屏障修护精华。",
                "concerns_list": ["屏障修护"],
                "key_ingredients_list": ["神经酰胺", "角鲨烷"],
            }
        ]

        pitfalls = Presenter()._build_recommendation_pitfalls(turn, products)

        self.assertTrue(pitfalls)
        self.assertIn("先单独用3-5天", pitfalls[0]["description"])
        self.assertNotIn("不要和多款新精华同时上脸", pitfalls[0]["description"])

    def test_recommendation_pitfalls_do_not_pad_generic_tips_when_product_note_exists(self):
        turn = CanonicalTurn(raw_message="300以内精华推荐", category="精华")
        products = [
            {
                "name": "A醇精华",
                "brand": "测试",
                "category": "精华",
                "pitfalls": "A醇新手先隔天夜间用，孕期和敏感爆发期先避开。",
                "description": "定位：A醇抗老精华",
            }
        ]

        pitfalls = Presenter()._build_recommendation_pitfalls(turn, products)
        descriptions = " ".join(item["description"] for item in pitfalls)

        self.assertEqual(len(pitfalls), 1)
        self.assertIn("孕期", descriptions)
        self.assertNotIn("抗老、提亮或修护类精华都建议先单品测试", descriptions)
        self.assertNotIn("商品库价格是入库参考价", descriptions)

    def test_face_cream_pitfalls_use_product_data_before_local_thick_apply_template(self):
        turn = CanonicalTurn(raw_message="面霜推荐", category="面霜", skin_type="敏感肌")
        products = [
            {
                "name": "理肤泉新B5面霜",
                "brand": "理肤泉",
                "category": "面霜",
                "price": 88,
                "description": "理肤泉官方旗舰店新B5面霜40ml，修护面霜定位。",
                "target_users": "干燥泛红、屏障受损、需要保湿修护舒缓的人群",
                "key_ingredients_list": ["泛醇/B5", "积雪草苷"],
                "concerns_list": ["修护", "舒缓", "保湿", "干燥泛红", "屏障受损"],
                "suitable_skin": "干燥、泛红、屏障受损肌；敏感肌/脆弱肌可参考，需按个体耐受试用",
                "pitfalls": "官方搜索结果显示618活动价；此前详情页无法访问，完整成分以官方详情/备案/包装为准",
            }
        ]

        pitfalls = Presenter()._build_recommendation_pitfalls(turn, products)
        descriptions = " ".join(item["description"] for item in pitfalls)

        self.assertRegex(descriptions, r"个体耐受|完整成分|屏障受损")
        self.assertNotIn("厚敷可做急救", descriptions)
        self.assertNotIn("油皮避免全脸厚敷过夜", descriptions)

    def test_face_cream_pitfalls_use_skin_and_ingredient_data_when_pitfalls_missing(self):
        turn = CanonicalTurn(raw_message="面霜推荐", category="面霜", skin_type="敏感肌")
        products = [
            {
                "name": "理肤泉新B5面霜",
                "brand": "理肤泉",
                "category": "面霜",
                "description": "理肤泉官方旗舰店新B5面霜40ml，修护面霜定位。",
                "target_users": "干燥泛红、屏障受损、需要保湿修护舒缓的人群",
                "key_ingredients_list": ["泛醇/B5", "积雪草苷"],
                "concerns_list": ["修护", "舒缓", "保湿", "干燥泛红", "屏障受损"],
                "suitable_skin": "干燥、泛红、屏障受损肌；敏感肌/脆弱肌可参考，需按个体耐受试用",
            }
        ]

        pitfalls = Presenter()._build_recommendation_pitfalls(turn, products)
        descriptions = " ".join(item["description"] for item in pitfalls)

        self.assertRegex(descriptions, r"个体耐受|屏障受损|泛醇")
        self.assertNotIn("厚敷可做急救", descriptions)
        self.assertNotIn("油皮避免全脸厚敷过夜", descriptions)

    def test_incomplete_ingredient_notice_is_not_user_facing_attention_point(self):
        presenter = Presenter()
        product = {
            "name": "雅诗兰黛第七代小棕瓶特润修护肌活精华露",
            "brand": "雅诗兰黛",
            "category": "精华",
            "description": "雅诗兰黛第七代小棕瓶特润修护肌活精华露，经典小棕瓶最新版。",
            "key_ingredients_list": ["二裂酵母发酵产物溶胞物", "猴面包树籽提取物", "三肽-32"],
            "concerns_list": ["修护", "保湿", "细纹干纹", "维稳"],
            "pitfalls": "完整INCI以官方备案/包装为准，当前详情文本未核全。",
        }

        note = presenter._extract_note(product, {}, "精华")
        facts = V2ShoppingAgent()._build_llm_product_facts(product, 1)

        self.assertNotIn("商品资料未核全完整成分", note)
        self.assertNotIn("未核全完整成分", facts["商品资料提醒"])
        self.assertRegex(note, r"维稳|修护|低频")

    def test_sensitive_skin_trial_warning_does_not_become_exclusion_warning(self):
        warning = Presenter()._extract_product_data_warning({
            "name": "SK-II神仙水精华液",
            "category": "精华",
            "suitable_skin": "多种肤质；敏感肌需先试用",
            "key_ingredients_list": ["PITERA™"],
        })

        self.assertIn("先局部试用", warning)
        self.assertNotIn("除外", warning)
        self.assertNotIn("需谨慎", warning)

    def test_retriever_normalize_product_uses_chinese_spec_metadata(self):
        product = Retriever()._normalize_product({
            "name": "SK-II神仙水精华液230ml",
            "price": "1650",
            "description": "适合肤质：多种肤质；敏感肌需先试用",
            "specifications": {
                "适合肤质": "多种肤质适用 敏感肌适用",
                "适合人群": "日常维稳提亮人群",
                "核心成分": "PITERA™、烟酰胺、泛醇",
                "主打功效": "调理肤质；保湿；透亮",
                "定位": "高端酵母精华水定位",
                "备注": "官方详情页230ml细腻紧致透亮",
            },
        })

        self.assertEqual(product["suitable_skin"], "多种肤质适用 敏感肌适用")
        self.assertEqual(product["target_users"], "日常维稳提亮人群")
        self.assertIn("PITERA™", product["key_ingredients_list"])
        self.assertIn("调理肤质", product["concerns_list"])
        self.assertEqual(product["positioning"], "高端酵母精华水定位")
        self.assertEqual(product["pitfalls"], "官方详情页230ml细腻紧致透亮")

    def test_normalize_reads_ocr_and_alt_ingredient_keys(self):
        # 成分只存在 OCR 提取键/主要功效成分里时，也要归一进 key_ingredients_list
        product = Retriever()._normalize_product({
            "name": "某精华",
            "price": "300",
            "specifications": {
                "OCR提取核心成分": "烟酰胺；传明酸；四重舒缓成分",
            },
        })
        joined = "、".join(product["key_ingredients_list"])
        self.assertIn("烟酰胺", joined)
        self.assertIn("传明酸", joined)

    def test_normalize_reads_ocr_concerns_key(self):
        product = Retriever()._normalize_product({
            "name": "某精华", "price": "300",
            "specifications": {"OCR提取功效": "美白；淡斑；提亮"},
        })
        self.assertIn("美白", product["concerns_list"])

    def test_normalize_drops_url_pitfalls_as_dirty(self):
        # pitfalls 是淘宝/搜索链接这种抓取残留 -> 必须清掉，不当注意点
        product = Retriever()._normalize_product({
            "name": "敷尔佳面膜", "price": "50",
            "specifications": {"pitfalls": "https://s.taobao.com/search?q=%E6%95%B7%E5%B0%94%E4%BD%B3"},
        })
        self.assertNotIn("http", product["pitfalls"])
        self.assertNotIn("taobao", product["pitfalls"])

    def test_normalize_keeps_real_pitfall_text(self):
        product = Retriever()._normalize_product({
            "name": "某爽肤水", "price": "200",
            "specifications": {"pitfalls": "含水杨酸，孕妇慎用；敏感肌建议先测试"},
        })
        self.assertIn("水杨酸", product["pitfalls"])

    def test_normalize_derives_pitfall_from_ingredients_when_missing(self):
        # 无 pitfalls 但成分含酒精 -> 派生"基于事实"的风险提醒，而不是留空
        product = Retriever()._normalize_product({
            "name": "某化妆水", "price": "200",
            "specifications": {"key_ingredients": "变性酒精、香精、水杨酸"},
        })
        self.assertTrue(product["pitfalls"])
        self.assertRegex(product["pitfalls"], r"酒精|酸|敏感")

    def test_recommendation_pitfalls_use_knowledge_warnings_before_templates(self):
        turn = CanonicalTurn(
            raw_message="面霜推荐",
            category="面霜",
            knowledge_pitfalls=[
                {
                    "title": "敏感期先稳屏障",
                    "description": "屏障不稳或泛红期不要同时新增多款功效产品，先把清洁、防晒和保湿做简单。",
                    "severity": "高",
                }
            ],
        )
        products = [
            {
                "name": "理肤泉新B5面霜",
                "brand": "理肤泉",
                "category": "面霜",
                "description": "理肤泉官方旗舰店新B5面霜40ml，修护面霜定位。",
            }
        ]

        pitfalls = Presenter()._build_recommendation_pitfalls(turn, products)
        descriptions = " ".join(item["description"] for item in pitfalls)

        self.assertIn("不要同时新增多款功效产品", descriptions)
        self.assertNotIn("厚敷可做急救", descriptions)

    def test_medium_pitfalls_are_merged_into_one_yellow_item(self):
        turn = CanonicalTurn(raw_message="300以内精华推荐", category="精华")
        products = [
            {
                "name": "A精华",
                "brand": "A",
                "category": "精华",
                "pitfalls": "烟酰胺提亮类先低频用，泛红刺痛时先停用观察。",
            },
            {
                "name": "B精华",
                "brand": "B",
                "category": "精华",
                "pitfalls": "A醇新手先隔天夜间用，不要和酸类同晚叠加。",
            },
            {
                "name": "C精华",
                "brand": "C",
                "category": "精华",
                "pitfalls": "敏感期先停用强功效精华，等屏障稳定再恢复。",
            },
        ]

        pitfalls = Presenter()._build_recommendation_pitfalls(turn, products)

        self.assertEqual(len(pitfalls), 1)
        self.assertEqual(pitfalls[0]["severity"], "中")
        self.assertIn("A精华", pitfalls[0]["description"])
        self.assertIn("B精华", pitfalls[0]["description"])
        self.assertIn("C精华", pitfalls[0]["description"])

    def test_high_pitfall_keeps_red_item_and_merges_medium_items(self):
        turn = CanonicalTurn(
            raw_message="面霜推荐",
            category="面霜",
            knowledge_pitfalls=[
                {
                    "title": "敏感期先稳屏障",
                    "description": "屏障不稳或泛红期不要同时新增多款功效产品。",
                    "severity": "高",
                }
            ],
        )
        products = [
            {
                "name": "理肤泉新B5面霜",
                "brand": "理肤泉",
                "category": "面霜",
                "suitable_skin": "干燥、泛红、屏障受损肌；敏感肌/脆弱肌可参考，需按个体耐受试用",
                "key_ingredients_list": ["泛醇/B5"],
                "concerns_list": ["屏障受损", "泛红"],
            }
        ]

        pitfalls = Presenter()._build_recommendation_pitfalls(turn, products)

        self.assertEqual([item["severity"] for item in pitfalls], ["高", "中"])
        self.assertIn("不要同时新增多款功效产品", pitfalls[0]["description"])
        self.assertIn("理肤泉新B5面霜", pitfalls[1]["description"])

    def test_followup_price_footer_not_repeated_by_secondary_supplement(self):
        turn = CanonicalTurn(
            raw_message="有没有便宜点，价格多少",
            followup_type=FollowupType.CHEAPER,
            secondary_intents=[{"followup_type": FollowupType.PRICE.value}],
        )
        products = [
            {"id": 1, "name": "贵价精华", "brand": "A", "price": 500},
            {"id": 2, "name": "平替精华", "brand": "B", "price": 120},
        ]

        text = Presenter().present_followup(turn, products)["text"]

        self.assertNotIn("这里展示的是入库参考价", text)
        self.assertEqual(text.count("参考价为入库时价格"), 1)

    def test_llm_prompt_requires_substantive_opening_summary(self):
        agent_py = Path("app/services/v2/agent.py").read_text(encoding="utf-8")

        self.assertIn("第一段", agent_py)
        self.assertIn("适合谁", agent_py)
        self.assertIn("为什么", agent_py)
        self.assertIn("怎么选", agent_py)
        self.assertIn("## 综合建议", agent_py)
        self.assertIn("- 参考价：", agent_py)
        self.assertIn("- 核心成分：", agent_py)
        self.assertIn("- 注意点：", agent_py)
        self.assertIn("成分侧重", agent_py)

    def test_llm_product_facts_include_product_data_warning_and_target_users(self):
        product = {
            "name": "理肤泉新B5面霜",
            "brand": "理肤泉",
            "category": "面霜",
            "price": 88,
            "target_users": "干燥泛红、屏障受损、需要保湿修护舒缓的人群",
            "key_ingredients_list": ["泛醇/B5", "积雪草苷"],
            "concerns_list": ["修护", "舒缓", "屏障受损"],
            "suitable_skin": "敏感肌/脆弱肌可参考，需按个体耐受试用",
        }

        facts = V2ShoppingAgent()._build_llm_product_facts(product, 1)

        self.assertIn("泛醇/B5", facts["关键成分"])
        self.assertIn("干燥泛红", facts["适合人群"])
        self.assertIn("个体耐受", facts["商品资料提醒"])

    def test_sanitize_plain_dedupes_reference_price_footer_variants(self):
        text = (
            "这里要注意：这里展示的是入库参考价，实时活动价和规格组合以下单页为准。\n"
            "参考价为入库时价格，实时活动价以商品链接为准。"
        )

        cleaned = Presenter._sanitize_plain(text)

        self.assertNotIn("这里展示的是入库参考价", cleaned)
        self.assertEqual(cleaned.count("参考价为入库时价格"), 1)

    def test_llm_cleaner_dedupes_reference_price_footer_variants(self):
        text = (
            "这里要注意：这里展示的是入库参考价，实时活动价和规格组合以下单页为准。\n"
            "参考价为入库时价格，实时活动价以商品链接为准。"
        )

        cleaned = V2ShoppingAgent._clean_llm_text(text)

        self.assertNotIn("这里展示的是入库参考价", cleaned)
        self.assertEqual(cleaned.count("参考价为入库时价格"), 1)

    def test_recommendation_llm_accepts_paragraph_with_price_and_name(self):
        text = """
干敏肌选抗初老精华，核心是兼顾修护与抗老。

**雅诗兰黛小棕瓶精华露50ml抗老紧致护肤品套装**
这款适合希望先稳定屏障的人。参考价约¥968 / 50ml。注意，初期建议单独使用。
"""
        products = [{"name": "雅诗兰黛小棕瓶精华露50ml抗老紧致护肤品套装"}]

        self.assertTrue(V2ShoppingAgent._is_usable_llm_text(text, products, AnswerMode.RECOMMENDATION.value))

    def test_recommendation_llm_rejects_empty_or_wrong_product(self):
        text = "随便推荐一个吧"
        products = [{"name": "雅诗兰黛小棕瓶精华露50ml抗老紧致护肤品套装"}]
        self.assertFalse(V2ShoppingAgent._is_usable_llm_text(text, products, AnswerMode.RECOMMENDATION.value))

        text2 = "这款兰蔻小黑瓶很好用"
        self.assertFalse(V2ShoppingAgent._is_usable_llm_text(text2, products, AnswerMode.RECOMMENDATION.value))

    def test_recommendation_llm_accepts_structured_product_blocks_and_summary(self):
        text = """
干敏肌选抗初老精华，核心是兼顾修护与抗老。

**雅诗兰黛小棕瓶精华露50ml抗老紧致护肤品套装**
- 参考价：约¥968 / 50ml
- 核心成分：二裂酵母、三肽-32
- 注意点：初期建议单独使用，观察耐受。

## 综合建议
更想稳屏障先看雅诗兰黛小棕瓶。
"""
        products = [{"name": "雅诗兰黛小棕瓶精华露50ml抗老紧致护肤品套装"}]

        self.assertTrue(V2ShoppingAgent._is_usable_llm_text(text, products, AnswerMode.RECOMMENDATION.value))

    def test_llm_cleaner_hides_backend_wording(self):
        text = V2ShoppingAgent._clean_llm_text("后端筛选出的这几款候选商品都比较适合你。")

        self.assertNotIn("后端", text)
        self.assertNotIn("候选商品", text)

    def test_frontend_does_not_render_second_decision_card_after_live_thinking(self):
        html = Path("app/static/chat.html").read_text(encoding="utf-8")

        self.assertRegex(
            html,
            re.compile(
                r"if \(liveDecisionProcess\) \{.*?"
                r"decisionProcessRendered = true;.*?"
                r"deferredPanels\.decisionProcess = null;.*?"
                r"return;.*?"
                r"displayDecisionProcess\(deferredPanels\.decisionProcess, aiDiv\);",
                re.S,
            ),
        )

    def test_frontend_keeps_live_thinking_until_first_token(self):
        html = Path("app/static/chat.html").read_text(encoding="utf-8")

        self.assertIn("persistUntilFirstToken: true", html)
        self.assertRegex(
            html,
            re.compile(
                r"function displayDecisionProcess\(process, targetWrapper = null, options = \{\}\).*?"
                r"if \(options\.persistUntilFirstToken\) return;",
                re.S,
            ),
        )

    def test_inline_product_image_caption_uses_compact_name(self):
        html = Path("app/static/chat.html").read_text(encoding="utf-8")

        self.assertRegex(
            html,
            re.compile(
                r"function buildInlineProductImage\(product, index = 0\).*?"
                r"const name = getCompactProductDisplayName\(product\);",
                re.S,
            ),
        )

    def test_inline_product_image_match_strips_recommendation_role_prefix(self):
        html = Path("app/static/chat.html").read_text(encoding="utf-8")

        self.assertIn("function stripInlineProductRolePrefix", html)
        self.assertRegex(
            html,
            re.compile(
                r"function findInlineProductIndexForLine\(line, inlineProducts, usedIndexes\).*?"
                r"stripInlineProductRolePrefix\(line\)",
                re.S,
            ),
        )

    def test_inline_product_image_match_normalizes_product_type_suffixes(self):
        html = Path("app/static/chat.html").read_text(encoding="utf-8")

        self.assertIn("function buildInlineProductMatchKeys", html)
        self.assertIn("['精华液', '精华']", html)
        self.assertIn("['精华乳', '精华']", html)
        self.assertIn("['精华露', '精华']", html)
        self.assertRegex(
            html,
            re.compile(
                r"function findInlineProductIndexForLine\(line, inlineProducts, usedIndexes\).*?"
                r"buildInlineProductMatchKeys\(stripInlineProductRolePrefix\(line\)\).*?"
                r"buildInlineProductMatchKeys\(name\)",
                re.S,
            ),
        )

    def test_inline_product_image_match_can_drop_product_type_suffix(self):
        html = Path("app/static/chat.html").read_text(encoding="utf-8")

        self.assertIn("['精华', '']", html)
        self.assertIn("['面霜', '']", html)
        self.assertIn("['防晒', '']", html)
        self.assertIn("pendingKeys", html)

    def test_inline_product_image_can_render_inside_list_items(self):
        html = Path("app/static/chat.html").read_text(encoding="utf-8")

        self.assertRegex(
            html,
            re.compile(
                r"if \(unorderedMatch\) \{.*?"
                r"findInlineProductIndexForLine\(unorderedMatch\[1\], inlineProducts, usedInlineImageIndexes\).*?"
                r"buildInlineProductImage\(inlineProducts\[productIndex\], productIndex\)",
                re.S,
            ),
        )

    def test_inline_product_image_plain_paragraph_requires_product_anchor(self):
        html = Path("app/static/chat.html").read_text(encoding="utf-8")

        self.assertIn("function findInlineProductAnchorIndexForLine", html)
        self.assertIn("lineKey.startsWith(productKey) || productKey.startsWith(lineKey)", html)
        self.assertRegex(
            html,
            re.compile(
                r"htmlParts\.push\(`<p>\$\{renderInlineMarkdown\(trimmed\)\}</p>`\);.*?"
                r"findInlineProductAnchorIndexForLine\(trimmed, inlineProducts, usedInlineImageIndexes\)",
                re.S,
            ),
        )
        self.assertNotRegex(
            html,
            re.compile(
                r"htmlParts\.push\(`<p>\$\{renderInlineMarkdown\(trimmed\)\}</p>`\);.*?"
                r"findInlineProductIndexForLine\(trimmed, inlineProducts, usedInlineImageIndexes\)",
                re.S,
            ),
        )

    def test_answer_contract_inline_images_use_serialized_product_shape(self):
        product = {
            "id": 12,
            "name": "玉泽皮肤屏障修护精华乳",
            "display_name": "玉泽（Dr.Yu）精华乳",
            "brand": "玉泽",
            "category": "精华",
            "price": 88,
            "image_url": "/static/images/products/yuze.png",
        }

        inline_images = V2ShoppingAgent()._build_inline_image_products([product])

        self.assertEqual(inline_images[0]["id"], 12)
        self.assertEqual(inline_images[0]["product_id"], 12)
        self.assertEqual(inline_images[0]["display_name"], "玉泽（Dr.Yu）精华乳")
        self.assertEqual(inline_images[0]["image_url"], "/static/images/products/yuze.png")

    def test_answer_contract_inline_images_keep_products_without_image_url(self):
        product = {
            "id": 99,
            "name": "SK-II神仙水精华液",
            "display_name": "SK-II神仙水精华液",
            "brand": "SK-II",
            "category": "精华",
            "price": 1650,
        }

        inline_images = V2ShoppingAgent()._build_inline_image_products([product])

        self.assertEqual(len(inline_images), 1)
        self.assertEqual(inline_images[0]["product_id"], 99)
        self.assertEqual(inline_images[0]["display_name"], "SK-II神仙水精华液")

    def test_frontend_compact_name_strips_specs_before_cutting_product_name(self):
        html = Path("app/static/chat.html").read_text(encoding="utf-8")

        self.assertIn("小白瓶", html)
        self.assertIn(r"raw = raw.replace(/\d+\s*(?:ml|mL|ML|g|G|克|毫升)/g, '');", html)
        self.assertRegex(html, re.compile(r"const cutWords = \[[^\]]*'小白瓶'[^\]]*'精华液'", re.S))

    def test_higher_price_followup_inherits_previous_budget_floor(self):
        history = [
            {"role": "user", "content": "300预算想要防晒"},
            {"role": "assistant", "content": "薇诺娜清透防晒乳 参考价约¥88。"},
        ]

        turn = TurnParser().parse("还有没有价格高些的", conversation_history=history)

        self.assertEqual(turn.intent, AnswerMode.FOLLOWUP)
        self.assertEqual(turn.followup_type, FollowupType.HIGHER_BUDGET)
        self.assertEqual(turn.category, "防晒")
        self.assertIsNotNone(turn.budget_min)
        self.assertGreater(turn.budget_min, 300)
        self.assertIsNone(turn.budget_max)

    def test_knowledge_ingredient_question_inherits_recent_product_context(self):
        history = [
            {
                "role": "assistant",
                "content": (
                    "赫莲娜HR全新第六代绿宝瓶精华\n"
                    "- 核心成分：高浓度海茴香提取物（植物干细胞）、益生菌防御因子、咖啡因"
                ),
            }
        ]

        turn = TurnParser().parse(
            "海茴香提取物（植物干细胞）、益生菌防御因子、咖啡因有什么用",
            conversation_history=history,
        )

        self.assertEqual(turn.intent, AnswerMode.KNOWLEDGE)
        self.assertIn("赫莲娜", turn.referenced_products)

    def test_ingredient_compatibility_question_is_knowledge_not_recommendation(self):
        turn = TurnParser().parse("烟酰胺和酸类可以一起用吗")

        self.assertEqual(turn.intent, AnswerMode.KNOWLEDGE)
        self.assertIsNone(turn.category)

    def test_active_ingredient_usage_question_is_knowledge_not_no_match(self):
        turn = TurnParser().parse("A醇白天能用吗")

        self.assertEqual(turn.intent, AnswerMode.KNOWLEDGE)

    def test_morning_c_evening_a_question_stays_general_knowledge(self):
        turn = TurnParser().parse("早C晚A是什么意思")
        result = Presenter().present_knowledge(turn)

        self.assertEqual(turn.intent, AnswerMode.KNOWLEDGE)
        self.assertIn("维C", result["text"])
        self.assertIn("A醇", result["text"])

    def test_ingredient_difference_question_is_knowledge_not_product_compare(self):
        turn = TurnParser().parse("玻色因和A醇有什么区别")

        self.assertEqual(turn.intent, AnswerMode.KNOWLEDGE)

    def test_compact_budget_after_chinese_text_is_extracted(self):
        turn = TurnParser().parse("油敏肌300以内精华推荐")

        self.assertEqual(turn.budget_max, 300)
        self.assertIsNone(turn.budget_min)

    def test_image_context_alt_query_is_followup_without_text_history(self):
        turn = TurnParser().parse(
            "200以内的平替",
            image_context={
                "results": [{"id": 36, "brand": "玉兰油", "category": "精华"}],
                "brand": "玉兰油",
                "category": "精华",
            },
        )

        self.assertEqual(turn.intent, AnswerMode.FOLLOWUP)
        self.assertEqual(turn.followup_type, FollowupType.CHEAPER)
        self.assertEqual(turn.category, "精华")
        self.assertEqual(turn.budget_max, 200)

    def test_image_context_suitability_query_is_judgement_without_text_history(self):
        turn = TurnParser().parse(
            "这款通勤可以吗",
            image_context={
                "results": [{"id": 51, "brand": "安热沙", "category": "防晒"}],
                "brand": "安热沙",
                "category": "防晒",
            },
        )

        self.assertEqual(turn.intent, AnswerMode.JUDGEMENT)
        self.assertEqual(turn.brand, "安热沙")
        self.assertEqual(turn.category, "防晒")

    def test_llm_product_facts_include_useful_product_detail_fields(self):
        facts = V2ShoppingAgent()._build_llm_product_facts(
            {
                "name": "赫莲娜HR全新第六代绿宝瓶精华30ml",
                "brand": "赫莲娜",
                "category": "精华",
                "price": 1080,
                "description": (
                    "定位：赫莲娜绿宝瓶精华（悦活强韧青春精华露），海茴香植物干细胞主打强韧抗氧。\n"
                    "适合人群：需要屏障强韧、抗氧维稳、熬夜暗沉护理的人群\n"
                    "核心成分：高浓度海茴香提取物（植物干细胞）、益生菌防御因子、咖啡因"
                ),
                "specifications": {
                    "texture": "清爽精华露",
                    "usage_time": "早晚，爽肤水后",
                    "shop_name": "HR赫莲娜官方旗舰店",
                    "source_type": "京东官方旗舰店人工核验",
                    "商品编号": "100049",
                    "_source_file": "raw.html",
                },
            },
            1,
        )

        self.assertIn("海茴香", facts["商品详情摘要"])
        self.assertEqual(facts["商品扩展信息"]["texture"], "清爽精华露")
        self.assertEqual(facts["商品扩展信息"]["usage_time"], "早晚，爽肤水后")
        self.assertEqual(facts["商品扩展信息"]["shop_name"], "HR赫莲娜官方旗舰店")
        self.assertNotIn("_source_file", facts["商品扩展信息"])

    def test_knowledge_answer_uses_related_product_ingredient_data(self):
        turn = CanonicalTurn(
            raw_message="海茴香提取物（植物干细胞）、益生菌防御因子、咖啡因有什么用",
            category="精华",
        )
        result = Presenter().present_knowledge(
            turn,
            related_products=[{
                "name": "赫莲娜HR全新第六代绿宝瓶精华30ml",
                "brand": "赫莲娜",
                "category": "精华",
                "target_users": "需要屏障强韧、抗氧维稳、熬夜暗沉护理的人群",
                "suitable_skin": "干皮/混干/熟龄肌，敏感肌先少量试用",
                "key_ingredients_list": ["高浓度海茴香提取物（植物干细胞）", "益生菌防御因子", "咖啡因"],
                "concerns_list": ["强韧屏障", "抗氧", "维稳"],
            }],
        )

        text = result["text"]

        self.assertIn("赫莲娜HR全新第六代绿宝瓶精华", text)
        self.assertIn("海茴香", text)
        self.assertIn("益生菌防御因子", text)
        self.assertIn("咖啡因", text)
        self.assertRegex(text, r"强韧|抗氧|维稳|屏障")

    def test_knowledge_query_reranks_products_by_mentioned_ingredients(self):
        products = [
            {
                "id": 59,
                "name": "SK-II神仙水精华液",
                "brand": "SK-II",
                "key_ingredients_list": ["PITERA™", "烟酰胺", "泛醇"],
                "positioning": "PITERA酵母水标杆",
            },
            {
                "id": 39,
                "name": "赫莲娜HR全新第六代绿宝瓶精华",
                "brand": "赫莲娜",
                "key_ingredients_list": ["高浓度海茴香提取物（植物干细胞）", "益生菌防御因子", "咖啡因"],
                "positioning": "海茴香植物干细胞主打强韧抗氧",
            },
        ]

        ranked = V2ShoppingAgent._rank_products_for_knowledge_query(
            products,
            "海茴香提取物（植物干细胞）、益生菌防御因子、咖啡因有什么用",
        )

        self.assertEqual(ranked[0]["id"], 39)

    def test_knowledge_retrieval_prefers_ingredient_text_matches_before_brand_fallback(self):
        class FakeRetriever:
            async def retrieve_by_text_terms(self, terms, limit=8):
                self.terms = terms
                return [{
                    "id": 39,
                    "name": "赫莲娜HR全新第六代绿宝瓶精华",
                    "brand": "赫莲娜",
                    "category": "精华",
                    "key_ingredients_list": ["高浓度海茴香提取物（植物干细胞）", "益生菌防御因子", "咖啡因"],
                    "positioning": "海茴香植物干细胞主打强韧抗氧",
                }]

            async def retrieve_products(self, **kwargs):
                return [{
                    "id": 115,
                    "name": "迪奥Dior烈艳蓝金口红",
                    "brand": "迪奥",
                    "category": "口红",
                    "key_ingredients_list": ["海茴香提取物"],
                }]

        agent = V2ShoppingAgent.__new__(V2ShoppingAgent)
        agent.retriever = FakeRetriever()
        agent.ranker = type("FakeRanker", (), {"rank": lambda self, candidates, turn, top_n=8: candidates[:top_n]})()
        turn = CanonicalTurn(
            raw_message="海茴香提取物（植物干细胞）、益生菌防御因子、咖啡因有什么用",
            referenced_products=["迪奥", "SK-II", "赫莲娜"],
        )

        products = asyncio.run(agent._retrieve_for_knowledge(turn))

        self.assertEqual([item["id"] for item in products], [39])
        self.assertIn("益生菌防御因子", agent.retriever.terms)

    def test_general_knowledge_query_does_not_attach_unrelated_products(self):
        class FakeRetriever:
            async def retrieve_by_text_terms(self, terms, limit=8):
                return [{"id": 94, "name": "咖啡因眼部精华", "brand": "THE ORDINARY"}]

            async def retrieve_products(self, **kwargs):
                return []

        agent = V2ShoppingAgent.__new__(V2ShoppingAgent)
        agent.retriever = FakeRetriever()
        agent.ranker = type("FakeRanker", (), {"rank": lambda self, candidates, turn, top_n=8: candidates[:top_n]})()
        turn = CanonicalTurn(raw_message="早C晚A是什么意思")

        products = asyncio.run(agent._retrieve_for_knowledge(turn))

        self.assertEqual(products, [])

    def test_cheaper_followup_uses_previous_budget_floor_as_new_ceiling(self):
        history = [
            {"role": "user", "content": "干敏肌想要抗初老精华，预算 1000 左右"},
            {"role": "assistant", "content": "雅诗兰黛小棕瓶\n修丽可紫米精华\n赫莲娜绿宝瓶"},
        ]

        turn = TurnParser().parse("有没有便宜一点的平替", conversation_history=history)

        self.assertEqual(turn.intent, AnswerMode.FOLLOWUP)
        self.assertEqual(turn.followup_type, FollowupType.CHEAPER)
        self.assertEqual(turn.category, "精华")
        self.assertIsNone(turn.budget_min)
        self.assertEqual(turn.budget_max, 800)

    def test_ordinal_referenced_product_retrieval_ignores_inherited_budget(self):
        # "第二款是什么成分" 指代明确锚点：即便继承的预算会过滤掉它，也必须返回被点名的那款，
        # 不能因预算为空 fall through 到通用检索，换成一个便宜的无关商品。
        class FakeRetriever:
            def __init__(self):
                self.calls = []

            async def retrieve_products(self, **kwargs):
                self.calls.append(kwargs)
                if kwargs.get("brand") == "修丽可" and (kwargs.get("budget_max") or 0):
                    return []
                if kwargs.get("brand") == "修丽可":
                    return [{"id": 7, "name": "修丽可紫米精华", "display_name": "修丽可紫米精华",
                             "brand": "修丽可", "category": "精华", "price": 1050, "price_val": 1050,
                             "key_ingredients_list": ["玻色因"]}]
                return [{"id": 36, "name": "玉兰油小白瓶", "display_name": "玉兰油小白瓶",
                         "brand": "玉兰油", "category": "精华", "price": 350, "price_val": 350}]

            async def retrieve_by_name_fuzzy(self, name, limit=5):
                if "修丽可" in name:
                    return [{"id": 7, "name": "修丽可紫米精华", "display_name": "修丽可紫米精华",
                             "brand": "修丽可", "category": "精华", "price": 1050, "price_val": 1050,
                             "key_ingredients_list": ["玻色因"]}]
                return []

        retriever = FakeRetriever()
        agent = V2ShoppingAgent.__new__(V2ShoppingAgent)
        agent.retriever = retriever
        turn = CanonicalTurn(
            raw_message="第二款是什么成分，具体有什么用",
            category="精华",
            budget_min=640,
            budget_max=960,
            followup_type=FollowupType.INGREDIENT,
            referenced_products=["修丽可"],
            conversation_history=[{"role": "assistant", "content": "雅诗兰黛小棕瓶\n修丽可紫米精华\n赫莲娜绿宝瓶"}],
        )

        products = asyncio.run(agent._retrieve_for_followup(turn))

        self.assertTrue(products)
        self.assertEqual(products[0]["brand"], "修丽可")
        self.assertNotIn(36, [p.get("id") for p in products])

    def test_cheaper_followup_retrieval_excludes_seen_products_and_respects_ceiling(self):
        class FakeRetriever:
            async def retrieve_products(self, **kwargs):
                self.kwargs = kwargs
                return [
                    {
                        "id": 1,
                        "name": "雅诗兰黛小棕瓶",
                        "display_name": "雅诗兰黛小棕瓶",
                        "brand": "雅诗兰黛",
                        "category": "精华",
                        "price": 968,
                        "price_val": 968,
                    },
                    {
                        "id": 2,
                        "name": "玉泽屏障精华",
                        "display_name": "玉泽屏障精华",
                        "brand": "玉泽",
                        "category": "精华",
                        "price": 88,
                        "price_val": 88,
                    },
                ]

        retriever = FakeRetriever()
        agent = V2ShoppingAgent.__new__(V2ShoppingAgent)
        agent.retriever = retriever
        turn = CanonicalTurn(
            raw_message="有没有便宜一点的平替",
            category="精华",
            budget_max=800,
            followup_type=FollowupType.CHEAPER,
            conversation_history=[
                {"role": "assistant", "content": "雅诗兰黛小棕瓶\n修丽可紫米精华\n赫莲娜绿宝瓶"},
            ],
        )

        products = asyncio.run(agent._retrieve_for_followup(turn))

        self.assertEqual(retriever.kwargs["budget_max"], 800)
        self.assertEqual([item["id"] for item in products], [2])

    def test_higher_budget_followup_does_not_return_seen_low_price_when_no_new_match(self):
        class FakeRetriever:
            async def retrieve_products(self, **kwargs):
                self.kwargs = kwargs
                return [{
                    "id": 1,
                    "name": "薇诺娜清透防晒乳",
                    "display_name": "薇诺娜清透防晒乳",
                    "brand": "薇诺娜",
                    "category": "防晒",
                    "price": 88,
                    "price_val": 88,
                }]

        retriever = FakeRetriever()
        agent = V2ShoppingAgent.__new__(V2ShoppingAgent)
        agent.retriever = retriever
        turn = CanonicalTurn(
            raw_message="还有没有价格高些的",
            category="防晒",
            budget_min=303,
            followup_type=FollowupType.HIGHER_BUDGET,
            conversation_history=[
                {"role": "assistant", "content": "薇诺娜清透防晒乳\nBiore碧柔水活防晒水润凝蜜"},
            ],
        )

        result = asyncio.run(agent._retrieve_for_followup(turn))

        self.assertEqual(result, [])
        self.assertEqual(retriever.kwargs["budget_min"], 303)


class V2AnswerContractValidatorTest(unittest.TestCase):
    def test_llm_must_keep_all_presenter_skeleton_markers(self):
        agent = V2ShoppingAgent.__new__(V2ShoppingAgent)
        draft = (
            "先说结论：兰蔻更适合日常。\n\n"
            "## 对比结论\n\n"
            "## 分项判断\n\n"
            "**兰蔻小白管**\n"
            "- 价格：约¥350\n"
            "## 怎么选\n"
        )
        # LLM 丢掉了「## 分项判断」和「## 怎么选」骨架 -> 必须判不合格
        broken = (
            "兰蔻和安热沙都不错，你可以看预算和肤质来选，日常通勤兰蔻更省心。"
        )

        self.assertFalse(
            agent._llm_keeps_presenter_skeleton(broken, draft)
        )

    def test_llm_that_preserves_skeleton_is_accepted(self):
        agent = V2ShoppingAgent.__new__(V2ShoppingAgent)
        draft = (
            "## 对比结论\n\n## 分项判断\n\n**兰蔻小白管**\n- 价格：约¥350\n## 怎么选\n"
        )
        enriched = (
            "## 对比结论\n\n先说结论：兰蔻更适合日常通勤，安热沙更适合户外。\n\n"
            "## 分项判断\n\n**兰蔻小白管**\n- 价格：约¥350，日常肤感更轻。\n\n"
            "## 怎么选\n\n预算接近就看肤感：兰蔻轻薄，安热沙防水更强。"
        )

        self.assertTrue(
            agent._llm_keeps_presenter_skeleton(enriched, draft)
        )

    def test_extract_skeleton_markers_covers_headings_bold_and_field_labels(self):
        agent = V2ShoppingAgent.__new__(V2ShoppingAgent)
        draft = (
            "## 图片识别结果\n- 品牌：薇诺娜\n- 参考价：约¥88\n\n"
            "## 注意点\n**能不能用**\n"
        )

        markers = agent._extract_skeleton_markers(draft)

        self.assertIn("## 图片识别结果", markers)
        self.assertIn("## 注意点", markers)
        self.assertIn("**能不能用**", markers)
        self.assertIn("- 品牌：", markers)
        self.assertIn("- 参考价：", markers)


class V2IntentModelFallbackTest(unittest.TestCase):
    def test_model_fallback_only_runs_when_rules_uncertain(self):
        from app.services.v2.intent_classifier import IntentClassifier

        classifier = IntentClassifier()
        # 规则已经能明确判成推荐，不该触发模型兜底
        confident = SimpleNamespace(answer_mode=AnswerMode.RECOMMENDATION, confidence=0.85)
        self.assertFalse(classifier._should_use_model_fallback("300以内防晒", confident))

    def test_model_fallback_runs_when_rules_low_confidence(self):
        from app.services.v2.intent_classifier import IntentClassifier
        from app.services.v2.models import FollowupType

        classifier = IntentClassifier()
        uncertain = SimpleNamespace(
            answer_mode=AnswerMode.FOLLOWUP,
            confidence=0.86,
            followup_type=FollowupType.OTHER,
        )
        self.assertTrue(classifier._should_use_model_fallback("那个咋整啊", uncertain))

    def test_default_recommendation_with_strong_slots_skips_model(self):
        from app.services.v2.intent_classifier import IntentClassifier

        classifier = IntentClassifier()
        # 规则兜底到默认推荐，但已抽到品类/诉求这类强槽位 -> 意图已明确，不该再烧模型
        default_rec = SimpleNamespace(
            answer_mode=AnswerMode.RECOMMENDATION,
            confidence=0.85,
            reason="default_recommendation",
            followup_type=None,
        )
        self.assertFalse(
            classifier._should_use_model_fallback(
                "敏感肌面霜推荐", default_rec, {"category": "面霜", "concerns": ["修护"]}
            )
        )

    def test_default_recommendation_without_slots_still_uses_model(self):
        from app.services.v2.intent_classifier import IntentClassifier

        classifier = IntentClassifier()
        # 规则兜底到默认推荐且没有任何强槽位 -> 意图不明确，仍需模型兜底
        default_rec = SimpleNamespace(
            answer_mode=AnswerMode.RECOMMENDATION,
            confidence=0.85,
            reason="default_recommendation",
            followup_type=None,
        )
        self.assertTrue(
            classifier._should_use_model_fallback("这玩意到底啥原理啊", default_rec, {})
        )

    def test_named_rule_verdict_below_threshold_is_trusted_not_sent_to_model(self):
        from app.services.v2.intent_classifier import IntentClassifier

        classifier = IntentClassifier()
        # 规则已明确判成 knowledge(具名 reason)，即便置信度0.75低于阈值，也不该再烧模型
        knowledge = SimpleNamespace(
            answer_mode=AnswerMode.KNOWLEDGE,
            confidence=0.75,
            reason="knowledge_query",
            followup_type=None,
        )
        self.assertFalse(
            classifier._should_use_model_fallback("烟酰胺是什么", knowledge, {})
        )

    def test_model_fallback_result_is_ignored_when_it_invents_unknown_mode(self):
        from app.services.v2.intent_classifier import IntentClassifier

        classifier = IntentClassifier()
        # 模型返回不认识的 mode -> 必须被丢弃，保持 None
        self.assertIsNone(classifier._coerce_model_intent({"mode": "banana"}))
        self.assertIsNone(classifier._coerce_model_intent({}))

    def test_model_fallback_result_maps_known_mode(self):
        from app.services.v2.intent_classifier import IntentClassifier

        intent = IntentClassifier()._coerce_model_intent(
            {"mode": "knowledge", "reason": "user_asks_definition"}
        )
        self.assertIsNotNone(intent)
        self.assertEqual(intent.answer_mode, AnswerMode.KNOWLEDGE)

    def test_classify_does_not_call_model_when_flag_disabled(self):
        from app.services.v2.intent_classifier import IntentClassifier

        classifier = IntentClassifier()
        called = {"n": 0}

        def _boom(*args, **kwargs):
            called["n"] += 1
            return None

        classifier._model_intent_fallback = _boom
        # 默认 INTENT_LLM_ENABLED=False，明确的推荐语句不该触发模型
        intent = classifier.classify("300以内防晒", [], {"category": "防晒"})
        self.assertEqual(called["n"], 0)
        self.assertEqual(intent.answer_mode, AnswerMode.RECOMMENDATION)

    def test_classify_uses_model_result_when_enabled_and_uncertain(self):
        from app.services.v2.intent_classifier import IntentClassifier

        classifier = IntentClassifier()
        classifier.model_fallback_enabled = True
        seen = {"msg": None}

        def _fake_model(msg, history, slots, rule_intent):
            seen["msg"] = msg
            return classifier._coerce_model_intent({"mode": "knowledge"})

        classifier._model_intent_fallback = _fake_model
        # 一句规则很难拦住的模糊话，落到低置信默认推荐 -> 模型兜底改判 knowledge
        intent = classifier.classify("这玩意到底啥原理啊", [], {})
        self.assertEqual(seen["msg"], "这玩意到底啥原理啊")
        self.assertEqual(intent.answer_mode, AnswerMode.KNOWLEDGE)


class V2IntentAsyncModelFallbackTest(unittest.TestCase):
    """异步意图兜底：这是真正能在 FastAPI 事件循环里生效的路径。

    旧实现用 asyncio.run() 在运行中的循环里会崩，模型永远接不上。
    这里锁定 classify_async 用 await 调模型，且默认行为与 sync classify 等价。
    """

    def test_classify_async_matches_sync_when_flag_disabled(self):
        from app.services.v2.intent_classifier import IntentClassifier

        classifier = IntentClassifier()
        sync_intent = classifier.classify("300以内防晒", [], {"category": "防晒"})
        async_intent = asyncio.run(
            classifier.classify_async("300以内防晒", [], {"category": "防晒"})
        )
        self.assertEqual(async_intent.answer_mode, sync_intent.answer_mode)
        self.assertEqual(async_intent.answer_mode, AnswerMode.RECOMMENDATION)

    def test_classify_async_does_not_call_model_when_rules_confident(self):
        from app.services.v2.intent_classifier import IntentClassifier

        classifier = IntentClassifier()
        classifier.model_fallback_enabled = True
        called = {"n": 0}

        async def _boom(*args, **kwargs):
            called["n"] += 1
            return None

        classifier._model_intent_fallback_async = _boom
        # 显式对比是高置信规则(0.95)，不该触发模型兜底
        intent = asyncio.run(
            classifier.classify_async(
                "小棕瓶和小黑瓶哪个好", [], {"compare_targets": ["小棕瓶", "小黑瓶"]}
            )
        )
        self.assertEqual(called["n"], 0)
        self.assertEqual(intent.answer_mode, AnswerMode.COMPARE)

    def test_classify_async_awaits_model_when_enabled_and_uncertain(self):
        from app.services.v2.intent_classifier import IntentClassifier

        classifier = IntentClassifier()
        classifier.model_fallback_enabled = True
        seen = {"msg": None}

        async def _fake_model(msg, history, slots, rule_intent):
            seen["msg"] = msg
            return classifier._coerce_model_intent({"mode": "knowledge"})

        classifier._model_intent_fallback_async = _fake_model
        intent = asyncio.run(
            classifier.classify_async("这玩意到底啥原理啊", [], {})
        )
        self.assertEqual(seen["msg"], "这玩意到底啥原理啊")
        self.assertEqual(intent.answer_mode, AnswerMode.KNOWLEDGE)

    def test_classify_async_falls_back_to_rules_when_model_raises(self):
        from app.services.v2.intent_classifier import IntentClassifier

        classifier = IntentClassifier()
        classifier.model_fallback_enabled = True

        async def _explode(*args, **kwargs):
            raise RuntimeError("model down")

        classifier._model_intent_fallback_async = _explode
        # 模型连不上/抛错 -> 必须回退规则结果，不能让请求崩
        intent = asyncio.run(
            classifier.classify_async("这玩意到底啥原理啊", [], {})
        )
        self.assertIsNotNone(intent)
        self.assertIn(intent.answer_mode, set(AnswerMode))

    def test_colloquial_intents_rescued_by_model_when_rules_miss(self):
        """真实痛点回归：一批意图明确但很口语的绕话句，规则会漏判成默认推荐，

        模型兜底必须把它们救回正确 mode。用 mock 模型按 mode 关键词返回，
        确定性验证"规则漏 -> 模型接住"这条链路，不依赖真实 LLM。
        """
        from app.services.v2.intent_classifier import IntentClassifier

        classifier = IntentClassifier()
        classifier.model_fallback_enabled = True

        async def _fake_model(msg, history, slots, rule_intent):
            if any(k in msg for k in ["原理", "是不是就是", "为啥"]):
                return classifier._coerce_model_intent({"mode": "knowledge"})
            if any(k in msg for k in ["便宜点", "更能打"]):
                return classifier._coerce_model_intent({"mode": "followup"})
            if any(k in msg for k in ["选哪个", "纠结"]):
                return classifier._coerce_model_intent({"mode": "compare"})
            if any(k in msg for k in ["能整这个", "别碰", "能用吗"]):
                return classifier._coerce_model_intent({"mode": "judgement"})
            return None

        classifier._model_intent_fallback_async = _fake_model

        cases = [
            ("这东西到底是啥原理啊", AnswerMode.KNOWLEDGE),
            ("视黄醇是不是就是A醇换了个名字", AnswerMode.KNOWLEDGE),
            ("我这种一擦就红的皮能整这个吗", AnswerMode.JUDGEMENT),
            ("孕期是不是最好别碰这类", AnswerMode.JUDGEMENT),
            ("这俩到底选哪个不亏", AnswerMode.COMPARE),
            ("有没有再便宜点的路子", AnswerMode.FOLLOWUP),
            ("能不能整个更能打的", AnswerMode.FOLLOWUP),
        ]
        for msg, expected in cases:
            intent = asyncio.run(classifier.classify_async(msg, [], {}))
            self.assertEqual(intent.answer_mode, expected, f"绕话未被救回: {msg}")

    def test_clear_recommendation_with_slots_not_sent_to_model(self):
        """反向保护：标准推荐句(带品类/诉求)不能被送去模型，避免每条推荐平白烧钱。"""
        from app.services.v2.intent_classifier import IntentClassifier

        classifier = IntentClassifier()
        classifier.model_fallback_enabled = True
        called = {"n": 0}

        async def _count(*args, **kwargs):
            called["n"] += 1
            return None

        classifier._model_intent_fallback_async = _count
        intent = asyncio.run(
            classifier.classify_async(
                "敏感肌面霜推荐", [], {"category": "面霜", "concerns": ["修护"]}
            )
        )
        self.assertEqual(called["n"], 0)
        self.assertEqual(intent.answer_mode, AnswerMode.RECOMMENDATION)


class V2AsyncParseTest(unittest.TestCase):
    def test_parse_async_matches_sync_parse_for_budget_and_skin_inheritance(self):
        parser = TurnParser()
        history = [{"role": "user", "content": "我是干敏肌，预算300以内"}]

        sync_turn = parser.parse("想要修护面霜", conversation_history=history)
        async_turn = asyncio.run(
            parser.parse_async("想要修护面霜", conversation_history=history)
        )

        self.assertEqual(async_turn.intent, sync_turn.intent)
        self.assertEqual(async_turn.intent, AnswerMode.RECOMMENDATION)
        self.assertEqual(async_turn.skin_type, "干敏肌")
        self.assertEqual(async_turn.budget_max, 300)
        self.assertIsNone(async_turn.budget_min)

    def test_parse_async_matches_sync_parse_for_followup_ingredient(self):
        parser = TurnParser()
        history = [{"role": "assistant", "content": "雅诗兰黛小棕瓶\n赫莲娜绿宝瓶"}]

        sync_turn = parser.parse("第一款咖啡因有什么用", conversation_history=history)
        async_turn = asyncio.run(
            parser.parse_async("第一款咖啡因有什么用", conversation_history=history)
        )

        self.assertEqual(async_turn.intent, sync_turn.intent)
        self.assertEqual(async_turn.followup_type, sync_turn.followup_type)
        self.assertEqual(async_turn.followup_type, FollowupType.INGREDIENT)
        self.assertEqual(async_turn.referenced_products, ["小棕瓶"])


class V2SemanticEmbeddingIntentTest(unittest.TestCase):
    """语义向量意图层：规则/字符向量没拦住时，用真语义命中意图样本。

    用可注入的假 encode(不联网)确定性验证：绕话被语义层接住、embedding挂了整层跳过。
    """

    def _matcher(self, encode_batch, min_score=0.55):
        from app.services.v2.semantic_embedding_intent import SemanticEmbeddingIntentMatcher
        from app.services.v2.semantic_intent_retriever import IntentSample
        samples = [
            IntentSample(text="帮我对比两款哪个好", answer_mode=AnswerMode.COMPARE, label="compare"),
            IntentSample(text="敏感肌能不能用", answer_mode=AnswerMode.JUDGEMENT, label="judgement"),
            IntentSample(text="烟酰胺是什么意思", answer_mode=AnswerMode.KNOWLEDGE, label="knowledge"),
        ]
        return SemanticEmbeddingIntentMatcher(samples, encode_batch=encode_batch, min_score=min_score)

    def test_semantic_matcher_hits_paraphrase(self):
        # 假 encode：给"对比类"词一个方向、其它另一个方向，模拟语义相近
        async def fake_encode(texts):
            out = []
            for t in texts:
                if any(k in t for k in ["对比", "选哪个", "哪个好", "纠结"]):
                    out.append([1.0, 0.0, 0.0])
                elif any(k in t for k in ["能用", "能不能", "适合"]):
                    out.append([0.0, 1.0, 0.0])
                else:
                    out.append([0.0, 0.0, 1.0])
            return out

        matcher = self._matcher(fake_encode)
        hit = asyncio.run(matcher.match("这俩到底选哪个不亏", has_history=False))
        self.assertIsNotNone(hit)
        self.assertEqual(hit.answer_mode, AnswerMode.COMPARE)

    def test_semantic_matcher_returns_none_below_threshold(self):
        async def fake_encode(texts):
            # query 与所有样本都正交 -> 相似度0 -> 不该命中
            out = []
            for t in texts:
                if "外星" in t:
                    out.append([0.0, 0.0, 0.0, 1.0])
                else:
                    out.append([1.0, 0.0, 0.0, 0.0])
            return out
        matcher = self._matcher(fake_encode)
        hit = asyncio.run(matcher.match("外星科技降维打击", has_history=False))
        self.assertIsNone(hit)

    def test_semantic_matcher_degrades_when_encode_raises(self):
        async def boom_encode(texts):
            raise RuntimeError("embedding down")
        matcher = self._matcher(boom_encode)
        # embedding 抛错 -> 必须返回 None，绝不崩
        hit = asyncio.run(matcher.match("这俩到底选哪个不亏", has_history=False))
        self.assertIsNone(hit)

    def test_semantic_matcher_degrades_when_encode_returns_empty(self):
        async def empty_encode(texts):
            return []
        matcher = self._matcher(empty_encode)
        hit = asyncio.run(matcher.match("这俩到底选哪个不亏", has_history=False))
        self.assertIsNone(hit)

    def test_semantic_matcher_excludes_history_samples_when_no_history(self):
        """首轮无历史时，依赖上下文的样本(如 followup)不能参与匹配，避免绕话被误判成追问。"""
        from app.services.v2.semantic_embedding_intent import SemanticEmbeddingIntentMatcher
        from app.services.v2.semantic_intent_retriever import IntentSample

        async def fake_encode(texts):
            # 让 query 与 followup 样本方向完全一致(相似度=1)，与 knowledge 正交
            out = []
            for t in texts:
                if "便宜" in t or "追问" in t:
                    out.append([1.0, 0.0])
                else:
                    out.append([0.0, 1.0])
            return out

        samples = [
            IntentSample(text="有没有便宜点的追问", answer_mode=AnswerMode.FOLLOWUP,
                         followup_type=FollowupType.CHEAPER, needs_history=True, label="followup_cheaper"),
            IntentSample(text="烟酰胺是什么", answer_mode=AnswerMode.KNOWLEDGE, label="knowledge"),
        ]
        matcher = SemanticEmbeddingIntentMatcher(samples, encode_batch=fake_encode, min_score=0.5)
        # 无历史：即便和 followup 样本相似度最高，也必须跳过它，返回 None(不误判成追问)
        hit = asyncio.run(matcher.match("有没有便宜点的路子", has_history=False))
        self.assertIsNone(hit)
        # 有历史：followup 样本可参与，正常命中
        hit2 = asyncio.run(matcher.match("有没有便宜点的路子", has_history=True))
        self.assertIsNotNone(hit2)
        self.assertEqual(hit2.answer_mode, AnswerMode.FOLLOWUP)


class V2ClassifierSemanticLayerTest(unittest.TestCase):
    def _make_classifier(self, fake_match):
        from app.services.v2.intent_classifier import IntentClassifier
        c = IntentClassifier()
        c.model_fallback_enabled = True
        c.semantic_embedding_enabled = True
        c._semantic_intent_match = fake_match  # 注入假语义层
        return c

    def test_semantic_layer_rescues_before_model(self):
        from app.services.v2.models import SemanticIntent

        async def fake_match(msg, has_history):
            return SemanticIntent(AnswerMode.COMPARE, 0.7, "semantic_embedding:compare")

        model_called = {"n": 0}

        async def model_boom(*a, **k):
            model_called["n"] += 1
            return None

        c = self._make_classifier(fake_match)
        c._model_intent_fallback_async = model_boom
        intent = asyncio.run(c.classify_async("这俩到底选哪个不亏", [], {}))
        self.assertEqual(intent.answer_mode, AnswerMode.COMPARE)
        self.assertEqual(model_called["n"], 0)  # 语义层命中就不该再调大模型

    def test_model_still_used_when_semantic_layer_misses(self):
        from app.services.v2.models import SemanticIntent

        async def fake_match(msg, has_history):
            return None  # 语义层没把握

        async def fake_model(msg, history, slots, rule_intent):
            return SemanticIntent(AnswerMode.KNOWLEDGE, 0.7, "model_fallback:x")

        c = self._make_classifier(fake_match)
        c._model_intent_fallback_async = fake_model
        intent = asyncio.run(c.classify_async("这玩意到底啥原理啊", [], {}))
        self.assertEqual(intent.answer_mode, AnswerMode.KNOWLEDGE)

    def test_semantic_layer_skipped_when_rules_confident(self):
        called = {"n": 0}

        async def fake_match(msg, has_history):
            called["n"] += 1
            return None

        c = self._make_classifier(fake_match)
        # 显式对比是高置信规则，语义层不该被触发
        asyncio.run(c.classify_async("小棕瓶和小黑瓶哪个好", [], {"compare_targets": ["小棕瓶", "小黑瓶"]}))
        self.assertEqual(called["n"], 0)


if __name__ == "__main__":
    unittest.main()
