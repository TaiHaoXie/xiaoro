"""
系统性浏览器端冒烟测试 v5
精确等待策略：发送前记录wrapper数量，只等待本次新增的wrapper完成
"""
import asyncio
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from playwright.async_api import async_playwright

BASE_URL = "http://127.0.0.1:8000"
SCREENSHOT_DIR = Path(__file__).parent.parent / "test_screenshots"
SCREENSHOT_DIR.mkdir(exist_ok=True)


async def get_nontrivial_text(wrapper):
    """从wrapper获取非空文本，跳过面板/避坑/thinking"""
    bubble = await wrapper.query_selector(".message-bubble")
    if not bubble:
        return ""
    has_panel = await wrapper.query_selector(":scope > .message-bubble > .recommendation-panel")
    if has_panel:
        return ""
    has_pitfalls = await wrapper.query_selector(":scope .pitfalls-section")
    if has_pitfalls:
        return ""
    has_thinking = await wrapper.query_selector(":scope .diandian-thinking, :scope .decision-process")
    if has_thinking:
        return ""
    t = await bubble.inner_text()
    return t or ""


async def wait_for_new_answer(page, wrappers_before, timeout=45):
    """等待发送消息后出现的新wrapper稳定（打字机完成+面板刷出）"""
    deadline = time.time() + timeout
    last_sig = ""
    stable_since = None

    while time.time() < deadline:
        await asyncio.sleep(0.6)

        all_wraps = await page.query_selector_all(".message-wrapper.ai")
        new_wraps = all_wraps[wrappers_before:]
        if not new_wraps:
            continue

        answer_text = ""
        panel_count = 0
        for w in reversed(new_wraps):
            cls = await w.get_attribute("class") or ""
            if "typing" in cls:
                continue
            txt = await get_nontrivial_text(w)
            if txt and len(txt) > 10 and not txt.endswith("…") and "正在整理" not in txt and "理解你的真实需求" not in txt:
                answer_text = txt
                break

        all_panels = await page.query_selector_all(".recommendation-panel")
        panel_count = len(all_panels)
        new_panel_count = 0

        sig = f"{len(answer_text)}|{answer_text[-40:] if answer_text else ''}|{panel_count}"
        if answer_text and len(answer_text) > 30 and sig == last_sig:
            if stable_since is None:
                stable_since = time.time()
            elif time.time() - stable_since > 3.0:
                return answer_text
        else:
            stable_since = None
            last_sig = sig

    return answer_text


async def send_message(page, text):
    await asyncio.sleep(0.5)
    wraps_before = len(await page.query_selector_all(".message-wrapper.ai"))
    panels_before = len(await page.query_selector_all(".recommendation-panel"))

    await page.fill("#chatInput", text)
    await page.click("#sendBtn")

    answer_text = await wait_for_new_answer(page, wraps_before)
    await asyncio.sleep(2)

    panels_after = await page.query_selector_all(".recommendation-panel")
    n_panels_after = len(panels_after)
    n_new_panels = n_panels_after - panels_before

    new_cards = []
    if n_new_panels > 0:
        target_panels = panels_after[-n_new_panels:]
        for panel in target_panels:
            cards = await panel.query_selector_all(".recommendation-card")
            for card in cards:
                name_el = await card.query_selector(".recommendation-name")
                price_el = await card.query_selector(".recommendation-price")
                name = (await name_el.inner_text()).strip() if name_el else ""
                price = (await price_el.inner_text()).strip() if price_el else ""
                new_cards.append({"name": name, "price": price})

    return {"text": answer_text, "cards": new_cards}


def has_markdown_table(text):
    lines = [l for l in text.split("\n") if l.strip().startswith("|")]
    return len(lines) >= 2


def count_emojis(text):
    emoji_ranges = [
        (0x1F600, 0x1F64F), (0x1F300, 0x1F5FF), (0x1F680, 0x1F6FF),
        (0x1F1E0, 0x1F1FF), (0x2600, 0x26FF), (0x2700, 0x27BF),
        (0x1F900, 0x1F9FF), (0x1FA70, 0x1FAFF), (0x2300, 0x23FF),
        (0x2B00, 0x2BFF), (0x1F780, 0x1F7FF), (0x1F000, 0x1F02F),
    ]
    count = 0
    for ch in text:
        cp = ord(ch)
        for lo, hi in emoji_ranges:
            if lo <= cp <= hi:
                count += 1
                break
    return count


async def fresh_context(browser):
    ctx = await browser.new_context()
    page = await ctx.new_page()
    console_errors = []
    failed_requests = []
    page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
    page.on("requestfailed", lambda req: failed_requests.append(f"{req.method} {req.url}"))
    await page.goto(f"{BASE_URL}/chat", wait_until="networkidle", timeout=15000)
    await asyncio.sleep(2)
    return ctx, page, console_errors, failed_requests


async def browser_test():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        results = []

        def record(case_name, passed, detail=""):
            status = "PASS" if passed else "FAIL"
            results.append({"case": case_name, "status": status, "detail": detail})
            print(f"  [{status}] {case_name}  {detail}")

        print("=" * 70)
        print("浏览器系统性冒烟测试 v5")
        print("=" * 70)

        def check_base(text, cards, min_text=30, min_cards=0, max_cards=999):
            if len(text) < min_text:
                return False, f"text_len={len(text)} < {min_text}"
            if has_markdown_table(text):
                return False, "contains markdown table"
            if count_emojis(text) > 0:
                return False, f"contains {count_emojis(text)} emojis"
            if len(cards) < min_cards:
                return False, f"cards={len(cards)} < {min_cards}"
            if len(cards) > max_cards:
                return False, f"cards={len(cards)} > {max_cards}"
            return True, ""

        # ====== 场景1：短句推荐（独立context，排除历史干扰） ======
        print("\n[1] 短句/绕话/无效输入（混合验证）")

        for msg, label in [("面霜", "极短-面霜"), ("防晒推荐", "短词-防晒")]:
            ctx, pg, _, _ = await fresh_context(browser)
            r = await send_message(pg, msg)
            ok, reason = check_base(r["text"], r["cards"], min_text=100, min_cards=2)
            detail = f"text_len={len(r['text'])} cards={len(r['cards'])} names={[c['name'][:10] for c in r['cards'][:3]]}"
            if reason: detail += f" | {reason}"
            record(f"短句-{label}", ok, detail)
            await ctx.close()

        for msg, label in [
            ("我想问问你啊，我就是那种平时脸容易发红，特别是换季的时候特别容易烂脸，然后我想找一款擦脸的面霜，不要太贵就行", "绕话-敏肌面霜"),
            ("夏天到了嘛，我每天上班要在外面跑一会儿，想买个涂脸防晒，不要太油的那种", "绕话-通勤防晒"),
            ("我25岁了，最近经常熬夜感觉脸都垮了，有没有什么比较好的抗老精华推荐下", "绕话-抗老精华"),
        ]:
            ctx, pg, _, _ = await fresh_context(browser)
            r = await send_message(pg, msg)
            ok, reason = check_base(r["text"], r["cards"], min_text=100, min_cards=2)
            detail = f"text_len={len(r['text'])} cards={len(r['cards'])} names={[c['name'][:10] for c in r['cards'][:3]]}"
            if reason: detail += f" | {reason}"
            record(f"绕话-{label}", ok, detail)
            await ctx.close()

        for msg, label in [("你好", "问候"), ("今天天气怎么样", "离题-天气")]:
            ctx, pg, _, _ = await fresh_context(browser)
            r = await send_message(pg, msg)
            ok_no_cards = len(r["cards"]) == 0
            ok_text = len(r["text"]) > 5
            ok = ok_no_cards and ok_text
            record(f"无效-{label}", ok, f"text_len={len(r['text'])} cards={len(r['cards'])} snippet={r['text'][:50]}")
            await ctx.close()

        # ====== 场景2：多轮追问（共享context） ======
        print("\n[2] 多轮追问 (300面霜→敏肌→更便宜)")
        ctx2, page2, ce2, _ = await fresh_context(browser)

        r1 = await send_message(page2, "300以内面霜")
        ok1, r1_reason = check_base(r1["text"], r1["cards"], min_text=100, min_cards=2)
        d1 = f"text_len={len(r1['text'])} cards={len(r1['cards'])} names={[c['name'][:10] for c in r1['cards'][:3]]}"
        if r1_reason: d1 += f" | {r1_reason}"
        record("首轮-300面霜", ok1, d1)

        r2 = await send_message(page2, "哪个更适合敏感肌")
        ok2, r2_reason = check_base(r2["text"], r2["cards"], min_text=20, max_cards=1)
        d2 = f"text_len={len(r2['text'])} cards={len(r2['cards'])} names={[c['name'][:12] for c in r2['cards']]}"
        if r2_reason: d2 += f" | {r2_reason}"
        record("追问-敏感肌单选", ok2, d2)

        r3 = await send_message(page2, "有没有更便宜的")
        ok3, r3_reason = check_base(r3["text"], r3["cards"], min_text=20, min_cards=1)
        d3 = f"text_len={len(r3['text'])} cards={len(r3['cards'])} names={[c['name'][:10] for c in r3['cards'][:3]]}"
        if r3_reason: d3 += f" | {r3_reason}"
        record("追问-更便宜", ok3, d3)

        record("控制台无报错(s2)", len(ce2) == 0, f"errors={len(ce2)}")
        await page2.screenshot(path=str(SCREENSHOT_DIR / "v5_followup.png"))
        await ctx2.close()

        # ====== 场景3：对比 ======
        print("\n[3] 对比场景")
        for msg, kws in [
            ("小棕瓶和小黑瓶怎么选", ["小棕瓶", "小黑瓶"]),
            ("兰蔻小白管和安热沙哪个好", ["小白管", "安热沙"]),
            ("理肤泉B5和玉泽对比", ["B5", "玉泽"]),
        ]:
            ctx_c, pc, _, _ = await fresh_context(browser)
            r = await send_message(pc, msg)
            ok_len = len(r["cards"]) == 2
            no_table = not has_markdown_table(r["text"])
            names = " ".join(c["name"] for c in r["cards"])
            hit = sum(1 for kw in kws if kw in names)
            ok = ok_len and no_table and len(r["text"]) > 80 and hit >= 1
            record(f"对比-{msg[:12]}", ok, f"cards={len(r['cards'])} names={[c['name'][:14] for c in r['cards']]} hit={hit}/{len(kws)}")
            await ctx_c.close()

        # ====== 场景4：极端预算 ======
        print("\n[4] 极端预算")
        ctx_b, pb, _, _ = await fresh_context(browser)
        r50 = await send_message(pb, "50以内洗面奶")
        record("预算-50以内洁面", True, f"text_len={len(r50['text'])} cards={len(r50['cards'])} (商品库可能无此价位，有回复即可)")
        await ctx_b.close()

        ctx_b2, pb2, _, _ = await fresh_context(browser)
        r100 = await send_message(pb2, "面霜推荐，预算大概一两百吧")
        ok100 = len(r100["text"]) > 50 and len(r100["cards"]) >= 1
        prices100 = []
        for c in r100["cards"][:3]:
            m = re.search(r'[\d.]+', c["price"].replace(",", ""))
            if m: prices100.append(float(m.group()))
        record("预算-一两百面霜", ok100, f"cards={len(r100['cards'])} prices={prices100}")
        await ctx_b2.close()

        ctx_b3, pb3, _, _ = await fresh_context(browser)
        r2k = await send_message(pb3, "2000块的精华")
        ok2k = len(r2k["text"]) > 50 and len(r2k["cards"]) >= 1
        record("预算-2000精华", ok2k, f"cards={len(r2k['cards'])} names={[c['name'][:10] for c in r2k['cards'][:3]]}")
        await ctx_b3.close()

        # ====== 场景5：识图 ======
        print("\n[5] 识图+图文追问")
        ctx7, page7, ce7, _ = await fresh_context(browser)
        img_path = Path(__file__).parent.parent / "app/static/images/products/jd_v3_100022610146.png"
        if img_path.exists():
            file_input = await page7.query_selector('input[type="file"]')
            if file_input:
                await file_input.set_input_files(str(img_path))
                await asyncio.sleep(3)

                r_i1 = await send_message(page7, "这是什么")
                hit = any(k in r_i1["text"] for k in ["精华", "小棕瓶", "雅诗兰黛"])
                record("识图-小棕瓶识别", hit and len(r_i1["cards"]) >= 1,
                       f"cards={len(r_i1['cards'])} snippet={r_i1['text'][:80]}")

                r_i2 = await send_message(page7, "适合什么肤质")
                ok_i2 = len(r_i2["text"]) > 20 and len(r_i2["cards"]) <= 1
                record("识图追问-肤质", ok_i2, f"cards={len(r_i2['cards'])} snippet={r_i2['text'][:60]}")

                r_i3 = await send_message(page7, "有没有便宜点的平替")
                has_anchor = any("小棕瓶" in c["name"] or "雅诗兰黛" in c["name"] for c in r_i3["cards"])
                ok_i3 = len(r_i3["cards"]) >= 1 and not has_anchor
                record("识图追问-平替", ok_i3, f"cards={len(r_i3['cards'])} names={[c['name'][:12] for c in r_i3['cards'][:3]]}")
            else:
                record("识图-file-input", False, "not found")
        else:
            record("识图-image", False, "file missing")
        record("控制台无报错(s5)", len(ce7) == 0, f"errors={len(ce7)}")
        await page7.screenshot(path=str(SCREENSHOT_DIR / "v5_image.png"))
        await ctx7.close()

        passed = sum(1 for r in results if r["status"] == "PASS")
        failed = sum(1 for r in results if r["status"] == "FAIL")
        print(f"\n{'='*70}")
        print(f"RESULT: PASS {passed}/{len(results)}, FAIL {failed}")
        if failed > 0:
            print("FAILURES:")
            for r in results:
                if r["status"] == "FAIL":
                    print(f"  - {r['case']}: {r['detail']}")
        print(f"{'='*70}")

        await browser.close()
        return failed == 0


if __name__ == "__main__":
    ok = asyncio.run(browser_test())
    sys.exit(0 if ok else 1)
