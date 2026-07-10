#!/usr/bin/env python3
"""
最小 LLM Smoke Test：验证 SiliconFlow(DeepSeek) 连通性，脱敏输出。
不烧太多 token，只发一条短消息。
"""
import sys
import asyncio
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except Exception:
    pass

from app.config import settings


def mask(key: str) -> str:
    if not key:
        return "(空)"
    if len(key) <= 8:
        return "***"
    return f"{key[:4]}...{key[-4:]}"


async def main():
    print("=" * 60)
    print("LLM Smoke Test")
    print("=" * 60)
    print(f"DEFAULT_LLM_PROVIDER = {settings.DEFAULT_LLM_PROVIDER}")
    print(f"SILICONFLOW_API_KEY  = {mask(settings.SILICONFLOW_API_KEY)}")
    print(f"SILICONFLOW_API_BASE = {settings.SILICONFLOW_API_BASE}")
    print(f"SILICONFLOW_CHAT_MODEL = {settings.SILICONFLOW_CHAT_MODEL}")
    print(f"V2_DISABLE_LLM       = {settings.V2_DISABLE_LLM}")
    print()

    from app.services.llm import get_llm_service
    svc = get_llm_service()

    messages = [
        {"role": "system", "content": "你是一个简洁的助手，用一句话回答。"},
        {"role": "user", "content": "只回复两个字：连通"},
    ]

    print(">>> 非流式调用 (siliconflow) ...")
    try:
        text = await svc.chat(messages, temperature=0.0, max_tokens=20, use_cache=False, provider="siliconflow")
        print(f"✅ 非流式成功: {text[:100]!r}")
    except Exception as e:
        print(f"❌ 非流式失败: {type(e).__name__}: {e}")
        return 1

    print()
    print(">>> 流式调用 (siliconflow) ...")
    try:
        chunks = []
        async for chunk in svc.chat_stream(messages, temperature=0.0, max_tokens=20, provider="siliconflow"):
            chunks.append(chunk)
        joined = "".join(chunks)
        print(f"✅ 流式成功, 共{len(chunks)}个片段: {joined[:100]!r}")
    except Exception as e:
        print(f"❌ 流式失败: {type(e).__name__}: {e}")
        return 1

    print()
    print(">>> 默认 provider 非流式 ...")
    try:
        text2 = await svc.chat(
            [{"role": "user", "content": "1+1=? 只回复数字"}],
            temperature=0.0, max_tokens=10, use_cache=False,
        )
        print(f"✅ 默认provider成功: {text2[:50]!r}")
    except Exception as e:
        print(f"❌ 默认provider失败: {type(e).__name__}: {e}")
        return 1

    print()
    print("=" * 60)
    print("🎉 Smoke Test 全部通过")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
