# Real LLM Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在保持 V2 五流格式不回退的前提下，把真实 LLM 安全接入 V2 润色链路，并建立可重复的真实 API 验收流程。

**Architecture:** 先补齐 provider 配置契约和 smoke test，避免“字段缺失导致假 fallback”；再让 `LLMService` 用统一 provider 解析逻辑调用真实模型；最后只允许 V2 在 presenter 骨架内做润色，商品选择仍由规则+BGE决定。LLM 裁判继续默认影子/关闭，不直接决定商品。

**Tech Stack:** FastAPI, Pydantic Settings, OpenAI-compatible SDK, pytest, existing `scripts/llm_e2e_test.py`, existing V2 `answer_contract` guard.

---

## Current State

- `.env` 已有 `DEFAULT_LLM_PROVIDER`、`SILICONFLOW_API_KEY`、`SILICONFLOW_API_BASE`、`SILICONFLOW_CHAT_MODEL`、`KIMI_API_KEY`、`DOUBAO_API_KEY`、`DOUBAO_CHAT_MODEL`。
- `app/services/llm.py` 已引用 `settings.DEFAULT_LLM_PROVIDER` 和 `settings.SILICONFLOW_*`。
- `app/config.py` 当前没有声明 `DEFAULT_LLM_PROVIDER` 和 `SILICONFLOW_*`，会导致真实 LLM 初始化阶段属性缺失。
- V2 真实 LLM 入口在 `app/services/v2/agent.py::_try_generate_llm_text()`，只要不设置 `V2_DISABLE_LLM=1` 就会尝试调用 LLM。
- V2 已有格式护栏：`_is_usable_llm_text()` 和 `_llm_keeps_presenter_skeleton()`，LLM 文案不合格会回退本地 presenter。

## File Map

- Modify: `app/config.py`
  - 声明真实 LLM provider 配置字段。
  - 保持默认 provider 可控，建议 `siliconflow` 优先，`kimi/doubao` 作为 fallback。

- Modify: `.env.example`
  - 补齐 `DEFAULT_LLM_PROVIDER` 和 `SILICONFLOW_*` 示例，防止配置漂移。

- Modify: `app/services/llm.py`
  - 把 provider 解析收敛成一个内部函数，统一 chat/chat_stream/chat_sync 的 provider 行为。
  - 对 key/base/model 缺失给出清晰错误，不吞成“模型内容失败”。

- Create: `tests/test_llm_provider_config.py`
  - 测配置字段存在、默认 provider 合法、provider 缺 key 时失败信息清晰。

- Create: `scripts/llm_smoke_test.py`
  - 最小真实 API 探针：只发一句短 prompt，输出 provider/model/generation ok，不跑全链路。

- Modify: `scripts/llm_e2e_test.py`
  - 增加 LLM 接入验收阈值：真实 `generation_source=llm` 数量必须达到下限。
  - 继续记录 fallback 数量，但 fallback 不应成为“通过”的主要路径。

---

## Task 1: Provider 配置契约

**Files:**
- Modify: `app/config.py`
- Modify: `.env.example`
- Create: `tests/test_llm_provider_config.py`

- [ ] **Step 1: 写配置字段缺失的失败测试**

Create `tests/test_llm_provider_config.py`:

```python
from app.config import settings


def test_real_llm_provider_settings_exist():
    assert hasattr(settings, "DEFAULT_LLM_PROVIDER")
    assert hasattr(settings, "SILICONFLOW_API_KEY")
    assert hasattr(settings, "SILICONFLOW_API_BASE")
    assert hasattr(settings, "SILICONFLOW_CHAT_MODEL")


def test_default_llm_provider_is_supported():
    assert settings.DEFAULT_LLM_PROVIDER in {"siliconflow", "kimi", "doubao"}
```

- [ ] **Step 2: 跑测试确认失败**

Run:

```bash
.venv/bin/python -m pytest tests/test_llm_provider_config.py -q
```

Expected: FAIL，因为 `app/config.py` 尚未声明 `DEFAULT_LLM_PROVIDER` / `SILICONFLOW_*`。

- [ ] **Step 3: 在 `app/config.py` 补字段**

Add under LLM API config:

```python
    DEFAULT_LLM_PROVIDER: str = "siliconflow"

    SILICONFLOW_API_KEY: str = ""
    SILICONFLOW_API_BASE: str = "https://api.siliconflow.cn/v1"
    SILICONFLOW_CHAT_MODEL: str = "Qwen/Qwen2.5-72B-Instruct"
```

- [ ] **Step 4: 更新 `.env.example`**

Add:

```env
# 默认 LLM provider: siliconflow/kimi/doubao
DEFAULT_LLM_PROVIDER=siliconflow

# SiliconFlow API (OpenAI 兼容，推荐先用于真实 LLM 验证)
SILICONFLOW_API_KEY=your_siliconflow_api_key_here
SILICONFLOW_API_BASE=https://api.siliconflow.cn/v1
SILICONFLOW_CHAT_MODEL=Qwen/Qwen2.5-72B-Instruct
```

- [ ] **Step 5: 跑配置测试**

Run:

```bash
.venv/bin/python -m pytest tests/test_llm_provider_config.py -q
```

Expected: PASS.

---

## Task 2: LLMService provider 解析收敛

**Files:**
- Modify: `app/services/llm.py`
- Test: `tests/test_llm_provider_config.py`

- [ ] **Step 1: 增加 provider 解析测试**

Append to `tests/test_llm_provider_config.py`:

```python
import pytest

from app.services.llm import LLMService


def test_llm_service_resolves_supported_provider():
    service = LLMService()
    client, model_name = service._resolve_chat_client("siliconflow", model=None)
    assert client is service.siliconflow_client
    assert model_name == service._provider_model("siliconflow")


def test_llm_service_rejects_unknown_provider():
    service = LLMService()
    with pytest.raises(ValueError, match="Unsupported LLM provider"):
        service._resolve_chat_client("unknown-provider", model=None)
```

- [ ] **Step 2: 跑测试确认失败**

Run:

```bash
.venv/bin/python -m pytest tests/test_llm_provider_config.py -q
```

Expected: FAIL，因为 `_resolve_chat_client()` / `_provider_model()` 还不存在。

- [ ] **Step 3: 在 `LLMService` 增加解析函数**

Add methods inside `LLMService`:

```python
    def _provider_model(self, provider: str) -> str:
        if provider == "doubao":
            return settings.DOUBAO_CHAT_MODEL
        if provider == "kimi":
            return settings.KIMI_CHAT_MODEL
        if provider == "siliconflow":
            return settings.SILICONFLOW_CHAT_MODEL
        raise ValueError(f"Unsupported LLM provider: {provider}")

    def _resolve_chat_client(self, provider: str, model: Optional[str] = None):
        if provider == "doubao":
            return self.doubao_client, model or settings.DOUBAO_CHAT_MODEL
        if provider == "kimi":
            return self.kimi_client, model or settings.KIMI_CHAT_MODEL
        if provider == "siliconflow":
            return self.siliconflow_client, model or settings.SILICONFLOW_CHAT_MODEL
        raise ValueError(f"Unsupported LLM provider: {provider}")
```

- [ ] **Step 4: 替换 `_chat_without_cache()` 内部 provider 分支**

Change:

```python
provider = provider or settings.DEFAULT_LLM_PROVIDER
client, model_name = self._resolve_chat_client(provider, model)
```

Remove recursive fallback for unknown provider; unknown provider should fail clearly and let `_chat_with_fallback()` try next provider.

- [ ] **Step 5: 让 `chat_stream()` 使用默认 provider 和解析函数**

Change signature:

```python
provider: Optional[str] = None
```

Inside:

```python
provider = provider or settings.DEFAULT_LLM_PROVIDER
client, model_name = self._resolve_chat_client(provider, model)
```

- [ ] **Step 6: 跑配置与编译测试**

Run:

```bash
.venv/bin/python -m py_compile app/services/llm.py app/config.py
.venv/bin/python -m pytest tests/test_llm_provider_config.py -q
```

Expected: PASS.

---

## Task 3: 真实 API smoke test

**Files:**
- Create: `scripts/llm_smoke_test.py`

- [ ] **Step 1: 创建 smoke test 脚本**

Create `scripts/llm_smoke_test.py`:

```python
#!/usr/bin/env python3
import asyncio
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except Exception:
    pass

from app.config import settings
from app.services.llm import get_llm_service


async def main():
    provider = settings.DEFAULT_LLM_PROVIDER
    service = get_llm_service()
    text = await service.chat(
        [
            {"role": "system", "content": "你只输出一句中文，不要解释。"},
            {"role": "user", "content": "回复：LLM连接正常"},
        ],
        provider=provider,
        temperature=0.0,
        max_tokens=32,
        use_cache=False,
    )
    ok = "LLM" in text or "连接" in text or len(text.strip()) >= 2
    print(f"provider={provider}")
    print(f"model={service._provider_model(provider)}")
    print(f"response={text.strip()}")
    print(f"ok={ok}")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: 跑 smoke test**

Run without `V2_DISABLE_LLM=1`:

```bash
.venv/bin/python scripts/llm_smoke_test.py
```

Expected: prints `ok=True`.

---

## Task 4: V2 真实 LLM E2E 验收

**Files:**
- Modify: `scripts/llm_e2e_test.py`

- [ ] **Step 1: 增加 LLM 命中率阈值**

At the end of `scripts/llm_e2e_test.py`, after counts are printed, enforce:

```python
    min_llm_count = 10
    if llm_count < min_llm_count:
        print(f"❌ LLM真实润色次数不足: {llm_count} < {min_llm_count}")
        return 1
```

Keep `total_issues > 0` as failure.

- [ ] **Step 2: 跑真实 LLM E2E**

Run:

```bash
unset V2_DISABLE_LLM
.venv/bin/python scripts/llm_e2e_test.py
```

Expected:
- `LLM润色` 大于等于阈值。
- `问题` 为 0，或只剩明确可修的文案质量问题。
- 没有商品数量越界。
- 没有格式骨架丢失。

---

## Task 5: 真实 LLM 后的格式回归

**Files:**
- No code unless tests fail.

- [ ] **Step 1: 继续跑零 Token 基线，确保真实接入不破坏 fallback**

Run:

```bash
V2_DISABLE_LLM=1 .venv/bin/python scripts/test_v2_golden_cases.py
V2_DISABLE_LLM=1 .venv/bin/python scripts/nollm_e2e_test.py
```

Expected:
- Golden: `40/40 PASS`
- No-LLM E2E: current total all PASS.

- [ ] **Step 2: 跑浏览器真实 LLM 五流自检**

Start service without `V2_DISABLE_LLM=1`:

```bash
.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Manual/browser checks:
- 推荐流：有思考链路、商品卡、`## 综合建议`。
- 识图流：有图片锚点、商品卡、综合建议。
- 对比流：有 `## 对比结论`、`## 分项判断`、`## 怎么选` 或综合建议。
- 问答判断流：聚焦单品，不扩成推荐列表。
- 追问流：继承上轮候选/赢家，不空答。
- Console/page errors 为空。

---

## Task 6: 后续规划方向

**目标:** 真实 LLM 通过后，不急着让 LLM 决定商品，而是分层升级。

- [ ] **Phase A: LLM 只做文案润色**
  - 商品检索、排序、赢家选择继续由规则+BGE决定。
  - 评估标准：格式不崩、商品不乱、文案更自然。

- [ ] **Phase B: LLM 影子裁判追问范围**
  - 开启 `V2_FOLLOWUP_LLM_JUDGE_SHADOW_ENABLED=true`。
  - 只记录 LLM 对 IN/OUT/AMBIGUOUS 的判断，不应用。
  - 对比规则裁判和 LLM 影子裁判的分歧，人工看 20-50 条。

- [ ] **Phase C: 小流量主动裁判**
  - 只在 AMBIGUOUS 或规则低置信度时启用 LLM 裁判。
  - LLM 只判范围/引用对象，不决定商品。

- [ ] **Phase D: 质量评估集**
  - 建立 `llm_quality_cases`：格式、事实、推荐理由、不过度营销、无脏文本、无编造。
  - 将“推荐理由是否具体”纳入评分，但不牺牲当前格式合同。

- [ ] **Phase E: 前端状态可视化**
  - 显示当前候选池、焦点商品、累计约束、赢家继承。
  - 只作为调试/内测开关，不默认给普通用户展示。

---

## Self-Review

- Spec coverage: 覆盖真实 provider 配置、LLMService、smoke test、真实 E2E、零 Token 回归、浏览器复验和后续方向。
- Placeholder scan: 无 TBD/TODO/implement later。
- Type consistency: 使用现有 `settings`、`LLMService`、`scripts/llm_e2e_test.py`、`V2_DISABLE_LLM`、`generation_source` 命名。

