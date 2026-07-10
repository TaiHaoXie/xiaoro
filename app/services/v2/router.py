import re
from typing import Optional
from .models import CanonicalTurn, RouteDecision, AnswerMode, FollowupType


GENERIC_KNOWLEDGE_KEYWORDS = ["怎么", "如何", "为什么", "是什么", "可以", "能", "多久", "多少", "区别", "功效", "作用"]
KNOWLEDGE_PREFIXES = ["什么是", "请问", "科普", "解释一下", "了解一下"]


class Router:
    def route(self, turn: CanonicalTurn) -> RouteDecision:
        # 显式对比（有和/与/vs+两个商品目标+对比信号）优先级最高，防止被classifier误判
        if self._is_explicit_compare(turn):
            return RouteDecision(
                answer_mode=AnswerMode.COMPARE,
                confidence=0.95,
                reason="用户明确要求对比两个或多个商品"
            )

        if turn.is_followup:
            return RouteDecision(
                answer_mode=AnswerMode.FOLLOWUP,
                confidence=0.9,
                reason="检测到追问上下文",
                followup_type=turn.followup_type
            )

        # 单品判断（怎么样/好用吗/油不油/清洁力等）优先级高于分类器结果
        if self._is_product_judgement(turn):
            return RouteDecision(
                answer_mode=AnswerMode.JUDGEMENT,
                confidence=0.9,
                reason="明确单品判断：判断某款产品是否适合/评价/如何使用"
            )

        if turn.intent:
            return RouteDecision(
                answer_mode=turn.intent,
                confidence=turn.intent_confidence or 0.85,
                reason=turn.intent_reason or "semantic_classifier",
                followup_type=turn.followup_type,
            )

        if self._is_no_match(turn):
            return RouteDecision(
                answer_mode=AnswerMode.NO_MATCH,
                confidence=0.8,
                reason="缺少必要信息，无法进行推荐"
            )

        if self._is_knowledge_query(turn):
            return RouteDecision(
                answer_mode=AnswerMode.KNOWLEDGE,
                confidence=0.75,
                reason="检测到知识问答意图"
            )

        return RouteDecision(
            answer_mode=AnswerMode.RECOMMENDATION,
            confidence=0.85,
            reason="默认推荐模式"
        )

    def _is_explicit_compare(self, turn: CanonicalTurn) -> bool:
        """判定是否"从一堆里挑"（对比/挑选）。

        主判据是**结构**：解析器抽出了 >=2 个可比目标(compare_targets)。
        这个信号在 turn_parser._extract_compare_targets 里已经过了立案门
        （有挑选信号或连接词 + 真的抽到>=2个不同目标），抗改写、不靠具体比较词小抄。
        兼容旧路径：句子里出现 "A和B/A vs B" 这类结构 + 任一比较词，也算。
        """
        msg = turn.raw_message or ""

        if (
            turn.is_followup
            and turn.followup_type == FollowupType.USAGE_TIME
            and len(turn.compare_targets or []) < 2
            and self._is_time_dimension_compare(msg)
        ):
            return False

        # 结构证据优先：抽到 >=2 个可比目标，直接判对比/挑选。
        if len(turn.compare_targets) >= 2:
            return True

        # 兜底：没抽到具名目标，但句子本身是明显的 A和B/A vs B 比较结构。
        compare_words = ["对比", "比较", "哪个", "哪款", "谁更", "选哪", "拿哪",
                         "怎么选", "区别", "差异", "更适合", "更好", "vs", "VS", "pk", "PK"]
        has_compare_word = any(w in msg for w in compare_words)
        has_ab_structure = any(c in msg for c in ["和", "与", "跟", "还是", "vs", "VS"])
        if has_ab_structure and has_compare_word:
            return True

        return False

    @staticmethod
    def _is_time_dimension_compare(msg: str) -> bool:
        time_terms = ["白天", "晚上", "夜间", "夜里", "晚间", "日间", "早上", "早晨", "上午", "睡前", "夜晚", "早晚", "日夜"]
        has_connector = any(c in msg for c in ["和", "与", "跟", "还是", "vs", "VS"])
        time_hits = sum(1 for t in time_terms if t in msg)
        has_multi_ordinal = len(re.findall(r"第\s*[一二两三四五六七八九1-9]\s*(?:款|个|支|瓶|种)", msg or "")) >= 2
        if has_multi_ordinal:
            return False
        return has_connector and time_hits >= 2

    def _is_no_match(self, turn: CanonicalTurn) -> bool:
        msg = turn.raw_message

        if turn.image_context:
            return False

        if any(kw in msg for kw in ["你好", "您好", "hi", "hello", "在吗", "在不在", "帮个忙"]):
            if len(msg) < 10:
                return True

        has_category = turn.category is not None
        has_concern = len(turn.concerns) > 0
        has_brand = turn.brand is not None
        has_recommend = any(s in msg for s in ["推荐", "买什么", "选什么", "用什么", "求推荐", "想买", "帮我选"])

        if not has_category and not has_concern and not has_brand and not has_recommend and not turn.image_context:
            if len(msg) < 8 and not self._is_knowledge_query(turn):
                return True

        return False

    def _is_knowledge_query(self, turn: CanonicalTurn) -> bool:
        msg = turn.raw_message

        for prefix in KNOWLEDGE_PREFIXES:
            if msg.startswith(prefix):
                if not turn.category and not turn.brand and len(turn.concerns) == 0:
                    return True

        if "怎么" in msg and "选" not in msg and "买" not in msg and "推荐" not in msg:
            if not turn.category and not turn.brand:
                return True

        if "是什么" in msg and len(turn.concerns) == 0:
            return True

        return False

    JUDGEMENT_SIGNALS = [
        "能用吗", "可以用吗", "可以吗", "适合吗", "适合我吗",
        "敏感肌能用", "油皮能用", "干皮能用", "孕妇能用", "孕妇可以",
        "我能用吗", "可以用么", "能用么", "适不适合", "可不可以用",
        "适合什么年龄", "适合什么肤质", "适合什么皮肤", "适合什么人",
        "怎么用", "如何用", "使用方法", "用法", "什么时候用",
        "白天用", "晚上用", "要避光", "要不要洗", "要洗掉吗",
        "能不能用", "会不会过敏", "会不会爆痘", "会不会闷痘",
        "可以天天用吗", "每天用", "能天天用吗",
        "怎么样", "好不好", "好用吗", "值得买吗", "靠谱吗", "好吗",
        "推荐吗", "行不行", "能买吗",
        "油不油", "闷不闷", "假不假白", "搓不搓泥", "干不干",
        "控油吗", "闷痘吗", "卡粉吗", "拔干吗", "刺激吗",
        "清洁力", "好用不", "真的假的", "真的有用吗",
    ]

    def _is_product_judgement(self, turn: CanonicalTurn) -> bool:
        msg = turn.raw_message
        # 必须明确指向某个单品：通过name_clue或brand+品类定位到单品
        has_target = bool(turn.name_clues) or bool(turn.brand and turn.category)
        if not has_target:
            return False
        # 判定类信号（评价/适用/用法/功效）
        has_signal = any(s in msg for s in self.JUDGEMENT_SIGNALS)
        # 价格信号：有具体商品名时问"多少钱/价格/贵不贵"也属于单品查询（判断/告知价格）
        has_price_signal = any(s in msg for s in ["多少钱", "什么价格", "价格多少", "价位多少", "贵不贵", "多少钱一支", "多少钱一瓶"])
        if not (has_signal or has_price_signal):
            return False
        # 排除对比场景（已在前面处理）
        if any(s in msg for s in ["对比", "比较", "哪个好", "哪个更好", "选哪个", "和", "与"]):
            pass
        return True
