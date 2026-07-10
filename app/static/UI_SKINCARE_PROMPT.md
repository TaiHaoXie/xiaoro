# 🌸 护肤品类决策过程可视化UI优化 - Gemini Prompt

## 给Gemini的完整Prompt（直接复制）

---

```
你是一位专注于女性美妆护肤产品的UI/UX设计师，擅长小红书、完美日记、丝芙兰等女性向产品的设计语言。

请重新设计以下"AI护肤推荐决策过程"组件，要求：

## 🎯 核心目标
为女性用户打造专业、温暖、可信赖的护肤推荐决策展示，让用户理解AI为什么推荐这款产品。

## 💎 设计风格
- **女性审美**：柔和、精致、有温度
- **莫兰迪色系**：温柔粉彩、低饱和度
- **专业可信**：成分分析、肤质匹配一目了然
- **参考产品**：小红书护肤笔记、丝芙兰产品页、雅萌护肤仪App

## 🎨 配色方案（莫兰迪色系）

```css
:root {
  /* 主色 - 莫兰迪粉紫 */
  --primary: #D4BCC8;        /* 燕麦粉紫 */
  --primary-light: #E8D5DD;  /* 浅粉紫 */
  --primary-dark: #C5A8B8;   /* 深粉紫 */

  /* 辅助色 */
  --accent: #B5C4D1;         /* 雾霾蓝 */
  --success: #C8D4C2;        /* 鼠尾草绿 */
  --warning: #E8C8B8;        /* 脏粉 */
  --neutral: #D8D0C8;        /* 燕麦米 */

  /* 背景 */
  --bg-primary: #FAF8F6;     /* 暖白 */
  --bg-secondary: #F5F2EE;   /* 浅灰 */
  --bg-card: #FFFFFF;        /* 纯白 */

  /* 文字 */
  --text-primary: #5A5A5A;   /* 深灰 */
  --text-secondary: #8A8A8A; /* 浅灰 */
  --text-hint: #B8B8B8;      /* 提示灰 */

  /* 边框/分割 */
  --border: #EBE8E4;         /* 浅灰边 */
  --divider: #F0EDE9;        /* 分割线 */
}
```

## 📐 组件设计

### 整体结构
```
┌─────────────────────────────────────────────────────┐
│  AI推荐逻辑                    [查看详情 ▼]    │
│  为你找到最适合的护肤方案                           │
│  ┌───────────────────────────────────────────────┐  │
│  │  💧 肤质分析                  匹配度 95%     │  │
│  │  干性肌肤 · 有干燥细纹困扰 · 偏好温和产品     │  │
│  │  ┌─────────────────────────────────────────┐  │  │
│  │  │ ✅ 你的肤质特征                        │  │  │
│  │  │    洗后紧绷 · 易脱皮 · 细纹明显        │  │  │
│  │  └─────────────────────────────────────────┘  │  │
│  ├───────────────────────────────────────────────┤  │
│  │  🔍 成分匹配                  有效性 88%     │  │
│  │  二裂酵母 · 透明质酸 · 修护保湿成分         │  │
│  │  ┌─────────────────────────────────────────┐  │  │
│  │  │ ✅ 关键成分解析                        │  │  │
│  │  │    二裂酵母 10% → 修护肌肤屏障          │  │  │
│  │  │    透明质酸 1% → 深层补水保湿          │  │  │
│  │  └─────────────────────────────────────────┘  │  │
│  ├───────────────────────────────────────────────┤  │
│  │  📊 产品对比                  综合评分 4.7/5 │  │
│  │  从12款精华中筛选，兰蔻小黑瓶排名 TOP1      │  │
│  │  ┌─────────────────────────────────────────┐  │  │
│  │  │ 对比数据                               │  │  │
│  │  │    价格: ¥1080 | 规格: 50ml | 评分: 4.7│  │  │
│  │  └─────────────────────────────────────────┘  │  │
│  ├───────────────────────────────────────────────┤  │
│  │  ✨ 最终推荐                  推荐指数 ★★★★★│  │
│  │  兰蔻小黑瓶精华 · 最适合你的抗初老需求       │  │
│  │  ┌─────────────────────────────────────────┐  │  │
│  │  │ 💡 推荐理由                             │  │  │
│  │  │    ✅ 100% 适合干性肌肤                 │  │  │
│  │  │    ✅ 含有修护屏障的二裂酵母             │  │  │
│  │  │    ✅ 质地清爽不黏腻，四季可用           │  │  │
│  │  └─────────────────────────────────────────┘  │  │
│  └───────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────┘
```

---

## ⚠️ 重要设计要求（必须遵守）

### 1. 去掉所有Emoji，使用专业图标
**禁止使用**：💧🔍📊✨✅📦等emoji
**使用替代方案**：
- SVG图标（线性风格，2px线宽）
- CSS几何图形
- 或使用Lucide/Feather图标库

### 2. 图标系统（专业线性图标）

| 步骤类型 | SVG图标 | 颜色 |
|---------|---------|------|
| 肤质分析 | 水滴轮廓 | #B5C4D1 |
| 成分匹配 | 放大镜轮廓 | #C8D4C2 |
| 产品对比 | 柱状图轮廓 | #D4BCC8 |
| 最终推荐 | 星星轮廓 | #E8C8B8 |

### 3. 专业图标示例（SVG代码）

```html
<!-- 水滴图标（肤质分析） -->
<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
  <path d="M12 2.69l5.66 5.66a8 8 0 1 1-11.31 0z"/>
</svg>

<!-- 放大镜图标（成分匹配） -->
<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
  <circle cx="11" cy="11" r="8"/>
  <path d="m21 21-4.35-4.35"/>
</svg>

<!-- 图表图标（产品对比） -->
<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
  <path d="M12 20V10"/>
  <path d="M18 20V4"/>
  <path d="M6 20v-4"/>
</svg>

<!-- 星星图标（最终推荐） -->
<svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor" stroke="none">
  <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/>
</svg>
```

### 步骤卡片设计

```css
.decision-step {
  background: #FFFFFF;
  border: 1px solid #EBE8E4;
  border-radius: 16px;
  padding: 16px;
  margin-bottom: 12px;
  box-shadow: 0 2px 8px rgba(212, 188, 200, 0.08);
  transition: all 0.3s ease;
}

.decision-step:hover {
  box-shadow: 0 4px 16px rgba(212, 188, 200, 0.12);
  transform: translateY(-2px);
}
```

### 匹配度/进度条

```css
.match-badge {
  display: inline-flex;
  align-items: center;
  padding: 4px 12px;
  border-radius: 20px;
  font-size: 12px;
  font-weight: 500;
  background: linear-gradient(135deg, #E8D5DD, #D4BCC8);
  color: #FFFFFF;
}

.match-excellent { background: linear-gradient(135deg, #C8D4C2, #A8C8B8); }
.match-good { background: linear-gradient(135deg, #D4BCC8, #C5A8B8); }
.match-fair { background: linear-gradient(135deg, #E8C8B8, #D8C0B0); }
```

### 展开区域（圆角卡片）

```css
.detail-card {
  background: linear-gradient(135deg, #FAF8F6, #F5F2EE);
  border-radius: 12px;
  padding: 12px 16px;
  margin-top: 12px;
  border-left: 3px solid #D4BCC8;
}

.detail-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 0;
  font-size: 13px;
  color: #5A5A5A;
}

/* 使用SVG勾选图标替代emoji */
.detail-item::before {
  content: '';
  display: inline-block;
  width: 16px;
  height: 16px;
  background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 24 24' fill='none' xmlns='http://www.w3.org/2000/svg'%3E%3Cpath d='M20 6L9 17l-5-5' stroke='%23C8D4C2' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E");
  background-size: contain;
  background-repeat: no-repeat;
}
```

### 折叠头部（柔和风格）

```css
.decision-header {
  background: linear-gradient(135deg, rgba(212, 188, 200, 0.1), rgba(181, 196, 209, 0.1));
  border-radius: 16px;
  padding: 14px 20px;
  cursor: pointer;
  display: flex;
  justify-content: space-between;
  align-items: center;
  transition: all 0.3s ease;
}

.decision-header:hover {
  background: linear-gradient(135deg, rgba(212, 188, 200, 0.15), rgba(181, 196, 209, 0.15));
}

.header-title {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 15px;
  font-weight: 500;
  color: #5A5A5A;
}

.toggle-icon {
  color: #D4BCC8;
  transition: transform 0.3s ease;
}

.toggle-icon.open {
  transform: rotate(180deg);
}
```

### 成分标签（药丸风格）

```css
.ingredient-tag {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 10px;
  border-radius: 12px;
  font-size: 11px;
  background: rgba(212, 188, 200, 0.15);
  color: #8A7A82;
  margin: 2px;
}

.ingredient-tag.highlight {
  background: rgba(200, 212, 194, 0.2);
  color: #6A8A7A;
}
```

### 推荐理由卡片

```css
.reason-card {
  background: linear-gradient(135deg, rgba(212, 188, 200, 0.08), rgba(200, 212, 194, 0.08));
  border-radius: 12px;
  padding: 14px;
  margin-top: 12px;
}

.reason-item {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 8px 0;
  font-size: 13px;
  color: #5A5A5A;
  line-height: 1.6;
}

.reason-icon {
  flex-shrink: 0;
  width: 20px;
  height: 20px;
  background: #C8D4C2;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
}
```

## 🎬 动画效果

```css
/* 步骤卡片淡入 */
@keyframes fadeInUp {
  from {
    opacity: 0;
    transform: translateY(20px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.decision-step {
  animation: fadeInUp 0.5s ease backwards;
}

.decision-step:nth-child(1) { animation-delay: 0.1s; }
.decision-step:nth-child(2) { animation-delay: 0.2s; }
.decision-step:nth-child(3) { animation-delay: 0.3s; }

/* 展开动画 */
@keyframes expand {
  from {
    opacity: 0;
    max-height: 0;
  }
  to {
    opacity: 1;
    max-height: 500px;
  }
}

.detail-card {
  animation: expand 0.3s ease;
}
```

## 📦 交付要求

请生成完整的HTML/CSS/JS代码，包含：

1. **完整组件**：4个护肤决策步骤示例
2. **交互功能**：折叠/展开、步骤动画
3. **莫兰迪配色**：使用CSS变量定义
4. **响应式设计**：移动端适配
5. **护肤场景**：成分、肤质、功效展示
6. **⚠️ 禁止使用Emoji**：全部使用SVG图标或CSS图形
7. **大厂品质**：参考小红书、丝芙兰的设计细节

## 🌟 设计参考

- 小红书护肤笔记：温柔专业的图文排版
- 丝芙兰产品页：清晰的产品信息展示
- 雅萌App：护肤流程的可视化引导
- 完美日记小程序：柔和的配色和卡片设计

---

开始生成代码，确保视觉品质达到女性护肤产品的专业水准。
```

---

## 简洁版Prompt（备用）

```
优化护肤推荐决策过程可视化组件：

要求：
1. 女性审美：柔和、精致、有温度
2. 莫兰迪色系：#D4BCC8 燕麦粉紫为主色
3. 护肤场景：展示肤质、成分、功效分析
4. 大厂品质：参考小红书、丝芙兰设计
5. 圆角卡片：16px大圆角，柔和阴影
6. ⚠️ 禁止使用Emoji：使用SVG线性图标

具体设计：
- 头部：渐变背景 + 折叠按钮
- 步骤图标：SVG水滴、放大镜、图表、星星
- 匹配度：渐变药丸标签
- 展开区：浅灰背景 + 左侧彩色边条
- 理由卡片：SVG勾选图标 + 温柔文案

生成完整的HTML/CSS/JS代码。
```

---

## 关键设计词

发送给Gemini时加上这些关键词：

```
女性向产品、护肤美妆、莫兰迪配色、温柔专业、
小红书风格、丝芙兰品质、大厂设计、
16px圆角、柔和阴影、渐变背景、药丸标签、
组件化设计、移动端友好、
禁止emoji、SVG图标、线性风格、2px线宽
```
