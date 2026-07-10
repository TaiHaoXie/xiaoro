"""意图路由 + 对比检索 基线诊断（完全不调用大模型，零 token 成本）。

只测最容易"换个问法就崩"的那层：TurnParser 解析 → Router/IntentClassifier 判意图
→ 对比目标抽取。以及多轮追问（序号、"哪个更适合"）的意图判定。

用法：python scripts/intent_baseline_probe.py
"""
import asyncio
import sys

sys.path.insert(0, ".")

# 每条: (说明, 用户话, 期望意图, 期望"候选数>=2"是否成立)
SINGLE_TURN = [
    ("对比-标准说法", "小棕瓶和小黑瓶哪个好", "compare", True),
    ("对比-维度说法(上次崩的)", "雅诗兰黛DW和阿玛尼权力粉底液哪个遮瑕好", "compare", True),
    ("对比-改写1", "DW比权力谁遮瑕强", "compare", True),
    ("对比-改写2", "权力和DW我该拿哪个", "compare", True),
    ("对比-三个目标", "珂润、玉泽、理肤泉这三个哪个适合敏感肌", "compare", True),
    ("对比-口语没比较词", "安热沙小金瓶跟兰蔻小白管我选哪瓶", "compare", True),
    ("单品判断", "珂润面霜好用吗", "judgement", None),
    ("推荐-正常", "油皮夏天用什么防晒好", "recommendation", None),
    ("推荐-带预算", "25岁混干皮想抗初老，预算500以内精华", "recommendation", None),
]

# 多轮: (说明, 第一轮问, 历史助手回复(模拟三款), 追问, 期望意图)
MULTI_TURN = [
    ("追问-第一款怎么样", "油皮抗老精华推荐",
     "给你三款：资生堂红腰子抗老精华，理肤泉B5修护精华，玉兰油小白瓶淡斑精华。",
     "第一款怎么样", "judgement/followup"),
    ("追问-第二款适合敏感肌吗", "油皮抗老精华推荐",
     "给你三款：资生堂红腰子抗老精华，理肤泉B5修护精华，玉兰油小白瓶淡斑精华。",
     "第二款适合敏感肌吗", "judgement/followup"),
    ("追问-哪个更适合敏感肌(三选一)", "油皮抗老精华推荐",
     "给你三款：资生堂红腰子抗老精华，理肤泉B5修护精华，玉兰油小白瓶淡斑精华。",
     "哪个更适合敏感肌", "compare/followup"),
]


async def main():
    from app.database.postgres import init_postgres_pool, close_postgres_pool
    await init_postgres_pool()
    from app.services.v2.turn_parser import TurnParser
    from app.services.v2.router import Router
    from app.services.v2.retriever import Retriever
    from app.services.v2.ranker import Ranker

    parser = TurnParser()
    router = Router()
    retriever = Retriever()
    ranker = Ranker()

    print("=" * 72)
    print("单轮：意图判定 + 对比目标抽取（无 LLM）")
    print("=" * 72)
    for note, q, expect_intent, expect_multi in SINGLE_TURN:
        turn = await parser.parse_async(q, session_id="probe", conversation_history=[])
        route = router.route(turn)
        mode = route.answer_mode.value if hasattr(route.answer_mode, "value") else str(route.answer_mode)
        targets = turn.compare_targets or []

        # 实际能不能召回>=2个不同商品（对比场景才关心）
        recalled = 0
        recalled_names = []
        if targets:
            seen = set()
            for t in targets:
                ms = await retriever.retrieve_by_name_fuzzy(t, limit=3)
                ms = ranker.rank(ms, turn, top_n=3)
                for p in ms:
                    pid = p.get("id")
                    if pid not in seen:
                        seen.add(pid)
                        recalled_names.append(f"{p.get('brand')}·{(p.get('name') or '')[:14]}")
                        break
            recalled = len(seen)

        ok_intent = (mode == expect_intent)
        flag = "✅" if ok_intent else "❌"
        print(f"\n{flag} [{note}] {q}")
        print(f"    意图: {mode}  (期望 {expect_intent})  reason={route.reason}")
        print(f"    抽到的对比目标: {targets}")
        if targets:
            multi_ok = recalled >= 2
            print(f"    实际召回 {recalled} 款: {recalled_names}  {'✅≥2' if multi_ok else '❌<2'}")

    print("\n" + "=" * 72)
    print("多轮：序号追问 / 三选一追问（无 LLM，只看意图与候选定位）")
    print("=" * 72)
    for note, q1, assistant_reply, q2, expect in MULTI_TURN:
        history = [
            {"role": "user", "content": q1},
            {"role": "assistant", "content": assistant_reply},
        ]
        turn = await parser.parse_async(q2, session_id="probe2", conversation_history=history)
        route = router.route(turn)
        mode = route.answer_mode.value if hasattr(route.answer_mode, "value") else str(route.answer_mode)
        print(f"\n[{note}] 追问: {q2}")
        print(f"    意图: {mode}  (期望 {expect})  reason={route.reason}")
        print(f"    followup_type: {turn.followup_type}")
        print(f"    referenced_products(历史锚点): {turn.referenced_products}")
        print(f"    compare_targets: {turn.compare_targets}")
        print(f"    name_clues: {turn.name_clues}")

    await close_postgres_pool()


if __name__ == "__main__":
    asyncio.run(main())
