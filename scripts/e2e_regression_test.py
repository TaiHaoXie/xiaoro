#!/usr/bin/env python3
"""
端到端真实链路回归测试（100条case）
- 走V2ShoppingAgent完整链路（BGE向量检索+Milvus+真实Retriever/Ranker/Presenter）
- monkey-patch掉LLM润色（省API钱），但BGE embedding/CLIP会正常走
- 覆盖：推荐/对比/知识/单品判断/追问/绕话/多轮
"""
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

from app.database.postgres import init_postgres_pool
from app.services.v2.agent import V2ShoppingAgent


# ============================================================
# 100条测试用例
# 单轮query: (label, query)
# 多轮序列: (label, [q1, q2, ...])
# ============================================================
SINGLE_CASES: List[Tuple[str, str]] = [
    # ─── 基础推荐：肤质+品类+预算 ───
    ("R01 油皮精华300", "油皮精华预算300以内"),
    ("R02 干皮面霜500", "干皮面霜500以内，保湿好的"),
    ("R03 敏感肌洁面", "敏感肌用的温和洁面，有什么推荐"),
    ("R04 油皮防晒通勤", "油皮日常通勤用的防晒，清爽不油腻"),
    ("R05 美白精华300", "想美白，300以内的精华推荐一下"),
    ("R06 抗初老800", "抗初老精华800左右推荐"),
    ("R07 补水面膜敏感肌", "敏感肌可以用的补水面膜"),
    ("R08 油皮爽肤水控油", "油皮夏天用的爽肤水，能控油的"),
    ("R09 干皮眼霜不长粒", "干皮用的眼霜，不要长脂肪粒"),
    ("R10 油皮持妆粉底", "油皮持妆粉底液，不脱妆的那种"),
    ("R11 修护屏障面霜", "屏障受损了，想修护的面霜"),
    ("R12 淡斑精华", "想淡斑，有什么精华推荐"),
    ("R13 干皮防晒通勤", "干皮通勤防晒200以内"),
    ("R14 油皮清爽乳液", "油皮用的清爽乳液"),
    ("R15 学生党平价防晒", "学生党，预算100以内的防晒"),
    ("R16 痘肌精华", "长痘，想找适合痘肌的精华"),
    ("R17 孕妇可用防晒", "怀孕了，推荐孕妇能用的防晒"),
    ("R18 熬夜党精华", "经常熬夜，有没有什么急救精华"),
    ("R19 敏感肌防晒", "敏感肌用的防晒，不要刺激的"),
    ("R20 男油皮洁面", "男生油皮，洗面奶推荐"),
    ("R21 秋冬干皮面霜", "秋冬用，干皮面霜不搓泥"),
    ("R22 眼部消肿精华", "早上起来眼睛肿，消肿的眼精华"),
    ("R23 烟酰胺精华入门", "第一次用烟酰胺，推荐个入门款"),
    ("R24 A醇抗老入门", "想开始用A醇抗老，入门选哪个"),
    ("R25 妆前防晒不搓泥", "妆前用的防晒，不搓泥不卡粉"),
    ("R26 混油T区控油", "混油皮肤，T区特别油，推荐水和精华"),
    ("R27 刷酸后修护", "刚刷完酸，需要修护的产品"),
    ("R28 油皮冬天不干燥", "油皮冬天也会干，推荐不油又保湿的"),
    ("R29 25岁初抗老", "25岁了想开始抗初老，精华选什么"),
    ("R30 30岁熟龄肌面霜", "30岁熟龄肌用的抗老面霜"),

    # ─── 高频别名单品判断 ───
    ("J01 小棕瓶怎么样", "雅诗兰黛小棕瓶怎么样，适合油皮吗"),
    ("J02 SK2神仙水", "SK2神仙水适合什么肤质"),
    ("J03 安热沙小金瓶", "小金瓶防晒油不油"),
    ("J04 兰蔻粉水干皮", "兰蔻大粉水适合干皮吗"),
    ("J05 菌菇水敏感肌", "悦木之源菌菇水敏感肌能用吗"),
    ("J06 紫米精华玻色因", "修丽可紫米精华含多少玻色因"),
    ("J07 B5霜修护", "理肤泉B5霜怎么用，能当面霜吗"),
    ("J08 OLAY小白瓶烟酰胺", "OLAY小白瓶烟酰胺浓度高吗"),
    ("J09 绿宝瓶抗老", "赫莲娜绿宝瓶抗老效果怎么样"),
    ("J10 蓝胖子防晒", "资生堂蓝胖子防晒适合通勤吗"),

    # ─── 商品对比 ───
    ("C01 棕瓶vs黑瓶", "小棕瓶和小黑瓶哪个好"),
    ("C02 B5vs玉泽敏感", "理肤泉B5和玉泽哪个适合敏感肌"),
    ("C03 小金瓶vs小白管", "安热沙小金瓶和兰蔻小白管哪个通勤好"),
    ("C04 DWvs权力油皮", "雅诗兰黛DW和阿玛尼权力粉底哪个适合油皮"),
    ("C05 紫米vsCE", "修丽可紫米和CE精华哪个抗老好"),
    ("C06 双抗vs小白瓶美白", "珀莱雅双抗和OLAY小白瓶哪个美白效果好"),
    ("C07 珂润vs薇诺娜特护", "珂润面霜和薇诺娜特护霜哪个修复好"),
    ("C08 神仙水vs菌菇水", "SK2神仙水和悦木之源菌菇水哪个适合油皮"),
    ("C09 蓝胖子vs安热沙", "资生堂蓝胖子和安热沙小金瓶哪个更防水"),
    ("C10 珂润vs芙丽芳丝洁面", "珂润洗面奶和芙丽芳丝哪个更温和"),

    # ─── 纯知识问答 ───
    ("K01 早C晚A是什么", "早C晚A是什么意思，新手怎么开始"),
    ("K02 烟酰胺作用", "烟酰胺有什么作用，敏感肌能用吗"),
    ("K03 玻色因A醇区别", "玻色因和A醇有什么区别，能一起用吗"),
    ("K04 神经酰胺是什么", "神经酰胺是什么成分，修护屏障有用吗"),
    ("K05 水杨酸果酸区别", "水杨酸和果酸的区别是什么，哪个更适合痘肌"),
    ("K06 二裂酵母是什么", "二裂酵母是什么，和神仙水PITERA是一回事吗"),
    ("K07 敏感肌A醇吗", "敏感肌可以用A醇吗，怎么建立耐受"),
    ("K08 维C白天用吗", "维C精华白天能用吗，要避光吗"),
    ("K09 防晒需要卸妆吗", "防晒需要卸妆吗，洗面奶能洗干净吗"),
    ("K10 面膜天天敷好吗", "面膜可以天天敷吗，多久敷一次合适"),

    # ─── 绕话/自然表达（模拟真人说话方式） ───
    ("N01 夏天脸油要死", "我夏天脸油死了，给我整一套？"),
    ("N02 学生党没钱", "学生党没啥钱，想美白有啥推荐"),
    ("N03 脸红烫", "我脸最近又红又烫，用什么能救救"),
    ("N04 30岁了细纹", "都30了眼睛下面细纹出来了，咋办"),
    ("N05 刚生完孩子", "刚生完孩子，想用点安全的护肤"),
    ("N06 一晒就黑", "我特别容易晒黑，夏天防晒怎么选"),
    ("N07 黑头多", "鼻子黑头特别多，用啥能改善"),
    ("N08 换季脱皮", "一到换季脸就脱皮，还疼，咋办"),
]

FOLLOWUP_SEQUENCES: List[Tuple[str, List[str]]] = [
    ("F01 油皮精华→第一款多少钱", ["油皮精华300以内", "第一款多少钱"]),
    ("F02 油皮精华→第二款对油皮友好吗", ["油皮精华300以内", "第二款对油皮友好吗"]),
    ("F03 小棕瓶→有什么成分", ["小棕瓶怎么样", "它主要有什么成分"]),
    ("F04 OLAY小白瓶→含烟酰胺吗", ["OLAY小白瓶怎么样", "含不含烟酰胺"]),
    ("F05 绿宝瓶→有没有平替", ["赫莲娜绿宝瓶怎么样", "有没有平替"]),
    ("F06 修丽可CE→有没有更便宜的", ["修丽可CE精华怎么样", "有没有更便宜的"]),
    ("F07 A醇精华→白天晚上", ["A醇精华推荐", "白天用还是晚上用"]),
    ("F08 SK2→多少钱", ["SK2神仙水怎么样", "多少钱"]),
    ("F09 多轮油皮→精华→300以内", ["我是油皮", "想要精华", "300以内"]),
    ("F10 理肤泉B5→敏感肌可用吗", ["理肤泉B5怎么样", "敏感肌可以用吗"]),
    ("F11 敏感肌面霜→第一款成分", ["敏感肌面霜500以内", "第一款主要成分是什么"]),
    ("F12 防晒→要不要卸妆", ["油皮防晒清爽不油腻", "这款需要卸妆吗"]),
    ("F13 推荐→再给几款", ["美白精华300以内推荐", "还有别的吗，再给几款"]),
    ("F14 小棕瓶→和小黑瓶比呢", ["小棕瓶怎么样", "和小黑瓶比哪个好"]),
    ("F15 粉水→和菌菇水比呢", ["兰蔻粉水怎么样", "和菌菇水比哪个好"]),
    ("F16 双抗→能和A醇一起用吗", ["珀莱雅双抗精华怎么样", "能和A醇一起用吗"]),
    ("F17 多轮干皮→眼霜→不长粒→多少钱", ["我是干皮", "想买眼霜", "不要长脂肪粒", "第一款多少钱"]),
    ("F18 小金瓶→需要卸妆吗", ["小金瓶防晒怎么样", "这个需要专门卸妆吗"]),
    ("F19 珂润→可以天天用吗", ["珂润面霜怎么样", "可以天天用吗"]),
    ("F20 烟酰胺知识→搭配什么", ["烟酰胺有什么作用", "搭配什么用效果好"]),
]


async def collect_agent_response(
    agent: V2ShoppingAgent,
    query: str,
    session_id: str,
    history: List[Dict[str, Any]] = None,
    image_context: Dict = None,
) -> Dict[str, Any]:
    """收集agent完整输出，从events中提取最终result和turn信息。"""
    answer_contract = None
    text_chunks: List[str] = []
    products_from_event = []
    comparison = None
    pitfalls = []
    error_msg = None
    final_result = None
    turn_debug = {}

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
        elif etype == "message":
            chunk = data.get("content")
            if chunk:
                text_chunks.append(chunk)
        elif etype == "products":
            products_from_event = data.get("products") or []
        elif etype == "comparison":
            comparison = data
        elif etype == "pitfalls":
            pitfalls = data if isinstance(data, list) else []
        elif etype == "error":
            error_msg = data.get("message")

    text = "".join(text_chunks)

    # 从products事件里取商品（已经是serialized，含spec）
    result = {
        "answer_mode": answer_contract.get("answer_mode") if answer_contract else "unknown",
        "followup_type": answer_contract.get("followup_type") if answer_contract else None,
        "text": text,
        "products": products_from_event,
        "comparison": comparison,
        "pitfalls": pitfalls,
        "error": error_msg,
        "answer_contract": answer_contract,
    }
    return {"result": result}


def format_single_result(idx: int, label: str, query: str, resp: Dict) -> str:
    lines = []
    result = resp.get("result") or {}
    mode = result.get("answer_mode")
    mode_str = mode.value if hasattr(mode, "value") else str(mode or "unknown")
    products = result.get("products") or []
    text = (result.get("text") or "").strip()
    ftype = result.get("followup_type") or ""
    ftype_str = f"/{ftype}" if ftype else ""

    ac = result.get("answer_contract") or {}
    error_msg = result.get("error")

    issues = []

    if error_msg:
        issues.append(f"错误: {error_msg[:80]}")

    # ── 检查 ──
    # Guard：商品数量
    from app.services.v2.models import AnswerMode, FollowupType
    expected_max = None
    if mode_str == AnswerMode.COMPARE.value:
        expected_max = 2
    elif mode_str == AnswerMode.RECOMMENDATION.value:
        expected_max = 3
    elif mode_str == AnswerMode.JUDGEMENT.value:
        expected_max = 3
    elif mode_str == AnswerMode.KNOWLEDGE.value:
        pass
    elif mode_str == AnswerMode.FOLLOWUP.value:
        ftype_val = ftype or ""
        single_ftypes = {FollowupType.PRICE.value, FollowupType.INGREDIENT.value, FollowupType.USAGE_TIME.value, FollowupType.SUITABILITY.value}
        if ftype_val in single_ftypes:
            expected_max = 1
    if expected_max is not None and len(products) > expected_max:
        issues.append(f"商品数超Guard: 期望≤{expected_max} 实际{len(products)}")

    # NO_MATCH非预期检查
    if mode_str == AnswerMode.NO_MATCH.value and len(query) > 4:
        if not any(k in query for k in ["你好", "在吗", "hi", "hello"]):
            issues.append("误判NO_MATCH")

    # unknown模式（可能出错）
    if mode_str == "unknown" and not error_msg and len(query) > 4:
        issues.append("意图未识别/无返回")

    # 价格规格检查
    if mode_str in (AnswerMode.FOLLOWUP.value, AnswerMode.JUDGEMENT.value, AnswerMode.COMPARE.value) and len(products) >= 1:
        for p in products[:2]:
            price = p.get("price_val") or p.get("price") or 0
            spec = p.get("spec") or ""
            pname = p.get("name") or p.get("display_name") or ""
            if price and not spec:
                if "ml" not in pname and "g" not in pname and "片" not in pname:
                    issues.append(f"价格无规格: {pname[:15]} ¥{price}")

    # 脏文本检查
    dirty = ["【数据来源】", "OCR识别", "产地参数", "备案号", "生产企业", "价格见详情页", "正品保障"]
    for d in dirty:
        if d in text:
            issues.append(f"脏文本: {d}")

    # 生硬标签检查
    stiff = ["预算贴合款", "性价比首选", "进阶功效款", "特殊场景款", "总结一下", "综上所述"]
    for s in stiff:
        if s in text:
            issues.append(f"生硬标签: {s}")

    # 截断检查
    if text.endswith("注意点：") or text.endswith("注意点:") or "注意点：肌肤泛红起皮" in text:
        issues.append("文案截断")

    # 知识问答不带商品时应该有实际内容
    if mode_str == AnswerMode.KNOWLEDGE.value and len(products) == 0 and len(text) < 20:
        issues.append("知识问答文案过短")

    issue_str = " ⚠️ " + " | ".join(issues) if issues else " ✅"

    prod_parts = []
    for p in products[:3]:
        pv = int(p.get("price_val") or p.get("price") or 0)
        sp = p.get("spec") or ""
        nm = p.get("display_name") or p.get("name") or ""
        if len(nm) > 25:
            nm = nm[:25]
        prod_parts.append(f"{nm}({pv}{'/' + sp if sp else ''})")
    prod_str = "、".join(prod_parts) if prod_parts else "(无)"

    lines.append(f"[{idx+1:02d}] {label} | {query}")
    lines.append(f"  意图: {mode_str}{ftype_str} | 商品数: {len(products)}")
    lines.append(f"  商品: {prod_str}")
    lines.append(f"  状态: {issue_str}")

    preview = text[:300].replace("\n", " ")
    if len(text) > 300:
        preview += "..."
    lines.append(f"  文案: {preview}")
    return "\n".join(lines), issues


def format_followup_seq(seq_idx: int, label: str, sequence: List[str], results: List[Dict]) -> Tuple[str, List]:
    lines = [f"{'─' * 70}", f"[{seq_idx+1:02d}] === {label} ==="]
    all_issues = []
    for i, (q, resp) in enumerate(zip(sequence, results)):
        text, issues = format_single_result(seq_idx * 100 + i, label + f"[轮{i+1}]", q, resp)
        # 缩进
        for ln in text.split("\n"):
            lines.append(("  ↳ " if i > 0 else "") + ln)
        all_issues.extend(issues)
    return "\n".join(lines), all_issues


async def main():
    print("初始化数据库连接池...")
    await init_postgres_pool()
    agent = V2ShoppingAgent()

    # 关闭LLM润色，省API钱
    async def _no_llm(self, *a, **kw):
        return None
    agent._try_generate_llm_text = _no_llm.__get__(agent, V2ShoppingAgent)

    print("✅ Agent 初始化完成（LLM润色已关闭以节省成本）\n")

    all_output: List[str] = []
    all_issues: List[Tuple[int, str, List[str]]] = []
    idx = 0

    # 跑单轮case
    print(f"▶ 开始跑单轮case: {len(SINGLE_CASES)}条")
    for label, query in SINGLE_CASES:
        try:
            resp = await collect_agent_response(agent, query, f"e2e_{idx}")
            text, issues = format_single_result(idx, label, query, resp)
            all_output.append(text)
            if issues:
                all_issues.append((idx, query, issues))
        except Exception as e:
            import traceback
            all_output.append(f"[{idx+1:02d}] {label} | {query}\n  ❌ 异常: {e}\n{traceback.format_exc()[-200:]}")
            all_issues.append((idx, query, [f"异常: {e}"]))
        idx += 1
        if idx % 10 == 0:
            print(f"  已跑 {idx}/{len(SINGLE_CASES)}...")

    # 跑多轮序列
    print(f"\n▶ 开始跑多轮序列: {len(FOLLOWUP_SEQUENCES)}组")
    for label, sequence in FOLLOWUP_SEQUENCES:
        history: List[Dict[str, Any]] = []
        results = []
        session_id = f"e2e_seq_{idx}"
        for q in sequence:
            resp = await collect_agent_response(agent, q, session_id, history)
            results.append(resp)
            history.append({"role": "user", "content": q})
            r_text = (resp.get("result") or {}).get("text", "")
            history.append({"role": "assistant", "content": r_text})
        text, issues = format_followup_seq(idx, label, sequence, results)
        all_output.append(text)
        if issues:
            all_issues.append((idx, label, issues))
        idx += 1

    # 输出
    print("\n".join(all_output))

    total = len(SINGLE_CASES) + len(FOLLOWUP_SEQUENCES)
    print(f"\n{'='*80}")
    print(f"测试完成: 共{total}组场景, 发现{len(all_issues)}组存在问题")
    if all_issues:
        print("\n问题汇总:")
        for i, (qidx, q, issues) in enumerate(all_issues, 1):
            print(f"  {i}. [{qidx+1:02d}] {q[:50]}")
            for issue in issues[:3]:
                print(f"     - {issue[:150]}")

    # 把结果存文件
    out_path = Path(__file__).parent.parent / "test_results_e2e.txt"
    out_path.write_text("\n".join(all_output), encoding="utf-8")
    print(f"\n完整结果已保存到: {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
