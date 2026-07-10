# Debug Session: image-chat-stream

Status: [OPEN]

## Symptoms
- Image recognition appears accurate, but the caution/notes look like generic reminders instead of knowledge-base or product-specific notes.
- Follow-up after image recognition may return the same kind of answer again.
- Sending sometimes appears to fail.
- Output appears very fast, possibly not streaming.

## Hypotheses
1. Image caution generation falls back to generic Presenter copy instead of using product-specific evidence or knowledge-base facts.
2. Image follow-up context is not carried into the chat request, so the backend treats the turn as a normal/repeated question.
3. The backend still emits SSE chunks, but the frontend buffers or replaces content at the end, making the UI look non-streaming.
4. A frontend exception or aborted request leaves `isStreaming` / send button state stuck, causing occasional “message not sent”.
5. The browser is running stale `chat.html` JavaScript after recent edits, so current behavior does not match the latest code.

## Evidence Log
- Browser snapshot showed image recognition itself is accurate enough:
  - Fresh image matched `Fresh馥蕾诗红茶凝时焕颜面膜`, similarity about `59.5%`.
  - MAC image matched `MACM.A.C魅可全新升级无瑕粉底`, similarity about `57.6%`.
- Browser snapshot showed the MAC image caution panel is generic:
  - `确认使用场景：下单前确认规格、实时价格和使用场景，敏感肌第一次上脸先局部测试`
  - `下单前看实时价：商品库价格是入库参考价，活动价、规格和赠品组合需要以详情页为准`
- Browser console showed repeated aborted chat stream requests:
  - `net::ERR_ABORTED http://127.0.0.1:8000/api/v1/chat/stream`
  - stack traces point to frontend `sendStreamingMessage`.
- Browser network log showed recent flow:
  - multiple successful `POST /api/v1/image-search/upload`
  - followed by failed `POST /api/v1/chat/stream failed=net::ERR_ABORTED`
  - targeted network log grep found aborted stream requests at entries `[86]`, `[92]`, `[96]`, `[98]`.
  - the same log also contains aborted `/chat` document and `/@vite/client` requests, so some aborts may be page navigation/reload related.
- DOM state after inspection:
  - `typingCount=0`
  - `sendDisabled=false`
  - last image follow-up returned a short judgement answer for the MAC product.
- Direct SSE probe:
  - `POST /api/v1/chat/stream` returned `text/event-stream`.
  - It emitted `37` SSE events and `26` message chunks.
  - All events completed in about `0.067s`, so the transport is streaming but the UX appears effectively non-streaming.
- Code evidence:
  - `/api/v1/chat.py` defaults to v2 agent via `getattr(settings, "USE_V2_AGENT", True)`.
  - `app/services/v2/agent.py` builds the full Presenter result first, then `_split_into_chunks(text, chunk_size=25)` and yields chunks without pacing.
  - v2 agent does not call `app/services/llm.py::chat_stream`.
  - `Presenter._present_image_identification` uses `_build_recommendation_pitfalls`.
  - `_build_recommendation_pitfalls` only uses product `_specs.pitfalls`, hardcoded category/product heuristics, and generic fallback.
  - The RAG knowledge-base pitfall extractor exists in `app/services/rag.py`, but is not invoked by the v2 image identification Presenter path.
  - Frontend `app/static/chat.html` currently has no `isStreaming`, `AbortController`, `currentRequest`, or `sendBtn.disabled` guard around `sendChatMessage` / `sendStreamingMessage`.

## Current Rule
- No business logic modification before runtime evidence is collected.

## Fix Applied
- V2 remains the main chat path. The fix reconnects missing V2 migration capabilities instead of reverting to the legacy agent.
- `app/services/v2/agent.py`
  - Image-context turns now try to enrich `turn.image_context["knowledge_pitfalls"]` from the existing RAG pitfall search.
  - Knowledge lookup failures are logged and ignored so image recognition still degrades gracefully.
- `app/services/v2/presenter.py`
  - Image identification pitfalls now prioritize product `_specs.pitfalls`, then knowledge-base pitfalls, then product/category-specific rules, then generic fallback.
  - Explicit `粉底液` now normalizes to `底妆`.
  - Base-makeup cautions now mention color shade, oxidation, wear test, cakiness, acne/closed-comedone risk, and makeup removal instead of generic price/spec checks.
- `app/static/chat.html`
  - Added `isSendingMessage` and `setSendingState` to guard duplicate sends and reset the button state.
  - Added `renderAssistantTextWithTypewriter` so the backend can stay contract-driven while the UI shows a readable typewriter effect.
  - SSE parse errors and backend `error` events are no longer conflated; backend errors can surface to the user.
- Tests:
  - Added `tests/test_v2_image_and_frontend_regressions.py`.
  - Added Golden static case `39` for frontend send guard and typewriter rendering.

## Verification
- `python3 -m unittest tests.test_v2_image_and_frontend_regressions` -> PASS 2/2.
- Golden Cases -> PASS 39/39.
- Legacy 20 cases -> PASS 20/20.
- Semantic Matrix -> PASS 35/35, using test-only dependency stubs for missing local `openai`.
- `python3 -m py_compile app/services/v2/presenter.py app/services/v2/agent.py scripts/test_v2_golden_cases.py tests/test_v2_image_and_frontend_regressions.py` -> PASS.
- `git diff --check` for touched files -> PASS.
- Browser smoke:
  - `setSendingState` and `renderAssistantTextWithTypewriter` are loaded.
  - Sending `300以内防晒` disables/busies the send button during response.
  - A visible partial typewriter state appeared (`挑性价比防...`).
  - After completion, send button returned to enabled and input cleared.
