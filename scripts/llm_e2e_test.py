#!/usr/bin/env python3
"""
完整链路回归测试（30条，开LLM润色）
- 走V2ShoppingAgent完整链路（BGE向量检索+真实LLM润色）
- 覆盖：推荐/对比/知识/单品判断/追问/自然语言/识图
- 不做monkey-patch，真实调用LLM
"""
import sys
import asyncio
from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except Exception:
    pass

from app.database.postgres import init_postgres_pool
from app.services.v2.agent import V2ShoppingAgent


SINGLE_CASES: List[Tuple[str, str]] = [
    ("T01 油皮防晒", "油皮夏天通勤用什么防晒，要清爽不油腻"),
    ("T02 干皮面霜", "干皮面霜，500以内，保湿要好"),
    ("T03 早C晚A", "早C晚A是什么意思，新手怎么开始"),
    ("T04 小棕瓶怎么样", "雅诗兰黛小棕瓶适合油皮吗"),
    ("T05 对比棕瓶黑瓶", "小棕瓶和小黑瓶哪个更适合抗初老"),
    ("T06 敏感肌洁面", "敏感肌用什么洗面奶比较温和"),
    ("T07 学生美白", "学生党没啥钱，想美白有啥推荐"),
    ("T08 烟酰胺作用", "烟酰胺有什么作用，敏感肌能用吗"),
    ("T09 小金瓶防晒", "安热沙小金瓶防晒油不油，需要卸妆吗"),
    ("T10 B5玉泽对比", "理肤泉B5和玉泽哪个更适合敏感肌泛红"),
    ("T11 玻色因区别", "玻色因和A醇有什么区别，能一起用吗"),
    ("T12 熬夜急救", "经常熬夜脸黄，有没有什么急救精华"),
    ("T13 防晒通勤", "日常通勤防晒选小金瓶还是小白管"),
    ("T14 脸红修护", "我脸最近又红又烫，用什么能修护"),
    ("T15 粉水怎么样", "兰蔻粉水好用吗，适合干皮吗"),
]

MULTI_TURN_CASES: List[Tuple[str, List[str]]] = [
    ("T16 油皮→精华→多少钱", ["我是油皮", "想要精华", "300以内", "第一款多少钱"]),
    ("T17 小棕瓶→成分", ["雅诗兰黛小棕瓶怎么样", "它主要成分是什么"]),
    ("T18 双抗→和A醇", ["珀莱雅双抗精华怎么样", "能和A醇一起用吗"]),
    ("T19 干皮→眼霜→适合吗", ["我是干皮", "想买眼霜", "不长脂肪粒的", "第一款适合我吗"]),
    ("T20 小金瓶→卸妆", ["小金瓶防晒怎么样", "这个需要专门卸妆吗"]),
    ("T21 SK2→适合肤质", ["SK2神仙水适合什么肤质", "敏感肌能用吗"]),
    ("T22 敏感肌→成分→价格", ["敏感肌面霜推荐", "第一款主要成分是什么", "多少钱"]),
    ("T23 烟酰胺→搭配", ["烟酰胺有什么作用", "搭配什么用效果好"]),
    ("T24 珂润→天天用", ["珂润面霜怎么样", "可以天天用吗"]),
    ("T25 蓝胖子→油不油", ["资生堂蓝胖子防晒油不油"]),
]

IMAGE_CASES: List[Tuple[str, str]] = [
    ("T26 图-安热沙油皮", "我拍了瓶安热沙小金瓶，这个适合油皮吗"),
    ("T27 图-小棕瓶评价", "这是雅诗兰黛小棕瓶吧，适合什么肤质"),
    ("T28 图-小黑瓶对比", "图片里这是兰蔻小黑瓶吧，和小棕瓶比哪个好"),
    ("T29 图-B5修护", "这管理肤泉B5可以修护泛红吗"),
    ("T30 图-口红色号", "这是MAC的Ruby Woo口红吗，黄皮能用吗"),
]


async def collect_agent_response(
    agent: V2ShoppingAgent,
    query: str,
    session_id: str,
    history=None,
    image_context=None,
) -> Dict[str, Any]:
    answer_contract = None
    text_chunks = []
    products_from_event = []
    pitfalls = []
    comparison = None
    error = None
    generation_source = None

    async for event in agent.chat_stream_events(
        message=query,
        session_id=session_id,
        conversation_history=history or [],
        image_context=image_context,
    ):
        etype = event.get("event")
        data = event.get("data") or {}
        if etype == "answer_contract":
            answer_contract = data.get("answer_contract") or {}
            generation_source = answer_contract.get("generation_source")
        elif etype == "message":
            if data.get("content"):
                text_chunks.append(data["content"])
        elif etype == "products":
            products_from_event = data.get("products") or []
        elif etype == "pitfalls":
            pitfalls = data.get("pitfalls") or []
        elif etype == "comparison":
            comparison = data.get("comparison")
        elif etype == "error":
            error = data.get("message") or str(data)

    text = "".join(text_chunks)
    return {
        "answer_mode": answer_contract.get("answer_mode") if answer_contract else "unknown",
        "followup_type": answer_contract.get("followup_type") if answer_contract else None,
        "text": text,
        "products": products_from_event,
        "pitfalls": pitfalls,
        "comparison": comparison,
        "error": error,
        "generation_source": generation_source,
    }


def format_products(products, limit=3):
    if not products:
        return "(无)"
    names = []
    for p in products[:limit]:
        name = (p.get("display_name") or p.get("name") or "")[:25]
        price = p.get("price_val") or p.get("price") or ""
        spec = p.get("spec") or ""
        price_str = f"({int(price)}/{spec})" if price and spec else (f"({int(price)})" if price else "")
        names.append(f"{name}{price_str}")
    return "、".join(names)


def evaluate_result(label: str, query: str, result: Dict[str, Any]) -> List[str]:
    issues = []
    text = result.get("text", "") or ""
    products = result.get("products", [])
    mode = result.get("answer_mode", "unknown")
    err = result.get("error")
    gen = result.get("generation_source")

    if err:
        issues.append(f"错误: {err[:80]}")
        return issues

    if not text or len(text.strip()) < 5:
        issues.append("返回空文案")
        return issues

    if mode == "no_match" and not any(skip in query for skip in ["我是干皮", "我是油皮", "我是敏感肌", "我是混油"]):
        if len(query) > 4:
            issues.append(f"误判NO_MATCH（mode={mode}）")

    guard_limits = {"followup": 1, "compare": 2, "recommendation": 3, "judgement": 3, "knowledge": 0}
    limit = guard_limits.get(mode, 3)
    if limit > 0 and len(products) > limit + 1:
        issues.append(f"Guard超限: 预期≤{limit}款, 实际{len(products)}款")

    normalized = text.rstrip()
    disclaimer_suffix = ["参考价为入库时价格", "实时活动价以商品链接为准", "以下单页为准", "仅供参考", "商品页面为准", "避免残留", "防晒效果"]
    is_disclaimer_end = any(normalized.rstrip("。_* \n#").endswith(suf[:10]) for suf in disclaimer_suffix)
    if not is_disclaimer_end:
        if len(normalized) > 100:
            stripped = normalized.rstrip("。_* \n#")
            if not stripped.endswith(("。", "！", "？", "…", "：", "」", "》", ")")):
                last_30 = normalized[-30:]
                if not any(suf in last_30 for suf in disclaimer_suffix):
                    issues.append(f"文案可能截断(结尾: ...{last_30[-20:]})")

    for dirty in ["产地参数", "非特殊用途", "生产企业", "备案号", "联合研发品牌", "国妆备进字"]:
        if dirty in text:
            issues.append(f"脏文本: '{dirty}'")
            break

    if mode in ("recommendation", "judgement", "compare") and len(products) == 0:
        issues.append(f"商品查询路径无商品返回(mode={mode})")

    if mode == "knowledge" and len(text) < 20:
        issues.append("知识问答文案过短")

    return issues


async def run_all_tests():
    await init_postgres_pool()
    agent = V2ShoppingAgent()

    results = []
    total_issues = 0
    llm_count = 0
    local_count = 0

    async def run_turn(label, query, session_id, history=None, image_context=None, turn_idx=None):
        nonlocal total_issues, llm_count, local_count
        try:
            result = await collect_agent_response(agent, query, session_id, history, image_context)
            issues = evaluate_result(label, query, result)
            turn_label = f"{label}" + (f"[轮{turn_idx}]" if turn_idx else "")
            status = "⚠️ " + "; ".join(issues) if issues else "✅"
            gen = result.get("generation_source") or "unknown"
            if gen == "llm":
                llm_count += 1
            elif gen == "local_fallback":
                local_count += 1
            print(f"{turn_label:50s} | {query[:30]:30s} | 意图:{result['answer_mode']:15s} | 商品:{len(result['products'])} | LLM:{gen:15s} | {status}")
            if not issues and result.get("text"):
                preview = result["text"].replace("\n", " ")[:120]
                print(f"  ↳ 文案: {preview}...")
            elif issues:
                preview = result.get("text", "").replace("\n", " ")[:200]
                print(f"  ↳ 问题文案: {preview}")
                total_issues += 1
            results.append({"label": turn_label, "query": query, "result": result, "issues": issues})
            return result
        except Exception as e:
            print(f"  ❌ 异常: {e}")
            total_issues += 1
            results.append({"label": label, "query": query, "error": str(e)})
            return None

    print("=" * 100)
    print("完整链路LLM回归测试（30条）")
    print("=" * 100)

    print("\n─── 单轮测试 ───")
    for idx, (label, query) in enumerate(SINGLE_CASES, 1):
        sid = f"llm_test_{idx}"
        await run_turn(label, query, sid)
        await asyncio.sleep(0.3)

    print("\n─── 多轮追问测试 ───")
    for midx, (label, queries) in enumerate(MULTI_TURN_CASES, 16):
        sid = f"llm_multi_{midx}"
        history = []
        for ti, q in enumerate(queries, 1):
            r = await run_turn(label, q, sid, history=history, turn_idx=ti)
            if r and r.get("products"):
                prod_names = [p.get("display_name") or p.get("name", "") for p in r["products"]]
                history.append({"role": "assistant", "content": r.get("text", "")[:200], "products": prod_names[:3]})
            history.append({"role": "user", "content": q})
            await asyncio.sleep(0.3)

    print("\n─── 识图场景测试（文本描述图片内容）───")
    for iidx, (label, query) in enumerate(IMAGE_CASES, 26):
        sid = f"llm_img_{iidx}"
        await run_turn(label, query, sid)
        await asyncio.sleep(0.3)

    print("\n" + "=" * 100)
    print(f"测试完成: 共{len(SINGLE_CASES)+len(MULTI_TURN_CASES)+len(IMAGE_CASES)}组场景")
    print(f"LLM润色: {llm_count}次 | 本地兜底: {local_count}次 | 问题: {total_issues}处")
    if total_issues > 0:
        print("\n问题汇总:")
        for r in results:
            if r.get("issues"):
                print(f"  - {r['label']}: {'; '.join(r['issues'])}")

    output_path = Path(__file__).parent.parent / "test_results_llm.txt"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(f"完整链路LLM回归测试结果\n")
        f.write(f"LLM润色: {llm_count}次 | 本地兜底: {local_count}次 | 问题: {total_issues}处\n\n")
        for r in results:
            f.write(f"{'='*80}\n")
            f.write(f"[{r['label']}] {r.get('query', '')}\n")
            if r.get("error"):
                f.write(f"  ❌ 异常: {r['error']}\n")
                continue
            res = r.get("result", {})
            f.write(f"  意图: {res.get('answer_mode')} | 商品: {len(res.get('products',[]))} | LLM: {res.get('generation_source')}\n")
            if r.get("issues"):
                f.write(f"  ⚠️ 问题: {'; '.join(r['issues'])}\n")
            f.write(f"  文案:\n{res.get('text','')}\n\n")
    print(f"\n完整结果已保存到: {output_path}")


if __name__ == "__main__":
    asyncio.run(run_all_tests())
