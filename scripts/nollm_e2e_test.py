#!/usr/bin/env python3
"""
零 LLM token 的端到端测试：直接打 V2 SSE 接口，验证意图识别/检索/本地 presenter 链路。
需要服务以 V2_DISABLE_LLM=1 启动在 localhost:8000。
BGE embedding（向量检索层）照常使用，不消耗 chat token。
"""
import sys
import json
import asyncio
import time
import httpx
from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except Exception:
    pass


BASE_URL = "http://localhost:8000"
TIMEOUT = 90.0

FORBIDDEN_SUBSTRINGS = [
    "油皮亲妈", "干皮亲妈", "敏感肌亲妈", "混油亲妈",
    "闭眼入", "YYDS", "yyds", "性价比之王", "绝绝子", "封神",
    "亲，", "亲~", "亲！",
    "后端", "候选商品", "系统筛选",
    "数据不足", "资料有限", "暂无评价", "未抓到", "手头没有",
    "我库里", "我手里", "我手头", "我这边",
]


def red_flags(text: str) -> List[str]:
    flags = []
    for f in FORBIDDEN_SUBSTRINGS:
        if f in text:
            flags.append(f)
    return flags


async def sse_chat(client: httpx.AsyncClient, message: str, session_id: str,
                   history: List[Dict[str, Any]] = None) -> Dict[str, Any]:
    """打 SSE chat/stream 接口，按标准 SSE 协议解析 event+data，返回结构化结果。"""
    payload: Dict[str, Any] = {
        "message": message,
        "session_id": session_id,
        "stream": True,
    }
    if history:
        payload["conversation_history"] = history

    events: List[Dict[str, Any]] = []
    full_text = ""
    answer_contract = None
    intent_event = None
    products: List[Dict[str, Any]] = []
    pitfalls: List[Dict[str, Any]] = []

    async with client.stream("POST", f"{BASE_URL}/api/v1/chat/stream",
                             json=payload, timeout=TIMEOUT) as resp:
        resp.raise_for_status()

        # 标准 SSE 解析：按空行切分事件块，每块由多行 field:value 组成
        current_event: str = "message"
        current_data_lines: List[str] = []

        def flush_event():
            nonlocal current_event, current_data_lines
            if not current_data_lines:
                current_event = "message"
                return
            raw = "\n".join(current_data_lines)
            current_data_lines = []
            try:
                data = json.loads(raw)
            except Exception:
                current_event = "message"
                return
            evt = {"event": current_event, "data": data}
            events.append(evt)
            et = current_event
            if et == "intent":
                nonlocal intent_event
                intent_event = data
                # 兼容两种字段：answer_mode（presenter合同）/ intent（V2原始事件）
                if isinstance(data, dict) and "answer_mode" not in data and "intent" in data:
                    data["answer_mode"] = data["intent"]
            elif et == "answer_contract":
                nonlocal answer_contract
                answer_contract = data.get("answer_contract") if isinstance(data, dict) else None
            elif et == "products":
                nonlocal products
                if isinstance(data, dict):
                    products = data.get("products") or []
            elif et == "pitfalls":
                nonlocal pitfalls
                if isinstance(data, dict):
                    pitfalls = data.get("pitfalls") or []
            elif et == "message":
                if isinstance(data, dict):
                    content = data.get("content") or ""
                    if content and not data.get("done"):
                        nonlocal_full_text[0] += content
            elif et == "error":
                print(f"     !! SSE error: {data}")
            current_event = "message"

        nonlocal_full_text = [""]  # 用 list 包装让内部函数能修改

        async for line in resp.aiter_lines():
            if line == "":
                flush_event()
                continue
            if line.startswith(":"):
                continue  # SSE 注释
            if line.startswith("event:"):
                current_event = line[len("event:"):].strip()
            elif line.startswith("data:"):
                current_data_lines.append(line[len("data:"):].strip())
            # 其他字段(id:, retry:)忽略

        flush_event()
        full_text = nonlocal_full_text[0]

    return {
        "events": events,
        "text": full_text,
        "intent": intent_event,
        "contract": answer_contract,
        "products": products,
        "pitfalls": pitfalls,
    }


async def json_chat_message(client: httpx.AsyncClient, message: str, session_id: str,
                            history: List[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "message": message,
        "session_id": session_id,
        "stream": False,
    }
    if history:
        payload["conversation_history"] = history
    resp = await client.post(f"{BASE_URL}/api/v1/chat/message", json=payload, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


# ============ 测试用例 ============

MESSAGE_CASES: List[Tuple[str, str, Dict[str, Any]]] = [
    ("非流式-message走V2", "300以内面霜推荐",
     {"mode": "recommendation", "min_products": 1, "must_contain_any": ["## 综合建议"]}),
]


SINGLE_CASES: List[Tuple[str, str, Dict[str, Any]]] = [
    ("对比-DW权力遮瑕", "DW比权力谁遮瑕强",
     {"mode": "compare", "min_products": 2, "must_have_brands": ["雅诗兰黛", "阿玛尼"]}),
    ("对比-三个敏感肌", "珂润、玉泽、理肤泉哪个适合敏感肌",
     {"mode": "compare", "min_products": 3}),
    ("对比-哪个好改写", "DW和权力拿哪个遮瑕好",
     {"mode": "compare", "min_products": 2}),
    ("对比-怎么选三款", "小棕瓶、小黑瓶、紫米精华怎么选",
     {"mode": "compare", "min_products": 3}),
    ("对比-四款对比", "兰蔻小白管、安热沙小金瓶、理肤泉大哥大、怡思丁防晒哪个通勤好",
     {"mode": "compare", "min_products": 3}),
    ("对比-无显式比较词", "DW 权力 遮瑕",
     {"mode_any": ["compare", "recommendation"], "min_products": 2}),
    ("判断-单品评价", "珂润乳液怎么样",
     {"mode_any": ["judgement", "compare"], "min_products": 1, "max_products": 1}),
    ("判断-适合吗", "小棕瓶适合敏感肌吗",
     {"mode_any": ["judgement", "followup"], "min_products": 1, "max_products": 1}),
    ("判断-单品好不好用", "珀莱雅双抗精华好用吗",
     {"mode_any": ["judgement", "followup"], "min_products": 1, "max_products": 1}),
    ("推荐-油皮精华", "油皮精华推荐300以内",
     {"mode": "recommendation", "min_products": 2}),
    ("推荐-干皮防晒", "干皮防晒通勤用200以内",
     {"mode": "recommendation", "min_products": 2}),
    ("对比-vs写法", "兰蔻小黑瓶 vs 雅诗兰黛小棕瓶",
     {"mode": "compare", "min_products": 2}),
    ("判断-ANR怎么样", "ANR小棕瓶好吗",
     {"mode_any": ["judgement", "compare"], "min_products": 1, "max_products": 1}),
    ("判断-单品价格查询不影响聚焦", "小棕瓶多少钱",
     {"mode_any": ["followup", "judgement"], "min_products": 1, "max_products": 1}),
    ("判断-pk写法", "兰蔻极光水和SK2神仙水pk",
     {"mode": "compare", "min_products": 2}),
    ("判断-单品带价格不跑偏", "珂润乳液多少钱",
     {"mode_any": ["followup", "judgement"], "min_products": 1, "max_products": 1}),
    ("判断-贵不贵问法", "小棕瓶贵不贵",
     {"mode_any": ["followup", "judgement"], "min_products": 1, "max_products": 1}),
    ("推荐-敏感肌防晒", "敏感肌防晒推荐",
     {"mode": "recommendation", "min_products": 2}),
    ("对比-两个卸妆", "贝德玛和理肤泉卸妆哪个好用",
     {"mode": "compare", "min_products": 2}),
    ("判断-单品不带问号", "兰蔻小白管防晒怎么样",
     {"mode_any": ["judgement", "compare"], "min_products": 1, "max_products": 1}),
    ("对比-四款粉底液", "DW 权力 大师 菁纯 哪个遮瑕好",
     {"mode": "compare", "min_products": 2}),
    ("推荐-学生党平价", "平价保湿面霜推荐",
     {"mode": "recommendation", "min_products": 2}),
]

FOLLOWUP_CASES: List[Tuple[str, List[str], Dict[str, Any]]] = [
    ("追问-第一款怎么样",
     ["油皮精华300以内", "第一款怎么样"],
     {"mode_any": ["followup", "judgement"], "min_products": 1, "max_products": 1}),
    ("追问-第二款适合敏感肌吗",
     ["敏感肌面霜推荐", "第二款适合敏感肌吗"],
     {"mode_any": ["followup", "judgement"], "min_products": 1, "max_products": 1}),
    ("追问-三选一跳号问功效",
     ["珂润、玉泽、理肤泉哪个适合敏感肌", "第三款主要功效是什么"],
     {"mode_any": ["followup"], "min_products": 1, "max_products": 1,
      "must_contain_any": ["功效", "主打", "针对", "舒缓", "修护", "保湿", "安心"]}),
    ("追问-三款里哪个最适合",
     ["珂润、玉泽、理肤泉哪个适合敏感肌", "哪个最适合屏障受损"],
     {"mode_any": ["followup", "compare", "judgement"], "min_products": 1}),
    ("追问-价格",
     ["油皮精华300以内", "第一款多少钱"],
     {"mode_any": ["followup"], "min_products": 1, "max_products": 1}),
    ("追问-成分",
     ["小棕瓶怎么样", "有什么核心成分"],
     {"mode_any": ["followup"], "min_products": 1, "max_products": 1}),
    ("追问-单品功效",
     ["珀莱雅双抗精华怎么样", "它主要功效是什么"],
     {"mode_any": ["followup"], "min_products": 1, "max_products": 1,
      "must_contain_any": ["抗氧", "抗糖", "提亮", "去黄", "虾青素", "麦角硫因", "功效", "主打"]}),
    ("追问-它干嘛用的",
     ["理肤泉B5怎么样", "它干嘛用的"],
     {"mode_any": ["followup"], "min_products": 1, "max_products": 1}),
    ("追问-第一款用法",
     ["油皮精华300以内", "第一款怎么用"],
     {"mode_any": ["followup"], "min_products": 1, "max_products": 1,
      "must_contain_any": ["用法", "使用", "取适量", "涂", "按摩"]}),
    ("追问-第二款价格",
     ["敏感肌面霜推荐", "第二款多少钱"],
     {"mode_any": ["followup"], "min_products": 1, "max_products": 1,
      "must_contain_any": ["¥", "约", "参考价", "价格"]}),
    ("追问-第一款含酒精吗",
     ["珂润、玉泽、理肤泉哪个适合敏感肌", "第一款含酒精吗"],
     {"mode_any": ["followup"], "min_products": 1, "max_products": 1}),
    ("追问-它主打什么",
     ["小棕瓶怎么样", "它主打什么"],
     {"mode_any": ["followup"], "min_products": 1, "max_products": 1,
      "must_contain_any": ["主打", "功效", "修护", "抗初老", "维稳"]}),
    ("追问-防晒怎么用",
     ["敏感肌防晒推荐", "第一款怎么用"],
     {"mode_any": ["followup"], "min_products": 1, "max_products": 1,
      "must_contain_any": ["涂", "出门前", "硬币", "补涂", "防晒"]}),
    ("浅多轮-油痘肌二选一",
     ["推荐几款敏感肌用的防晒", "第一款和第二款哪个更适合油痘肌"],
     {"mode_any": ["compare"], "followup_scope": "in_candidates", "min_products": 2,
      "must_contain_any": ["优先选", "油痘肌"]}),
    ("浅多轮-赢家后续它多少钱",
     ["推荐几款敏感肌用的防晒", "第一款和第二款哪个更适合油痘肌", "它多少钱"],
     {"mode_any": ["followup"], "followup_scope": "in_candidates", "min_products": 1, "max_products": 1,
      "must_have_brands": ["碧柔"], "must_contain_any": ["参考价", "¥"]}),
    ("浅多轮-避开酒精重搜",
     ["推荐几款敏感肌用的防晒", "第一款和第二款哪个更适合油痘肌", "那不要含酒精的呢"],
     {"mode_any": ["followup"], "followup_scope": "out_of_candidates", "exclude_terms_any": ["酒精"],
      "min_products": 1, "must_contain_any": ["重新看", "酒精", "优先看"]}),
    ("浅多轮-候选里选敏感肌",
     ["推荐干皮用的面霜", "这几款里哪个更适合敏感肌"],
     {"mode_any": ["followup"], "followup_scope": "in_candidates", "min_products": 1, "max_products": 1,
      "must_contain_any": ["更贴合敏感肌", "敏感肌"]}),
    ("浅多轮-聚焦后便宜点",
     ["推荐干皮用的面霜", "这几款里哪个更适合敏感肌", "那便宜点呢"],
     {"mode_any": ["followup"], "followup_scope": "out_of_candidates", "followup_type": "cheaper",
      "min_products": 1, "must_contain_any": ["平价", "价格", "便宜"]}),
    ("浅多轮-换肤质重搜",
     ["推荐干皮用的粉底液", "那油皮呢"],
     {"mode_any": ["followup"], "followup_scope": "out_of_candidates", "min_products": 2,
      "must_contain_any": ["按油皮重新看", "油皮"]}),
    ("浅多轮-口语指代功效",
     ["推荐个油皮用的精华", "这东西主打什么"],
     {"mode_any": ["followup"], "followup_scope": "in_candidates", "min_products": 1, "max_products": 1,
      "must_contain_any": ["主打", "功效", "核心成分"]}),
    ("浅多轮-复合价格适合性",
     ["推荐个面霜", "第三款适合干皮吗 多少钱"],
     {"mode_any": ["followup"], "followup_scope": "in_candidates", "min_products": 1, "max_products": 1,
      "must_contain_any": ["参考价", "适用肤质", "干皮"]}),
    ("浅多轮-换一批",
     ["敏感肌防晒推荐", "还有没有别的"],
     {"mode_any": ["followup"], "followup_scope": "out_of_candidates", "min_products": 1}),
    ("浅多轮-第二款功效",
     ["油皮精华300以内", "第二款主要功效是什么"],
     {"mode_any": ["followup"], "followup_scope": "in_candidates", "min_products": 1, "max_products": 1,
      "must_contain_any": ["主打", "功效"]}),
    ("浅多轮-单品后继续问它",
     ["小棕瓶怎么样", "它主打什么"],
     {"mode_any": ["followup"], "followup_scope": "in_candidates", "min_products": 1, "max_products": 1,
      "must_contain_any": ["主打", "修护", "抗初老", "维稳"]}),
    ("浅多轮-第一款用法锚定",
     ["敏感肌防晒推荐", "第一款怎么用"],
     {"mode_any": ["followup"], "followup_scope": "in_candidates", "min_products": 1, "max_products": 1,
      "must_contain_any": ["出门前", "涂", "补涂"]}),
    ("浅多轮-避开厚重",
     ["推荐干皮用的面霜", "不要太厚重的呢"],
     {"mode_any": ["followup"], "followup_scope": "out_of_candidates", "exclude_terms_any": ["厚重"],
      "min_products": 1}),
    ("浅多轮-第一三款遮瑕",
     ["推荐干皮用的粉底液", "第一款和第三款哪个遮瑕更好"],
     {"mode_any": ["compare"], "followup_scope": "in_candidates", "min_products": 2,
      "must_contain_any": ["遮瑕", "优先选", "怎么选"]}),
    ("浅多轮-粉底便宜点",
     ["推荐干皮用的粉底液", "那便宜点呢"],
     {"mode_any": ["followup"], "followup_scope": "out_of_candidates", "followup_type": "cheaper",
      "min_products": 1}),
    ("浅多轮-面霜贵一点",
     ["推荐敏感肌面霜", "贵一点呢"],
     {"mode_any": ["followup"], "followup_scope": "out_of_candidates", "followup_type": "higher_budget",
      "min_products": 1}),
    ("浅多轮-油皮精华里选敏感",
     ["油皮精华300以内", "这几款里哪个更适合敏感肌"],
     {"mode_any": ["followup"], "followup_scope": "in_candidates", "min_products": 1,
      "must_contain_any": ["敏感肌", "优先", "更贴合"]}),
    ("浅多轮-候选外还有吗",
     ["敏感肌防晒推荐", "这几款以外还有吗"],
     {"mode_any": ["followup"], "followup_scope": "out_of_candidates", "min_products": 1}),
    ("浅多轮-连续压价两轮",
     ["干皮粉底液推荐300以内", "便宜点呢", "再便宜点呢"],
     {"mode_any": ["followup"], "followup_scope": "out_of_candidates", "min_products": 1}),
    ("浅多轮-避开香精再问敏感肌",
     ["面霜推荐", "不要香精的", "这里面哪个最适合干敏皮"],
     {"mode_any": ["followup"], "followup_scope": "in_candidates", "min_products": 1, "max_products": 1,
      "exclude_terms_any": ["香精"]}),
    ("浅多轮-三款对比后问赢家成分",
     ["推荐几款油皮精华", "第一款第二款第三款哪个更适合", "它的核心成分是什么"],
     {"mode_any": ["followup"], "followup_scope": "in_candidates", "min_products": 1, "max_products": 1}),
    ("浅多轮-推荐后问第二个多少钱",
     ["敏感肌防晒推荐", "第二款价格多少"],
     {"mode_any": ["followup"], "followup_scope": "in_candidates", "min_products": 1, "max_products": 1,
      "must_contain_any": ["¥", "约", "参考价"]}),
    ("浅多轮-这个和刚才那个哪个好",
     ["小棕瓶和小黑瓶哪个好", "那这个和紫米呢"],
     {"mode_any": ["compare", "followup"], "followup_scope": "in_candidates", "min_products": 2}),
    ("浅多轮-第三款适合油痘肌吗",
     ["油皮粉底液推荐", "第三款适合油痘肌吗"],
     {"mode_any": ["followup"], "followup_scope": "in_candidates", "min_products": 1, "max_products": 1}),
    ("浅多轮-不要闷痘的再推荐",
     ["粉底推荐", "不要闷痘的"],
     {"mode_any": ["followup"], "followup_scope": "out_of_candidates", "exclude_terms_any": ["闷痘"],
      "min_products": 1}),
    ("浅多轮-换个牌子呢",
     ["雅诗兰黛小棕瓶怎么样", "换个牌子呢"],
     {"mode_any": ["followup"], "followup_scope": "out_of_candidates", "min_products": 1}),
    ("浅多轮-这个白天能用吗",
     ["推荐个精华", "第一款白天能用吗"],
     {"mode_any": ["followup"], "followup_scope": "in_candidates", "min_products": 1, "max_products": 1}),
    ("浅多轮-这几个哪个保湿最好",
     ["干皮面霜推荐", "这几个哪个保湿效果最好"],
     {"mode_any": ["followup", "compare"], "followup_scope": "in_candidates", "min_products": 1}),
    ("浅多轮-有没有小样",
     ["推荐个面霜", "有没有小样装的"],
     {"mode_any": ["followup", "recommendation"], "min_products": 1}),
    ("浅多轮-孕妇能用吗",
     ["敏感肌防晒推荐", "第一款孕妇能用吗"],
     {"mode_any": ["followup"], "followup_scope": "in_candidates", "min_products": 1, "max_products": 1}),
    ("浅多轮-对比完再要更便宜的",
     ["DW和权力哪个好", "有没有更便宜的"],
     {"mode_any": ["followup"], "followup_scope": "out_of_candidates", "followup_type": "cheaper",
      "min_products": 1}),
    ("省钱推进-温和度对比后问用法",
     ["推荐几款油皮用的防晒", "第一款和第二款哪个更温和", "它怎么用"],
     {"mode_any": ["followup"], "followup_scope": "in_candidates", "min_products": 1, "max_products": 1,
      "must_contain_any": ["涂", "出门前", "补涂", "用法"]}),
    ("省钱推进-避开酒精后集内选清爽",
     ["推荐油皮用的防晒", "不要酒精的", "这几款里哪个最清爽"],
     {"mode_any": ["followup"], "followup_scope": "in_candidates", "exclude_terms_any": ["酒精"],
      "min_products": 1}),
    ("省钱推进-贵一点后问第二款价格",
     ["干皮面霜推荐", "贵一点", "第二款多少钱"],
     {"mode_any": ["followup"], "followup_scope": "in_candidates", "min_products": 1, "max_products": 1,
      "must_contain_any": ["¥", "约", "参考价"]}),
    ("省钱推进-对比后换肤质重搜",
     ["粉底液推荐", "第一款和第二款哪个更适合干皮", "那油皮重新选呢"],
     {"mode_any": ["followup"], "followup_scope": "out_of_candidates", "min_products": 1,
      "must_contain_any": ["油皮", "重新"]}),
    ("省钱推进-同时避开厚重香精",
     ["面霜推荐", "不要厚重也不要香精"],
     {"mode_any": ["followup"], "followup_scope": "out_of_candidates", "exclude_terms_any": ["厚重", "香精"],
      "min_products": 1}),
    ("省钱推进-对比后找平替",
     ["小棕瓶和小黑瓶哪个好", "便宜点的替代呢"],
     {"mode_any": ["followup"], "followup_scope": "out_of_candidates", "followup_type": "cheaper",
      "min_products": 1}),
    ("省钱推进-集内选不刺激",
     ["油皮精华推荐", "这几个里面哪个更不刺激"],
     {"mode_any": ["followup"], "followup_scope": "in_candidates", "min_products": 1}),
    ("省钱推进-第一三款白天对比",
     ["敏感肌面霜推荐", "第一款和第三款哪个更适合白天"],
     {"mode_any": ["compare"], "followup_scope": "in_candidates", "min_products": 2,
      "must_contain_any": ["白天", "优先选", "怎么选"]}),
    ("省钱推进-推荐后问是否含酒精",
     ["推荐个防晒", "它含酒精吗"],
     {"mode_any": ["followup"], "followup_scope": "in_candidates", "min_products": 1, "max_products": 1}),
    ("省钱推进-干皮粉底不卡粉",
     ["干皮粉底液推荐", "第一款和第二款比哪个不容易卡粉"],
     {"mode_any": ["compare"], "followup_scope": "in_candidates", "min_products": 2,
      "must_contain_any": ["干皮", "卡粉", "优先选"]}),
    ("省钱推进-上探后问主打",
     ["推荐平价精华", "贵一点的呢", "第一款主打什么"],
     {"mode_any": ["followup"], "followup_scope": "in_candidates", "min_products": 1, "max_products": 1,
      "must_contain_any": ["主打", "功效"]}),
    ("省钱推进-避开油腻后夏天选优",
     ["推荐油皮面霜", "别太油腻", "那这几个哪个适合夏天"],
     {"mode_any": ["followup"], "followup_scope": "in_candidates", "exclude_terms_any": ["油腻"],
      "min_products": 1}),
    ("省钱推进-二三款屏障对比后问价格",
     ["推荐敏感肌精华", "第二款和第三款哪个更适合屏障受损", "这个多少钱"],
     {"mode_any": ["followup"], "followup_scope": "in_candidates", "min_products": 1, "max_products": 1,
      "must_contain_any": ["¥", "约", "参考价"]}),
    ("省钱推进-避开搓泥后问第一款",
     ["防晒推荐", "不要搓泥的", "第一款适合油皮吗"],
     {"mode_any": ["followup"], "followup_scope": "in_candidates", "exclude_terms_any": ["搓泥"],
      "min_products": 1, "max_products": 1}),
    ("状态机-IN序号锚定",
     ["油皮精华300以内", "第二款多少钱"],
     {"mode_any": ["followup"], "followup_scope": "in_candidates", "followup_type": "price",
      "min_products": 1, "max_products": 1, "must_have_brands": ["资生堂"]}),
    ("状态机-OUT候选外重搜",
     ["敏感肌防晒推荐", "这几款以外还有吗"],
     {"mode_any": ["followup"], "followup_scope": "out_of_candidates", "min_products": 1}),
    ("状态机-AMBIGUOUS越界序号",
     ["敏感肌面霜推荐", "第九款怎么样"],
     {"mode_any": ["followup"], "followup_scope": "ambiguous"}),
    ("状态机-预算下探",
     ["干皮粉底液推荐300以内", "便宜点呢"],
     {"mode_any": ["followup"], "followup_scope": "out_of_candidates", "followup_type": "cheaper",
      "min_products": 1, "must_contain_any": ["平价", "价格", "便宜"]}),
    ("状态机-排除条件累积",
     ["防晒推荐", "不要酒精的"],
     {"mode_any": ["followup"], "followup_scope": "out_of_candidates", "exclude_terms_any": ["酒精"],
      "min_products": 1}),
    ("状态机-焦点继承",
     ["油皮精华300以内", "第二款主要功效是什么", "它多少钱"],
     {"mode_any": ["followup"], "followup_scope": "in_candidates", "followup_type": "price",
      "min_products": 1, "max_products": 1, "must_have_brands": ["资生堂"]}),
    ("状态机-对比集继承",
     ["DW和权力哪个好", "第一款和第二款哪个更适合干皮", "它多少钱"],
     {"mode_any": ["followup"], "followup_scope": "in_candidates", "followup_type": "price",
      "min_products": 1, "max_products": 1, "must_have_brands": ["雅诗兰黛"]}),
    ("状态机-决策赢家继承",
     ["推荐几款敏感肌用的防晒", "第一款和第二款哪个更适合油痘肌", "它多少钱"],
     {"mode_any": ["followup"], "followup_scope": "in_candidates", "followup_type": "price",
      "min_products": 1, "max_products": 1, "must_have_brands": ["碧柔"]}),
]


def check_case(label: str, res: Dict[str, Any], expect: Dict[str, Any]) -> Tuple[bool, List[str]]:
    issues: List[str] = []
    intent = res.get("intent") or {}
    mode = intent.get("answer_mode") or intent.get("mode") or (res.get("contract") or {}).get("answer_mode")
    products = res.get("products") or []
    text = res.get("text") or ""

    if not text or len(text.strip()) < 20:
        issues.append(f"文案过短({len(text)}字)")

    flags = red_flags(text)
    if flags:
        issues.append(f"命中禁词: {flags}")

    if "mode" in expect:
        if mode != expect["mode"]:
            issues.append(f"意图期望={expect['mode']} 实际={mode}")
    if "mode_any" in expect:
        if mode not in expect["mode_any"]:
            issues.append(f"意图期望∈{expect['mode_any']} 实际={mode}")

    if "min_products" in expect:
        if len(products) < expect["min_products"]:
            issues.append(f"商品数期望>={expect['min_products']} 实际={len(products)}")
    if "max_products" in expect:
        if len(products) > expect["max_products"]:
            issues.append(f"商品数期望<={expect['max_products']} 实际={len(products)}")

    if "must_contain_any" in expect:
        if not any(kw in text for kw in expect["must_contain_any"]):
            issues.append(f"文案缺少关键信息(期望包含{expect['must_contain_any']}之一)")

    if "must_have_brands" in expect:
        product_brands = {str(p.get("brand", "")) for p in products}
        missing = [b for b in expect["must_have_brands"] if b not in product_brands]
        if missing:
            issues.append(f"缺少品牌: {missing} (实际返回品牌: {product_brands})")

    if "must_have_any_brand" in expect:
        product_brands = {str(p.get("brand", "")) for p in products}
        if not any(b in product_brands for b in expect["must_have_any_brand"]):
            issues.append(f"品牌列表里至少该有{expect['must_have_any_brand']}之一, 实际={product_brands}")

    entities = intent.get("entities") if isinstance(intent, dict) else {}
    entities = entities or {}
    contract = res.get("contract") or {}
    if "followup_scope" in expect:
        actual_scope = entities.get("followup_scope") or contract.get("followup_scope")
        if actual_scope != expect["followup_scope"]:
            issues.append(f"追问范围期望={expect['followup_scope']} 实际={actual_scope}")
    if "followup_type" in expect:
        actual_type = entities.get("followup_type") or contract.get("followup_type")
        if actual_type != expect["followup_type"]:
            issues.append(f"追问类型期望={expect['followup_type']} 实际={actual_type}")
    if "exclude_terms_any" in expect:
        actual_terms = entities.get("exclude_terms") or (contract.get("followup_state") or {}).get("constraints", {}).get("exclude_terms") or []
        if not any(term in actual_terms for term in expect["exclude_terms_any"]):
            issues.append(f"排除词期望包含{expect['exclude_terms_any']}之一 实际={actual_terms}")

    return (len(issues) == 0), issues


def check_message_case(label: str, data: Dict[str, Any], expect: Dict[str, Any]) -> Tuple[bool, List[str]]:
    issues: List[str] = []
    text = data.get("response") or ""
    products = data.get("products") or []
    metadata = data.get("metadata") or {}
    contract = metadata.get("answer_contract") or {}

    if not metadata.get("v2"):
        issues.append("metadata.v2 未标记为 True")
    if not isinstance(contract, dict) or not contract:
        issues.append("metadata.answer_contract 缺失")

    mode = contract.get("answer_mode") or (data.get("intent") or {}).get("intent")
    if "mode" in expect and mode != expect["mode"]:
        issues.append(f"意图期望={expect['mode']} 实际={mode}")
    if len(products) < expect.get("min_products", 0):
        issues.append(f"商品数期望>={expect['min_products']} 实际={len(products)}")
    if "must_contain_any" in expect and not any(kw in text for kw in expect["must_contain_any"]):
        issues.append(f"文案缺少关键信息(期望包含{expect['must_contain_any']}之一)")
    return (len(issues) == 0), issues


async def main():
    print("=" * 70)
    print("  V2 零-LLM 端到端测试（BGE 向量可用，大模型文案关闭）")
    print("=" * 70)

    async with httpx.AsyncClient() as client:
        # 健康检查
        try:
            h = await client.get(f"{BASE_URL}/health", timeout=5.0)
            print(f"[health] {h.status_code} {h.text[:120]}")
            h.raise_for_status()
        except Exception as e:
            print(f"❌ 服务不可用: {e}")
            return 1

        passed = 0
        failed = 0
        total = len(MESSAGE_CASES) + len(SINGLE_CASES) + len(FOLLOWUP_CASES)
        run_id = str(int(time.time() * 1000))

        # ---- 非流式 /message ----
        print("\n--- 非流式 /message ---")
        for i, (label, query, expect) in enumerate(MESSAGE_CASES, 1):
            sid = f"test_message_{run_id}_{i}"
            try:
                data = await json_chat_message(client, query, sid)
                ok, issues = check_message_case(label, data, expect)
                status = "✅" if ok else "❌"
                text_short = (data.get("response") or "").replace("\n", " ")[:90]
                n_prod = len(data.get("products") or [])
                mode = ((data.get("metadata") or {}).get("answer_contract") or {}).get("answer_mode") or "-"
                print(f"{status} [M{i:02d}] {label}  mode={mode}  prod={n_prod}")
                print(f"     「{text_short}」")
                for issue in issues:
                    print(f"     ⚠️  {issue}")
                if ok:
                    passed += 1
                else:
                    failed += 1
            except Exception as e:
                failed += 1
                import traceback
                print(f"❌ [M{i:02d}] {label}  请求异常: {e}")
                traceback.print_exc()

        # ---- 单轮 ----
        print("\n--- 单轮查询 ---")
        for i, (label, query, expect) in enumerate(SINGLE_CASES, 1):
            sid = f"test_single_{run_id}_{i}"
            try:
                res = await sse_chat(client, query, sid)
                ok, issues = check_case(label, res, expect)
                status = "✅" if ok else "❌"
                text_short = (res.get("text") or "").replace("\n", " ")[:90]
                mode = (res.get("intent") or {}).get("answer_mode") or "-"
                n_prod = len(res.get("products") or [])
                print(f"{status} [{i:02d}] {label}  mode={mode}  prod={n_prod}")
                print(f"     「{text_short}」")
                for issue in issues:
                    print(f"     ⚠️  {issue}")
                if ok:
                    passed += 1
                else:
                    failed += 1
            except Exception as e:
                failed += 1
                import traceback
                print(f"❌ [{i:02d}] {label}  请求异常: {e}")
                traceback.print_exc()

        # ---- 多轮 ----
        print("\n--- 多轮追问（session 历史） ---")
        for i, (label, turns, expect) in enumerate(FOLLOWUP_CASES, 1):
            sid = f"test_follow_{run_id}_{i}"
            last_res: Optional[Dict[str, Any]] = None
            history: List[Dict[str, Any]] = []
            turn_ok = True
            for ti, q in enumerate(turns):
                try:
                    res = await sse_chat(client, q, sid, history=history if ti > 0 else None)
                    last_res = res
                    history.append({"role": "user", "content": q})
                    history.append({"role": "assistant", "content": res.get("text", "")})
                except Exception as e:
                    print(f"❌ [F{i:02d}] {label} T{ti+1} 请求异常: {e}")
                    failed += 1
                    turn_ok = False
                    last_res = None
                    break
            if not turn_ok or last_res is None:
                continue
            ok, issues = check_case(label, last_res, expect)
            status = "✅" if ok else "❌"
            text_short = (last_res.get("text") or "").replace("\n", " ")[:90]
            mode = (last_res.get("intent") or {}).get("answer_mode") or "-"
            n_prod = len(last_res.get("products") or [])
            print(f"{status} [F{i:02d}] {label}  mode={mode}  prod={n_prod}")
            print(f"     「{text_short}」")
            for issue in issues:
                print(f"     ⚠️  {issue}")
            if ok:
                passed += 1
            else:
                failed += 1

        print("\n" + "=" * 70)
        print(f"  结果: 通过 {passed}/{total}, 失败 {failed}/{total}")
        print("=" * 70)
        return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
