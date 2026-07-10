-- ==================== 电商智能导购系统 - 数据库初始化脚本 ====================

-- 扩展
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ==================== 用户表 ====================

CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    phone VARCHAR(20) UNIQUE,
    email VARCHAR(100) UNIQUE,
    nickname VARCHAR(50),
    avatar_url TEXT,
    skin_type VARCHAR(20), -- 干性/油性/混合性/敏感性
    skin_concerns TEXT[], -- 痘印/敏感/暗沉/毛孔粗大等
    budget_range INT2RANGE, -- 预算范围
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- ==================== 会话表 ====================

CREATE TABLE IF NOT EXISTS conversations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    session_id VARCHAR(100) UNIQUE NOT NULL,
    title VARCHAR(200),
    status VARCHAR(20) DEFAULT 'active', -- active/archived
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- ==================== 消息表 ====================

CREATE TABLE IF NOT EXISTS messages (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    conversation_id UUID REFERENCES conversations(id) ON DELETE CASCADE,
    role VARCHAR(20) NOT NULL, -- user/assistant/system
    content TEXT NOT NULL,
    metadata JSONB DEFAULT '{}', -- 存储图片URL、产品列表等
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ==================== 商品表 ====================

CREATE TABLE IF NOT EXISTS products (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name VARCHAR(200) NOT NULL,
    brand VARCHAR(100),
    category VARCHAR(50), -- 护肤/彩妆/香水等
    subcategory VARCHAR(50), -- 面霜/精华/洁面等
    price DECIMAL(10,2),
    original_price DECIMAL(10,2),
    image_url TEXT,
    description TEXT,
    ingredients TEXT[], -- 成分列表
    skin_types TEXT[], -- 适合肤质
    concerns TEXT[], -- 解决问题
    tags TEXT[], -- 标签

    -- 第三方平台信息
    platform_urls JSONB DEFAULT '{}', -- 各平台链接

    -- 统计信息
    view_count INT DEFAULT 0,
    purchase_count INT DEFAULT 0,

    -- 向量搜索
    embedding VECTOR(1536),

    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 创建索引
CREATE INDEX idx_products_category ON products(category);
CREATE INDEX idx_products_brand ON products(brand);
CREATE INDEX idx_products_price ON products(price);
CREATE INDEX idx_products_skin_types ON products USING GIN(skin_types);
CREATE INDEX idx_products_concerns ON products USING GIN(concerns);

-- 全文搜索索引
CREATE INDEX idx_products_search ON products USING GIN(to_tsvector('simple', name || ' ' || COALESCE(description, '')));

-- ==================== 推荐记录表 ====================

CREATE TABLE IF NOT EXISTS recommendations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    conversation_id UUID REFERENCES conversations(id) ON DELETE CASCADE,
    product_id UUID REFERENCES products(id),
    score DECIMAL(3,2), -- 推荐分数
    reason TEXT, -- 推荐理由
    position INT, -- 推荐位置
    clicked BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_recommendations_conversation ON recommendations(conversation_id);
CREATE INDEX idx_recommendations_product ON recommendations(product_id);

-- ==================== 评测反馈表 ====================

CREATE TABLE IF NOT EXISTS conversation_evaluations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    conversation_id UUID REFERENCES conversations(id) ON DELETE CASCADE,
    feedback_type VARCHAR(20), -- thumbs_up/thumbs_down
    response_time INT, -- 响应时间(ms)
    user_comment TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ==================== 评测统计表 ====================

CREATE TABLE IF NOT EXISTS evaluation_stats (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    stat_date DATE NOT NULL,
    metric_name VARCHAR(50) NOT NULL,
    metric_value DECIMAL(10,2),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(stat_date, metric_name)
);

-- ==================== 文档表（RAG知识库） ====================

CREATE TABLE IF NOT EXISTS documents (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    title VARCHAR(200),
    content TEXT NOT NULL,
    source VARCHAR(100), -- 来源：专家文章/用户评测/品牌介绍等
    source_url TEXT,
    category VARCHAR(50),
    tags TEXT[],

    -- 向量搜索
    embedding VECTOR(1536),

    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_documents_category ON documents(category);
CREATE INDEX idx_documents_tags ON documents USING GIN(tags);

-- ==================== 价格历史表 ====================

CREATE TABLE IF NOT EXISTS price_history (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    product_id UUID REFERENCES products(id) ON DELETE CASCADE,
    platform VARCHAR(50) NOT NULL, -- 天猫/京东/拼多多
    price DECIMAL(10,2) NOT NULL,
    url TEXT,
    recorded_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_price_history_product ON price_history(product_id);
CREATE INDEX idx_price_history_platform ON price_history(platform);
CREATE INDEX idx_price_history_date ON price_history(recorded_at);

-- ==================== 定时任务日志表 ====================

CREATE TABLE IF NOT EXISTS task_logs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    task_name VARCHAR(100) NOT NULL,
    status VARCHAR(20), -- success/failed
    result JSONB,
    error_message TEXT,
    started_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

CREATE INDEX idx_task_logs_name ON task_logs(task_name);
CREATE INDEX idx_task_logs_status ON task_logs(status);
CREATE INDEX idx_task_logs_started ON task_logs(started_at);

-- ==================== 触发器 ====================

-- 更新时间戳触发器函数
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- 为需要的表添加触发器
CREATE TRIGGER update_users_updated_at
    BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER update_conversations_updated_at
    BEFORE UPDATE ON conversations
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER update_products_updated_at
    BEFORE UPDATE ON products
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- ==================== 初始化数据 ====================

-- 插入示例文档（知识库）
INSERT INTO documents (title, content, source, category, tags) VALUES
('护肤基础知识：了解你的肤质', '肤质主要分为四种类型：干性肌肤、油性肌肤、混合性肌肤和敏感性肌肤。干性肌肤需要加强保湿，油性肌肤需要控油清洁，混合性肌肤需要分区护理，敏感性肌肤需要温和低刺激产品。', '专家文章', '护肤基础', ARRAY['肤质', '基础知识']),
('成分解析：玻尿酸的保湿原理', '玻尿酸（透明质酸）是人体内天然存在的物质，能锁住自身重量1000倍的水分。护肤品中的玻尿酸主要通过在皮肤表面形成保湿膜，减少水分蒸发来达到保湿效果。', '成分分析', '成分解析', ARRAY['玻尿酸', '保湿']),
('敏感肌护理指南', '敏感肌肤护理要点：1.选择温和无香精产品 2.避免酒精和强刺激性成分 3.做好基础保湿 4.注意防晒 5.新品先在耳后测试。推荐成分：神经酰胺、积雪草、芦荟。', '护理指南', '敏感肌', ARRAY['敏感肌', '护理']) ON CONFLICT DO NOTHING;

-- ==================== 完成 ====================

-- 输出初始化完成信息
DO $$
BEGIN
    RAISE NOTICE '数据库初始化完成！';
    RAISE NOTICE '已创建表：users, conversations, messages, products, recommendations, conversation_evaluations, evaluation_stats, documents, price_history, task_logs';
END $$;
