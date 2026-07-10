#!/usr/bin/env python3
"""
V2: 7字段规则抽取器

核心改进：
- 按段落（OCR块/换行块）处理，而非短句，避免信息颗粒过碎
- 段落级去重（simhash式首句去重）
- 强化广告过滤：跨品牌/跨品类推荐内容识别
- QA识别：识别Q: ... A: ... 块和问句+回答的上下文
- 每个字段返回干净、独立的段落列表
"""
import re
from typing import Dict, List

# ========== 跨品类/跨品牌噪声词（出现即丢弃段落） ==========
CROSS_CATEGORY_NOISE = [
    "vivo","OPPO","华为","小米","iPhone","苹果手机","三星手机","手机数码",
    "冰箱","洗衣机","空调","电视","电脑","笔记本","显示器","键盘","耳机",
    "零食","食品","饮料","牛奶","奶粉","尿不湿","纸尿裤","纸巾","卫生纸",
    "服饰","男装","女装","鞋靴","箱包","内衣","童装","珠宝","黄金首饰",
    "图书","文具","玩具","乐器","运动户外","自行车","汽车用品",
    "厨具","锅具","餐具","收纳","清洁用品","洗衣液","牙膏","洗发水",
    "保健品","维生素","钙片","鱼油","蛋白粉","感冒灵","退烧药",
]

# 活动/营销噪声（段落命中2个以上就丢弃）
PROMO_KEYWORDS = [
    "赠品","礼赠","买赠","买一送一","赠完即止","赠品以",
    "优惠券","满减","满￥","满元","PLUS券","品类券","店铺券","叠加券",
    "秒杀","限时抢购","限时特惠","活动价","到手价","日常价","专柜价","直播间",
    "京东物流","顺丰包邮","包邮","发货时间","现货速发","发货地","京东快递",
    "客服","售后保障","7天无理由","保价","假一罚十","正品保障","假一赔十",
    "加入购物车","立即抢购","立即购买","点击下单","抢购中","即刻购买","马上购买","一键购买","立即抢购",
    "收藏关注","入会领券","会员专享","会员价","积分兑换",
    "电子发票","开票","扫码","二维码","长按识别","小程序",
    "下拉查看","点击查看","详情查看","详见包装","查看详情","详情点击","点击了解",
    "明星同款","代言人","广告",
    "预售","定金","尾款","开门红","618","双11","双12","年货节","购物节","百亿补贴",
    "以详情页为准","图片仅供参考",
    "货号","商品编号","备案号","批准文号","生产许可证",
]

# QA相关
QA_Q_PATTERNS = [
    r"Q[:：]\s*(.{4,100}?[?？])",
    r"问[:：]\s*(.{4,100}?[?？])",
    r"(.{4,80}(?:可以吗|能不能|可不可以|适合吗|敏感肌能用吗|孕妇能用吗|油皮能用吗|干皮能用吗|早晚都能用吗|要洗吗|需要洗吗|会闷痘吗|搓泥吗|需要搭配|要叠|用在哪|怎么用|如何使用|有没有效果|好用吗|怎么样))",
]
QA_A_PATTERNS = [
    r"A[:：]\s*([\s\S]{10,400}?)(?=\nQ[:：]|\n问[:：]|\n[A-Z\u4e00-\u9fa5]{2,8}[:：]|$)",
    r"答[:：]\s*([\s\S]{10,400}?)(?=\nQ[:：]|\n问[:：]|$)",
]

# 机制/原理
MECHANISM_KEYWORDS = [
    "原理","作用机理","为什么能","因为","从而","进而","促进","抑制","激活","调控",
    "屏障","神经酰胺","角质层","皮脂膜","细胞间质","紧密连接","角化包膜",
    "渗透","深入肌底","肌底","表皮层","真皮层",
    "锁水","补水原理","保湿原理","修护原理","形成保护膜","在皮肤表面",
    "PBS","PPAR","仿生","仿生脂","磷脂","胆固醇","游离脂肪酸",
    "透明质酸","玻尿酸","分子钉","角鲨烷","烟酰胺","视黄醇","A醇",
    "抗氧化","抗糖化","自由基","黑色素","酪氨酸酶","胶原蛋白","弹性蛋白",
    "缓释","包裹技术","渗透技术","水油平衡",
    "修护皮肤屏障","屏障自修护","屏障修护","强韧屏障","巩固屏障",
    "减少水分流失","防止水分流失","锁住水分",
]

# 用法/步骤
USAGE_KEYWORDS = [
    "使用方法","用法","使用步骤","如何使用","怎么使用",
    "早晚","晨间","晚间","白天","晚上","睡前","晨起",
    "洁面后","爽肤水后","化妆水后","精华后","乳液后","面霜前","防晒前","护肤最后一步",
    "取适量","按压","泵","均匀涂抹","轻轻按摩","由内向外","由下向上","拍打","点涂",
    "厚敷","薄涂","乳化","推开","揉出泡沫","泡沫","冲洗","洗掉",
    "STEP","Step","①","②","③","④","⑤","第一步","第二步","第三步",
    "硬币大小","珍珠大小","黄豆大小","一元硬币",
    "每周","每天","每日","一天","妆前","上妆前",
]
USAGE_QTY_PATTERNS = [
    r"(?:按压|取|挤|压)(\d+\s*[~-]\s*\d+|\d+)\s*泵",
    r"(\d+\s*[~-]\s*\d+|\d+)\s*泵(?:用量|的量)?",
    r"(?:约|取|取量为稍大于|大概)(1元硬币|一元硬币|珍珠|黄豆|一泵|两泵|三泵)",
]

# 注意事项/安全
SAFETY_KEYWORDS = [
    "注意事项","温馨提示","警示","禁忌","慎用","不适合",
    "过敏","敏感测试","皮试","不适请停用","出现红肿","出现刺痛",
    "立即停用","停止使用","如有不适","若感不适",
    "避开眼周","避免接触眼睛","切勿入口","放在儿童","儿童不易触及",
    "孕妇","哺乳期","孕期","妊娠期",
    "破损肌肤","伤口","炎症部位",
    "保质期","储存方法","避光","阴凉处","阴凉干燥",
    "仅限外用","外用产品",
    "使用前请","初次使用",
    "不慎入眼","请即用清水冲洗",
    "伤、肿疮、湿疹","异常症状",
]

# 质地/肤感
TEXTURE_KEYWORDS = [
    "质地","肤感","触感","手感","体感",
    "清爽","油腻","厚重","轻薄","水润","水感","清透","透气",
    "哑光","滋润感","不拔干","不紧绷",
    "丝滑","绵密","奶油","乳霜质地","凝露","啫喱","精华水质地",
    "泡沫丰富","泡沫绵密","泡沫细腻",
    "延展性","好推开","易推开","容易推开",
    "吸收快","秒吸收","快速吸收","上脸吸收","不粘腻","不黏腻",
    "黏腻","粘腻","搓泥","假滑","拔干","紧绷感",
    "水润感","水嫩","丝绒","雾面","奶油肌","光泽感",
    "牛奶水","蛋清质地","酸奶质地","土豆泥质地","慕斯质地",
    "流动性","稀薄","浓稠","奶昔质地","果冻质地","爆水",
]

# 品牌宣称/实验数据
CLAIM_KEYWORDS = [
    "专利","发明专利","专利号",
    "临床验证","临床实验","实验证明","实验证实","测试证明","测试显示","数据显示",
    "研究表明","皮肤科测试","经皮肤科",
    "NO\\.","No\\.1","第一名","销量冠军","TOP\\d+",
    "第\\d+代","全新升级","升级配方","新一代",
    "荣获","获得.*奖","大奖",
    "专利技术","独创","首创","独家技术","黑科技",
    "医研共创","联合研制","皮肤科医生推荐",
    "王牌单品","经典款","明星产品","口碑单品",
    "持续\\d+小时","\\d+天改善","\\d+周后",
    "热销\\d+","爆销\\d+","累计销量",
]
CLAIM_PERCENT_PATTERN = r"\d+(\.\d+)?\s*[%％]"

# 用户评价
REVIEW_KEYWORDS = [
    "回购","回购率","会回购","已回购","无限回购","再次购买",
    "搓泥","闷痘","闷闭口","起屑","卡粉","斑驳","浮粉","假白","氧化快","拔干",
    "油皮亲妈","干皮亲妈","油皮爱","干皮慎","混油友好","混干友好","敏感肌友好",
    "肤感好","肤感棒","体验感","用下来","用了一瓶",
    "好评","差评","中评","好评率","超\d+%买家","赞不绝口",
    "推荐购买","值得买","种草","拔草","踩雷","惊艳","绝绝子","救星","福音",
    "味道好闻","味道不好闻","淡香","清香",
    "一生推","强烈推荐","入股不亏","闭眼冲",
]
# 京东匿名评价模式：一***l / j***v / Enxin0716 / b***w / 辛丑年庚寅月 等用户名 + 评价正文
JD_ANON_REVIEW_PATTERN = r"(?:[a-zA-Z\u4e00-\u9fa5]{1,3}\*{2,}[a-zA-Z\u4e00-\u9fa50-9]{0,3}|[\u4e00-\u9fa5]{4,8}年[\u4e00-\u9fa5]{1,3}月)"


def split_paragraphs(text: str) -> List[str]:
    """按双换行/明显分隔拆段落"""
    # 统一换行
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # 在京东匿名用户名前插入分隔
    text = re.sub(r"\n?(" + JD_ANON_REVIEW_PATTERN[4:-1] + r")\s+", r"\n\1 ", text)
    # 在"全部问答/全部评价/查看更多N个回答/买家评价"等关键词前加分隔
    text = re.sub(r"(买家评价\([^)]+\)|全部评价|全部问答|查看更多\d+个回答)", r"\n\1\n", text)
    # 先按===标记切开
    parts = re.split(r"===(?:HTML正文|详情图OCR|历史详情OCR|manual补充OCR)===", text)
    paragraphs = []
    for part in parts:
        # 按空行分段
        blocks = re.split(r"\n{2,}", part)
        for b in blocks:
            b = b.strip()
            if not b:
                continue
            # 段内按单换行拆分（OCR常无空行，按句末标点切）
            sub_blocks = re.split(r"(?<=[。！？!?])\s*\n", b)
            for sb in sub_blocks:
                sb = re.sub(r"\n", " ", sb)  # OCR单换行转为空格
                sb = sb.strip()
                # 长度控制
                if len(sb) < 8:
                    continue
                if len(sb) > 600:
                    # 超长段按句号切
                    ss = re.split(r"(?<=[。！？!?])\s*", sb)
                    cur = ""
                    for s in ss:
                        if len(cur) + len(s) < 300:
                            cur += s
                        else:
                            if cur.strip():
                                paragraphs.append(cur.strip())
                            cur = s
                    if cur.strip():
                        paragraphs.append(cur.strip())
                else:
                    paragraphs.append(sb)
    return paragraphs


def is_cross_category_noise(p: str) -> bool:
    """是否为跨品类广告（手机/家电/食品等）"""
    hits = sum(1 for kw in CROSS_CATEGORY_NOISE if kw in p)
    return hits >= 1 and ("护肤" not in p and "化妆" not in p and "美容" not in p and "美肤" not in p)


def is_promo_noise(p: str) -> bool:
    """是否为纯营销/活动噪声"""
    hits = sum(1 for kw in PROMO_KEYWORDS if kw in p)
    if hits >= 3:
        return True
    # 短段落且全是活动词
    if hits >= 2 and len(p) < 40:
        return True
    return False


def is_junk(p: str) -> bool:
    if is_cross_category_noise(p):
        return True
    if is_promo_noise(p):
        return True
    # 京东/天猫店铺导航头
    if ("网站无障碍" in p and ("美妆护肤" in p or "面部护肤" in p or "进店逛逛" in p or "联系客服" in p) and len(p) < 300):
        return True
    if ("tb591549555" in p or "jd_lu9v32f5y" in p) and len(p) < 400:
        return True
    # "为你推荐" 之后都是推荐商品
    if p.startswith("为你推荐") or p.startswith("搭配购买") or p.startswith("似乎出了点问题"):
        return True
    # 纯数字/符号/空格
    if not re.search(r"[\u4e00-\u9fa5A-Za-z]{4,}", p):
        return True
    # 大量重复字符
    if re.search(r"(.)\1{8,}", p):
        return True
    # URL/电话
    if re.search(r"https?://|1\d{10}|400[-\s]?\d{3}[-\s]?\d{4}", p):
        return True
    # 导航条/面包屑
    if re.match(r"^(首页|全部|分类|登录|注册|购物车|我的京东|手机京东)", p) and len(p) < 30:
        return True
    # OCR长串数字编号（SKU/货号/备案号串）
    if re.search(r"\d{6,}", p) and len(re.findall(r"[\u4e00-\u9fa5]", p)) < 8:
        return True
    # 汉字占比过低（OCR乱码/混排噪声，如"蛇燥性敏感航"）
    cn_chars = len(re.findall(r"[\u4e00-\u9fa5]", p))
    if len(p) > 20 and cn_chars / max(len(p), 1) < 0.4:
        return True
    # 段落内连续2个以上的OCR识别错词典型模式
    bad_ocr_patterns = ["燥性敏感", "蛇燥", "潤浸保湿", "潤", "蛇增性", "燥性敬感", "乾燥性", "MoeeFrl", "INTENSIVE MOISTURECARE", "MoistureFacial"]
    bad_hits = sum(1 for pat in bad_ocr_patterns if pat in p)
    if bad_hits >= 2:
        return True
    # 商品详情页产品矩阵/系列导航（同时出现≥4个产品系列名的块状图OCR）
    series_words = ["洗颜料", "洁颜泡沫", "化妆水", "水润乳液", "滋润乳霜", "乳液", "乳霜"]
    series_hits = sum(1 for w in series_words if w in p)
    if series_hits >= 4 and ("即刻购买" in p or "立即购买" in p or "购买" in p):
        return True
    return False


def dedup(items: List[str], key_len: int = 50) -> List[str]:
    """归一化去重"""
    seen = set()
    out = []
    for s in items:
        s = s.strip()
        if not s:
            continue
        key = re.sub(r"\s+", "", s)[:key_len]
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


def keyword_match(paragraphs: List[str], keywords: List[str], extra_regex: List[str] = None, min_len: int = 10) -> List[str]:
    out = []
    for p in paragraphs:
        if is_junk(p):
            continue
        if len(p) < min_len:
            continue
        hit = False
        for kw in keywords:
            if kw in p:
                hit = True
                break
        if not hit and extra_regex:
            for pat in extra_regex:
                if re.search(pat, p):
                    hit = True
                    break
        if hit:
            out.append(p)
    return dedup(out)


def extract_qa(text: str) -> List[str]:
    items = []
    # 1) 块级Q:A:
    for m in re.finditer(r"(?:Q[:：]|问[:：])\s*(.{4,120}?[?？])\s*(?:A[:：]|答[:：])\s*([\s\S]{10,300}?)(?=\s*(?:Q[:：]|问[:：])\s|$)", text):
        q = m.group(1).strip()
        a = re.sub(r"\s+", " ", m.group(2)).strip()
        a = a[:250]
        if is_junk(q) or is_junk(a):
            continue
        # 去掉A中跨行的另一个Q
        a = re.split(r"(?:Q[:：]|问[:：])", a)[0].strip()
        items.append(f"Q: {q} A: {a}")
    paragraphs = split_paragraphs(text)
    # 2) 段落级：包含"？"或问句关键字且看起来像问答
    for p in paragraphs:
        if is_junk(p):
            continue
        # 包含问句且长度适中
        if re.search(r"(?:可以吗|能不能|可不可以|适合吗|能用吗|会闷痘吗|搓泥吗|要洗吗|怎么用|如何用|好用吗|怎么样|真的假的|有没有|有没有必要|要不要|早晚都)", p):
            # 过滤纯问无答或过短
            if len(p) >= 20 and len(p) <= 400:
                items.append(p)
    # 3) 明确Q&A提示词段落
    for p in paragraphs:
        if is_junk(p):
            continue
        if any(kw in p for kw in ["常见问题", "Q&A", "FAQ", "你问我答", "大家都在问", "问大家"]):
            if 10 <= len(p) <= 500:
                items.append(p)
    return dedup(items)[:20]


def extract_usage_qty(text: str) -> List[str]:
    qtys = []
    for pat in USAGE_QTY_PATTERNS:
        for m in re.finditer(pat, text):
            qtys.append(m.group(0).strip())
    return dedup(qtys)


def extract_seven_fields(text: str) -> Dict[str, List[str]]:
    if not text:
        return {k: [] for k in ["qa_facts","mechanism_notes","usage_steps","safety_notes","texture_notes","claim_notes","user_review_notes"]}
    paragraphs = split_paragraphs(text)
    qa = extract_qa(text)
    mechanism = keyword_match(paragraphs, MECHANISM_KEYWORDS)
    usage = keyword_match(paragraphs, USAGE_KEYWORDS)
    # 追加泵数/用量
    for q in extract_usage_qty(text):
        if not any(q in u for u in usage):
            usage.append(f"建议用量：{q}")
    # 用法段清洗：过滤超长拼接段、明显营销段
    usage_clean = []
    for u in usage:
        # 超过300字的长段通常是OCR拼接，截到第一个句号/分号
        if len(u) > 300:
            cut = re.search(r"[。；;]", u)
            if cut and cut.start() > 30:
                u = u[:cut.start() + 1]
            else:
                u = u[:200]
        # 段落含"即刻购买/立即购买/加入购物车"等营销词就丢弃
        if any(kw in u for kw in ["即刻购买","立即购买","加入购物车","立即抢购","马上购买","一键购买","点击购买","进店逛逛"]):
            continue
        # 纯评价统计/好评率数字块（"超XX%买家赞不绝口"这种），不是用法
        if re.search(r"超\d+%买家|赞不绝口|好评率|评价数|评论数|\d{4,}\s+(?:用后|上脸|清洁|保湿|控油|不油)", u):
            continue
        # 实验数据/测试报告段，不是用法
        if any(kw in u for kw in ["测试结果出自","第三方机构","受试者","试验报告","使用效果因人而异"]):
            continue
        # 皱纹原理图/肌肤结构图，不是用法
        if any(kw in u for kw in ["角质层","表皮","真皮","透明质酸低下","皱纹产生","视黄醇的效果"]) and "取" not in u and "涂" not in u and "按摩" not in u:
            continue
        # 必须含至少一个真正的"用法动作"关键词才算有用法信息
        if not any(kw in u for kw in ["取","涂","抹","按压","按摩","推开","拍打","厚敷","薄涂","冲洗","揉搓","均匀","上脸","卸妆","擦拭","洁面","爽肤水","化妆水","乳液","面霜","防晒","早晚","白天","晚上","妆前","睡前","晨起","Step","STEP","①","②","③","第一步","第二步","第三步"]):
            continue
        usage_clean.append(u.strip())
    usage = dedup(usage_clean)[:8]
    safety = keyword_match(paragraphs, SAFETY_KEYWORDS)
    texture = keyword_match(paragraphs, TEXTURE_KEYWORDS)
    claim = keyword_match(paragraphs, CLAIM_KEYWORDS, extra_regex=[CLAIM_PERCENT_PATTERN])
    review = keyword_match(paragraphs, REVIEW_KEYWORDS)

    # 后处理：
    # - safety里过滤掉仅作为评价提到"不会过敏"的夸奖段（保留真正注意事项）
    safety_clean = []
    for s in safety:
        # 这些是真·注意事项
        if any(kw in s for kw in ["注意事项","请停止使用","慎用","避开眼周","孕妇","哺乳期","破损","保质期","储存","不慎入眼","请勿使用","不适应肌肤时","异常时"]):
            safety_clean.append(s)
    safety = safety_clean if safety_clean else safety

    # - claim过滤掉和mechanism重复的技术性描述（简单按包含"专利/实验/数据/第X代/榜单"的核心宣称保留）
    claim = [c for c in claim if any(kw in c for kw in ["专利","临床","实验","测试","研究","NO.","第\d+代","升级","荣获","科技","医研","奖","TOP","王牌","明星产品","经典","持续\d+小时","%","％"])]
    claim = dedup(claim)[:15]

    # - review只保留确实是"用户口吻"的
    review_clean = []
    for r in review:
        if re.match(JD_ANON_REVIEW_PATTERN, r):
            review_clean.append(r)
            continue
        if any(kw in r for kw in ["回购","我","大家","用了","好用","不好用","搓泥","闷痘","卡粉","油皮","干皮","敏感肌","好评","差评","推荐","踩雷","种草","拔草","惊艳","味道","肤感","使用体验","整体评价","效果好","救星","福音","一生推","强烈推荐","闭眼冲","入股不亏"]):
            review_clean.append(r)
    review = dedup(review_clean)[:20]

    return {
        "qa_facts": qa[:15],
        "mechanism_notes": mechanism[:12],
        "usage_steps": usage[:12],
        "safety_notes": safety[:12],
        "texture_notes": texture[:12],
        "claim_notes": claim[:12],
        "user_review_notes": review[:15],
    }


if __name__ == "__main__":
    import sys, json
    agg_path = "/Users/bytedance/Desktop/xiaoro-shopping-master/.tmp_user_download_audit/aggregated_ocr_html_texts_20260708.json"
    agg = json.load(open(agg_path, encoding="utf-8"))
    samples = ["146","91","64","106","113","144","145","67","102"]
    for pid in samples:
        if pid not in agg:
            print(f"\n===== PID {pid}: 无文本 =====")
            continue
        fields = extract_seven_fields(agg[pid]["text"])
        print(f"\n===== PID {pid} (text_len={len(agg[pid]['text'])}) =====")
        total = 0
        for k, v in fields.items():
            total += len(v)
            print(f"  {k}: {len(v)}条")
            for item in v[:2]:
                print(f"    - {item[:120]}")
        print(f"  [总计 {total} 条事实]")
