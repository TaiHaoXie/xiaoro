# XiaoRo Shopping

AI 驱动的多模态电商导购 Agent，支持文字/图片提问、商品检索、对比决策、OCR 识别和流式推荐。

## 为什么值得看

- 把“搜索商品”升级成“辅助决策”
- 支持图文混合输入，能识别商品图、包装图、成分图
- 有完整的 RAG、意图识别、决策树和评价闭环
- 很适合作为 AI PM 求职作品，展示产品思维和落地能力

## 关键能力

- 意图识别：明确购买、比价决策、需求探索、售后转接
- 多模态搜索：图片向量检索 + OCR
- 决策辅助：对比分析、优缺点总结、购买建议
- 工程化能力：FastAPI、PostgreSQL、Milvus、Docker、Nginx

## 项目亮点

1. 通过 RAG 和商品知识库，减少“只会回答不会推荐”的问题。
2. 通过决策树和引用来源，让推荐过程更透明。
3. 通过流式输出，提升长链路查询的响应体验。
4. 通过画像和上下文记忆，让多轮对话更像真实导购。

## 快速开始

### 1. 装依赖

```bash
pip install -r requirements.txt
cp .env.example .env   # 按需填 SiliconFlow/DeepSeek API Key 等
```

### 2. 起依赖服务（PostgreSQL + Milvus）

```bash
docker-compose up -d   # 拉起 PostgreSQL、Milvus 等
```

### 3. 导入满血版数据（商品 + 知识库）

商品图片已随代码提供（`app/static/images/products/`），运行下面一条命令即可导入
103 条商品 + 96 条知识库 + 22 篇知识文档，得到与作者本地一致的数据：

```bash
python scripts/load_seed_dump.py
```

### 4. （可选）建图片向量索引，识图功能才能用

```bash
python scripts/batch_index_images.py
```

> 说明：识图依赖 CLIP 模型（首次启动自动从 HuggingFace 下载约 600MB）、Milvus 向量库
> 和上一步建立的图片索引。只用文字推荐/对比/追问的话，跳过第 4 步也能跑。

### 5. 启动

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

然后访问：

- `http://127.0.0.1:8000/docs`
- `http://127.0.0.1:8000/chat`
- `http://127.0.0.1:8000/image-search`

## 目录说明

- `app/`：核心后端服务
- `scripts/`：导入和批处理脚本
- `static/`：前端测试页
- `tests/`：测试
- `DEPLOY.md`：部署说明
- `IMPLEMENTATION.md`：实现说明
