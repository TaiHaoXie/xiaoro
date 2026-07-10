# -*- coding: utf-8 -*-
"""
意图分类测试模块

实现意图分类器的测试和评估
"""

import asyncio
import json
import csv
import re
from pathlib import Path
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
import logging

from app.config import settings
from app.prompts.intent_prompts import INTENT_CLASSIFIER_SYSTEM

logger = logging.getLogger(__name__)


# ==================== 意图类型定义 ====================

INTENT_TYPES = {
    "明确购买": "已知目标商品，准备下单",
    "比价决策": "在2-3个选项中犹豫",
    "需求探索": "有模糊需求，不确定买什么",
    "知识咨询": "了解产品知识，非购买意图",
    "优惠活动": "询问折扣、优惠券、活动",
    "库存咨询": "询问是否有货、到货时间",
    "新品咨询": "了解最近上架的商品",
    "闲聊寒暄": "打招呼、测试机器人",
    "售后服务": "退换货、物流查询、投诉"
}

INTENT_PRIORITIES = {
    "明确购买": "高",
    "比价决策": "高",
    "需求探索": "高",
    "知识咨询": "中",
    "优惠活动": "中",
    "库存咨询": "中",
    "新品咨询": "低",
    "闲聊寒暄": "低",
    "售后服务": "转接"
}


# ==================== 测试数据集 ====================

DEFAULT_TEST_DATA = [
    # 明确购买
    {"user_input": "iPhone 15 Pro 多少钱？", "expected_intent": "明确购买", "confidence": "高"},
    {"user_input": "这款 MacBook Pro 现在什么价格", "expected_intent": "明确购买", "confidence": "高"},
    {"user_input": "我要买 AirPods Pro", "expected_intent": "明确购买", "confidence": "高"},
    {"user_input": "给我下单小米14 Ultra", "expected_intent": "明确购买", "confidence": "高"},  # 更正：明确的购买意图

    # 比价决策
    {"user_input": "小米14 Ultra 和华为 P60 Pro 选哪个？", "expected_intent": "比价决策", "confidence": "高"},
    {"user_input": "iPhone 15 和 15 Pro 区别大吗", "expected_intent": "比价决策", "confidence": "高"},
    {"user_input": "索尼和Bose耳机哪个好？", "expected_intent": "比价决策", "confidence": "高"},
    {"user_input": "在这个价位，选华为还是小米？", "expected_intent": "比价决策", "confidence": "高"},
    {"user_input": "扫地机器人和吸尘器哪个好？", "expected_intent": "比价决策", "confidence": "高"},  # 更正：这是比较问题

    # 需求探索
    {"user_input": "我想买个送男朋友的礼物，预算2000左右", "expected_intent": "需求探索", "confidence": "高"},
    {"user_input": "想买个降噪耳机，不知道选哪个", "expected_intent": "需求探索", "confidence": "高"},
    {"user_input": "有没有适合学生党的笔记本推荐？", "expected_intent": "需求探索", "confidence": "高"},
    {"user_input": "我想换手机，平时喜欢拍照", "expected_intent": "需求探索", "confidence": "高"},

    # 知识咨询
    {"user_input": "什么是高刷新率屏幕？", "expected_intent": "知识咨询", "confidence": "中"},
    {"user_input": "OLED 屏幕是什么原理？", "expected_intent": "知识咨询", "confidence": "中"},
    {"user_input": "5G 网络有什么优势？", "expected_intent": "知识咨询", "confidence": "中"},
    {"user_input": "降噪耳机是如何工作的？", "expected_intent": "知识咨询", "confidence": "中"},

    # 优惠活动
    {"user_input": "有什么优惠券吗？", "expected_intent": "优惠活动", "confidence": "中"},
    {"user_input": "现在有什么优惠活动？", "expected_intent": "优惠活动", "confidence": "中"},
    {"user_input": "这个能用券吗？", "expected_intent": "优惠活动", "confidence": "中"},
    {"user_input": "双11有什么折扣？", "expected_intent": "优惠活动", "confidence": "中"},

    # 库存咨询
    {"user_input": "这个什么时候有货？", "expected_intent": "库存咨询", "confidence": "中"},
    {"user_input": "到货了能通知我吗？", "expected_intent": "库存咨询", "confidence": "中"},
    {"user_input": "还有现货吗？", "expected_intent": "库存咨询", "confidence": "中"},

    # 新品咨询
    {"user_input": "最近有什么新上的？", "expected_intent": "新品咨询", "confidence": "低"},
    {"user_input": "有什么新款手机？", "expected_intent": "新品咨询", "confidence": "低"},

    # 闲聊寒暄
    {"user_input": "你好", "expected_intent": "闲聊寒暄", "confidence": "低"},
    {"user_input": "在吗？", "expected_intent": "闲聊寒暄", "confidence": "低"},
    {"user_input": "你是机器人吗？", "expected_intent": "闲聊寒暄", "confidence": "低"},

    # 售后服务
    {"user_input": "怎么退货？", "expected_intent": "售后服务", "confidence": "高"},
    {"user_input": "物流到哪了？", "expected_intent": "售后服务", "confidence": "高"},
    {"user_input": "我买的耳机坏了怎么办？", "expected_intent": "售后服务", "confidence": "高"},
    {"user_input": "我要投诉！", "expected_intent": "售后服务", "confidence": "高"},
]


# ==================== 意图分类器 ====================

class IntentClassifier:
    """意图分类器"""

    def __init__(self):
        """初始化分类器"""
        self.intent_list = list(INTENT_TYPES.keys())

    def classify_by_rules(self, user_input: str) -> Dict[str, Any]:
        """
        基于规则进行意图分类（快速分类）

        Args:
            user_input: 用户输入

        Returns:
            分类结果
        """
        user_input_lower = user_input.lower()

        # 售后服务检测（最高优先级）
        from app.prompts.intent_prompts import AFTER_SALES_KEYWORDS
        for category, keywords in AFTER_SALES_KEYWORDS.items():
            if any(keyword in user_input_lower for keyword in keywords):
                return {
                    "intent": "售后服务",
                    "confidence": "高",
                    "method": "rule_based"
                }

        # 特殊规则：优先匹配
        special_rules = {
            # "和有什么区别" 是知识咨询，不是比价
            "和有什么区别": "知识咨询",
            # "什么是" 开头是知识咨询
            "什么是": "知识咨询",
            # "多少钱" 是明确购买
            "多少钱": "明确购买",
        }

        if any(term in user_input for term in ["这两个", "这两款", "两个", "两款"]) and any(term in user_input for term in ["哪个", "更稳", "纠结", "怎么选"]):
            return {
                "intent": "比价决策",
                "confidence": "高",
                "method": "rule_based"
            }

        if any(term in user_input for term in ["这款", "这个"]) and any(term in user_input for term in ["能不能用", "能用吗", "适不适合", "值得买吗"]):
            return {
                "intent": "明确购买",
                "confidence": "高",
                "method": "rule_based"
            }

        for pattern, intent in special_rules.items():
            if pattern in user_input_lower:
                return {
                    "intent": intent,
                    "confidence": "高",
                    "method": "rule_based"
                }

        # 规则匹配（按优先级排序）
        rules = {
            # 售后服务已在前面处理
            # 明确购买（含价格询问）
            "明确购买": ["多少钱", "价格多少", "我要买", "下单", "购买一台", "买一台", "现在什么价格"],
            # 比价决策（需要具体的产品比较）
            "比价决策": ["和", "还是", "选哪个", "哪个更好", "对比一下", "vs"],
            # 需求探索（模糊需求）
            "需求探索": ["想买个", "想换", "推荐", "适合", "有没有", "想要", "不知道选"],
            # 知识咨询（了解知识，非比较）
            "知识咨询": ["如何工作", "原理", "是什么", "怎么用", "和什么区别"],
            # 优惠活动
            "优惠活动": ["优惠", "折扣", "券", "活动", "便宜", "满减"],
            # 库存咨询
            "库存咨询": ["有货", "库存", "到货", "发货", "现货", "没到"],
            # 新品咨询
            "新品咨询": ["新品", "新款", "新上的", "刚出"],
            # 闲聊寒暄
            "闲聊寒暄": ["你好", "嗨", "在吗", "hello", "hi", "你是", "有人吗"],
        }

        # 匹配得分（加权）
        scores = {}
        for intent, keywords in rules.items():
            score = 0
            for kw in keywords:
                if kw == "vs":
                    if not re.search(r'(?i)(^|[\s/｜|,，])vs([\s/｜|,，]|$)', user_input):
                        continue
                elif kw not in user_input_lower:
                    continue
                # 长关键词权重更高
                weight = len(kw)
                score += weight
            if score > 0:
                scores[intent] = score

        if scores:
            top_intent = max(scores, key=scores.get)
            # 如果得分接近，进行二次判断
            sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
            if len(sorted_scores) > 1:
                top_score = sorted_scores[0][1]
                second_score = sorted_scores[1][1]
                # 如果前两名得分差距小于20%，选择优先级更高的
                if second_score / top_score > 0.8:
                    top_intent_priority = INTENT_PRIORITIES.get(sorted_scores[0][0], "低")
                    second_intent_priority = INTENT_PRIORITIES.get(sorted_scores[1][0], "低")
                    priority_order = {"高": 3, "中": 2, "低": 1, "转接": 0}
                    if priority_order.get(second_intent_priority, 0) > priority_order.get(top_intent_priority, 0):
                        top_intent = sorted_scores[1][0]

            confidence = "高" if scores[top_intent] > 5 else "中"
            return {
                "intent": top_intent,
                "confidence": confidence,
                "method": "rule_based",
                "scores": scores
            }

        # 默认返回需求探索
        return {
            "intent": "需求探索",
            "confidence": "低",
            "method": "rule_based"
        }

    async def classify_by_llm(self, user_input: str) -> Dict[str, Any]:
        """
        基于LLM进行意图分类

        Args:
            user_input: 用户输入

        Returns:
            分类结果
        """
        # 构建分类提示词
        intent_list_str = "\n".join([f"{i+1}. {intent}" for i, intent in enumerate(self.intent_list)])

        prompt = f"""你是意图分类器。请将用户输入分类为以下{len(self.intent_list)}类之一：

{intent_list_str}

用户输入：{user_input}

请只返回意图类型名称，不要其他内容。
"""

        try:
            from app.services.llm import get_llm_service
            llm_service = get_llm_service()

            response = await llm_service.achat(
                messages=[{"role": "system", "content": prompt}],
                temperature=0
            )

            result_text = response.strip()

            # 验证返回的意图是否有效
            if result_text in self.intent_list:
                return {
                    "intent": result_text,
                    "confidence": "高",
                    "method": "llm"
                }
            else:
                # 尝试模糊匹配
                for intent in self.intent_list:
                    if intent in result_text or result_text in intent:
                        return {
                            "intent": intent,
                            "confidence": "中",
                            "method": "llm"
                        }

        except Exception as e:
            logger.error(f"LLM分类失败: {e}")

        # LLM失败，回退到规则分类
        return self.classify_by_rules(user_input)

    async def classify(self, user_input: str, use_llm: bool = True) -> Dict[str, Any]:
        """
        意图分类（自动选择方法）

        Args:
            user_input: 用户输入
            use_llm: 是否使用LLM（默认True）

        Returns:
            分类结果
        """
        # 先尝试规则分类（快速）
        rule_result = self.classify_by_rules(user_input)

        # 如果规则分类置信度高，直接返回
        if rule_result["confidence"] == "高" and rule_result["intent"] != "需求探索":
            return rule_result

        # 否则使用LLM
        if use_llm:
            return await self.classify_by_llm(user_input)

        return rule_result


# ==================== 测试评估器 ====================

@dataclass
class TestResult:
    """测试结果"""
    total: int
    correct: int
    by_intent: Dict[str, Dict[str, int]]
    errors: List[Dict[str, Any]]

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total > 0 else 0

    @property
    def high_priority_accuracy(self) -> float:
        """高优先级意图准确率"""
        high_priority_total = 0
        high_priority_correct = 0

        for intent, stats in self.by_intent.items():
            if INTENT_PRIORITIES.get(intent) == "高":
                high_priority_total += stats["total"]
                high_priority_correct += stats["correct"]

        return high_priority_correct / high_priority_total if high_priority_total > 0 else 0


class IntentTester:
    """意图分类测试器"""

    def __init__(self, test_data: List[Dict] = None):
        """
        初始化测试器

        Args:
            test_data: 测试数据（可选）
        """
        self.test_data = test_data or DEFAULT_TEST_DATA
        self.classifier = IntentClassifier()

    async def run_test(self, use_llm: bool = True) -> TestResult:
        """
        运行测试

        Args:
            use_llm: 是否使用LLM分类

        Returns:
            测试结果
        """
        results = {
            "total": len(self.test_data),
            "correct": 0,
            "by_intent": {},
            "errors": []
        }

        for item in self.test_data:
            user_input = item["user_input"]
            expected = item["expected_intent"]

            # 分类
            if use_llm:
                result = await self.classifier.classify(user_input, use_llm=True)
            else:
                result = self.classifier.classify_by_rules(user_input)

            predicted = result["intent"]

            # 统计
            if expected not in results["by_intent"]:
                results["by_intent"][expected] = {"total": 0, "correct": 0}

            results["by_intent"][expected]["total"] += 1

            if predicted == expected:
                results["correct"] += 1
                results["by_intent"][expected]["correct"] += 1
            else:
                results["errors"].append({
                    "user_input": user_input,
                    "expected": expected,
                    "predicted": predicted,
                    "confidence": result.get("confidence", "N/A"),
                    "method": result.get("method", "N/A")
                })

        return TestResult(**results)

    def print_report(self, result: TestResult):
        """
        打印测试报告

        Args:
            result: 测试结果
        """
        print("\n" + "=" * 60)
        print("意图分类测试报告")
        print("=" * 60)
        print(f"总样本数: {result.total}")
        print(f"正确数量: {result.correct}")
        print(f"准确率: {result.accuracy:.2%}")
        print(f"高优先级意图准确率: {result.high_priority_accuracy:.2%}")
        print("=" * 60)

        print("\n各意图类型准确率:")
        for intent, stats in result.by_intent.items():
            acc = stats["correct"] / stats["total"]
            priority = INTENT_PRIORITIES.get(intent, "N/A")
            print(f"  {intent:12s} [{priority}]: {acc:.2%} ({stats['correct']}/{stats['total']})")

        if result.errors:
            print("\n错误案例:")
            for error in result.errors[:10]:  # 只显示前10个
                print(f"  ❌ {error['user_input']}")
                print(f"     预期: {error['expected']}, 预测: {error['predicted']} ({error['method']})")

        print("=" * 60 + "\n")

    def export_csv(self, result: TestResult, filepath: str):
        """
        导出测试结果到CSV

        Args:
            result: 测试结果
            filepath: 文件路径
        """
        with open(filepath, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(["意图类型", "总数", "正确数", "准确率", "优先级"])

            for intent, stats in result.by_intent.items():
                acc = stats["correct"] / stats["total"]
                priority = INTENT_PRIORITIES.get(intent, "N/A")
                writer.writerow([intent, stats["total"], stats["correct"], f"{acc:.2%}", priority])

        logger.info(f"测试结果已导出到: {filepath}")


# ==================== 主函数 ====================

async def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(description="意图分类测试")
    parser.add_argument("--method", choices=["rule", "llm", "both"], default="both",
                       help="分类方法")
    parser.add_argument("--export", type=str, help="导出结果到CSV")
    args = parser.parse_args()

    # 配置日志
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    tester = IntentTester()

    if args.method in ["rule", "both"]:
        print("\n🔍 测试规则分类器...")
        result = await tester.run_test(use_llm=False)
        tester.print_report(result)

        if args.export:
            tester.export_csv(result, args.export.replace(".csv", "_rule.csv"))

    if args.method in ["llm", "both"]:
        print("\n🤖 测试LLM分类器...")
        result = await tester.run_test(use_llm=True)
        tester.print_report(result)

        if args.export:
            tester.export_csv(result, args.export.replace(".csv", "_llm.csv"))


if __name__ == "__main__":
    asyncio.run(main())
