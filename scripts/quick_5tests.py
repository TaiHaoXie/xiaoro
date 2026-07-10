import requests
import json
import time
from datetime import datetime

URL = "http://localhost:8000/api/v1/chat/stream"

def parse_sse(text):
    events = []
    current_event = None
    current_data = []
    for line in text.split("\n"):
        if line.startswith("event: "):
            if current_event is not None:
                events.append((current_event, "\n".join(current_data)))
            current_event = line[7:].strip()
            current_data = []
        elif line.startswith("data: "):
            current_data.append(line[6:])
        elif line.strip() == "":
            if current_event is not None:
                events.append((current_event, "\n".join(current_data)))
                current_event = None
                current_data = []
    if current_event is not None:
        events.append((current_event, "\n".join(current_data)))
    return events

def chat(session_id, message, history):
    payload = {
        "session_id": session_id,
        "message": message,
        "conversation_history": history,
    }
    resp = requests.post(URL, json=payload, stream=True, timeout=30)
    text = ""
    for chunk in resp.iter_content(chunk_size=None, decode_unicode=True):
        if chunk:
            text += chunk
    events = parse_sse(text)
    full_text = ""
    intent_info = None
    products = []
    mode = None
    for evt_type, evt_data in events:
        try:
            d = json.loads(evt_data) if evt_data.strip() else {}
        except:
            continue
        if evt_type == "intent":
            intent_info = d
            mode = d.get("intent") or d.get("answer_mode")
        elif evt_type == "products":
            products = d.get("products", [])
        elif evt_type == "answer_contract":
            mode = d.get("answer_mode") or mode
        elif evt_type == "message":
            full_text += d.get("content", "")
    return {"text": full_text.strip(), "mode": mode, "products": products, "intent": intent_info}

def run_case(sid, turns):
    print(f"\n{'='*70}")
    print(f"SESSION: {sid}")
    print(f"{'='*70}")
    history = []
    for i, msg in enumerate(turns):
        print(f"\n>>> 用户: {msg}")
        r = chat(sid, msg, history)
        print(f"[mode={r['mode']}, products={len(r['products'])}]")
        print(f"<<< AI:\n{r['text']}")
        history.append({"role": "user", "content": msg})
        history.append({"role": "assistant", "content": r["text"]})
        time.sleep(0.3)

def main():
    ts = datetime.now().strftime("%H%M%S")
    
    cases = [
        # Case 1: 首轮直接对比 + 追问价格
        (f"q1_cmp_{ts}", [
            "DW和权力哪个遮瑕好",
            "第一款多少钱"
        ]),
        # Case 2: 推荐后口语化功效追问
        (f"q2_eff_{ts}", [
            "推荐个油皮用的精华",
            "这玩意主要功效是什么"
        ]),
        # Case 3: 推荐后序号选内对比+肤质追问
        (f"q3_pickcmp_{ts}", [
            "推荐几款敏感肌用的防晒",
            "第一款和第二款哪个更适合油痘肌"
        ]),
        # Case 4: 复合追问（适宜性+价格一起问）
        (f"q4_multi_{ts}", [
            "推荐个面霜",
            "第三款适合干皮吗 多少钱"
        ]),
        # Case 5: 换条件追问
        (f"q5_switch_{ts}", [
            "推荐干皮用的粉底液",
            "那油皮呢"
        ]),
    ]
    
    for sid, turns in cases:
        run_case(sid, turns)

if __name__ == "__main__":
    main()
