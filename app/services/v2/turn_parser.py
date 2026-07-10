import re
from typing import Optional, List, Dict, Any, Tuple
from .models import CanonicalTurn, AnswerMode, FollowupType
from .intent_classifier import IntentClassifier


CATEGORY_KEYWORDS = {
    "精华": ["精华", "精华液", "精华露", "serum"],
    "面霜": ["面霜", "乳霜", "face cream", "moisturizer"],
    "爽肤水": ["爽肤水", "化妆水", "柔肤水", "toner", "精华水"],
    "乳液": ["乳液", "lotion"],
    "防晒": ["防晒", "防晒霜", "防晒乳", "sunscreen", "防晒喷雾"],
    "洁面": ["洁面", "洗面奶", "洁面乳", "cleanser"],
    "眼霜": ["眼霜", "eye cream"],
    "面膜": ["面膜", "mask"],
    "粉底液": ["粉底", "粉底液", "foundation"],
    "气垫": ["气垫", "气垫bb", "气垫cc", "气垫粉底", "cushion"],
    "散粉": ["散粉", "蜜粉", "粉饼", "powder", "定妆粉"],
    "隔离妆前": ["妆前乳", "隔离", "隔离霜", "妆前", "primer"],
    "遮瑕": ["遮瑕", "遮瑕膏", "遮瑕液", "concealer"],
    "腮红": ["腮红", "胭脂", "blush"],
    "口红": ["口红", "唇膏", "lipstick"],
    "卸妆": ["卸妆", "卸妆水", "卸妆油", "卸妆膏", "洁肤液"],
    "香水": ["香水", "香氛", "perfume"],
}

SKIN_TYPE_KEYWORDS = {
    "干敏肌": ["干敏肌", "干敏"],
    "油敏肌": ["油敏肌", "油敏"],
    "干皮": ["干皮", "干性", "干敏", "干燥"],
    "油皮": ["油皮", "油肌", "油性", "出油", "大油皮"],
    "混油皮": ["混油", "混合油", "t区油"],
    "混干皮": ["混干", "混合干"],
    "敏感肌": ["敏感肌", "敏感", "敏皮", "易过敏", "屏障受损", "容易红", "泛红", "烂脸", "刺痛"],
    "中性皮": ["中性皮", "正常皮"],
    "全肤质": ["全肤质", "所有肤质", "任何肤质"],
}

CONCERN_KEYWORDS = {
    "抗初老": ["抗初老", "抗老", "抗衰老", "初老", "淡纹", "紧致", "抗皱"],
    "美白": ["美白", "提亮", "淡斑", "去黄", "提亮肤色"],
    "保湿": ["保湿", "补水", "滋润", "水润"],
    "控油": ["控油", "去油", "清爽", "不油腻"],
    "祛痘": ["祛痘", "痘痘", "闭口", "粉刺", "消炎"],
    "修护": ["修护", "修复", "屏障", "维稳", "舒缓"],
    "防晒": ["防晒", "防紫外线"],
    "遮瑕": ["遮瑕", "遮痘印", "遮盖"],
    "持妆": ["持妆", "不脱妆", "持久"],
}

BRAND_KEYWORDS = [
    "雅诗兰黛", "兰蔻", "资生堂", "SK-II", "sk2", "赫莲娜", "海蓝之谜",
    "薇诺娜", "玉泽", "理肤泉", "雅漾", "修丽可", "欧莱雅", "珀莱雅",
    "自然堂", "百雀羚", "丸美", "温碧泉", "花西子", "完美日记", "毛戈平",
    "科颜氏", "悦木之源", "贝德玛", "可复美", "敷尔佳", "OLAY", "Anessa",
    "NARS", "nars", "MAC", "mac", "迪奥", "Dior", "香奈儿", "Chanel",
    "纪梵希", "Givenchy", "圣罗兰", "YSL", "ysl", "阿玛尼", "Armani",
    "安热沙", "ANESSA", "安耐晒", "怡思丁", "ISDIN",
    "珂润", "Curel", "花王珂润", "碧柔", "Biore", "freeplus", "芙丽芳丝",
    "夸迪", "蒂佳婷", "Dr.Jart", "植村秀", "Shu-uemura", "The Ordinary",
    "CPB", "肌肤之钥", "苏菲娜", "SOFINA", "Sofina", "怡丽丝尔", "ELIXIR",
    "祖玛珑", "Jo Malone", "Tom Ford", "TOM FORD",
]

BRAND_ALIAS_MAP = {
    "sk2": "SK-II",
    "sk-ii": "SK-II",
    "skii": "SK-II",
    "olay": "玉兰油",
    "anessa": "安热沙",
    "安耐晒": "安热沙",
    "dior": "迪奥",
    "chanel": "香奈儿",
    "ysl": "圣罗兰",
    "armani": "阿玛尼",
    "hr": "赫莲娜",
    "mac": "MAC",
    "nars": "NARS",
    "curel": "珂润",
    "biore": "碧柔",
    "freeplus": "芙丽芳丝",
    "dr.jart": "蒂佳婷",
    "cpb": "CPB",
    "肌肤之钥": "CPB",
    "sofina": "苏菲娜",
    "elixir": "怡丽丝尔",
}

FOLLOWUP_PRONOUNS = [
    "它", "这个", "那个", "这一款", "那一款", "这款", "那款", "哪款", "哪个", "哪一个",
    "这个产品", "那个产品", "这玩意", "这东西", "这个东西", "它们",
]

FOLLOWUP_TYPE_KEYWORDS = {
    FollowupType.PRICE: ["多少钱", "价格", "贵不贵", "价位", "多少钱"],
    FollowupType.INGREDIENT: ["成分", "配方", "含不含", "有没有含", "有没有烟酰胺", "有没有A醇", "有没有酒精", "有没有香精"],
    FollowupType.EFFICACY: ["功效", "作用", "效果", "主打什么", "主打啥", "管什么", "管啥", "干嘛用", "干吗用", "干什么用", "有什么用", "有什么效果", "什么功效", "什么作用", "啥功效", "啥作用", "啥效果", "主要功效", "主要作用", "主要效果", "能干嘛", "能干啥", "能做什么"],
    FollowupType.SUITABILITY: ["适合", "友好", "友好吗", "能用吗", "可以用吗", "敏感肌能用", "孕妇能用", "油皮能用", "油肌能用", "干皮能用", "我能用吗", "可以天天用吗", "能天天用吗"],
    FollowupType.USAGE_TIME: ["晚上", "夜间", "夜间用", "夜里", "晚间", "白天", "日间", "早上", "早晨", "上午", "晚上用", "白天用", "早晚", "日夜", "什么时候用", "睡前", "妆前", "怎么用", "用法", "使用方法", "卸妆", "要卸吗", "需要卸妆", "要洗吗", "需要洗"],
    FollowupType.CHEAPER: ["便宜", "更便宜", "平价", "更平价", "平替", "性价比高", "便宜点", "学生党"],
    FollowupType.HIGHER_BUDGET: ["预算更高", "高预算", "贵一点", "更贵", "进阶款", "加预算", "价格高些", "价格高一点", "价位高些", "价位高一点", "高些", "贵些", "更高价"],
    FollowupType.MORE_OPTIONS: ["除了", "除此之外", "还有别的吗", "还有没有别的", "还有没有其他", "换一批", "再来几款"],
}

DAY_TIME_KEYWORDS = ["白天", "日间", "早上", "早晨", "上午", "通勤", "妆前"]
NIGHT_TIME_KEYWORDS = ["晚上", "夜间", "夜里", "晚间", "睡前", "夜晚"]
BOTH_TIME_KEYWORDS = ["早晚", "白天和晚上", "白天晚上", "日间夜间", "日夜", "分别怎么用", "什么时候用"]

COMPARE_SIGNALS = ["对比", "比较", "和", "与", "跟", "vs", "VS", "哪个好", "哪个更好", "哪个更适合", "怎么选", "选哪个", "区别", "差异"]

# "从一堆里挑一个"的挑选信号：不含具体维度词，抗改写。
# 只要出现这些 + 句子里有 >=2 个可比目标，就够立案为"对比/挑选"，不必命中具体比较词。
PICK_SIGNALS = [
    "哪个", "哪款", "哪一个", "哪一款", "哪瓶", "哪支", "哪种",
    "谁", "谁更", "谁比较", "谁好", "更适合", "更好", "怎么选", "选哪", "挑哪",
    "拿哪", "留哪", "买哪", "该选", "选择", "对比", "比较", "区别", "差异", "pk", "PK",
]
# 连接多个目标的结构词（A和B、A跟B、A、B、A vs B）
TARGET_CONNECTORS = ["和", "与", "跟", "、", "还是", "vs", "VS", "对比", "比"]

# 产品别名 → 品牌/品类/名称片段线索，用于昵称直接命中
PRODUCT_ALIAS_MAP = {
    "ANR":      {"brand": "雅诗兰黛", "category": "精华", "name_clue": "小棕瓶"},
    "anr":      {"brand": "雅诗兰黛", "category": "精华", "name_clue": "小棕瓶"},
    "小棕瓶":   {"brand": "雅诗兰黛", "category": "精华", "name_clue": "小棕瓶"},
    "小棕瓶精华": {"brand": "雅诗兰黛", "category": "精华", "name_clue": "小棕瓶"},
    "小棕瓶眼霜": {"brand": "雅诗兰黛", "category": "眼霜", "name_clue": "小棕瓶"},
    "OLAY小白瓶": {"brand": "玉兰油", "category": "精华", "name_clue": "小白瓶"},
    "小白瓶":   {"brand": "玉兰油",   "category": "精华", "name_clue": "小白瓶"},
    "淡斑小白瓶": {"brand": "玉兰油", "category": "精华", "name_clue": "小白瓶"},
    "小白管":   {"brand": "兰蔻",     "category": "防晒", "name_clue": "小白管"},
    "兰蔻小白管": {"brand": "兰蔻",   "category": "防晒", "name_clue": "小白管"},
    "小黑瓶":   {"brand": "兰蔻",     "category": "精华", "name_clue": "小黑瓶"},
    "SK2神仙水": {"brand": "SK-II",   "category": "精华", "name_clue": "神仙水"},
    "sk2神仙水": {"brand": "SK-II",   "category": "精华", "name_clue": "神仙水"},
    "神仙水":   {"brand": "SK-II",    "category": "精华", "name_clue": "神仙水"},
    "红腰子":   {"brand": "资生堂",   "category": "精华", "name_clue": "红腰子"},
    "绿宝瓶":   {"brand": "赫莲娜",   "category": "精华", "name_clue": "绿宝瓶"},
    "双抗":     {"brand": "珀莱雅",   "category": "精华", "name_clue": "双抗"},
    "双抗精华": {"brand": "珀莱雅",   "category": "精华", "name_clue": "双抗"},
    "B5霜":     {"brand": "理肤泉",   "category": "面霜", "name_clue": "B5"},
    "B5面膜":   {"brand": "理肤泉",   "category": "面膜", "name_clue": "B5"},
    "B5精华":   {"brand": "理肤泉",   "category": "精华", "name_clue": "B5"},
    "珂润面霜": {"brand": "珂润",     "category": "面霜", "name_clue": "珂润面霜"},
    "珂润洁面": {"brand": "珂润",     "category": "洁面", "name_clue": "珂润洁面"},
    "B5":       {"brand": "理肤泉",   "category": "面霜", "name_clue": "B5"},
    "小金瓶":   {"brand": "安热沙",   "category": "防晒", "name_clue": "小金瓶"},
    "安耐晒小金瓶": {"brand": "安热沙", "category": "防晒", "name_clue": "小金瓶"},
    "ANESSA小金瓶": {"brand": "安热沙", "category": "防晒", "name_clue": "小金瓶"},
    "蓝胖子":   {"brand": "资生堂",   "category": "防晒", "name_clue": "蓝胖子"},
    "蓝朋友":   {"brand": "欧莱雅",   "category": "精华", "name_clue": "蓝朋友"},
    "菌菇水":   {"brand": "悦木之源", "category": "爽肤水", "name_clue": "菌菇水"},
    "蘑菇水":   {"brand": "悦木之源", "category": "爽肤水", "name_clue": "菌菇水"},
    "金盏花水": {"brand": "科颜氏",   "category": "爽肤水", "name_clue": "金盏花"},
    "金水":     {"brand": "科颜氏",   "category": "爽肤水", "name_clue": "金盏花"},
    "粉水":     {"brand": "兰蔻",     "category": "爽肤水", "name_clue": "粉水"},
    "兰蔻粉水": {"brand": "兰蔻",     "category": "爽肤水", "name_clue": "粉水"},
    "大粉水":   {"brand": "兰蔻",     "category": "爽肤水", "name_clue": "粉水"},
    "贝德玛粉水": {"brand": "贝德玛", "category": "卸妆", "name_clue": "粉水"},
    "舒妍多效洁肤液": {"brand": "贝德玛", "category": "卸妆", "name_clue": "粉水"},
    "DW":       {"brand": "雅诗兰黛", "category": "粉底液", "name_clue": "DW"},
    "dw":       {"brand": "雅诗兰黛", "category": "粉底液", "name_clue": "DW"},
    "DW粉底":   {"brand": "雅诗兰黛", "category": "粉底液", "name_clue": "DW"},
    "权力粉底": {"brand": "阿玛尼",   "category": "粉底液", "name_clue": "权力"},
    "权力粉底液": {"brand": "阿玛尼", "category": "粉底液", "name_clue": "权力"},
    "权力持妆": {"brand": "阿玛尼",   "category": "粉底液", "name_clue": "权力"},
    "权力":     {"brand": "阿玛尼",   "category": "粉底液", "name_clue": "权力"},
    "大师粉底": {"brand": "阿玛尼",   "category": "粉底液", "name_clue": "大师"},
    "大师粉底液": {"brand": "阿玛尼", "category": "粉底液", "name_clue": "大师"},
    "大师":     {"brand": "阿玛尼",   "category": "粉底液", "name_clue": "大师"},
    "CE精华":   {"brand": "修丽可",   "category": "精华", "name_clue": "CE"},
    "CE":       {"brand": "修丽可",   "category": "精华", "name_clue": "CE"},
    "ce精华":   {"brand": "修丽可",   "category": "精华", "name_clue": "CE"},
    "紫米精华": {"brand": "修丽可",   "category": "精华", "name_clue": "紫米"},
    "紫米":     {"brand": "修丽可",   "category": "精华", "name_clue": "紫米"},
    "紫熨斗":   {"brand": "欧莱雅",   "category": "眼霜", "name_clue": "紫熨斗"},
    "玻色因":   {"brand": "赫莲娜",   "category": None,   "name_clue": "玻色因"},
    "377":      {"brand": None,       "category": "精华", "name_clue": "377"},
    "苏菲娜妆前乳": {"brand": "苏菲娜", "category": "隔离妆前", "name_clue": "妆前乳"},
    "苏菲娜隔离": {"brand": "苏菲娜", "category": "隔离妆前", "name_clue": "妆前乳"},
    "CPB长管":   {"brand": "CPB",     "category": "隔离妆前", "name_clue": "长管"},
    "长管隔离":   {"brand": "CPB",     "category": "隔离妆前", "name_clue": "长管"},
    "肌肤之钥长管": {"brand": "CPB",   "category": "隔离妆前", "name_clue": "长管"},
    "红气垫":     {"brand": "阿玛尼",  "category": "气垫",     "name_clue": "红气垫"},
    "黑气垫":     {"brand": "YSL",     "category": "气垫",     "name_clue": "黑气垫"},
    "菁纯眼霜":   {"brand": "兰蔻",    "category": "眼霜",     "name_clue": "菁纯眼霜"},
    "菁纯气垫":   {"brand": "兰蔻",    "category": "气垫",     "name_clue": "菁纯气垫"},
    "菁纯面霜":   {"brand": "兰蔻",    "category": "面霜",     "name_clue": "菁纯面霜"},
    "菁纯粉底":   {"brand": "兰蔻",    "category": "粉底液",   "name_clue": "菁纯粉底"},
    "菁纯粉底液": {"brand": "兰蔻",    "category": "粉底液",   "name_clue": "菁纯粉底"},
    "菁纯":       {"brand": "兰蔻",    "category": None,       "name_clue": "菁纯"},
    "金盏花面膜": {"brand": "科颜氏",  "category": "面膜",     "name_clue": "金盏花面膜"},
    "白泥面膜":   {"brand": "科颜氏",  "category": "面膜",     "name_clue": "白泥"},
    "白泥":       {"brand": "科颜氏",  "category": "面膜",     "name_clue": "白泥"},
    "大哥大":     {"brand": "理肤泉",  "category": "防晒",     "name_clue": "大哥大"},
    "大哥大防晒": {"brand": "理肤泉",  "category": "防晒",     "name_clue": "大哥大"},
    "贝德玛卸妆水": {"brand": "贝德玛", "category": "卸妆",    "name_clue": "粉水"},
    "大白饼":     {"brand": "NARS",    "category": "散粉",     "name_clue": "大白饼"},
    "高潮腮红":   {"brand": "NARS",    "category": "腮红",     "name_clue": "高潮"},
    "Orgasm":     {"brand": "NARS",    "category": "腮红",     "name_clue": "高潮"},
    "NARS遮瑕":   {"brand": "NARS",    "category": "遮瑕",     "name_clue": "遮瑕膏"},
}

BUDGET_PATTERN = re.compile(
    r"(?:预算|大概|准备|打算|想花|价位在?|价格在?|控制在|不超过|低于|小于|¥|￥)?\s*"
    r"(?P<low>\d+(?:\.\d+)?)\s*(?:-|到|~|—|至)\s*(?P<high>\d+(?:\.\d+)?)\s*"
    r"(?:块|元|rmb|RMB)?\s*(?P<flex_range>左右|以内|以下|之间|上下)?|"
    r"(?:"
    r"(?:预算|大概|准备|打算|想花|价位在?|价格在?|控制在|不超过|低于|小于|¥|￥)\s*(?P<single_a>\d+(?:\.\d+)?)"
    r"|"
    r"(?<![A-Za-z0-9])(?P<single_b>\d+(?:\.\d+)?)\s*(?:块|元|rmb|RMB|预算|价位)"
    r"|"
    r"(?<![A-Za-z0-9])(?P<single_c>\d+(?:\.\d+)?)\s*(?P<flex_single>左右|以内|以下|上下|附近|差不多)"
    r")"
    r"\s*(?:块|元|rmb|RMB)?"
)

GENERIC_RECOMMEND_SIGNALS = ["推荐", "介绍", "买什么", "选什么", "用什么", "求推荐", "想买", "帮我选", "选哪个", "哪个好"]


class TurnParser:
    def __init__(self):
        self.intent_classifier = IntentClassifier()

    def parse(self, raw_message: str, session_id: Optional[str] = None,
              conversation_history: Optional[List[Dict[str, Any]]] = None,
              image_context: Optional[Dict[str, Any]] = None) -> CanonicalTurn:
        turn, msg, slots = self._prepare_turn(raw_message, session_id, conversation_history, image_context)
        semantic = self.intent_classifier.classify(msg, turn.conversation_history, slots)
        return self._finalize_turn(turn, msg, semantic)

    async def parse_async(self, raw_message: str, session_id: Optional[str] = None,
                          conversation_history: Optional[List[Dict[str, Any]]] = None,
                          image_context: Optional[Dict[str, Any]] = None) -> CanonicalTurn:
        """异步解析：意图分类走 classify_async，让模型兜底能在事件循环里生效。

        槽位抽取与跨轮继承逻辑与同步 parse 完全一致，只是意图那一步用 await。
        """
        turn, msg, slots = self._prepare_turn(raw_message, session_id, conversation_history, image_context)
        semantic = await self.intent_classifier.classify_async(msg, turn.conversation_history, slots)
        return self._finalize_turn(turn, msg, semantic)

    def _prepare_turn(self, raw_message: str, session_id: Optional[str],
                      conversation_history: Optional[List[Dict[str, Any]]],
                      image_context: Optional[Dict[str, Any]]) -> Tuple[CanonicalTurn, str, Dict[str, Any]]:
        msg = raw_message.strip()
        slot_msg = msg
        if image_context and "\n\n补充图片线索：" in slot_msg:
            # 图片分析补充里可能带商品价格；预算必须只来自用户原话，不能从识图结果反推。
            slot_msg = slot_msg.split("\n\n补充图片线索：", 1)[0].strip()
        turn = CanonicalTurn(
            raw_message=msg,
            session_id=session_id,
            conversation_history=conversation_history or [],
            image_context=image_context,
        )

        turn.budget_min, turn.budget_max, turn.budget_flexible = self._extract_budget(slot_msg)
        turn.skin_type = self._extract_skin_type(slot_msg)
        turn.concerns = self._extract_concerns(slot_msg)
        turn.category = self._extract_category(slot_msg)
        turn.brand = self._extract_brand(slot_msg)
        turn.compare_targets = self._extract_compare_targets(slot_msg)

        rescue_signals = [
            "又红又烫", "红又烫", "红烫", "发红发烫", "脸红", "泛红",
            "烂脸", "刺痛", "屏障受损", "救救", "急救修护",
        ]
        if not turn.category and any(signal in slot_msg for signal in rescue_signals):
            turn.category = "面霜"
            if not turn.skin_type:
                turn.skin_type = "敏感肌"
            if "修护" not in turn.concerns:
                turn.concerns.append("修护")

        if not turn.category and "脂肪粒" in slot_msg:
            turn.category = "眼霜"
            if "不长脂肪粒" not in turn.concerns:
                turn.concerns.append("不长脂肪粒")

        # 产品昵称/别名直接命中（如小棕瓶/小白管/B5霜）。
        # 产品别名比普通品类词更强：比如"粉水能卸防晒吗"里的"防晒"是使用问题，
        # 不是商品品类；命中粉水后必须锚到贝德玛卸妆。
        alias_brand_locked = False
        alias_category_locked = False
        for alias, hint in sorted(PRODUCT_ALIAS_MAP.items(), key=lambda item: len(item[0]), reverse=True):
            if not self._contains_alias(slot_msg, alias):
                continue
            if hint.get("brand") and not alias_brand_locked:
                turn.brand = self._canonical_brand(hint["brand"])
                alias_brand_locked = True
            if hint.get("category") and not alias_category_locked:
                turn.category = hint["category"]
                alias_category_locked = True
            if hint.get("name_clue") and hint["name_clue"] not in turn.name_clues:
                turn.name_clues.append(hint["name_clue"])

        if image_context:
            results = image_context.get("results") or []
            top_products = [r for r in results[:3] if isinstance(r, dict)]
            product_ids = []
            img_brands = []
            img_categories = []
            for p in top_products:
                pid = p.get("id") or p.get("product_id")
                if pid and pid not in product_ids:
                    try:
                        product_ids.append(int(pid))
                    except (TypeError, ValueError):
                        pass
                b = p.get("brand")
                if b and b not in img_brands:
                    img_brands.append(b)
                c = p.get("category")
                if c and c not in img_categories:
                    img_categories.append(c)

            # 判断是否"找平替/相似/其他选择"——这种场景下不要把图片品牌做强过滤
            want_alt = any(w in msg for w in ["平替", "替代", "相似款", "类似款", "同款以外", "别的", "其他", "还有什么", "其他选择", "同类推荐"])

            img_brand = image_context.get("brand") or (img_brands[0] if img_brands else None)
            img_category = image_context.get("category") or (img_categories[0] if img_categories else None)

            if img_category and not turn.category:
                turn.category = img_category
            if img_brand:
                turn.image_anchor_brand = img_brand
                if not want_alt and not turn.brand:
                    turn.brand = img_brand

            turn.image_context["product_ids"] = product_ids
            if img_brand:
                turn.image_context["anchor_brand"] = img_brand

            ocr_info = image_context.get("ocr_info") or {}
            key_info = ocr_info.get("key_info") if isinstance(ocr_info, dict) else None
            if isinstance(key_info, dict):
                if not turn.brand and not want_alt and key_info.get("brand"):
                    ob = key_info["brand"]
                    if isinstance(ob, list) and ob:
                        turn.brand = ob[0]
                    elif isinstance(ob, str):
                        turn.brand = ob
                if not turn.category and key_info.get("category"):
                    oc = key_info["category"]
                    if isinstance(oc, str):
                        for cat, kws in CATEGORY_KEYWORDS.items():
                            if cat in oc or any(kw in oc for kw in kws):
                                turn.category = cat
                                break

        slots = {
            "category": turn.category,
            "brand": turn.brand,
            "concerns": turn.concerns,
            "compare_targets": turn.compare_targets,
            "name_clues": turn.name_clues,
            "image_context": turn.image_context,
        }
        return turn, msg, slots

    def _finalize_turn(self, turn: CanonicalTurn, msg: str, semantic) -> CanonicalTurn:
        turn.intent = semantic.answer_mode
        turn.intent_confidence = semantic.confidence
        turn.intent_reason = semantic.reason
        turn.matched_intent_example = semantic.matched_example
        turn.intent_vector_score = semantic.vector_score
        turn.secondary_intents = semantic.secondary_intents
        turn.is_followup = semantic.answer_mode == AnswerMode.FOLLOWUP
        turn.followup_type = semantic.followup_type
        turn.usage_time_focus = semantic.usage_time_focus


        # 用户画像类信息跨品类也要继承；"我是油敏肌"之后再问防晒/精华，都应该继续带上肤质。
        if not turn.skin_type and turn.conversation_history:
            for hmsg in reversed(turn.conversation_history[-8:]):
                if hmsg.get("role") != "user":
                    continue
                h_skin = self._extract_skin_type(hmsg.get("content", "") or "")
                if h_skin:
                    turn.skin_type = h_skin
                    break

        if (
            turn.intent in {AnswerMode.RECOMMENDATION, AnswerMode.COMPARE, AnswerMode.JUDGEMENT}
            and turn.budget_min is None
            and turn.budget_max is None
            and turn.conversation_history
        ):
            for hmsg in reversed(turn.conversation_history[-8:]):
                if hmsg.get("role") != "user":
                    continue
                hb_min, hb_max, hb_flexible = self._extract_budget(hmsg.get("content", "") or "")
                if hb_min is not None or hb_max is not None:
                    turn.budget_min = hb_min
                    turn.budget_max = hb_max
                    turn.budget_flexible = hb_flexible
                    break

        # 追问场景：如果当前消息没提品类/品牌/预算/肤质，从历史user消息里反向继承最近的约束
        if turn.is_followup and turn.conversation_history:
            turn.referenced_products = self._extract_referenced_products(turn.conversation_history)
            ordinal = self._extract_ordinal_reference(msg)
            if ordinal is not None:
                ordered = self._extract_ordered_products_from_last_answer(turn.conversation_history)
                if ordered and 0 <= ordinal < len(ordered):
                    turn.referenced_products = [ordered[ordinal]]
                elif ordered:
                    turn.referenced_products = ordered[:1]
            wants_higher_budget = (
                turn.followup_type == FollowupType.HIGHER_BUDGET
                or any(item.get("followup_type") == FollowupType.HIGHER_BUDGET.value for item in turn.secondary_intents)
            )
            wants_cheaper_budget = (
                turn.followup_type == FollowupType.CHEAPER
                or any(item.get("followup_type") == FollowupType.CHEAPER.value for item in turn.secondary_intents)
            )
            need = dict(
                cat=not turn.category,
                brand=not turn.brand,
                bmin=turn.budget_min is None,
                bmax=turn.budget_max is None,
                skin=not turn.skin_type,
            )
            previous_budget_min = None
            previous_budget_max = None
            for hmsg in reversed(turn.conversation_history[-6:]):
                if hmsg.get("role") != "user":
                    continue
                htext = hmsg.get("content", "") or ""
                if not htext:
                    continue
                if need["cat"]:
                    h_cat = self._extract_category(htext)
                    if h_cat:
                        turn.category = h_cat
                        need["cat"] = False
                if need["brand"]:
                    h_brand = self._extract_brand(htext)
                    if h_brand:
                        turn.brand = h_brand
                        need["brand"] = False
                if (need["bmin"] or need["bmax"]) and (turn.budget_min is None and turn.budget_max is None):
                    hb_min, hb_max, _ = self._extract_budget(htext)
                    if hb_max is not None or hb_min is not None:
                        previous_budget_min = hb_min
                        previous_budget_max = hb_max
                        if wants_higher_budget:
                            floor = hb_max or hb_min
                            if floor is not None:
                                turn.budget_min = float(floor) * 1.01
                                turn.budget_max = None
                                turn.budget_flexible = True
                        elif wants_cheaper_budget:
                            ceiling = hb_min or hb_max
                            if ceiling is not None:
                                turn.budget_min = None
                                turn.budget_max = float(ceiling)
                                turn.budget_flexible = True
                        else:
                            turn.budget_min = hb_min
                            turn.budget_max = hb_max
                        need["bmin"] = need["bmax"] = False
                if need["skin"]:
                    h_skin = self._extract_skin_type(htext)
                    if h_skin:
                        turn.skin_type = h_skin
                        need["skin"] = False
                if not any(need.values()):
                    break

            if wants_higher_budget and turn.budget_min is None:
                floor = previous_budget_max or previous_budget_min
                if floor is not None:
                    turn.budget_min = float(floor) * 1.01
                    turn.budget_max = None
                    turn.budget_flexible = True
            if wants_cheaper_budget and turn.budget_max is None:
                ceiling = previous_budget_min or previous_budget_max
                if ceiling is not None:
                    turn.budget_min = None
                    turn.budget_max = float(ceiling)
                    turn.budget_flexible = True

        if (
            turn.intent == AnswerMode.KNOWLEDGE
            and turn.conversation_history
            and any(cue in msg for cue in ["成分", "有什么用", "有啥用", "作用", "干嘛的", "配方"])
        ):
            turn.referenced_products = self._extract_referenced_products(turn.conversation_history)

        turn.user_anxiety = self._detect_anxiety(msg)
        turn.budget_drift = turn.budget_min is None and turn.budget_max is None and any(w in msg for w in ["预算", "太贵", "便宜"])

        return turn

    def _detect_followup(self, msg: str, history: List[Dict[str, Any]]) -> bool:
        if not history:
            return False
        has_pronoun = any(p in msg for p in FOLLOWUP_PRONOUNS)
        is_short = len(msg) <= 15
        has_followup_keyword = any(
            any(kw in msg for kw in kws)
            for kws in FOLLOWUP_TYPE_KEYWORDS.values()
        )
        if has_pronoun and is_short:
            return True
        if has_followup_keyword and is_short:
            return True
        if is_short and not self._has_recommend_signal(msg) and not self._has_category(msg):
            return True
        return False

    def _has_recommend_signal(self, msg: str) -> bool:
        return any(s in msg for s in GENERIC_RECOMMEND_SIGNALS)

    def _has_category(self, msg: str) -> bool:
        return any(
            any(kw in msg for kw in kws)
            for kws in CATEGORY_KEYWORDS.values()
        )

    def _extract_budget(self, msg: str) -> Tuple[Optional[float], Optional[float], bool]:
        normalized_msg = self._normalize_budget_text(msg)
        match = BUDGET_PATTERN.search(normalized_msg.replace(" ", ""))
        if not match:
            return None, None, False

        groups = match.groupdict()

        if groups.get("low") and groups.get("high"):
            low = float(groups["low"])
            high = float(groups["high"])
            flexible = bool(groups.get("flex_range"))
            return low, high, flexible

        single = groups.get("single_a") or groups.get("single_b") or groups.get("single_c")
        if not single:
            return None, None, False

        val = float(single)
        flex_word = groups.get("flex_single") or ""
        if not flex_word:
            compact = normalized_msg.replace(" ", "")
            tail = compact[match.end():match.end() + 4]
            for word in ("左右", "上下", "附近", "差不多", "以内", "以下", "之内", "内"):
                if tail.startswith(word):
                    flex_word = word
                    break
        is_upper_bound = flex_word in ("以内", "以下", "之内", "内") or groups.get("flex_range") in ("以内", "以下", "之内", "内")
        flexible = bool(flex_word) or is_upper_bound

        if is_upper_bound:
            return None, val, True
        if flex_word in ("左右", "上下", "附近", "差不多"):
            return val * 0.8, val * 1.2, True
        return val * 0.9, val * 1.1, True

    @staticmethod
    def _normalize_budget_text(msg: str) -> str:
        def cn_to_number(token: str) -> Optional[int]:
            digits = {
                "零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
                "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
            }
            units = {"十": 10, "百": 100, "千": 1000}
            token = token.strip()
            if not token:
                return None
            if token.isdigit():
                return int(token)
            if re.fullmatch(r"[一二两三四五六七八九]千[一二两三四五六七八九]", token):
                return digits[token[0]] * 1000 + digits[token[-1]] * 100
            total = 0
            section = token
            if "万" in token:
                high, low = token.split("万", 1)
                high_val = cn_to_number(high) if high else 1
                low_val = cn_to_number(low) if low else 0
                if high_val is None or low_val is None:
                    return None
                return high_val * 10000 + low_val
            num = 0
            used_unit = False
            for ch in section:
                if ch in digits:
                    num = digits[ch]
                elif ch in units:
                    used_unit = True
                    if num == 0:
                        num = 1
                    total += num * units[ch]
                    num = 0
                else:
                    return None
            total += num
            return total if (used_unit or total > 0) else None

        cn_num_pattern = re.compile(
            r"(?P<num>[零〇一二两三四五六七八九十百千万]+)"
            r"(?=\s*(?:元|块|左右|以内|以下|上下|附近|差不多|之内|内|预算|价位|，|,|。|$))"
        )

        def repl(match: re.Match) -> str:
            value = cn_to_number(match.group("num"))
            return str(value) if value is not None else match.group("num")

        return cn_num_pattern.sub(repl, msg)

    def _extract_skin_type(self, msg: str) -> Optional[str]:
        for skin, keywords in SKIN_TYPE_KEYWORDS.items():
            for kw in keywords:
                if kw in msg:
                    return skin
        return None

    def _extract_concerns(self, msg: str) -> List[str]:
        found = []
        for concern, keywords in CONCERN_KEYWORDS.items():
            for kw in keywords:
                if kw in msg:
                    found.append(concern)
                    break
        return found

    def _extract_category(self, msg: str) -> Optional[str]:
        for cat, keywords in CATEGORY_KEYWORDS.items():
            for kw in keywords:
                if kw in msg:
                    return cat
        return None

    def _extract_brand(self, msg: str) -> Optional[str]:
        for brand in BRAND_KEYWORDS:
            if brand.lower() in msg.lower():
                return self._canonical_brand(brand)
        for alias, canonical in BRAND_ALIAS_MAP.items():
            if alias.lower() in msg.lower():
                return canonical
        return None

    def _extract_compare_targets(self, msg: str) -> List[str]:
        # 立案门（放宽、抗改写）：命中"挑选信号"(哪个/谁更/选哪/拿哪…) 或 目标连接词(和/跟/、/vs/比)
        # 才去抽目标。不再强依赖"哪个好/对比"这类具体比较词小抄——那是"换个问法就崩"的根。
        # 真正决定是不是对比的，是下面"抽出来的目标数 >= 2"，而不是命中了哪个词。
        has_pick = any(s in msg for s in PICK_SIGNALS)
        has_connector = any(c in msg for c in TARGET_CONNECTORS)
        if not (has_pick or has_connector):
            return []
        positioned = []
        alias_positions = {}
        explicit_brands = set()
        for brand in BRAND_KEYWORDS:
            pos = self._alias_index(msg, brand)
            if pos >= 0:
                canonical = self._canonical_brand(brand)
                explicit_brands.add(canonical)
                positioned.append((pos, canonical))
        for alias, hint in sorted(PRODUCT_ALIAS_MAP.items(), key=lambda item: len(item[0]), reverse=True):
            pos = self._alias_index(msg, alias)
            if pos < 0:
                continue
            name_clue = hint.get("name_clue") or alias
            alias_brand = self._canonical_brand(hint.get("brand"))
            alias_positions[pos] = (name_clue, alias_brand)
        for pos, (name_clue, alias_brand) in alias_positions.items():
            positioned = [
                (p, t) for p, t in positioned
                if not (alias_brand and t == alias_brand and abs(p - pos) <= 6)
            ]
            positioned.append((pos, name_clue))
        targets = [brand for _, brand in sorted(positioned, key=lambda item: item[0])]
        deduped = []
        for target in targets:
            if target not in deduped:
                deduped.append(target)
        targets = deduped
        # 只有真的抽到 >=2 个不同目标，才算"一堆可挑的候选"。单个目标不是对比，
        # 交给 judgement/recommendation 处理，避免"珂润面霜好用吗"被误判成对比。
        if len(targets) < 2:
            return []
        return targets

    @staticmethod
    def _canonical_brand(brand: Optional[str]) -> Optional[str]:
        if not brand:
            return brand
        return BRAND_ALIAS_MAP.get(str(brand).lower(), brand)

    @staticmethod
    def _contains_alias(msg: str, alias: str) -> bool:
        return TurnParser._alias_index(msg, alias) >= 0

    @staticmethod
    def _alias_index(msg: str, alias: str) -> int:
        if not msg or not alias:
            return -1
        if re.search(r"[A-Za-z]", alias):
            return msg.lower().find(alias.lower())
        return msg.find(alias)

    def _detect_followup_type(self, msg: str) -> FollowupType:
        for kw in FOLLOWUP_TYPE_KEYWORDS[FollowupType.USAGE_TIME]:
            if kw in msg:
                return FollowupType.USAGE_TIME
        for ftype, keywords in FOLLOWUP_TYPE_KEYWORDS.items():
            if ftype == FollowupType.USAGE_TIME:
                continue
            for kw in keywords:
                if kw in msg:
                    return ftype
        return FollowupType.OTHER

    def _detect_usage_time_focus(self, msg: str) -> str:
        if any(kw in msg for kw in BOTH_TIME_KEYWORDS):
            return "both"
        has_day = any(kw in msg for kw in DAY_TIME_KEYWORDS)
        has_night = any(kw in msg for kw in NIGHT_TIME_KEYWORDS)
        if has_day and has_night:
            return "both"
        if has_day:
            return "day"
        if has_night:
            return "night"
        return "general"

    def _extract_referenced_products(self, history: List[Dict[str, Any]]) -> List[str]:
        products = []
        for msg in reversed(history[-5:]):
            if msg.get("role") == "assistant":
                content = msg.get("content", "")
                for ref in self._extract_ordered_product_refs(content):
                    if ref not in products:
                        products.append(ref)
        return products

    @staticmethod
    def _extract_ordinal_reference(msg: str) -> Optional[int]:
        """解析"第N款/个/支/瓶"，返回0基序号；没有则None。"""
        m = re.search(r"第\s*([一二三四五六1-6])\s*(?:款|个|支|瓶|种)", msg or "")
        if not m:
            return None
        mapping = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5,
                   "1": 0, "2": 1, "3": 2, "4": 3, "5": 4, "6": 5}
        return mapping.get(m.group(1))

    def _extract_ordered_products_from_last_answer(self, history: List[Dict[str, Any]]) -> List[str]:
        """只从最近一条助手回复里，按出现顺序抽出品牌序列。

        "第N款"必须锚定到上一轮真实展示的那一款，不能把多轮历史混在一起。
        """
        for msg in reversed(history):
            if msg.get("role") != "assistant":
                continue
            content = msg.get("content", "") or ""
            return self._extract_ordered_product_refs(content)
        return []

    def _extract_ordered_product_refs(self, content: str) -> List[str]:
        """从助手回复中按出现顺序抽取可回指的商品锚点。

        旧逻辑只扫品牌名，遇到"玉兰油小白瓶"这类品牌不在 BRAND_KEYWORDS、或同品牌多款时容易漏/混。
        这里同时扫品牌和 PRODUCT_ALIAS_MAP；别名命中时用 name_clue，能更贴近真实展示的那一款。
        """
        positioned = []
        for brand in BRAND_KEYWORDS:
            pos = self._alias_index(content, brand)
            if pos >= 0:
                positioned.append((pos, self._canonical_brand(brand)))
        for alias, hint in sorted(PRODUCT_ALIAS_MAP.items(), key=lambda item: len(item[0]), reverse=True):
            pos = self._alias_index(content, alias)
            if pos < 0:
                continue
            alias_brand = self._canonical_brand(hint.get("brand"))
            name_clue = hint.get("name_clue") or alias
            # 优先用 name_clue（产品昵称）作为锚点，避免同品牌多款商品混淆
            # （如"小棕瓶"→"雅诗兰黛小棕瓶"而不是单纯的"雅诗兰黛"）
            ref = name_clue
            # 只有当别名附近没有同品牌，且 ref 以品牌名开头时才去重
            positioned = [
                (p, t) for p, t in positioned
                if not (alias_brand and t == alias_brand and abs(p - pos) <= 8 and len(t) <= len(alias_brand) + 2)
            ]
            positioned.append((pos, ref))
        ordered = []
        for _, ref in sorted(positioned, key=lambda item: item[0]):
            if ref and ref not in ordered:
                ordered.append(ref)
        return ordered

    def _detect_anxiety(self, msg: str) -> bool:
        anxiety_words = ["会不会", "怕", "担心", "害怕", "过敏", "烂脸", "刺激", "踩雷", "翻车", "不好用", "纠结"]
        return any(w in msg for w in anxiety_words)
