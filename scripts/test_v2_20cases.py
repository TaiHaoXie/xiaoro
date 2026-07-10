"""一期能力20场景自测 - 同步版本用urllib"""
import json, os, sys, subprocess, urllib.request, urllib.error, uuid

sys.path.insert(0, os.getcwd())
BASE = "http://127.0.0.1:8000"


def post_chat(msg, sid, image_context=None, history=None):
    payload = {"message": msg, "session_id": sid, "stream": True}
    if image_context:
        payload["image_context"] = image_context
    if history:
        payload["conversation_history"] = history
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{BASE}/api/v1/chat/stream",
        data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    content = ""
    intent = None
    error = None
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            text = resp.read().decode()
        for line in text.split("\n"):
            if line.startswith("data:"):
                try:
                    d = json.loads(line[5:].strip())
                except Exception:
                    continue
                if not isinstance(d, dict):
                    continue
                if "intent" in d and "v2" in d:
                    intent = d
                if d.get("content") is not None:
                    content += d["content"]
                if d.get("error") or (d.get("code") == "AGENT_ERROR"):
                    error = d.get("message") or d.get("error")
    except urllib.error.HTTPError as e:
        error = f"HTTP {e.code}: {e.read().decode()[:200]}"
    except Exception as e:
        error = str(e)
    return {"intent": intent, "content": content, "error": error}


def post_image_search(path):
    import uuid
    boundary = "----TestBoundary" + uuid.uuid4().hex
    files = []
    files.append(f"--{boundary}\r\n".encode())
    files.append(f'Content-Disposition: form-data; name="file"; filename="{os.path.basename(path)}"\r\n'.encode())
    files.append(b"Content-Type: image/png\r\n\r\n")
    with open(path, "rb") as f:
        files.append(f.read())
    files.append(f"\r\n--{boundary}\r\n".encode())
    files.append(b'Content-Disposition: form-data; name="min_score"\r\n\r\n0.3\r\n')
    files.append(f"--{boundary}--\r\n".encode())
    body = b"".join(files)
    req = urllib.request.Request(
        f"{BASE}/api/v1/image-search/upload",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


def build_image_context(sr):
    results = sr.get("results", [])[:3]
    top1 = results[0] if results else {}
    return {
        "results": [{k: r.get(k) for k in ("id", "brand", "name", "category", "price", "similarity", "image_url") if k in r} for r in results],
        "ocr_info": sr.get("ocr_info"),
        "analysis": sr.get("analysis"),
        "policy": "image_recommendation",
        "brand": top1.get("brand"),
        "category": top1.get("category"),
    }


CASES = [
    {"id": 1, "type": "recommend", "name": "300以内面霜", "msg": "300以内面霜"},
    {"id": 2, "type": "recommend", "name": "敏感肌面霜", "msg": "敏感肌面霜"},
    {"id": 3, "type": "recommend", "name": "200以内防晒", "msg": "200以内防晒"},
    {"id": 4, "type": "recommend", "name": "油皮精华", "msg": "油皮精华"},
    {"id": 5, "type": "recommend", "name": "眼霜推荐", "msg": "眼霜推荐"},
    {"id": 6, "type": "recommend", "name": "氨基酸洗面奶", "msg": "氨基酸洗面奶"},
    {"id": 7, "type": "judge", "name": "理肤泉B5敏感肌能用吗", "msg": "理肤泉B5敏感肌能用吗"},
    {"id": 8, "type": "judge", "name": "珀莱雅防晒油皮可以吗", "msg": "珀莱雅防晒油皮可以用吗"},
    {"id": 9, "type": "judge", "name": "小棕瓶适合什么年龄", "msg": "小棕瓶适合什么年龄"},
    {"id": 10, "type": "compare", "name": "理肤泉B5和玉泽哪个好", "msg": "理肤泉B5和玉泽哪个好"},
    {"id": 11, "type": "compare", "name": "兰蔻小白管和安热沙对比", "msg": "兰蔻小白管和安热沙对比"},
    {"id": 12, "type": "image_search", "name": "识图-珀莱雅防晒", "image": "app/static/images/products/tmall_v3_768314295559.png"},
    {"id": 13, "type": "image_search", "name": "识图-OLAY小白瓶", "image": "app/static/images/products/jd_v3_100241283549.png"},
    {"id": 14, "type": "image_alt", "name": "识图平替-珀莱雅防晒", "image": "app/static/images/products/tmall_v3_768314295559.png", "msg": "有没有平替", "expect_cat": "防晒"},
    {"id": 15, "type": "image_alt", "name": "识图推荐-珀莱雅其他款", "image": "app/static/images/products/tmall_v3_768314295559.png", "msg": "还有什么推荐", "expect_cat": "防晒"},
    {"id": 16, "type": "multimodal", "name": "图文-珀莱雅+油皮", "image": "app/static/images/products/tmall_v3_768314295559.png", "msg": "油皮能用吗", "expect_cat": "防晒"},
    {"id": 17, "type": "multimodal", "name": "图文-OLAY+200内平替", "image": "app/static/images/products/jd_v3_100241283549.png", "msg": "200以内的平替", "expect_cat": "精华"},
]

FOLLOWUPS = [
    {"id": 18, "seed": "300以内面霜", "followup": "有没有更便宜的", "name": "更便宜"},
    {"id": 19, "seed": "200以内防晒", "followup": "敏感肌能用的", "name": "敏感肌防晒"},
    {"id": 20, "seed": "油皮精华", "followup": "有没有平价替代", "name": "平价替代"},
]


def run():
    results = []
    print("=" * 70)
    print("一期能力20场景自测")
    print("=" * 70)

    for c in CASES:
        cid, name, t = c["id"], c["name"], c["type"]
        try:
            if t == "image_search":
                sr = post_image_search(c["image"])
                top1 = (sr.get("results") or [{}])[0]
                sim = top1.get("similarity", 0)
                brand = top1.get("brand", "?")
                cat = top1.get("category", "?")
                ok = bool(top1) and sim >= 70
                detail = f"top1={brand}/{cat} sim={sim}% cnt={len(sr.get('results', []))}"
            elif t in ("image_alt", "multimodal"):
                sr = post_image_search(c["image"])
                img_ctx = build_image_context(sr)
                r = post_chat(c["msg"], f"case{cid}", image_context=img_ctx)
                content, err = r["content"], r["error"]
                has_anchor = "看了你发的图片" in content
                has_rec = "综合建议" in content
                has_judgement = "能不能用" in content and "风险点" in content
                cat_ok = (not c.get("expect_cat")) or (c["expect_cat"] in content)
                ok = (not err) and has_anchor and (has_rec or has_judgement) and cat_ok and len(content) > 100
                detail = f"anchor={'✓' if has_anchor else '✗'} answer={'✓' if (has_rec or has_judgement) else '✗'} cat={'✓' if cat_ok else '✗'} err={err}"
            elif t == "compare":
                r = post_chat(c["msg"], f"case{cid}")
                content, err = r["content"], r["error"]
                ok = (not err) and len(content) > 80 and "##" in content
                intent_name = r["intent"].get("intent") if r["intent"] else None
                detail = f"intent={intent_name} err={err}"
            elif t == "judge":
                r = post_chat(c["msg"], f"case{cid}")
                content, err = r["content"], r["error"]
                intent_name = r["intent"].get("intent") if r["intent"] else None
                has_judgement = "能不能用" in content and "风险点" in content
                has_legacy_rec = "综合建议" in content and "##" in content
                ok = (not err) and intent_name in ("judgement", "recommendation") and (has_judgement or has_legacy_rec) and len(content) > 80
                detail = f"intent={intent_name} judgement={'✓' if has_judgement else '✗'} err={err}"
            else:
                r = post_chat(c["msg"], f"case{cid}")
                content, err = r["content"], r["error"]
                has_struct = "综合建议" in content and "##" in content
                ok = (not err) and has_struct and len(content) > 100
                intent_name = r["intent"].get("intent") if r["intent"] else None
                detail = f"intent={intent_name} err={err}"
            results.append({"id": cid, "name": name, "ok": ok, "detail": detail})
            mark = "✓" if ok else "✗"
            print(f"[{cid:2d}] {mark} {name:<30} {detail}")
            if not ok:
                snippet = (r.get("content", "") if 'r' in dir() else "")[:300].replace("\n", " ")
                if not snippet and t in ("image_alt","multimodal"):
                    pass
                if snippet:
                    print(f"     → {snippet[:200]}")
        except Exception as e:
            import traceback; traceback.print_exc()
            results.append({"id": cid, "name": name, "ok": False, "detail": f"EXCEPTION {e}"})
            print(f"[{cid:2d}] ✗ {name:<30} EXCEPTION {e}")

    # 追问
    for f in FOLLOWUPS:
        cid = f["id"]
        sid = f"case{cid}"
        try:
            r1 = post_chat(f["seed"], sid)
            hist = [{"role":"user","content":f["seed"]},{"role":"assistant","content":r1["content"]}]
            r2 = post_chat(f["followup"], sid, history=hist)
            content, err = r2["content"], r2["error"]
            intent_name = r2["intent"].get("intent") if r2["intent"] else None
            no_match = "暂时没找到" in content or "补充" in content
            ok = (not err) and len(content) > 80 and not no_match
            results.append({"id": cid, "name": f"追问-{f['name']}", "ok": ok, "detail": f"intent={intent_name} err={err}"})
            mark = "✓" if ok else "✗"
            print(f"[{cid:2d}] {mark} 追问-{f['name']:<24} seed='{f['seed']}' → '{f['followup']}' intent={intent_name}")
            if not ok:
                print(f"     → {content[:300]}")
        except Exception as e:
            results.append({"id": cid, "name": f"追问-{f['name']}", "ok": False, "detail": f"EXCEPTION {e}"})
            print(f"[{cid:2d}] ✗ 追问-{f['name']:<24} EXCEPTION {e}")

    passed = sum(1 for r in results if r["ok"])
    failed = [r for r in results if not r["ok"]]
    print("\n" + "=" * 70)
    print(f"RESULT: PASS {passed}/20, FAIL {len(failed)}")
    print("=" * 70)
    for r in failed:
        print(f"  ✗ [{r['id']}] {r['name']} — {r['detail']}")


if __name__ == "__main__":
    run()
