#!/usr/bin/env python3
import sys
import asyncio
from pathlib import Path
from typing import List, Dict, Any, Tuple

sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except Exception:
    pass

from app.services.v2.models import AnswerMode, FollowupType
from app.services.v2.turn_parser import TurnParser
from app.services.v2.router import Router
from app.services.v2.retriever import Retriever
from app.services.v2.ranker import Ranker
from app.services.v2.presenter import Presenter
from app.database.postgres import init_postgres_pool


SINGLE_QUERIES = [
    ("推荐-油皮精华", "油皮精华预算300以内"),
    ("推荐-敏感肌面霜", "敏感肌面霜500以内"),
    ("推荐-干皮防晒通勤", "干皮防晒200以内通勤用"),
    ("推荐-美白精华", "300以内美白精华"),
    ("推荐-油皮防晒清爽", "油皮防晒清爽不油腻"),
    ("推荐-抗初老精华", "抗初老精华800左右"),
    ("推荐-敏感肌洁面", "敏感肌洁面温和不紧绷"),
    ("推荐-油皮爽肤水", "油皮爽肤水控油"),
    ("推荐-干皮眼霜", "干皮眼霜不长脂肪粒"),
    ("推荐-补水面膜", "补水面膜敏感肌可用"),
    ("推荐-持妆粉底", "持妆粉底液油皮适用"),
    ("推荐-屏障修护", "修护屏障面霜"),
    ("推荐-油皮乳液", "油皮乳液清爽"),
    ("推荐-淡斑精华", "淡斑精华"),
    ("别名-ANR", "ANR小棕瓶怎么样"),
    ("别名-SK2", "SK2神仙水怎么样"),
    ("别名-小金瓶", "小金瓶防晒怎么样"),
    ("别名-粉水", "粉水适合干皮吗"),
    ("别名-菌菇水", "菌菇水敏感肌能用吗"),
    ("别名-紫米", "紫米精华怎么样"),
    ("别名-B5", "B5霜怎么样"),
    ("别名-小白瓶", "小白瓶美白怎么样"),
    ("别名-绿宝瓶", "绿宝瓶抗老怎么样"),
    ("对比-棕瓶黑瓶", "小棕瓶和小黑瓶哪个好"),
    ("对比-B5玉泽", "理肤泉B5和玉泽哪个适合敏感肌"),
    ("对比-安热沙小白管", "安热沙小金瓶和兰蔻小白管哪个通勤好"),
    ("对比-DW权力", "雅诗兰黛DW和阿玛尼权力哪个适合油皮"),
    ("知识-早C晚A", "早C晚A是什么"),
    ("知识-烟酰胺", "烟酰胺有什么作用"),
    ("知识-玻色因A醇", "玻色因和A醇区别"),
    ("判断-单品油敏", "敏感肌能用A醇吗"),
]

FOLLOWUP_SEQUENCES = [
    ("追问-价格", ["油皮精华300以内", "第一款多少钱"]),
    ("追问-适配油肌", ["油皮精华300以内", "第二款对油肌友好吗"]),
    ("追问-成分", ["小棕瓶怎么样", "有什么成分"]),
    ("追问-成分烟酰胺", ["OLAY淡斑小白瓶怎么样", "含不含烟酰胺"]),
    ("追问-平替", ["赫莲娜绿宝瓶怎么样", "有没有平替"]),
    ("追问-更便宜", ["修丽可CE精华怎么样", "有没有更便宜的"]),
    ("追问-用法日夜", ["A醇精华怎么样", "白天用还是晚上用"]),
    ("追问-价格SK2", ["SK2神仙水怎么样", "多少钱"]),
    ("多轮-肤质+品类+预算", ["我是油皮", "想要精华", "300以内"]),
    ("追问-敏感B5", ["理肤泉B5怎么样", "敏感肌可以用吗"]),
]


def format_result(idx: int, label: str, query: str, result: Dict[str, Any], turn: Any, is_followup: bool = False) -> str:
    lines = []
    mode = result.get("answer_mode")
    mode_str = mode.value if hasattr(mode, "value") else str(mode)
    products = result.get("products") or []
    text = result.get("text") or ""

    issues = []

    expected_count = None
    ftype_str = ""
    if mode_str == AnswerMode.RECOMMENDATION.value:
        expected_count = 3
    elif mode_str == AnswerMode.COMPARE.value:
        expected_count = 2
    elif mode_str == AnswerMode.JUDGEMENT.value:
        expected_count = 3 if not (turn and turn.image_context) else 1
    elif mode_str == AnswerMode.FOLLOWUP.value:
        ftype = turn.followup_type if turn else None
        ftype_str = ftype.value if hasattr(ftype, "value") else str(ftype or "")
        has_anchor = bool(turn.referenced_products or turn.name_clues) if turn else False
        if ftype_str in {FollowupType.PRICE.value, FollowupType.INGREDIENT.value, FollowupType.USAGE_TIME.value} or has_anchor:
            expected_count = 1
        elif ftype_str == FollowupType.CHEAPER.value:
            expected_count = 3
        else:
            expected_count = 3

    if expected_count is not None and len(products) > expected_count:
        issues.append(f"商品数超预期: 期望≤{expected_count}, 实际{len(products)}")

    if mode_str == AnswerMode.FOLLOWUP.value and len(products) >= 1 and expected_count == 1:
        p = products[0]
        price = p.get("price_val") or p.get("price") or 0
        spec = Presenter._extract_product_spec(p)
        if price and not spec and "ml" not in text and "g" not in text and "价格见详情" not in text:
            issues.append("价格可能没带规格")

    dirty_markers = ["【数据来源】", "OCR", "产地参数", "备案号", "生产企业"]
    for marker in dirty_markers:
        if marker in text:
            issues.append(f"含脏文本: {marker}")

    stiff_markers = ["预算贴合款", "性价比首选", "特殊场景款"]
    for marker in stiff_markers:
        if marker in text:
            issues.append(f"仍有生硬标签: {marker}")

    if mode_str == AnswerMode.NO_MATCH.value and not any(k in query for k in ["你好", "在吗"]):
        if len(query) > 4:
            issues.append("误判为NO_MATCH")

    issue_str = " ⚠️ " + " | ".join(issues) if issues else " ✅"

    indent = "  ↳ " if is_followup else ""
    lines.append(f"{indent}[{idx+1:02d}] {label} | {query}")
    lines.append(f"{indent}  意图: {mode_str}" + (f"/{ftype_str}" if ftype_str else "") + f" | 商品数: {len(products)} | 肤质: {turn.skin_type or '-' if turn else '-'}")
    prod_parts = []
    for p in products[:3]:
        price_v = int(p.get("price_val") or p.get("price") or 0)
        spec_v = Presenter._extract_product_spec(p)
        name_v = Presenter._short_product_name(p)
        prod_parts.append(f"{name_v}({price_v}{'/' + spec_v if spec_v else ''})")
    lines.append(f"{indent}  商品: " + "、".join(prod_parts))
    lines.append(f"{indent}  状态: {issue_str}")

    preview = text[:240].replace("\n", " ")
    if len(text) > 240:
        preview += "..."
    lines.append(f"{indent}  文案: {preview}")

    return "\n".join(lines)


async def run_pipeline(
    parser: TurnParser,
    router: Router,
    retriever: Retriever,
    ranker: Ranker,
    presenter: Presenter,
    query: str,
    session_id: str,
    history: List[Dict] = None,
) -> Tuple[Dict[str, Any], Any]:
    turn = await parser.parse_async(
        raw_message=query,
        session_id=session_id,
        conversation_history=history or [],
    )
    route = router.route(turn)

    products = []
    if route.answer_mode == AnswerMode.NO_MATCH:
        result = presenter.present_no_match(turn)
    elif route.answer_mode == AnswerMode.KNOWLEDGE:
        def _is_general_knowledge(query: str) -> bool:
            text = str(query or "")
            if any(term in text for term in ["早C晚A", "早c晚a", "早C", "晚A"]):
                return True
            if any(term in text for term in ["是什么", "什么是", "有什么用", "有什么作用", "什么作用", "什么意思", "科普", "解释"]):
                if any(term in text for term in ["玻色因", "A醇", "视黄醇", "视黄醛", "烟酰胺", "酸类", "水杨酸", "果酸", "维C", "早C晚A", "神经酰胺", "二裂酵母"]):
                    return True
            if any(term in text for term in ["玻色因", "A醇", "视黄醇", "视黄醛", "烟酰胺", "酸类", "水杨酸", "果酸", "维C"]) and any(
                cue in text for cue in ["区别", "差异", "一起用", "同时用", "混用", "搭配", "白天", "晚上", "能用吗", "哪个好"]
            ):
                return True
            return False

        if _is_general_knowledge(turn.raw_message):
            related_products = []
        else:
            try:
                related_products = await _retrieve_for_turn(retriever, turn)
                related_products = ranker.rank(related_products, turn, top_n=3)
            except Exception:
                related_products = []
        result = presenter.present_knowledge(turn, related_products=related_products)
    elif route.answer_mode == AnswerMode.COMPARE:
        if turn.compare_targets:
            seen_ids = set()
            for target in turn.compare_targets[:2]:
                matches = await retriever.retrieve_by_name_fuzzy(target, limit=3)
                if matches:
                    ranked = ranker.rank(matches, turn, top_n=3)
                    for p in ranked:
                        if p["id"] not in seen_ids:
                            products.append(p)
                            seen_ids.add(p["id"])
                            break
            if len(products) < 2:
                candidates = await _retrieve_for_turn(retriever, turn)
                products = ranker.rank(candidates, turn, top_n=2)
        else:
            candidates = await _retrieve_for_turn(retriever, turn)
            products = ranker.rank(candidates, turn, top_n=2)
        result = presenter.present_compare(turn, products)
    elif route.answer_mode == AnswerMode.FOLLOWUP:
        candidates = await _retrieve_for_followup(retriever, turn)
        products = ranker.rank(candidates, turn, top_n=4)
        result = presenter.present_followup(turn, products)
    elif route.answer_mode == AnswerMode.JUDGEMENT:
        candidates = await _retrieve_for_turn(retriever, turn)
        products = ranker.rank(candidates, turn, top_n=3)
        if not products and turn.brand:
            matches = await retriever.retrieve_by_name_fuzzy(turn.brand, limit=5)
            products = ranker.rank(matches, turn, top_n=3)
        result = presenter.present_judgement(turn, products)
    else:
        candidates = await _retrieve_for_turn(retriever, turn)
        products = ranker.rank(candidates, turn, top_n=8)
        result = presenter.present_recommendation(turn, products)

    result = _apply_guard(turn, route.answer_mode, result)
    result["products"] = _serialize_products_for_output(result.get("products") or [])

    return result, turn


def _serialize_products_for_output(products: List[Dict]) -> List[Dict]:
    out = []
    for p in products:
        item = dict(p)
        item["spec"] = Presenter._extract_product_spec(p)
        out.append(item)
    return out


def _apply_guard(turn: Any, answer_mode: Any, result: Dict) -> Dict:
    try:
        products = result.get("products") or []
        mode_str = answer_mode.value if hasattr(answer_mode, "value") else str(answer_mode)
        max_expected = None

        if mode_str == AnswerMode.FOLLOWUP.value:
            ftype = turn.followup_type
            ftype_str = ftype.value if hasattr(ftype, "value") else str(ftype or "")
            has_anchor = bool(turn.referenced_products or turn.name_clues)
            if ftype_str in {FollowupType.PRICE.value, FollowupType.INGREDIENT.value, FollowupType.USAGE_TIME.value} or has_anchor:
                max_expected = 1
            elif ftype_str == FollowupType.SUITABILITY.value and has_anchor:
                max_expected = 1
        elif mode_str == AnswerMode.COMPARE.value:
            max_expected = 2
        elif mode_str == AnswerMode.RECOMMENDATION.value:
            max_expected = 3
        elif mode_str == AnswerMode.JUDGEMENT.value:
            max_expected = 1 if turn.image_context else 3

        if max_expected is not None and len(products) > max_expected:
            result["products"] = products[:max_expected]
        return result
    except Exception:
        return result


async def _retrieve_for_turn(retriever: Retriever, turn: Any) -> List[Dict]:
    db_products = await retriever.retrieve_products(
        category=turn.category,
        brand=turn.brand,
        concerns=turn.concerns if turn.concerns else None,
        skin_type=turn.skin_type,
        budget_min=turn.budget_min,
        budget_max=turn.budget_max,
        limit=20,
    )
    # 多级 fallback：带concerns查不到时放松功效词，保留预算
    if not db_products and turn.category and turn.concerns and (turn.budget_min is not None or turn.budget_max is not None):
        db_products = await retriever.retrieve_products(
            category=turn.category,
            brand=turn.brand,
            budget_min=turn.budget_min,
            budget_max=turn.budget_max,
            limit=20,
        )
    elif len(db_products) < 3 and turn.category and turn.concerns and (turn.budget_min is not None or turn.budget_max is not None):
        supplements = await retriever.retrieve_products(
            category=turn.category,
            brand=turn.brand,
            budget_min=turn.budget_min,
            budget_max=turn.budget_max,
            limit=20,
        )
        seen = {p["id"] for p in db_products}
        for p in supplements:
            if p["id"] not in seen:
                db_products.append(p)
                seen.add(p["id"])
    # 仍为空：放宽预算±35%
    if not db_products and turn.category and turn.budget_min is not None and turn.budget_max is not None:
        mid_budget = (turn.budget_min + turn.budget_max) / 2
        db_products = await retriever.retrieve_products(
            category=turn.category,
            brand=turn.brand,
            budget_min=mid_budget * 0.65,
            budget_max=mid_budget * 1.35,
            limit=20,
        )
    # 仍为空：不带concerns、不带预算，按category+brand查
    if not db_products:
        db_products = await retriever.retrieve_products(
            category=turn.category,
            brand=turn.brand,
            skin_type=turn.skin_type,
            limit=20,
        )
    # 单品场景：brand+别名检索
    if not db_products and turn.brand:
        try:
            name_clues = list(getattr(turn, 'name_clues', None) or [])
            if turn.brand:
                name_clues.insert(0, turn.brand)
            for clue in name_clues[:2]:
                matches = await retriever.retrieve_by_name_fuzzy(clue, limit=8)
                if matches:
                    db_products = matches
                    break
        except Exception:
            pass
    return db_products


async def _retrieve_for_followup(retriever: Retriever, turn: Any) -> List[Dict]:
    products = []
    if turn.brand:
        products = await retriever.retrieve_by_name_fuzzy(turn.brand, limit=8)
    if not products and turn.referenced_products:
        for ref in turn.referenced_products[:2]:
            matches = await retriever.retrieve_by_name_fuzzy(ref, limit=5)
            products.extend(matches)
    if not products:
        products = await _retrieve_for_turn(retriever, turn)
    return products


async def run_batch_test():
    try:
        await init_postgres_pool()
        print("✅ 数据库连接池初始化成功")
    except Exception as e:
        print(f"⚠️ 数据库初始化失败: {e}")
        return

    parser = TurnParser()
    router = Router()
    retriever = Retriever()
    ranker = Ranker()
    presenter = Presenter()

    results_all = []
    issues_found = []
    idx = 0

    for label, query in SINGLE_QUERIES:
        try:
            result, turn = await run_pipeline(parser, router, retriever, ranker, presenter, query, f"batch_{idx}")
            output = format_result(idx, label, query, result, turn)
            results_all.append(output)
            if "⚠️" in output:
                issues_found.append((idx, query, output))
        except Exception as e:
            import traceback
            results_all.append(f"[{idx+1:02d}] {label} | {query} ❌ 异常: {e}")
            issues_found.append((idx, query, f"{e}\n{traceback.format_exc()[-300:]}"))
        idx += 1

    for label, sequence in FOLLOWUP_SEQUENCES:
        history = []
        session_id = f"batch_seq_{idx}"
        seq_outputs = []
        for q_idx, query in enumerate(sequence):
            try:
                result, turn = await run_pipeline(parser, router, retriever, ranker, presenter, query, session_id, history)
                output = format_result(idx, label, query, result, turn, is_followup=(q_idx > 0))
                seq_outputs.append(output)
                if "⚠️" in output:
                    issues_found.append((idx, query, output))
                history.append({"role": "user", "content": query})
                history.append({"role": "assistant", "content": result.get("text", "")})
            except Exception as e:
                import traceback
                seq_outputs.append(f"  ↳ [{q_idx+1}] {query} ❌ 异常: {e}")
                issues_found.append((idx, query, f"{e}\n{traceback.format_exc()[-300:]}"))
        results_all.append(f"{'─'*70}")
        results_all.append(f"[{idx+1:02d}] === {label} (多轮序列) ===")
        results_all.extend(seq_outputs)
        idx += 1

    total = len(SINGLE_QUERIES) + len(FOLLOWUP_SEQUENCES)
    print("\n".join(results_all))
    print(f"\n{'='*80}")
    print(f"测试完成: 共{total}组场景, 发现{len(issues_found)}个问题")
    if issues_found:
        print("\n问题汇总:")
        for i, (qidx, q, issue) in enumerate(issues_found, 1):
            print(f"  {i}. [{qidx+1:02d}] {q}")
            issue_lines = [l for l in issue.split("\n") if "⚠️" in l or "❌" in l]
            if issue_lines:
                print(f"     {issue_lines[0].strip()[:200]}")


if __name__ == "__main__":
    asyncio.run(run_batch_test())
