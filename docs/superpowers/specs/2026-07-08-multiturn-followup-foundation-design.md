# 二期：多轮追问地基 — 设计文档

> 状态：设计定稿，**架构部分待收数后实施**；展示层安全补丁在收数前先行落地。
> 日期：2026-07-08
> 背景：一期单轮导购已稳定（121单测 + 35条零-LLM E2E 全绿）。本设计针对**多轮追问**链路的系统性缺陷，是二期"像真人导购"的地基。

---

## 一、问题背景（大白话）

一期做到了"帮你搜商品"，单轮问答很稳。二期目标是"帮你做决策"，核心依赖**多轮能记住上下文、能正确回指**。

当前多轮链路的总病根：**服务端完全没有"候选集缓存"**——session 只存对话纯文本，"上一轮到底给用户展示了哪几款"只能靠正则重新解析 assistant 文本 + 重新查库还原。这导致系统分不清用户追问时到底是：

- **(A) 在上轮推荐的几款里挑**："这几个里哪个最适合屏障受损" / "第二款主要功效是啥"
- **(B) 就某个新商品扩展提问**："兰蔻小黑瓶怎么样"（点名一个上轮没出现的新品）
- **(C) 改条件重新搜**："那干皮的呢"（换了肤质，要重新推荐）

现在对这三种场景几乎都用"按名字/品牌去库里重新搜"这一招，导致 A 跳出候选集、B 召回错商品、C 条件重置失效。

---

## 二、Bug 审计清单（16项，四层）

### 🔴 严重（架构级，收数后实施）

| # | 层 | Bug | 触发场景 | 位置 |
|---|---|---|---|---|
| 1 | 选内选外 | SUITABILITY/PRICE/INGREDIENT 等 6 个 followup_type 没有专属分支，全掉进"按品牌重新检索"，把上轮没展示的同品牌商品塞进来 | "这几款哪个最适合干皮" | `agent.py` `_retrieve_for_followup` 492-610 |
| 2 | 选内选外 | `_seen_in_history` 品牌级误伤：靠品牌名子串判"已展示"，上轮出现一款理肤泉，"换一批"会把全部理肤泉删光 | "换一批"/更多选项 | `agent.py` 612-632 |
| 3 | 上下文继承 | 改条件失效：referenced_products 分支只传 category+brand，丢掉新肤质，"油皮精华→那干皮的呢"还锚在油皮商品 | 改肤质/改条件追问 | `agent.py` 581-588 |
| 4 | 指代 | 跳轮指代割裂：序号走"仅最近一条"、代词走"最近5条累积"，"推荐ABC→聊别的→第二款"几乎必错 | 中间插话后回指 | `turn_parser.py` 399-406 |
| 5 | 指代 | 同品牌多款丢失：`str.find()` 只取首次位置，上轮"雅诗兰黛小棕瓶+雅诗兰黛面霜"，面霜永远召不回 | 同品牌两款并存 | `turn_parser.py` `_alias_index` 691-697 |
| 6 | 指代 | name_clue 跨品牌重名（一期后期改动引入）："粉水"是兰蔻/贝德玛/舒妍三家共用，丢了品牌上下文可能张冠李戴 | 昵称跨品牌 | `turn_parser.py` 775 |
| 7 | 根因 | 完全没有候选集缓存：session 只存纯文本，上轮展示集只能靠正则还原+重查库 | — | `chat.py` `_V2_SESSION_HISTORIES` 197 |

### 🟡 中等（用户可感知）

| # | 层 | Bug | 位置 |
|---|---|---|---|
| 8 | 上下文继承 | "平替"仍继承旧品牌："雅诗兰黛平替"被锁成"更便宜的雅诗兰黛"，与换品牌意图矛盾 | `turn_parser.py` 435-439 |
| 9 | 上下文继承 | CHEAPER 预算根本没降：纯上限锚点时"便宜点"得到同一个上限 | `turn_parser.py` 452 |
| 10 | 展示 | SUITABILITY 没有"能用/不能用"结论，直接堆原始字段（走 JUDGEMENT 反而有结论，链路不一致）| `presenter.py` 135-147 |
| 11 | 展示 | "官方/标注的适合肤质："生硬前缀 | `presenter.py` 145 |
| 12 | 展示 | 字段半句截断：`suitable_skin[:28]` 裸切不 rstrip，留悬空顿号"…干燥性敏感肌、" | `presenter.py` 1292 |
| 13 | 展示 | PRICE 分支唯独缺 FOOTER_NOTE，最该有"参考价"免责声明的地方反而没有 | `presenter.py` 66-70 |
| 14 | 选内选外 | image_context+CHEAPER 几乎是死代码：普通 CHEAPER 先 return 了 | `agent.py` 533-571 |
| 15 | 指代 | 序号越界静默锚到第一款："第五款"但只有3款→给第1款，方向错 | `turn_parser.py` 400-406 |

### ⚪ 轻微（记录，暂不改）

| # | Bug |
|---|---|
| 16 | 历史窗口口径不统一（-5/-6/-8 三套）；肤质字典顺序取首个不处理否定；分支行首空格不一致 |

### 因果链

这16个不是散点，是一条因果链：
**#7（没候选集缓存）是总病根 → #1#2#3（分流全错）→ #4#5#6（指代还原不准放大偏差）→ #10~#13（展示层堆字段）**

---

## 三、核心设计：候选集缓存 + 选内/选外仲裁

### 3.1 六个已定决策

1. **选内 vs 选外**：规则判定 + LLM 兜底（AMBIGUOUS 时；零 LLM 模式保守为选内）
2. **候选集边界**：严格最近一轮
3. **选内排序**：维度词匹配产品事实字段
4. **候选集存储**：服务端 session 存 last_candidates
5. **改动范围**：架构 + 话术层一起改
6. **打分兜底**：维度词匹配不到 → BGE 向量对候选集语义排序

### 3.2 新增概念 FollowupScope

在 `models.py` 加枚举：
- `IN_CANDIDATES = "in_candidates"` — 在上轮候选集内挑/问
- `OUT_OF_CANDIDATES = "out_of_candidates"` — 跳出上轮集，重新找新商品
- `AMBIGUOUS = "ambiguous"` — 不确定，交给 LLM 裁决；零 LLM 模式下默认保守 IN_CANDIDATES

CanonicalTurn 新增字段 `followup_scope: FollowupScope`。

### 3.3 数据流

```
用户追问
  ↓
TurnParser 判 followup_scope：
  IN_CANDIDATES 强信号：
    - 序号指代（第N款）且上轮有展示
    - 近指代词（这款/它）+ 无新商品名
    - 范围限定词（这几个/这里面/其中/从这几个）
    - 集内比较级（哪个最X/哪款更X）+ 上轮 compare/recommendation 且返回 ≥2 款
    - 承接式（那第二款呢/它怎么样）
  OUT_OF_CANDIDATES 强信号：
    - MORE_OPTIONS/CHEAPER/HIGHER_BUDGET
    - 提到不在 last_candidates 里的新商品名/品牌
    - 品类重置（上轮精华，这轮"那防晒呢"）
    - 否定重启（都不喜欢/换一个/重新推荐）
  AMBIGUOUS：其余模糊情况
  ↓
agent._retrieve_for_followup 分流：
  ├ IN_CANDIDATES → 从 session.last_candidates 取，不查库
  │   ├ 单点指代（第N款/点名）→ 直接返回那一款
  │   └ 集内比较（哪个最X）→ _rank_in_candidates：
  │       ① 维度词表匹配字段打分 → 命中最多的第一
  │       ② 全都没命中 → BGE 向量对候选集语义排序兜底
  │       ③ 向量也拉不开差距 → 诚实说"这几款差不多，主要差别在X"
  ├ OUT_OF_CANDIDATES → 现有检索新商品逻辑（排除已见，用 ID 而非品牌子串）
  └ AMBIGUOUS → LLM 判 in/out；零 LLM 默认 IN_CANDIDATES
  ↓
present_followup 重写话术（见 3.6）
```

### 3.4 Session 缓存新增 last_candidates

`chat.py` 的 `_V2_SESSION_HISTORIES` 每个 session 除了 `history`（消息列表），再存 `last_candidates: List[Dict]`（最近一次 assistant 发出 products 事件时的完整商品数据快照，最多 6 条）。

- **写入时机**：agent 处理完每轮产生 products 事件时写入
- **清空/覆盖**：主动发起新查询（非 followup）时重置；followup 且 scope=OUT_OF_CANDIDATES 返回新列表时覆盖
- **读取时机**：TurnParser 判定 scope 时读取（判断新提到的商品名是否在候选集内）；agent 分流时按 ID 取商品

**关键收益**：#1#2#3#4 从"靠脆弱文本正则还原"升级为"按 product_id 精确命中"，`_seen_in_history` 也改用 ID 而非品牌子串（修 #2 品牌误伤）。

### 3.5 集内排序：维度词 → 字段匹配（_rank_in_candidates）

针对"哪个最适合屏障受损/哪个更清爽/哪个遮瑕最好"这类集内比较，本地打分，不查 DB、不走 chat LLM：

| 用户维度词 | 匹配字段 | 加分逻辑 |
|---|---|---|
| 屏障/敏感/泛红/干敏/敏皮/脆弱 | suitable_skin + concerns_list + safety_notes | 命中"屏障修护/敏感肌/舒缓/泛红"+N |
| 油皮/控油/清爽/不黏/不油腻 | texture_notes + suitable_skin | 命中"油皮/清爽/控油/不黏腻"+N |
| 干皮/保湿/滋润/补水 | suitable_skin + concerns_list | 命中"干皮/保湿/滋润"+N |
| 遮瑕/持妆/不脱妆 | suitable_skin + concerns_list + category(粉底) | 命中"遮瑕/持妆"+N |
| 便宜/性价比/学生/平价 | price_val | 价格最低 +N |
| 贵/高端/贵妇 | price_val | 价格最高 +N |
| 美白/提亮/淡斑/去黄 | concerns_list + key_ingredients_list | 命中"美白/烟酰胺/377/VC"+N |
| 抗老/淡纹/紧致/初老 | concerns_list + key_ingredients_list | 命中"抗老/玻色因/A醇/视黄醇/胶原"+N |
| 防晒/通勤/户外 | category + concerns_list | category=防晒 且命中"通勤/户外"+N |

**规则**：命中维度最多的排第一，返回 top=1（单点答案），其余作备选。
**兜底链**：全都没命中维度词 → BGE embedding 对候选集做语义相似度排序（零 chat token）→ 向量也拉不开差距 → 诚实说"这几款差不多，主要差别在X"，绝不瞎排。

### 3.6 展示层重写（present_followup）

- **SUITABILITY**（"我混干能用吗"）：先给结论"能用/谨慎用/不建议"，再给 1-2 条理由，不倒原始字段（复用 present_judgement 的结论风格）
- **去生硬前缀**："官方/标注的适合肤质：" → 融进句子
- **去半句截断**：字段裸切后 rstrip 掉悬空顿号
- **PRICE 补 FOOTER_NOTE**
- **集内选优**：新增文案模板"这几款里，X 更适合你说的 Y，因为…"，复用 compare 结论风格但聚焦 1 款
- 不新起 answer_mode，仍走 FOLLOWUP，避免协议复杂度

### 3.7 零 LLM 兼容

- 所有规则信号 + 维度词匹配 + BGE 向量兜底，全程零 chat token
- AMBIGUOUS 降级为 IN_CANDIDATES（保守不跳出）
- 预留 LLM 裁决接口：以后上 chat LLM 时，AMBIGUOUS 才升级给 LLM 判 in/out

---

## 四、实施分期

### 阶段 0（收数前，安全补丁，已批准先行）

只改展示层，改动隔离、零架构风险，有 121 单测 + 35 E2E 兜底：

- **#11**：去掉 `presenter.py` 145 行"官方/标注的适合肤质："前缀
- **#12**：`presenter.py` 1292 行 `suitable_skin[:28]` 加 rstrip 去悬空顿号
- **#13**：`presenter.py` PRICE 分支补 FOOTER_NOTE

验收：跑 nollm_e2e_test.py（35条）+ pytest（121条）全绿。

### 阶段 1（收数后，架构地基）

按 3.2-3.7 实施，改动文件：

| 文件 | 改动 |
|---|---|
| `models.py` | 加 FollowupScope 枚举，CanonicalTurn 加 followup_scope 字段 |
| `turn_parser.py` | 加 scope 判定；修 #4 跳轮、#5 同品牌多款（`_alias_index` 改支持多位置）、#6 昵称带品牌、#8 平替不继承品牌、#9 CHEAPER 真降价、#15 序号越界 clamp |
| `intent_classifier.py` | scope 判定与 turn_parser 对齐 |
| `chat.py` | session 缓存加 last_candidates，products 事件写入 |
| `agent.py` | 改造 _retrieve_for_followup 分流；加 _rank_in_candidates；`_seen_in_history` 改 ID 匹配（修 #2）；修 #3 传 skin_type、#14 image CHEAPER 顺序；products 回写 session |
| `presenter.py` | 加集内选优话术；#10 SUITABILITY 先给结论 |
| `nollm_e2e_test.py` | 加集内选优/改条件/换一批的回归用例 |

### 阶段 2（更远，二期其余能力）

依赖本地基：用户画像/偏好记忆、对比差异化标签、评论总结与风险、搭配推荐。

---

## 五、风险与回归策略

- **阶段 0 风险极低**：纯展示层字符串处理，测试全覆盖
- **阶段 1 风险中等**：动主链路。回归策略——先加 last_candidates 缓存（只写不读，不改行为），验证无回归后再逐个切换分流逻辑到"读缓存"；每改一个 followup_type 分支跑一次全量测试
- **对抗测试**：阶段 1 需扩充 E2E 覆盖跳轮指代、同品牌多款、改条件、换一批品牌误伤等场景
