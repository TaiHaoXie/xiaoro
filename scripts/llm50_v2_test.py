"""V2 50轮真实LLM端到端测试脚本（修复bug后回归）。

覆盖：
A. 基础推荐 10条
B. 单品判断 12条
C. 商品对比 6条
D. 多轮追问 5个序列（每个Q1+Q2，共10轮）
E. 绕话/模糊/自然口语 5条
F. 7字段专项验证 4条
G. 彩妆香水 3条
合计：50轮
"""
import asyncio
import json
import re
import sys
import time
from typing import Any, Dict, List, Tuple

import httpx

API = "http://localhost:8000/api/v1/chat/stream"
FORBIDDEN = [
    "数据不足", "资料有限", "未查到", "没查到", "没有足够",
    "详情页没有", "抓不到", "抓取失败", "抓取不到", "暂无",
    "我手头", "我手里", "我这边", "我库里", "我手上",
    "后端", "候选商品", "系统筛选", "作为AI", "作为一个AI",
    "模型服务", "根据个人需求选择", "根据个人需求", "闭眼入",
    "强烈推荐", "性价比之王", "不容错过", "完美选择",
    "以下几款", "为您推荐", "亲", "宝宝们",
]
PRICE_RE = re.compile(r"[¥￥]\s*\d")

# 7字段信号词：用于检测字段吸收率
FIELD_SIGNALS = {
    "texture": ["质地", "肤感", "推开", "黏", "清爽", "滋润", "轻薄", "厚重", "顺滑", "润而不腻", "吸收", "水感", "泥膜", "粉质", "柔焦"],
    "usage": ["用法", "使用", "敷", "按摩", "洗", "涂抹", "用量", "泵", "厚敷", "薄涂", "少量", "早晚", "白天", "晚上", "洁面后"],
    "review": ["反馈", "评价", "不少人", "很多人", "买过的人", "用户", "吐槽", "普遍", "口碑"],
    "mechanism": ["屏障", "神经酰胺", "二裂酵母", "烟酰胺", "原理", "作用机制", "修护", "原理上", "通过", "主打", "核心成分"],
    "safety": ["敏感肌", "注意", "慎用", "耳后", "皮试", "刺激", "不建议", "不要", "避免", "风险", "先试"],
    "claim": ["官方称", "品牌资料", "品牌方", "官方资料", "实验", "专利", "数据显示", "据品牌"],
    "qa": ["常见", "问", "很多人问", "经常有人问"],
}


# 测试用例：(id, category, question)
CASES: List[Tuple[str, str, str]] = [
    # === A. 基础推荐 10 ===
    ("A01", "基础推荐", "我是油皮夏天容易脱妆，有什么清爽的防晒推荐"),
    ("A02", "基础推荐", "25岁混干皮想抗初老，预算500以内精华"),
    ("A03", "基础推荐", "敏感肌泛红用什么面霜修护比较好"),
    ("A04", "基础推荐", "熬夜皮肤暗沉发黄，想提亮肤色"),
    ("A05", "基础推荐", "学生党油皮，200以内的水乳套装"),
    ("A06", "基础推荐", "T区油两颊干，毛孔粗大怎么办"),
    ("A07", "基础推荐", "刚做完医美刷酸，用什么修护"),
    ("A08", "基础推荐", "沙漠干皮冬天用什么面霜"),
    ("A09", "基础推荐", "30岁干纹细纹比较明显，有什么眼霜"),
    ("A10", "基础推荐", "长时间户外暴晒，防水防汗的防晒"),
    # === B. 单品判断 12 ===
    ("B01", "单品判断", "珂润面霜好用吗，干皮能用吗"),
    ("B02", "单品判断", "兰蔻小黑瓶适合敏感肌吗"),
    ("B03", "单品判断", "玉泽皮肤屏障修护精华乳真的能修护吗"),
    ("B04", "单品判断", "雅诗兰黛DW粉底液夏天会不会脱妆"),
    ("B05", "单品判断", "安热沙小金瓶日常通勤用会不会太油"),
    ("B06", "单品判断", "理肤泉B5面膜能天天敷吗"),
    ("B07", "单品判断", "SK-II神仙水适合什么年龄用"),
    ("B08", "单品判断", "CPB长管隔离搓泥吗"),
    ("B09", "单品判断", "科颜氏白泥面膜清洁力怎么样"),
    ("B10", "单品判断", "苏菲娜妆前乳油皮控油真的有用吗"),
    ("B11", "单品判断", "贝德玛卸妆水敏感肌可以用吗"),
    ("B12", "单品判断", "资生堂红颜精华傲娇精华值得买吗"),
    # === C. 商品对比 6 ===
    ("C01", "商品对比", "小棕瓶和小黑瓶哪个好"),
    ("C02", "商品对比", "兰蔻小白管和安热沙小金瓶防晒怎么选"),
    ("C03", "商品对比", "理肤泉B5和玉泽屏障修护哪个更适合敏感肌"),
    ("C04", "商品对比", "雅诗兰黛DW和阿玛尼权力粉底液哪个遮瑕好"),
    ("C05", "商品对比", "YSL黑气垫和阿玛尼红气垫哪个适合油皮"),
    ("C06", "商品对比", "NARS大白饼和MAC定妆哪个控油好"),
    # === D. 多轮追问 5序列 10轮 ===
    ("D01a", "多轮追问", "珂润保湿面霜怎么样"),
    ("D01b", "多轮追问", "它怎么用啊，早晚都能涂吗"),
    ("D02a", "多轮追问", "油皮夏天用什么防晒好"),
    ("D02b", "多轮追问", "那要不要卸妆啊"),
    ("D03a", "多轮追问", "玉泽屏障修护乳好用吗"),
    ("D03b", "多轮追问", "和B5比呢"),
    ("D04a", "多轮追问", "小棕瓶抗老效果怎么样"),
    ("D04b", "多轮追问", "20岁用会不会太早"),
    ("D05a", "多轮追问", "CPB长管隔离怎么样"),
    ("D05b", "多轮追问", "干皮冬天用够滋润吗"),
    # === E. 绕话/自然口语 5 ===
    ("E01", "绕话口语", "我最近脸特别干还起皮，帮我看看有啥能救"),
    ("E02", "绕话口语", "夏天T区出油多，毛孔还明显，给点建议"),
    ("E03", "绕话口语", "我要去海边玩一周，应该备点什么护肤的"),
    ("E04", "绕话口语", "加班多了气色很差，有什么能让脸看起来好点"),
    ("E05", "绕话口语", "朋友送了我一套SK-II，我皮肤敏感怕不耐受"),
    # === F. 7字段专项验证 4 ===
    ("F01", "7字段-质地", "珂润乳液是什么质地，油不油"),
    ("F02", "7字段-用法", "安热沙小金瓶怎么用，要涂多少"),
    ("F03", "7字段-评价", "玉泽精华乳大家用着怎么样"),
    ("F04", "7字段-原理", "神经酰胺的作用是什么，珂润主打这个吗"),
    # === G. 彩妆香水 3 ===
    ("G01", "彩妆香水", "NARS高潮腮红适合黄皮吗"),
    ("G02", "彩妆香水", "祖玛珑蓝风铃香水什么味道"),
    ("G03", "彩妆香水", "阿玛尼红气垫2号色适合黄皮吗"),
]


async def collect_response(session_id: str, message: str) -> Dict[str, Any]:
    """通过SSE收集一次对话的完整响应。"""
    result = {"text": "", "products": [], "intent": None, "pitfalls": [], "answer_contract": None}
    async with httpx.AsyncClient(timeout=120.0) as client:
        async with client.stream("POST", API, json={
            "message": message,
            "session_id": session_id,
        }) as resp:
            current_event = None
            async for line in resp.aiter_lines():
                line = line.rstrip("\r")
                if line.startswith("event: "):
                    current_event = line[7:].strip()
                    continue
                if line.startswith("data: "):
                    raw = line[6:]
                    if raw == "[DONE]":
                        break
                    try:
                        evt_data = json.loads(raw)
                    except Exception:
                        continue
                    et = current_event
                    if et == "intent":
                        result["intent"] = evt_data.get("intent") or evt_data.get("scenario_intent") or evt_data.get("followup_type")
                    elif et == "answer_contract":
                        result["answer_contract"] = evt_data
                    elif et == "products":
                        result["products"] = evt_data.get("products") or []
                    elif et == "pitfalls":
                        result["pitfalls"] = evt_data.get("pitfalls") or []
                    elif et == "message":
                        result["text"] += evt_data.get("content") or ""
                # 空行重置event
                if line == "":
                    current_event = None
    return result


def evaluate(case_id: str, category: str, question: str, resp: Dict[str, Any]) -> Dict[str, Any]:
    text = resp.get("text") or ""
    products = resp.get("products") or []
    issues: List[str] = []

    # 1. 禁句红线
    hit_forbidden = [w for w in FORBIDDEN if w in text]
    if hit_forbidden:
        issues.append(f"禁词: {hit_forbidden}")

    # 2. 价格 ¥ 符号
    has_price = bool(PRICE_RE.search(text))
    if not has_price and products and category not in ("绕话口语",):
        # 对比/单品/推荐/追问里有商品必须有价格
        if category in ("基础推荐", "商品对比", "单品判断", "7字段-质地", "7字段-用法", "7字段-评价", "彩妆香水"):
            issues.append("缺价格¥符号")

    # 3. 商品名加粗
    bold_count = len(re.findall(r"\*\*[^*]+\*\*", text))
    if products and bold_count == 0 and category != "绕话口语":
        if category != "7字段-原理":
            issues.append("缺商品加粗名")

    # 4. 长度检查（过短回答视为异常）
    if len(text) < 40 and products:
        issues.append(f"回答过短({len(text)}字)")

    # 5. 7字段信号命中
    field_hits = {}
    for fk, signals in FIELD_SIGNALS.items():
        field_hits[fk] = any(s in text for s in signals)

    # 6. 对比类必须出现2个商品名
    if category == "商品对比":
        if bold_count < 2:
            issues.append(f"对比类商品加粗数<2 (实际{bold_count})")

    # 7. 判断类必须聚焦1款
    if category == "单品判断" and products:
        # 单品判断可以有少量相关品，但第一候选应该对题
        pass

    passed = len(issues) == 0
    return {
        "case_id": case_id,
        "category": category,
        "question": question,
        "passed": passed,
        "issues": issues,
        "text_len": len(text),
        "bold_count": bold_count,
        "has_price": has_price,
        "product_count": len(products),
        "top_products": [f"{p.get('brand','')} {(p.get('name','') or '')[:20]}" for p in products[:3]],
        "field_hits": field_hits,
        "text_preview": text[:200].replace("\n", " "),
    }


async def run_dialogue(case_id: str, q1: str, q2: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """多轮追问：同一session先问Q1再问Q2。"""
    sid = f"t50_{case_id}"
    r1 = await collect_response(sid, q1)
    await asyncio.sleep(1.0)
    r2 = await collect_response(sid, q2)
    return r1, r2


async def main():
    print("=" * 70)
    print(f"V2 50轮真实LLM端到端测试（修复bug后）- {time.strftime('%H:%M:%S')}")
    print("=" * 70)
    t0 = time.time()

    results: List[Dict[str, Any]] = []

    # 多轮序列配对（D01a+D01b, ...）
    followup_map = {}
    standalone: List[Tuple[str, str, str]] = []
    for cid, cat, q in CASES:
        if cid.endswith(("a", "b")) and cid[:-1].startswith("D"):
            followup_map.setdefault(cid[:-1], []).append((cid, cat, q))
        else:
            standalone.append((cid, cat, q))

    # 跑单条
    for cid, cat, q in standalone:
        sid = f"t50_{cid}"
        print(f"[{cid}] {cat}: {q[:40]}...", end=" ", flush=True)
        t1 = time.time()
        try:
            resp = await collect_response(sid, q)
            ev = evaluate(cid, cat, q, resp)
        except Exception as e:
            ev = {"case_id": cid, "category": cat, "question": q,
                  "passed": False, "issues": [f"异常: {e}"], "text_len": 0,
                  "bold_count": 0, "has_price": False, "product_count": 0,
                  "top_products": [], "field_hits": {k: False for k in FIELD_SIGNALS},
                  "text_preview": str(e)}
        ev["elapsed"] = round(time.time() - t1, 1)
        results.append(ev)
        mark = "✅" if ev["passed"] else "❌"
        print(f"{mark} {ev['elapsed']}s ({ev['text_len']}字, {ev['product_count']}商品)")
        if ev["issues"]:
            for iss in ev["issues"]:
                print(f"    ⚠️  {iss}")
        await asyncio.sleep(0.5)

    # 跑多轮追问
    for seq_id, pairs in followup_map.items():
        pairs_sorted = sorted(pairs, key=lambda x: x[0])
        if len(pairs_sorted) != 2:
            continue
        (cid1, cat1, q1), (cid2, cat2, q2) = pairs_sorted
        print(f"[{cid1}→{cid2}] {cat1}: {q1[:30]} → {q2[:30]}", end=" ", flush=True)
        t1 = time.time()
        try:
            r1, r2 = await run_dialogue(seq_id, q1, q2)
            ev1 = evaluate(cid1, cat1, q1, r1)
            ev2 = evaluate(cid2, cat2, q2, r2)
        except Exception as e:
            ev1 = {"case_id": cid1, "passed": False, "issues": [f"异常: {e}"], "text_len": 0,
                   "bold_count": 0, "has_price": False, "product_count": 0, "top_products": [],
                   "field_hits": {k: False for k in FIELD_SIGNALS}, "text_preview": str(e)}
            ev2 = {"case_id": cid2, "passed": False, "issues": [f"异常: {e}"], "text_len": 0,
                   "bold_count": 0, "has_price": False, "product_count": 0, "top_products": [],
                   "field_hits": {k: False for k in FIELD_SIGNALS}, "text_preview": str(e)}
        ev1["elapsed"] = round(time.time() - t1, 1)
        ev2["elapsed"] = ev1["elapsed"]
        results.append(ev1)
        results.append(ev2)
        all_pass = ev1["passed"] and ev2["passed"]
        mark = "✅" if all_pass else "❌"
        print(f"{mark} Q1:{ev1['text_len']}字 Q2:{ev2['text_len']}字 ({ev1['elapsed']}s)")
        for tag, ev in [("Q1", ev1), ("Q2", ev2)]:
            if ev["issues"]:
                for iss in ev["issues"]:
                    print(f"    {tag} ⚠️  {iss}")
        await asyncio.sleep(0.5)

    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    total_time = round(time.time() - t0, 1)
    avg_time = round(total_time / max(total, 1), 1)

    # 分类别统计
    cat_stats = {}
    for r in results:
        c = r["category"]
        s = cat_stats.setdefault(c, {"total": 0, "pass": 0, "issues": []})
        s["total"] += 1
        if r["passed"]:
            s["pass"] += 1
        s["issues"].extend(r["issues"])

    # 7字段吸收率
    field_total = {k: 0 for k in FIELD_SIGNALS}
    field_hit = {k: 0 for k in FIELD_SIGNALS}
    for r in results:
        for fk, hit in r.get("field_hits", {}).items():
            field_total[fk] += 1
            if hit:
                field_hit[fk] += 1

    print("\n" + "=" * 70)
    print(f"测试完成 - 总耗时 {total_time}s，平均 {avg_time}s/条")
    print(f"通过率：{passed}/{total} ({round(passed*100/total,1)}%)")
    print("=" * 70)

    print("\n【分类别结果】")
    for cat, s in cat_stats.items():
        rate = round(s["pass"] * 100 / max(s["total"], 1), 1)
        print(f"  {cat}: {s['pass']}/{s['total']} ({rate}%)")

    print("\n【7字段信号吸收率】")
    for fk in FIELD_SIGNALS.keys():
        t = field_total[fk]
        h = field_hit[fk]
        rate = round(h * 100 / max(t, 1), 1)
        print(f"  {fk}: {h}/{t} ({rate}%)")
    avg_field = round(sum(field_hit[k] for k in field_hit) * 100 / max(sum(field_total.values()), 1), 1)
    print(f"  综合7字段吸收率：{avg_field}%")

    # 失败用例
    failed = [r for r in results if not r["passed"]]
    if failed:
        print(f"\n【失败用例（{len(failed)}条）】")
        for r in failed:
            print(f"\n  ❌ {r['case_id']} [{r['category']}] Q: {r['question']}")
            print(f"     商品: {r.get('top_products')}")
            print(f"     问题: {r['issues']}")
            print(f"     预览: {r['text_preview'][:150]}")
    else:
        print("\n🎉 所有50轮测试全部通过！")

    # 持久化详细结果
    out = {
        "summary": {
            "total": total, "passed": passed, "rate": round(passed*100/total, 1),
            "total_time": total_time, "avg_time": avg_time,
            "cat_stats": cat_stats,
            "field_hit": field_hit, "field_total": field_total,
            "avg_field_rate": avg_field,
        },
        "cases": results,
    }
    with open("/tmp/v2_llm50_results.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n详细结果已存：/tmp/v2_llm50_results.json")


if __name__ == "__main__":
    asyncio.run(main())
