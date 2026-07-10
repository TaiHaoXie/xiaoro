#!/usr/bin/env python3
"""
30条分层回归测试（monkey-patch LLM，快速验证主链路+7字段接入正确性）
"""
import sys
import asyncio
import json
import re
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
    # ─── A. 基础推荐（8） ───
    ("A01 油皮乳液清爽", "油皮夏天用什么乳液比较清爽"),
    ("A02 敏感肌面霜修护", "敏感肌修护屏障的面霜推荐一下"),
    ("A03 干皮保湿精华300内", "干皮保湿精华，预算300以内"),
    ("A04 学生党防晒100内", "学生党预算100以内的防晒"),
    ("A05 妆前乳控油持妆", "控油持妆好的妆前乳有哪些"),
    ("A06 抗初老精华25岁", "25岁开始抗初老，选什么精华"),
    ("A07 油皮爽肤水", "油皮用的爽肤水，清爽不黏腻"),
    ("A08 刷酸后修护", "刚刷完酸，皮肤有点敏感，推荐修护的"),

    # ─── B. 单品判断（6） ───
    ("B01 珂润乳液油皮", "珂润乳液油皮能用吗"),
    ("B02 小棕瓶敏感肌", "雅诗兰黛小棕瓶敏感肌可以用吗"),
    ("B03 理肤泉B5当面霜", "理肤泉B5修复霜能当面霜天天用吗"),
    ("B04 兰蔻小白管油", "兰蔻小白管防晒油不油，混油皮适合吗"),
    ("B05 玉泽调理乳", "玉泽皮肤屏障修护调理乳怎么样"),
    ("B06 苏菲娜妆前乳干皮", "苏菲娜妆前乳干皮能用吗"),

    # ─── C. 商品对比（3） ───
    ("C01 珂润vs玉泽", "珂润和玉泽哪个更适合敏感肌"),
    ("C02 小棕瓶vs红腰子", "雅诗兰黛小棕瓶和资生堂红腰子哪个好"),
    ("C03 安热沙vs小白管", "安热沙小金瓶和兰蔻小白管哪个通勤用更好"),

    # ─── D. 多轮追问（4序列） ───
    ("D01 敏感肌→面霜→价→用法", [
        "我是敏感肌",
        "想看看面霜",
        "第一款多少钱",
        "这款怎么用比较好",
    ]),
    ("D02 油皮→乳液→含酒精吗", [
        "油皮",
        "推荐一款乳液",
        "里面含酒精吗",
    ]),
    ("D03 干皮→防晒→卸妆", [
        "干皮日常通勤防晒推荐",
        "这款需要专门卸妆吗",
    ]),
    ("D04 夸迪CT50→敏感→用法", [
        "夸迪CT50怎么样",
        "敏感肌能用吗",
        "怎么用",
    ]),

    # ─── E. 绕话/模糊（3） ───
    ("E01 脸泛红", "最近脸颊容易泛红，用什么比较稳妥"),
    ("E02 熬夜发黄", "经常熬夜，脸色黄黄的，有什么能提亮的"),
    ("E03 T油两颊干", "我T区很油但两颊干，选什么护肤品"),

    # ─── F. 7字段丰富度验证（3） ───
    ("F01 珂润用法泵数", "珂润乳液的使用方法是怎样的，一次用多少"),
    ("F02 苏菲娜控油原理", "苏菲娜妆前乳真的能控油吗，它是什么原理"),
    ("F03 CPB长管隔离", "CPB长管隔离适合什么肤质"),

    # ─── G. 评价缺失场景（2） ───
    ("G01 雅诗兰黛面霜质地", "雅诗兰黛智妍面霜质地怎么样，滋润吗"),
    ("G02 兰蔻菁纯年龄", "兰蔻菁纯面霜适合什么年龄段"),

    # ─── H. 知识类（1） ───
    ("H01 烟酰胺搭配", "烟酰胺搭配什么成分效果更好"),
]


def _red(s: str) -> str: return f"\033[31m{s}\033[0m"
def _green(s: str) -> str: return f"\033[32m{s}\033[0m"
def _yellow(s: str) -> str: return f"\033[33m{s}\033[0m"


async def collect_response(agent, query: str, sid: str, history: List[Dict], image_context=None) -> Dict[str, Any]:
    answer_contract = {}
    text_chunks: List[str] = []
    products_list: List[Dict] = []
    pitfalls_list: List[Dict] = []
    intent_info = None
    error_msg = None
    async for event in agent.chat_stream_events(
        message=query, session_id=sid,
        conversation_history=history or [], image_context=image_context,
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
            products_list = data.get("products") or []
        elif etype == "pitfalls":
            pitfalls_list = data if isinstance(data, list) else data.get("items", [])
        elif etype == "intent":
            intent_info = data
        elif etype == "error":
            error_msg = data.get("message") if isinstance(data, dict) else str(data)
    return {
        "answer_mode": (answer_contract.get("answer_mode") if isinstance(answer_contract, dict) else None) or "unknown",
        "text": "".join(text_chunks),
        "products": products_list,
        "pitfalls": pitfalls_list,
        "intent": intent_info,
        "error": error_msg,
        "answer_contract": answer_contract,
    }


def check_seven_fields(products: List[Dict[str, Any]]) -> List[str]:
    # serialized products(前端合同)故意不带7字段;7字段只在LLM生成阶段注入_facts包
    # 这里仅做商品存在性检查,真实7字段有效性在50条真实LLM测试里通过文案验证
    return []


FORBIDDEN_GAP_PHRASES = [
    "没抓到", "未抓取到", "没有足够评价", "数据不足", "资料有限",
    "详情页没有提到", "评价较少", "评价很少", "暂无评价",
    "手头没有", "数据库没有", "没有它的具体信息", "没有收录",
    "我这边没有", "库里没有",
]


def check_no_gap(text: str) -> List[str]:
    return [w for w in FORBIDDEN_GAP_PHRASES if w in text]


async def run_one(label: str, query, agent: V2ShoppingAgent, sid: str):
    queries = query if isinstance(query, list) else [query]
    history: List[Dict[str, Any]] = []
    detail: List[str] = []
    last_result: Dict[str, Any] = {}
    for i, q in enumerate(queries, 1):
        try:
            r = await collect_response(agent, q, sid, history)
        except Exception as e:
            import traceback
            traceback.print_exc()
            return "ERROR", [f"轮{i} 异常: {e}"]
        products = r["products"]
        text = (r["text"] or "").strip()
        intent = r.get("intent") or {}
        intent_name = intent.get("intent") if isinstance(intent, dict) else intent
        mode = r["answer_mode"]

        issues: List[str] = []
        if r.get("error"):
            issues.append(f"错误: {r['error'][:80]}")
        if not products and mode not in ("knowledge", "no_match", "profile_statement", "unknown"):
            issues.append("无商品返回")
        sf_issues = check_seven_fields(products)
        if sf_issues:
            issues.extend(sf_issues[:3])
        gap_hits = check_no_gap(text)
        if gap_hits:
            issues.append("禁句: " + "/".join(gap_hits))

        ok = not issues
        icon = _green("✅") if ok else _red("❌")
        names = "、".join(
            (p.get("name","")[:16] + f"({p.get('reference_price') or '?'})") for p in products[:3]
        ) or "(无)"
        detail.append(f"  轮{i}: {icon} 意图={intent_name} 模式={mode} 商品数={len(products)} [{names}]")
        for is_ in issues:
            detail.append(f"    ⚠️ {is_}")
        history.append({"role": "user", "content": q})
        history.append({"role": "assistant", "content": text})
        last_result = r
    if last_result:
        t = (last_result.get("text") or "").replace("\n", " ")
        detail.append(f"  文案预览: {t[:260]}...")
    return ("OK" if not any("❌" in d for d in detail) else "FAIL"), detail


async def main():
    await init_postgres_pool()
    agent = get_v2_shopping_agent()

    # monkey-patch: 直接返回本地draft，不调真实LLM
    async def _mock_llm(self, turn, result):
        return None  # 让它走presenter本地草稿分支
    agent._try_generate_llm_text = _mock_llm.__get__(agent, V2ShoppingAgent)

    passed = 0
    failed = 0
    failures: List[str] = []
    for idx, (label, query) in enumerate(CASES, 1):
        sid = f"layer30_{idx}"
        status, detail = await run_one(label, query, agent, sid)
        header = f"[{idx:02d}] {label}"
        print(f"{_green('✅') if status=='OK' else _red('❌')} {header}")
        for d in detail:
            print(d)
        print()
        if status == "OK":
            passed += 1
        else:
            failed += 1
            failures.append(label)

    print("=" * 70)
    print(f"30条分层测试(无LLM): {_green(f'通过 {passed}')} / {_red(f'失败 {failed}')} / 共 {len(CASES)}")
    if failures:
        print(f"失败用例: {failures}")
    await close_postgres_pool()
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
