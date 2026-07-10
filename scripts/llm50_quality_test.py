#!/usr/bin/env python3
"""
50条真实LLM完整链路测试
- 走真实SiliconFlow大模型（不mock）
- 覆盖推荐/单品判断/对比/多轮追问/绕话/知识/评价缺失天猫场景
- 自动评估：禁句暴露、7字段信息吸收率（质地/用法/评价/原理是否在文案中出现）、
           价格规格完整性、营销腔检测、整体自然度评分
"""
import sys
import asyncio
import json
import re
import time
from pathlib import Path
from typing import List, Dict, Any, Tuple

sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except Exception:
    pass

from app.database.postgres import init_postgres_pool, close_postgres_pool
from app.services.v2.agent import V2ShoppingAgent, get_v2_shopping_agent


CASES: List[Tuple[str, Any]] = [
    # ─── A. 基础推荐（12条） ───
    ("A01 油皮夏天乳液", "油皮夏天用什么乳液比较清爽，不要闷痘"),
    ("A02 敏感肌修护面霜", "敏感肌容易泛红，推荐个修护屏障的面霜"),
    ("A03 干皮保湿精华300", "干皮，想找个保湿好的精华，预算300以内"),
    ("A04 学生党防晒100", "学生党预算有限，100以内的防晒推荐一下"),
    ("A05 妆前乳控油持妆", "大油田，想要控油持妆好的妆前乳"),
    ("A06 25岁抗初老精华", "25岁了开始抗初老，用什么精华比较合适"),
    ("A07 油皮夏天爽肤水", "油皮夏天用什么爽肤水，清爽不黏腻"),
    ("A08 刷酸后修护", "刚刷完酸，皮肤有点敏感，用什么修护比较稳妥"),
    ("A09 干皮冬天面霜", "干皮冬天用什么面霜够滋润但不搓泥"),
    ("A10 男士油皮洁面", "男生油皮，用什么洗面奶洗得干净不紧绷"),
    ("A11 孕妇可用防晒", "怀孕了，有没有孕妇能用的温和防晒"),
    ("A12 熬夜提亮精华", "经常熬夜，脸色暗沉发黄，什么精华能提亮"),

    # ─── B. 单品判断（12条） ───
    ("B01 珂润乳液油皮", "珂润的乳液油皮能用吗，会不会太润"),
    ("B02 小棕瓶敏感肌", "雅诗兰黛小棕瓶敏感肌可以用吗"),
    ("B03 理肤泉B5天天用", "理肤泉B5修复霜能当面霜天天用吗"),
    ("B04 兰蔻小白管混油", "兰蔻小白管防晒油不油，混油皮夏天用可以吗"),
    ("B05 玉泽调理乳怎样", "玉泽皮肤屏障修护调理乳怎么样，值不值得买"),
    ("B06 珂润面霜冬天", "珂润的面霜冬天用够保湿吗"),
    ("B07 OLAY淡斑小白瓶", "OLAY淡斑小白瓶烟酰胺浓度高吗，新手能用吗"),
    ("B08 资生堂蓝胖子通勤", "资生堂蓝胖子防晒适合日常通勤吗"),
    ("B09 赫莲娜绿宝瓶", "赫莲娜绿宝瓶抗老效果怎么样，适合什么年龄"),
    ("B10 修丽可CE白天用", "修丽可CE精华白天用会不会反黑"),
    ("B11 倩碧黄油无油有油", "倩碧黄油有油和无油怎么选，混油皮选哪个"),
    ("B12 珀莱雅双抗A醇", "珀莱雅双抗精华能不能和A醇一起用"),

    # ─── C. 商品对比（6条） ───
    ("C01 珂润vs玉泽敏感", "珂润和玉泽哪个更适合敏感肌日常保湿"),
    ("C02 小棕瓶vs红腰子", "雅诗兰黛小棕瓶和资生堂红腰子怎么选"),
    ("C03 安热沙vs兰蔻", "安热沙小金瓶和兰蔻小白管哪个更适合通勤"),
    ("C04 科颜氏高保湿vs珂润", "科颜氏高保湿面霜和珂润面霜哪个更滋润"),
    ("C05 B5vs玉泽", "理肤泉B5和玉泽调理乳哪个修护屏障更好"),
    ("C06 双抗vs小白瓶", "珀莱雅双抗和OLAY小白瓶哪个提亮效果好"),

    # ─── D. 多轮追问（5条序列） ───
    ("D01 敏感肌→面霜→价格→用法", [
        "我是敏感肌",
        "想看看面霜",
        "第一款多少钱",
        "怎么用效果最好",
    ]),
    ("D02 油皮→乳液→酒精→早晚", [
        "油皮",
        "推荐一款乳液",
        "含酒精吗",
        "早晚都能用吗",
    ]),
    ("D03 干皮→防晒→卸妆→补涂", [
        "干皮日常通勤防晒推荐",
        "需要专门卸妆吗",
        "户外要不要补涂",
    ]),
    ("D04 夸迪CT50→敏感→用法→搭配", [
        "夸迪CT50怎么样",
        "敏感肌能用吗",
        "怎么用比较好",
        "可以搭配什么",
    ]),
    ("D05 珂润→油皮→夏天用量→闷痘", [
        "珂润乳液怎么样",
        "油皮夏天能用吗",
        "一次用多少",
        "会不会闷痘",
    ]),

    # ─── E. 自然语言/绕话（5条） ───
    ("E01 脸颊泛红", "最近脸颊容易泛红发痒，用什么能稳住"),
    ("E02 熬夜脸黄", "最近加班多，脸色黄黄的没气色，用什么好"),
    ("E03 T油两颊干", "我T区很油但两颊干，这是什么肤质，怎么护肤"),
    ("E04 屏障受损", "感觉屏障受损了，洗脸都刺痛，怎么救"),
    ("E05 妆后卡粉", "上妆总是卡粉起皮，是不是保湿没做好，推荐什么"),

    # ─── F. 7字段吸收验证（4条，命中已知有丰富OCR数据的商品） ───
    ("F01 珂润用量泵数", "珂润乳液一次按几泵比较合适"),
    ("F02 苏菲娜控油", "苏菲娜妆前乳控油真的有用吗，什么原理"),
    ("F03 玉泽PBS技术", "玉泽的PBS技术是什么，有什么作用"),
    ("F04 珂润敏感肌QA", "珂润敏感肌用之前要不要做皮试，怎么用"),

    # ─── G. 天猫/大牌（评价少的场景，看自然过渡）（3条） ───
    ("G01 雅诗兰黛胶原霜质地", "雅诗兰黛胶原霜质地怎么样，干皮适合滋润版吗"),
    ("G02 兰蔻菁纯定位", "兰蔻菁纯面霜适合什么年龄段，抗老效果怎么样"),
    ("G03 海蓝之谜奇迹云绒霜", "海蓝之谜奇迹云绒霜适合油皮吗"),

    # ─── H. 知识类（3条） ───
    ("H01 烟酰胺搭配", "烟酰胺搭配什么成分效果更好，要注意什么"),
    ("H02 早C晚A入门", "想尝试早C晚A，新手怎么入门不翻车"),
    ("H03 神经酰胺作用", "神经酰胺是什么，对皮肤有什么作用"),
]


def _red(s: str) -> str: return f"\033[31m{s}\033[0m"
def _green(s: str) -> str: return f"\033[32m{s}\033[0m"
def _yellow(s: str) -> str: return f"\033[33m{s}\033[0m"
def _cyan(s: str) -> str: return f"\033[36m{s}\033[0m"


async def collect_response(agent, query: str, sid: str, history: List[Dict]) -> Dict[str, Any]:
    answer_contract = {}
    text_chunks: List[str] = []
    products: List[Dict] = []
    intent_info = None
    error_msg = None
    t0 = time.time()
    async for event in agent.chat_stream_events(
        message=query, session_id=sid, conversation_history=history or [],
    ):
        etype = event.get("event")
        data = event.get("data") or {}
        if etype == "answer_contract":
            answer_contract = data.get("answer_contract") or data or {}
        elif etype == "message":
            c = data.get("content")
            if c:
                text_chunks.append(c)
        elif etype == "products":
            products = data.get("products") or []
        elif etype == "intent":
            intent_info = data
        elif etype == "error":
            error_msg = data.get("message") if isinstance(data, dict) else str(data)
    return {
        "answer_mode": (answer_contract.get("answer_mode") if isinstance(answer_contract, dict) else None) or "unknown",
        "text": "".join(text_chunks),
        "products": products,
        "intent": intent_info,
        "error": error_msg,
        "latency": time.time() - t0,
    }


# ─── 评估规则 ───
FORBIDDEN_GAP = [
    "没抓到", "未抓取到", "没有足够评价", "数据不足", "资料有限",
    "详情页没有提到", "评价较少", "评价很少", "暂无评价",
    "手头没有", "数据库没有", "没有它的具体信息", "没有收录",
    "我这边没有", "库里没有", "我没有相关", "我的信息有限",
    "没有这个商品", "不了解这款",
]
FORBIDDEN_MARKETING = [
    "闭眼入", "性价比之王", "强烈推荐", "不容错过", "入手不亏",
    "完美选择", "非常值得入手", "绝对值得",
]
FORBIDDEN_INTERNAL = [
    "后端", "候选商品", "系统筛选", "AI助手", "我是AI", "作为AI",
    "根据数据显示", "根据评价显示",
]
# 7字段信息在文案中出现的信号词（判断LLM是否真的吸收了）
TEXTURE_SIGNALS = ["质地", "肤感", "清爽", "滋润", "轻薄", "黏腻", "好推开", "吸收", "绵密", "丝滑", "哑光", "水润"]
USAGE_SIGNALS = ["用法", "按压", "泵", "洁面后", "早晚", "一次用", "取", "涂抹", "按摩至吸收", "用量"]
REVIEW_SIGNALS = ["反馈", "觉得", "很多人", "有人说", "用户", "买过的人", "评论", "油皮反馈", "干皮反馈"]
MECHANISM_SIGNALS = ["原理", "神经酰胺", "屏障", "修护", "配方", "技术", "作用机制", "主打", "成分", "专利"]
CLAIM_SIGNALS = ["官方", "品牌资料", "品牌称", "官方称", "实验", "数据显示", "研究表明"]


def evaluate_text(text: str, products: List[Dict]) -> Dict[str, Any]:
    issues: List[str] = []
    positives: List[str] = []

    # 禁句检查
    for w in FORBIDDEN_GAP:
        if w in text:
            issues.append(f"数据缺口暴露: '{w}'")
    for w in FORBIDDEN_MARKETING:
        if w in text:
            issues.append(f"营销腔: '{w}'")
    for w in FORBIDDEN_INTERNAL:
        if w in text:
            issues.append(f"内部词暴露: '{w}'")

    # 价格检查
    prices = re.findall(r"约?¥\s?\d+(?:\.\d+)?", text)
    if not prices:
        issues.append("文案中未出现价格")

    # 7字段吸收检查
    text_lower = text
    has_texture = any(s in text_lower for s in TEXTURE_SIGNALS)
    has_usage = any(s in text_lower for s in USAGE_SIGNALS)
    has_review = any(s in text_lower for s in REVIEW_SIGNALS)
    has_mechanism = any(s in text_lower for s in MECHANISM_SIGNALS)
    has_claim = any(s in text_lower for s in CLAIM_SIGNALS)
    absorbed = sum([has_texture, has_usage, has_review, has_mechanism, has_claim])

    if has_texture: positives.append("质地/肤感")
    if has_usage: positives.append("使用方法/用量")
    if has_review: positives.append("用户反馈")
    if has_mechanism: positives.append("原理/成分")
    if has_claim: positives.append("品牌/官方资料")

    return {
        "issues": issues,
        "positives": positives,
        "absorbed_count": absorbed,
        "has_texture": has_texture,
        "has_usage": has_usage,
        "has_review": has_review,
        "has_mechanism": has_mechanism,
        "has_claim": has_claim,
        "text_len": len(text),
    }


async def run_one(label: str, query, agent: V2ShoppingAgent, sid: str):
    queries = query if isinstance(query, list) else [query]
    history: List[Dict] = []
    last_eval = None
    last_result = None
    turn_details = []
    for i, q in enumerate(queries, 1):
        r = await collect_response(agent, q, sid, history)
        text = (r["text"] or "").strip()
        products = r["products"]
        mode = r["answer_mode"]
        intent = (r.get("intent") or {})
        intent_name = intent.get("intent") if isinstance(intent, dict) else intent

        # 只对最终一轮做严格质量评估
        eval_res = evaluate_text(text, products)
        last_eval = eval_res
        last_result = r

        issues = list(eval_res["issues"])
        if r.get("error"):
            issues.append(f"调用错误: {r['error'][:60]}")
        if not text and mode not in ("no_match",):
            issues.append("空文案")

        ok = not issues
        turn_details.append({
            "turn": i, "query": q, "ok": ok, "issues": issues,
            "mode": mode, "intent": intent_name,
            "n_products": len(products), "latency": r["latency"],
            "absorbed": eval_res["absorbed_count"],
            "positives": eval_res["positives"],
        })
        history.append({"role": "user", "content": q})
        history.append({"role": "assistant", "content": text})
    return {
        "label": label,
        "ok": all(d["ok"] for d in turn_details),
        "turns": turn_details,
        "final_text": (last_result or {}).get("text",""),
        "final_eval": last_eval,
        "final_mode": (last_result or {}).get("answer_mode"),
        "n_products_final": len((last_result or {}).get("products") or []),
    }


async def main():
    await init_postgres_pool()
    agent = get_v2_shopping_agent()
    # 不mock LLM;走真实大模型
    results: List[Dict] = []
    total = len(CASES)
    t0_all = time.time()
    for idx, (label, query) in enumerate(CASES, 1):
        sid = f"llm50_{idx}"
        print(f"[{idx}/{total}] {label} ...", end=" ", flush=True)
        try:
            res = await run_one(label, query, agent, sid)
            results.append(res)
            icon = _green("✅") if res["ok"] else _red("❌")
            absorbed = res["final_eval"]["absorbed_count"] if res["final_eval"] else 0
            pos = "、".join(res["final_eval"]["positives"]) if res["final_eval"] else ""
            print(f"{icon} 模式={res['final_mode']} 商品数={res['n_products_final']} 信息维度={absorbed}/5 ({pos})")
            if not res["ok"]:
                for d in res["turns"]:
                    for iss in d["issues"]:
                        print(f"    ⚠️ {iss}")
        except Exception as e:
            import traceback
            traceback.print_exc()
            results.append({"label": label, "ok": False, "turns": [], "final_text": "",
                            "final_eval": None, "final_mode": "ERROR", "n_products_final": 0,
                            "error": str(e)})
            print(f"{_red('❌')} 异常: {e}")

    elapsed = time.time() - t0_all

    # ─── 汇总统计 ───
    passed = sum(1 for r in results if r["ok"])
    failed = total - passed
    total_absorbed = 0
    total_possible = 0
    texture_cnt = usage_cnt = review_cnt = mech_cnt = claim_cnt = 0
    gap_violations = []
    marketing_violations = []
    internal_violations = []
    empty_violations = []
    for r in results:
        ev = r.get("final_eval")
        if not ev: continue
        total_absorbed += ev["absorbed_count"]
        total_possible += 5
        if ev["has_texture"]: texture_cnt += 1
        if ev["has_usage"]: usage_cnt += 1
        if ev["has_review"]: review_cnt += 1
        if ev["has_mechanism"]: mech_cnt += 1
        if ev["has_claim"]: claim_cnt += 1
        for d in r["turns"]:
            for iss in d["issues"]:
                if "数据缺口" in iss: gap_violations.append((r["label"], iss))
                elif "营销腔" in iss: marketing_violations.append((r["label"], iss))
                elif "内部词" in iss: internal_violations.append((r["label"], iss))
                elif "空文案" in iss or "未出现价格" in iss: empty_violations.append((r["label"], iss))

    print("\n" + "=" * 70)
    print(_cyan(f"50条真实LLM完整链路测试报告 (耗时 {elapsed:.1f}s)"))
    print("=" * 70)
    print(f"整体通过率: {_green(f'{passed}/{total}')} ({passed/total*100:.0f}%)")
    print()
    print(_cyan("── 7字段信息吸收率 ──"))
    print(f"质地/肤感维度出现率: {texture_cnt}/{total} ({texture_cnt/total*100:.0f}%)")
    print(f"用法/用量维度出现率: {usage_cnt}/{total} ({usage_cnt/total*100:.0f}%)")
    print(f"用户反馈维度出现率: {review_cnt}/{total} ({review_cnt/total*100:.0f}%)")
    print(f"原理/成分维度出现率: {mech_cnt}/{total} ({mech_cnt/total*100:.0f}%)")
    print(f"品牌资料维度出现率: {claim_cnt}/{total} ({claim_cnt/total*100:.0f}%)")
    print(f"综合维度平均: {total_absorbed}/{total_possible} ({total_absorbed/total_possible*100:.0f}%)")
    print()
    print(_cyan("── 违规项统计 ──"))
    print(f"数据缺口暴露: {_red(str(len(gap_violations)))} 条")
    for lbl, iss in gap_violations[:10]:
        print(f"  - {lbl}: {iss}")
    print(f"营销腔: {_yellow(str(len(marketing_violations)))} 条")
    for lbl, iss in marketing_violations[:5]:
        print(f"  - {lbl}: {iss}")
    print(f"内部词暴露: {_red(str(len(internal_violations)))} 条")
    for lbl, iss in internal_violations[:5]:
        print(f"  - {lbl}: {iss}")
    print(f"空文案/缺价格: {_yellow(str(len(empty_violations)))} 条")

    # 失败用例列表
    if failed > 0:
        print()
        print(_cyan("── 失败用例 ──"))
        for r in results:
            if not r["ok"]:
                print(f"  - {r['label']}")

    # 挑3条高质量样例展示
    print()
    print(_cyan("── 高质量文案样例（5维吸收>=4的前3条） ──"))
    high_q = sorted([r for r in results if r.get("final_eval") and r["final_eval"]["absorbed_count"] >= 4],
                    key=lambda x: -x["final_eval"]["absorbed_count"])
    for r in high_q[:3]:
        print(f"\n[{r['label']}] 模式={r['final_mode']} 维度={r['final_eval']['absorbed_count']}/5")
        print("-" * 60)
        print(r["final_text"][:600])
        if len(r["final_text"]) > 600:
            print("...")

    # 保存完整结果
    out_path = Path(__file__).parent.parent / ".tmp_user_download_audit" / "llm50_results.json"
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(json.dumps([
        {"label": r["label"], "ok": r["ok"], "final_mode": r["final_mode"],
         "n_products": r["n_products_final"],
         "absorbed": r["final_eval"]["absorbed_count"] if r["final_eval"] else 0,
         "positives": r["final_eval"]["positives"] if r["final_eval"] else [],
         "issues": [iss for d in r["turns"] for iss in d["issues"]],
         "final_text": r["final_text"],
         } for r in results
    ], ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n完整结果已保存: {out_path}")

    await close_postgres_pool()
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
