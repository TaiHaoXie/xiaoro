# 小 ro 导购 Agent 接力说明

更新时间：2026-06-24

## 当前状态

项目已经恢复到通用导购链路，不是录屏用的固定三款版本。文字 Q 和识图 Q 都走真实后端链路：意图识别 -> RAG/商品检索 -> 排序/决策过程 -> LLM 或本地兜底生成 -> SSE 流式前端渲染。

当前重点已处理：

- 推荐题：后端候选够时默认输出 3 款；如果模型正文漏写第三款，后端会按已排序候选补齐，避免前端少展示。
- 对比题：按用户实际提问数量控制，两个商品就对比 2 个，明确三款才对比 3 个。
- 单品判断：只围绕当前识别到的 1 个商品判断，不再凑 3 款推荐。
- 识图题：ANESSA MEN 男士防晒已补入商品库、OCR 关键词、100 元左右价格口径和白底商品图。
- 前端：流式 Markdown 预渲染、商品图按商品名匹配插入，对比场景只在“分别适合”区域插图。
- 决策过程：前端展示“拆解需求、检索商品、工具链路检查、打分器、反思复核、最终建议”，不再展示“未就绪能力走可解释降级”这类不适合用户看的话。

## 目前仍然偏弱的问题

1. 意图识别还是偏规则化

   现在主要靠关键词、预算、品类、商品别名和少量上下文规则。对常见首页问题已经兜住，但用户问法一变，比如省略商品名、连续追问、反问、混合诉求，就可能还需要补规则或更稳的分类器。

2. 情绪和用户状态识别还比较粗

   已经能识别着急、烦躁、担心刺激、纠结、预算漂移、风险规避等状态，但目前是关键词层。它还不能很好区分“轻微担心”和“强烈不满”，也不能稳定影响回答长度、语气和风险提示强度。

3. 商品库仍是体验上限

   推荐质量很依赖 seed 数据。现在精华、防晒、底妆等核心演示链路能跑，但更多品类、更多肤质和更多价格带还需要继续补真实 SKU、图片、别名、成分风险、规格和价格口径。

4. 摘要风格还有优化空间

   当前 prompt 已避免机械复述和广告腔，但“像点点一样自然、有判断感”的文案仍需要继续用真实问题回放调优。重点是让第一段给出取舍逻辑，而不是空承接。

5. 评测集还不够系统

   已有单测覆盖推荐数量、对比格式、识图排序、预算保护、前端插图等关键点，但还缺一套“用户体验回归集”：同一批真实用户问法跑完后，检查意图、商品数量、预算、图片和口吻。

## 建议后续补进方向

1. 做意图/情绪评测集

   建议新增 `tests/fixtures/user_queries.json`，沉淀 50-100 条真实问法，每条标注：

   - intent：推荐 / 对比 / 单品判断 / 知识咨询 / 价格咨询
   - scenario_intent：需求探索 / 比价决策 / 明确购买
   - user_state：着急 / 烦躁 / 担心 / 纠结 / 风险规避
   - expected_count：推荐 3、对比 2 或 3、单品 1

   这样后面改 prompt 或规则时，不会又把主链路改崩。

2. 把情绪识别从关键词升级为“状态层”

   目前 `extract_user_state` 已经有基础结构，下一步可以扩成：

   - frustration_level：none / low / mid / high
   - decision_state：exploring / comparing / hesitating / ready_to_buy
   - risk_focus：budget / irritation / authenticity / after_sales / ingredient
   - response_strategy：短答安抚 / 直接结论 / 详细解释 / 风险优先

   然后让 prompt 和决策过程都消费这个状态，而不是只展示出来。

3. 补商品别名和单品题库

   每补一个重点商品，建议至少补：

   - 标准商品名
   - 口语别名 / OCR 关键词
   - 白底图
   - 价格与规格
   - 不适合人群 / 成分风险
   - 常见问法，例如“敏感肌能不能用”“油皮会不会闷”“通勤适合吗”

4. 优化多轮上下文

   当前上下文能记录预算、品类、肤质、诉求和最近商品，但还需要更明确地区分：

   - 用户本轮新条件
   - 历史条件
   - 被用户否定过的条件
   - 只用于上一轮、不该污染后续的问题

   这是减少“预算漂移”和“上一轮商品混进下一轮”的关键。

5. 增加一键体验回归脚本

   建议做一个脚本，例如 `scripts/run_demo_regression.py`，自动跑：

   - 干敏肌 1000 左右抗初老精华推荐
   - 混油皮通勤持妆粉底推荐
   - 兰蔻小黑瓶 vs 小棕瓶对比
   - ANESSA MEN 图片 + 敏感肌能不能用

   输出每题的 intent、products 数量、正文商品覆盖、是否有图片、是否超预算。这样队友接力时不用手动点半天。

## 接力运行方式

先确认拉到最新提交：

```bash
git pull origin main
git log -1 --oneline
```

如果仓库里还能看到 `add_products.py`、`add_skincare_products.py` 或 `app/static/product-images/seed_backup_013900/`，说明没有拉到最新清理后的版本。

本地启动：

```bash
.venv/bin/pip install -r requirements.txt
docker compose up -d postgres redis
.venv/bin/python scripts/seed_beauty_products.py --reset
.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

前端地址：

```text
http://127.0.0.1:8000/static/chat.html
```

如果出现“没有在已核验商品库里命中真实 SKU”，优先不是改 prompt，而是确认数据库是否导入了 `data/beauty_products_seed.json`。当前 seed 里有 57 条美妆护肤商品，其中 9 条防晒 SKU，7 条价格在 300 元以内。

推荐回归命令：

```bash
.venv/bin/python -m unittest tests/test_homepage_prompt_intents.py tests/test_prompt_and_anessa_seed.py tests/test_image_search_ranking.py tests/test_compare_format.py tests/test_budget_response_guard.py tests/test_product_formatting.py tests/test_intent_entities.py tests/test_decision_tree_guards.py tests/test_stream_empty_fallback.py
node --test tests/chat_render_test.mjs
.venv/bin/python -m py_compile app/api/v1/image_search.py app/services/intent.py app/prompts/test_intent_classifier.py app/services/agent.py app/services/decision_tree.py app/services/prompts.py app/api/v1/chat.py
```

## 重点文件

- `app/services/intent.py`：旧意图、实体、肤质、预算、商品别名、用户状态识别。
- `app/prompts/test_intent_classifier.py`：场景意图分类，判断明确购买 / 比价决策 / 需求探索等。
- `app/services/agent.py`：推荐、对比、单品判断、流式输出和兜底生成主链路。
- `app/services/decision_tree.py`：前端思考链路/决策过程数据。
- `app/api/v1/image_search.py`：图片识别、OCR、视觉召回和商品匹配。
- `app/static/chat.html`：前端聊天页、流式 Markdown、商品图插入和对比展示。
- `data/beauty_products_seed.json`：商品 seed 数据。
- `tests/`：当前主链路回归测试。

## 接力原则

- 不要为了录屏写固定商品或前端硬删。
- 推荐数量规则放后端：推荐 3、对比按用户提问 2/3、单品 1。
- 商品事实优先来自 seed/数据库，不要让模型编价格、规格、适合人群。
- 文案要有导购判断，不要写空泛广告句。
- 每修一个体验问题，尽量补一条测试，避免后面再回退。
