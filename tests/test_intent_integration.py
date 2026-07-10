#!/usr/bin/env python3
"""
测试意图分类器集成

验证新的场景化意图分类系统是否正确集成到聊天API
"""

import asyncio
import sys
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.services.intent import get_intent_service, ScenarioIntent


async def test_intent_classification():
    """测试意图分类"""
    service = get_intent_service()

    test_cases = [
        ("iPhone 15 Pro 多少钱？", "明确购买", "高"),
        ("小米14 Ultra 和华为 P60 Pro 选哪个？", "比价决策", "高"),
        ("我想买个送男朋友的礼物，预算2000左右", "需求探索", "高"),
        ("什么是高刷新率屏幕？", "知识咨询", "中"),
        ("怎么退货？", "售后服务", "转接"),
        ("你好", "闲聊寒暄", "低"),
    ]

    print("=" * 70)
    print("意图分类集成测试")
    print("=" * 70)

    for query, expected_intent, expected_priority in test_cases:
        result = await service.recognize(query)

        # 检查结果
        scenario_match = "✅" if result.scenario_intent == expected_intent else "❌"
        priority_match = "✅" if result.priority == expected_priority else "❌"

        print(f"\n输入: {query}")
        print(f"  旧系统意图: {result.intent}")
        print(f"  场景意图: {result.scenario_intent} {scenario_match}")
        print(f"  优先级: {result.priority} {priority_match}")
        print(f"  置信度: {result.confidence:.2f}")

        if result.scenario_prompt:
            print(f"  场景提示词长度: {len(result.scenario_prompt)} 字符")

    print("\n" + "=" * 70)


async def test_intent_mapping():
    """测试意图映射"""
    from app.services.intent import INTENT_MAPPING, SCENARIO_TO_INTENT_MAPPING

    print("\n意图映射测试")
    print("=" * 70)

    print("\n旧系统 -> 新系统映射:")
    for old_intent, new_intent in list(INTENT_MAPPING.items())[:5]:
        print(f"  {old_intent:30s} -> {new_intent}")

    print("\n新系统 -> 旧系统映射:")
    for new_intent, old_intent in list(SCENARIO_TO_INTENT_MAPPING.items())[:5]:
        print(f"  {new_intent:15s} -> {old_intent}")

    print("\n" + "=" * 70)


async def main():
    """主函数"""
    await test_intent_classification()
    await test_intent_mapping()

    print("\n✅ 测试完成！")


if __name__ == "__main__":
    asyncio.run(main())
