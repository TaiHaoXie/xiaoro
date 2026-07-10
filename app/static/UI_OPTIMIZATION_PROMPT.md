# 🎨 决策过程可视化UI优化 - Gemini Prompt

## 直接复制给Gemini

---

```
你是一位专注于数据产品的资深UI/UX设计师，擅长 Linear、Notion、Stripe 等产品的简洁专业风格。

请重新设计以下"AI决策过程可视化"组件，要求：

## 🎯 核心目标
将"AI推荐逻辑"以清晰、专业、可信的方式展示给用户，体现技术透明度和可复现性。

## ❌ 当前问题
1. 使用Emoji图标（🔍📦📊）显得不够专业
2. 整体风格偏消费级，缺乏B2B产品的严谨感
3. 信息密度低，没有充分利用空间
4. 缺乏"数据驱动"的视觉语言

## ✅ 设计方向
参考以下产品的设计语言：
- **Linear**：极简、高效、深色模式友好
- **Notion Database**：结构化数据展示
- **Stripe Dashboard**：数据可视化
- **Vercel**：技术感、透明度

## 📐 具体设计要求

### 整体布局
```
┌─────────────────────────────────────────────────────────┐
│  AI推荐逻辑                              [折叠/展开]    │
│  ┌───────────────────────────────────────────────────┐  │
│  │ ○ 需求分析                    ████████░░ 85%     │  │
│  │   识别购物意图、预算范围、品牌偏好               │  │
│  │   └─ 意图类型: product_search | 预算: ¥5000    │  │
│  ├───────────────────────────────────────────────────┤  │
│  │ ◆ 商品检索                    ████████ 100%     │  │
│  │   从商品库中筛选匹配产品                         │  │
│  │   └─ 候选数: 23款 → 筛选后: 5款                 │  │
│  ├───────────────────────────────────────────────────┤  │
│  │ ◆ 匹配度分析                  ████████░░ 82%     │  │
│  │   计算每款商品与需求的匹配程度                   │  │
│  │   └─ 小米14 Ultra: 75% | Redmi K70 Pro: 95%    │  │
│  ├───────────────────────────────────────────────────┤  │
│  │ ◆ 智能排序                    ████████░░ 88%     │  │
│  │   按匹配度和性价比进行排序                       │  │
│  │   └─ 排序依据: 匹配度(40%) + 性价比(35%) + ... │  │
│  ├───────────────────────────────────────────────────┤  │
│  │ ● 最终推荐                    ████████ 95%      │  │
│  │   为你推荐最合适的商品                           │  │
│  │   └─ 推荐: Redmi K70 Pro | 理由: 性价比优秀    │  │
│  └───────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
```

### 图标系统（几何图形）

| 步骤类型 | 图标 | 说明 |
|---------|------|------|
| 分析类 | ○ | 空心圆，12px |
| 处理类 | ◆ | 实心菱形，10px |
| 计算类 | ◇ | 空心菱形，12px |
| 输出类 | ● | 实心圆，10px |

颜色统一使用：`#8B9DC3`（莫兰迪蓝）

### 步骤卡片设计

```css
.decision-step {
  background: #FFFFFF;
  border: 1px solid #E8E6E3;
  border-radius: 8px;
  padding: 12px 16px;
  margin-bottom: 8px;
  position: relative;
}

.decision-step::before {
  /* 左侧状态条 */
  content: '';
  position: absolute;
  left: 0;
  top: 12px;
  bottom: 12px;
  width: 3px;
  background: #8B9DC3;
  border-radius: 0 2px 2px 0;
}
```

### 进度条设计

```css
.progress-bar {
  height: 4px;
  background: #E8E6E3;
  border-radius: 2px;
  overflow: hidden;
}

.progress-fill {
  height: 100%;
  border-radius: 2px;
  transition: width 0.6s cubic-bezier(0.4, 0, 0.2, 1);
}

/* 颜色分级 */
.high { background: #9CAF88; }    /* >80% */
.medium { background: #8B9DC3; }  /* 60-80% */
.low { background: #D8CFC4; }    /* <60% */
```

### 数据区域（折叠）

```css
.step-data {
  background: #F7F7F5;
  border-radius: 6px;
  padding: 8px 12px;
  font-family: 'SF Mono', 'Monaco', 'Inconsolata', monospace;
  font-size: 11px;
  color: #6B7C96;
  margin-top: 8px;
  display: none;
}

.step-data.show {
  display: block;
  animation: slideDown 0.2s ease;
}
```

### 连接线

```css
.step-connector {
  position: absolute;
  left: 24px;
  top: 100%;
  bottom: -8px;
  width: 1px;
  background: linear-gradient(
    to bottom,
    #D8CFC4 0%,
    #D8CFC4 50%,
    transparent 100%
  );
}
```

### 头部折叠开关

```css
.decision-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 16px;
  background: rgba(139, 157, 195, 0.05);
  border-radius: 8px;
  cursor: pointer;
  user-select: none;
}

.decision-header:hover {
  background: rgba(139, 157, 195, 0.08);
}

.toggle-icon {
  transition: transform 0.3s ease;
}

.toggle-icon.open {
  transform: rotate(180deg);
}
```

### 字体系统

```css
/* 标题 */
.step-title {
  font-size: 13px;
  font-weight: 500;
  color: #3D3D3D;
  letter-spacing: -0.01em;
}

/* 描述 */
.step-desc {
  font-size: 12px;
  color: #8E8E93;
  line-height: 1.5;
}

/* 数据 */
.step-data {
  font-family: 'SF Mono', 'Monaco', monospace;
  font-size: 11px;
  color: #6B7C96;
}
```

## 📱 响应式

移动端（<768px）：
- 减少内边距：8px 12px
- 字体缩小1px
- 进度条高度：3px
- 数据区域默认折叠

## 🎬 动画

```css
@keyframes slideDown {
  from {
    opacity: 0;
    transform: translateY(-8px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@keyframes progressFill {
  from { width: 0; }
}
```

## 🎨 配色方案（莫兰迪色系）

```css
:root {
  --step-primary: #8B9DC3;     /* 主色 */
  --step-accent: #A8B8C8;      /* 强调 */
  --step-bg: #F7F7F5;          /* 背景点 */
  --step-border: #E8E6E3;      /* 边框 */
  --step-text: #3D3D3D;        /* 主文字 */
  --step-subtext: #8E8E93;     /* 副文字 */
  --progress-high: #9CAF88;     /* 高分 */
  --progress-mid: #8B9DC3;      /* 中分 */
  --progress-low: #D8CFC4;      /* 低分 */
}
```

## 📦 交付要求

请生成完整的HTML/CSS/JS代码，包含：

1. **完整组件**：5个步骤的示例数据
2. **交互功能**：头部折叠/展开、步骤数据展开
3. **动画效果**：进度条填充、区域展开
4. **莫兰迪色系**：使用CSS变量
5. **响应式设计**：移动端适配

## 🌟 参考链接

- Linear Design System: https://linear.app/design
- Notion Style Guide: https://notion.so
- Stripe Data Viz: https://stripe.com/blog

---

开始生成代码，确保视觉品质达到生产级别。
```

---

## 如果需要更简洁的版本

```
优化这个AI决策过程组件：

要求：
1. 去掉所有Emoji，用几何图形替代（○◆◇●）
2. 使用Linear/Notion风格的简洁设计
3. 莫兰迪蓝灰色系（#8B9DC3为主色）
4. 添加细进度条展示置信度
5. 数据区域用等宽字体，可折叠
6. 整体采用卡片+时间轴布局

参考：
- 左侧3px彩色状态条
- 右侧步骤信息
- 步骤间虚线连接
- 折叠/展开动画

生成完整的HTML/CSS/JS代码。
```

---

## 定制化选项

如果你想要特定风格，告诉我：

- [ ] 更极简（只有关键信息）
- [ ] 更详细（展示所有数据）
- [ ] 暗色模式支持
- [ ] 添加步骤完成动画
- [ ] 数据可视化（图表）
