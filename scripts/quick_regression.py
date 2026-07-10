#!/usr/bin/env python3
"""快速回归: 不启动HTTP,直接调用agent验证新别名和7字段效果"""
import sys, asyncio, json
sys.path.insert(0, ".")

async def main():
    from app.database.postgres import init_postgres_pool, close_postgres_pool
    await init_postgres_pool()
    from app.services.v2.agent import V2ShoppingAgent
    from app.services.v2.turn_parser import TurnParser

    agent = V2ShoppingAgent()

    # 1. 先测 parser 品牌别名命中
    parser = TurnParser()
    alias_tests = [
        "CPB长管隔离怎么样",
        "苏菲娜妆前乳推荐",
        "肌肤之钥长管油皮能用吗",
        "理肤泉大哥大防晒推荐",
        "红气垫和黑气垫对比",
        "金盏花面膜敏感肌",
        "大白饼定妆效果怎么样",
        "NARS遮瑕好用吗",
        "高潮腮红推荐",
        "贝德玛卸妆水",
        "菁纯眼霜",
    ]
    print("=== 品牌/昵称别名命中测试 ===")
    for q in alias_tests:
        turn = await parser.parse_async(q, session_id="alias_test", conversation_history=[])
        print(f"Q: {q}")
        print(f"  → brand={turn.brand}, category={turn.category}, name_clues={turn.name_clues}, intent={turn.intent}")

    # 2. 调用agent跑关键case
    cases = [
        ("理肤泉大哥大防晒推荐", None, "补全后的防晒（QA+质地+评价）"),
        ("我敏感肌，贝德玛粉水能不能用", None, "贝德玛卸妆水7字段suitability"),
        ("CPB长管隔离推荐", None, "CPB新别名命中"),
        ("苏菲娜妆前乳油皮推荐", None, "苏菲娜新别名命中"),
        ("NARS大白饼推荐", None, "大白饼定妆粉饼7字段效果"),
    ]
    print("\n=== 关键case LLM回复 ===")
    for q, history, label in cases:
        print(f"\n--- {label} ---")
        print(f"Q: {q}")
        text_parts = []
        products_seen = []
        async for evt in agent.chat_stream_events(q, session_id=f"case_{label}", conversation_history=history or []):
            et = evt.get("event")
            if et == "products":
                for p in evt["data"].get("products", [])[:3]:
                    products_seen.append(f'{p.get("brand")} {p.get("name","")[:30]}')
            elif et == "message":
                content = evt["data"].get("content", "")
                text_parts.append(content)
            elif et == "end":
                break
        full_text = "".join(text_parts)
        print(f"候选商品: {products_seen}")
        print(f"回答: {full_text[:500]}")

    # 3. 多轮追问测试
    print("\n=== 多轮追问测试 ===")
    hist = []
    q1 = "珂润面霜推荐"
    text_parts = []
    async for evt in agent.chat_stream_events(q1, session_id="followup_test", conversation_history=[]):
        if evt.get("event") == "message":
            text_parts.append(evt["data"].get("content", ""))
        elif evt.get("event") == "products":
            for p in evt["data"].get("products", [])[:1]:
                hist.append({"role": "assistant", "content": f"推荐了{p.get('brand')} {p.get('name','')}"})
        elif evt.get("event") == "end":
            break
    ans1 = "".join(text_parts)
    print(f"第一轮 Q: {q1}")
    print(f"第一轮 A: {ans1[:250]}...")
    hist.append({"role": "user", "content": q1})
    hist.append({"role": "assistant", "content": ans1})

    q2 = "它怎么用啊"
    text_parts = []
    async for evt in agent.chat_stream_events(q2, session_id="followup_test", conversation_history=hist):
        if evt.get("event") == "message":
            text_parts.append(evt["data"].get("content", ""))
        elif evt.get("event") == "end":
            break
    print(f"\n第二轮 Q: {q2}")
    print(f"第二轮 A(追问用法): {''.join(text_parts)[:400]}")

    await close_postgres_pool()


if __name__ == "__main__":
    asyncio.run(main())
