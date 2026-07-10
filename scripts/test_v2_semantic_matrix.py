"""V2 backend semantic matrix.

Backend-only checks for common paraphrases. This script intentionally avoids
frontend DOM assertions; it verifies the SSE contract and semantic stability.
"""
import sys
import uuid

from app.services.v2.semantic_intent_retriever import SemanticIntentRetriever
from scripts.test_v2_golden_cases import post_chat_raw, check_chat_result


EXPECTED_SAMPLE_LABELS = {
    "recommendation",
    "compare",
    "judgement",
    "knowledge",
    "followup_price",
    "followup_ingredient",
    "followup_suitability",
    "followup_cheaper",
    "followup_usage_day",
    "followup_usage_night",
    "followup_usage_both",
}


RECOMMENDATION_CASES = [
    {
        "name": "中文数字-三百以内面霜",
        "msg": "三百以内面霜",
        "expect_intent": "recommendation",
        "min_products": 3,
        "must_product_categories": ["面霜"],
        "product_price_max": 300,
        "price_check_count": 3,
        "must_contain": ["综合建议"],
        "min_length": 120,
    },
    {
        "name": "中文数字-面霜预算三百以内",
        "msg": "面霜预算三百以内",
        "expect_intent": "recommendation",
        "min_products": 3,
        "must_product_categories": ["面霜"],
        "product_price_max": 300,
        "price_check_count": 3,
        "must_contain": ["综合建议"],
        "min_length": 120,
    },
    {
        "name": "中文数字-一千左右干敏抗初老精华",
        "msg": "预算一千左右，干敏肌抗初老精华",
        "expect_intent": "recommendation",
        "min_products": 3,
        "must_product_categories": ["精华"],
        "product_price_min": 800,
        "product_price_max": 1200,
        "price_check_count": 3,
        "must_contain": ["综合建议"],
        "min_length": 160,
    },
    {
        "name": "中文数字-一千上下干敏抗老精华",
        "msg": "干敏肌抗老精华，一千上下",
        "expect_intent": "recommendation",
        "min_products": 3,
        "must_product_categories": ["精华"],
        "product_price_min": 800,
        "product_price_max": 1200,
        "price_check_count": 3,
        "must_contain": ["综合建议"],
        "min_length": 160,
    },
    {
        "name": "口语推荐-通勤防晒两百内",
        "msg": "通勤防晒两百以内，别太油",
        "expect_intent": "recommendation",
        "min_products": 2,
        "must_product_categories": ["防晒"],
        "product_price_max": 200,
        "price_check_count": 2,
        "must_contain": ["综合建议"],
        "min_length": 120,
    },
    {
        "name": "口语推荐-干皮屏障面霜",
        "msg": "干皮秋冬想找个三百以内修护屏障的面霜",
        "expect_intent": "recommendation",
        "min_products": 3,
        "must_product_categories": ["面霜"],
        "product_price_max": 300,
        "price_check_count": 3,
        "must_contain": ["综合建议"],
        "min_length": 120,
    },
    {
        "name": "口语推荐-油皮精华清爽",
        "msg": "油皮想要清爽点的精华，别太黏",
        "expect_intent": "recommendation",
        "min_products": 2,
        "must_product_categories": ["精华"],
        "must_contain": ["综合建议"],
        "min_length": 120,
    },
    {
        "name": "口语推荐-学生党洁面",
        "msg": "学生党想买温和洗面奶，一百以内",
        "expect_intent": "recommendation",
        "min_products": 2,
        "must_product_categories": ["洁面"],
        "product_price_max": 100,
        "price_check_count": 2,
        "must_contain": ["综合建议"],
        "min_length": 100,
    },
    {
        "name": "口语推荐-熬夜眼霜",
        "msg": "经常熬夜，想找个眼霜改善细纹和黑眼圈",
        "expect_intent": "recommendation",
        "min_products": 2,
        "must_product_categories": ["眼霜"],
        "must_contain": ["综合建议"],
        "min_length": 100,
    },
]

DIRECT_INTENT_CASES = [
    {
        "name": "单品判断-小棕瓶年龄",
        "msg": "小棕瓶适合多大年龄用",
        "expect_intent": "judgement",
        "min_products": 1,
        "must_product_brands": ["雅诗兰黛"],
        "must_contain": ["能不能用"],
        "min_length": 80,
    },
    {
        "name": "单品判断-B5闷痘",
        "msg": "理肤泉B5会不会闷痘",
        "expect_intent": "judgement",
        "min_products": 1,
        "must_product_brands": ["理肤泉"],
        "must_contain": ["风险点"],
        "min_length": 80,
    },
    {
        "name": "单品判断-防晒敏皮通勤",
        "msg": "珀莱雅防晒敏皮通勤能不能用",
        "expect_intent": "judgement",
        "min_products": 1,
        "must_product_brands": ["珀莱雅"],
        "must_product_categories": ["防晒"],
        "must_contain": ["能不能用"],
        "min_length": 80,
    },
    {
        "name": "对比-屏障修护",
        "msg": "理肤泉B5和玉泽哪个更适合修护屏障",
        "expect_intent": "compare",
        "min_products": 2,
        "must_product_brands": ["理肤泉", "玉泽"],
        "must_contain": ["对比结论", "怎么选"],
        "min_length": 100,
    },
    {
        "name": "对比-小棕瓶小黑瓶",
        "msg": "小棕瓶和小黑瓶怎么选，哪个更适合初老",
        "expect_intent": "compare",
        "min_products": 2,
        "max_products": 2,
        "product_brand_prefix": ["雅诗兰黛", "兰蔻"],
        "must_product_brands": ["雅诗兰黛", "兰蔻"],
        "must_contain": ["对比结论", "怎么选"],
        "min_length": 100,
    },
    {
        "name": "对比-通勤防晒",
        "msg": "兰蔻小白管和安热沙哪个更适合日常通勤",
        "expect_intent": "compare",
        "min_products": 2,
        "max_products": 2,
        "product_brand_prefix": ["兰蔻", "安热沙"],
        "must_product_categories": ["防晒"],
        "must_contain": ["对比结论", "怎么选"],
        "min_length": 100,
    },
    {
        "name": "知识-烟酰胺",
        "msg": "烟酰胺到底是干嘛的",
        "expect_intent": "knowledge",
        "min_products": 0,
        "must_contain": ["烟酰胺"],
        "min_length": 60,
    },
    {
        "name": "知识-玻色因",
        "msg": "玻色因是什么，为什么抗老产品都爱说它",
        "expect_intent": "knowledge",
        "min_products": 0,
        "must_contain": ["玻色因"],
        "min_length": 60,
    },
]


FOLLOWUP_CASES = [
    {
        "name": "夜间同义-晚上用哪个好",
        "seed": "干敏肌想要抗初老精华，预算 1000 左右",
        "followup": "晚上用哪个好",
        "expect_intent": "followup",
        "min_products": 3,
        "product_brand_prefix": ["雅诗兰黛"],
        "must_contain": ["夜间用优先"],
        "must_not_contain": ["补充一下", "暂时还没理解"],
        "min_length": 120,
    },
    {
        "name": "白天同义-早上通勤用哪个",
        "seed": "干敏肌想要抗初老精华，预算 1000 左右",
        "followup": "早上通勤用哪个",
        "expect_intent": "followup",
        "min_products": 3,
        "product_brand_prefix": ["赫莲娜"],
        "must_contain": ["白天用优先"],
        "must_not_contain": ["夜间用优先", "补充一下"],
        "min_length": 120,
    },
    {
        "name": "长句追问-白天通勤",
        "seed": "干敏肌想要抗初老精华，预算 1000 左右",
        "followup": "如果我主要是白天通勤用，这几款里哪一个更合适",
        "expect_intent": "followup",
        "min_products": 3,
        "product_brand_prefix": ["赫莲娜"],
        "must_contain": ["白天用优先"],
        "must_not_contain": ["夜间用优先", "补充一下", "暂时还没理解"],
        "min_length": 120,
    },
    {
        "name": "向量兜底-出门前省心",
        "seed": "干敏肌想要抗初老精华，预算 1000 左右",
        "followup": "上班出门前用的话，三款里谁更省心",
        "expect_intent": "followup",
        "min_products": 3,
        "product_brand_prefix": ["赫莲娜"],
        "must_contain": ["白天用优先"],
        "must_not_contain": ["夜间用优先", "补充一下", "暂时还没理解"],
        "min_length": 120,
    },
    {
        "name": "日夜同义-日间夜间分别怎么用",
        "seed": "干敏肌想要抗初老精华，预算 1000 左右",
        "followup": "日间夜间分别怎么用",
        "expect_intent": "followup",
        "min_products": 3,
        "product_brand_prefix": ["赫莲娜", "雅诗兰黛"],
        "must_contain": ["日夜分工", "白天优先", "夜间优先"],
        "min_length": 140,
    },
    {
        "name": "平价同义-有没有更平价的",
        "seed": "油皮精华",
        "followup": "有没有更平价的",
        "expect_intent": "followup",
        "min_products": 2,
        "must_contain": ["平价"],
        "must_not_contain": ["补充一下", "暂时还没理解"],
        "min_length": 80,
    },
    {
        "name": "向量兜底-睡觉前留一瓶",
        "seed": "干敏肌想要抗初老精华，预算 1000 左右",
        "followup": "睡觉前只留一瓶该留谁",
        "expect_intent": "followup",
        "min_products": 3,
        "product_brand_prefix": ["雅诗兰黛"],
        "must_contain": ["夜间用优先"],
        "must_not_contain": ["补充一下", "暂时还没理解"],
        "min_length": 120,
    },
    {
        "name": "长句追问-预算更平价",
        "seed": "油皮精华",
        "followup": "想压预算的话，这几款里有没有更平价的选择",
        "expect_intent": "followup",
        "min_products": 2,
        "must_contain": ["平价"],
        "must_not_contain": ["补充一下", "暂时还没理解"],
        "min_length": 80,
    },
    {
        "name": "向量兜底-不想花太多",
        "seed": "油皮精华",
        "followup": "不想花太多的话从刚才里面挑哪支",
        "expect_intent": "followup",
        "min_products": 2,
        "must_contain": ["平价"],
        "must_not_contain": ["补充一下", "暂时还没理解"],
        "min_length": 80,
    },
    {
        "name": "多意图-白天用顺便平价",
        "seed": "干敏肌想要抗初老精华，预算 1000 左右",
        "followup": "白天通勤用哪个更合适，顺便有没有没那么贵的",
        "expect_intent": "followup",
        "min_products": 3,
        "product_brand_prefix": ["赫莲娜"],
        "must_contain": ["白天用优先", "补充"],
        "must_not_contain": ["夜间用优先", "补充一下", "暂时还没理解"],
        "require_answer_contract": True,
        "contract_answer_mode": "followup",
        "contract_secondary_followup_types": ["cheaper"],
        "min_length": 140,
    },
    {
        "name": "多意图-敏皮稳不稳加成分",
        "seed": "200以内防晒",
        "followup": "如果我是敏皮通勤用哪个更稳，成分会不会刺激",
        "expect_intent": "followup",
        "min_products": 1,
        "max_products": 1,
        "product_brand_prefix": ["薇诺娜"],
        "must_contain": ["防晒", "补充"],
        "must_not_contain": ["补充一下", "暂时还没理解"],
        "require_answer_contract": True,
        "contract_answer_mode": "followup",
        "contract_secondary_followup_types": ["ingredient"],
        "min_length": 100,
    },
    {
        "name": "敏感肌同义-敏皮能用吗",
        "seed": "200以内防晒",
        "followup": "敏皮能用吗",
        "expect_intent": "followup",
        "min_products": 2,
        "must_contain": ["防晒"],
        "must_not_contain": ["补充一下", "暂时还没理解"],
        "min_length": 80,
    },
    {
        "name": "长句追问-敏皮更稳",
        "seed": "200以内防晒",
        "followup": "如果我是敏皮通勤用，这几个里面哪个更稳一点",
        "expect_intent": "followup",
        "min_products": 1,
        "max_products": 1,
        "product_brand_prefix": ["薇诺娜"],
        "must_contain": ["防晒"],
        "must_not_contain": ["补充一下", "暂时还没理解"],
        "min_length": 80,
    },
    {
        "name": "追问-第一款多少钱",
        "seed": "300以内面霜",
        "followup": "第一款大概多少钱",
        "expect_intent": "followup",
        "min_products": 1,
        "must_contain": ["参考价"],
        "contract_secondary_followup_types": [],
        "min_length": 50,
    },
    {
        "name": "追问-成分拆解",
        "seed": "干敏肌想要抗初老精华，预算 1000 左右",
        "followup": "第一瓶主要靠什么成分",
        "expect_intent": "followup",
        "min_products": 1,
        "must_contain": ["成分"],
        "min_length": 60,
    },
    {
        "name": "追问-会不会闷闭口",
        "seed": "300以内面霜",
        "followup": "这几款会不会闷闭口",
        "expect_intent": "followup",
        "min_products": 2,
        "must_not_contain": ["补充一下", "暂时还没理解"],
        "min_length": 80,
    },
    {
        "name": "多意图-平价加敏感",
        "seed": "300以内面霜",
        "followup": "想便宜点但敏感肌别翻车，这几个怎么选",
        "expect_intent": "followup",
        "min_products": 2,
        "must_contain": ["补充"],
        "contract_secondary_followup_types": ["cheaper"],
        "must_not_contain": ["补充一下", "暂时还没理解"],
        "min_length": 100,
    },
    {
        "name": "多意图-夜间加刺激",
        "seed": "干敏肌想要抗初老精华，预算 1000 左右",
        "followup": "晚上用哪个更好，刺激风险也顺便说下",
        "expect_intent": "followup",
        "min_products": 3,
        "product_brand_prefix": ["雅诗兰黛"],
        "must_contain": ["夜间用优先", "补充"],
        "contract_secondary_followup_types": ["ingredient"],
        "must_not_contain": ["补充一下", "暂时还没理解"],
        "min_length": 140,
    },
]


def run_case(case, idx):
    case = dict(case)
    case.setdefault("require_answer_contract", True)
    case.setdefault("contract_answer_mode", case.get("expect_intent"))
    sid = f"semantic-{idx}-{uuid.uuid4().hex[:8]}"
    if "seed" in case:
        first = post_chat_raw(case["seed"], sid)
        history = [
            {"role": "user", "content": case["seed"]},
            {"role": "assistant", "content": first["content"]},
        ]
        result = post_chat_raw(case["followup"], sid, history=history)
    else:
        result = post_chat_raw(case["msg"], sid)
    ok, failures, intent_name, product_count = check_chat_result(result, case)
    return ok, failures, intent_name, product_count


def check_sample_bank():
    retriever = SemanticIntentRetriever()
    failures = []
    labels = {}
    for sample in retriever.samples:
        labels.setdefault(sample.label, []).append(sample.text)

    actual_labels = set(labels)
    if actual_labels != EXPECTED_SAMPLE_LABELS:
        failures.append(f"样本标签集合不一致 actual={sorted(actual_labels)} expected={sorted(EXPECTED_SAMPLE_LABELS)}")

    for label in sorted(EXPECTED_SAMPLE_LABELS):
        texts = labels.get(label, [])
        if len(texts) != 50:
            failures.append(f"{label} 样本数={len(texts)} 期望=50")
        if len(set(texts)) != len(texts):
            failures.append(f"{label} 存在重复样本")

    all_texts = [sample.text for sample in retriever.samples]
    if len(set(all_texts)) != len(all_texts):
        failures.append("全局样本存在重复文本")

    return failures


def main():
    cases = RECOMMENDATION_CASES + DIRECT_INTENT_CASES + FOLLOWUP_CASES
    failed = []
    print("=" * 78)
    print("V2 Backend Semantic Matrix")
    print("=" * 78)
    sample_failures = check_sample_bank()
    if sample_failures:
        failed.append((0, {"name": "语义样本库门禁"}, sample_failures))
        print("[00] FAIL 语义样本库门禁")
        for failure in sample_failures:
            print(f"      - {failure}")
    else:
        print("[00] PASS 语义样本库门禁                 labels=11 samples=550")

    for idx, case in enumerate(cases, 1):
        ok, failures, intent_name, product_count = run_case(case, idx)
        mark = "PASS" if ok else "FAIL"
        print(f"[{idx:02d}] {mark} {case['name']:<30} intent={intent_name} products={product_count}")
        if not ok:
            failed.append((idx, case, failures))
            for failure in failures:
                print(f"      - {failure}")

    print("\n" + "=" * 78)
    print(f"RESULT: PASS {len(cases) - len(failed)}/{len(cases)}, FAIL {len(failed)}")
    print("=" * 78)
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
