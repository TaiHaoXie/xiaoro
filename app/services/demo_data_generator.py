"""
演示数据生成模块

用于快速生成测试用的商品属性和用户评价数据
"""

import random
from typing import List, Dict, Any
from datetime import datetime, timedelta


# 护肤品成分库
INGREDIENTS_DB = {
    "保湿": ["透明质酸", "甘油", "神经酰胺", "玻尿酸", "角鲨烷"],
    "抗老": ["视黄醇", "胜肽", "二裂酵母", "维生素C", "烟酰胺"],
    "美白": ["烟酰胺", "维生素C", "熊果苷", "377", "传明酸"],
    "祛痘": ["水杨酸", "茶树油", "烟酰胺", "积雪草", "芦荟"],
    "舒缓": ["积雪草", "芦荟", "燕麦", "尿囊素", "马齿苋"]
}

# 肤质匹配规则
SKIN_TYPE_MATCH = {
    "干性": {"保湿": 1.0, "抗老": 0.9, "美白": 0.8, "祛痘": 0.6},
    "油性": {"保湿": 0.7, "抗老": 0.8, "美白": 0.9, "祛痘": 1.0},
    "混合": {"保湿": 0.8, "抗老": 0.9, "美白": 0.9, "祛痘": 0.8},
    "敏感": {"保湿": 0.9, "抗老": 0.7, "美白": 0.6, "祛痘": 0.5, "舒缓": 1.0}
}

# 用户评价模板
REVIEW_TEMPLATES = [
    "用了{time}，{effect}，质地{texture}，会继续回购",
    "{brand}这款真的不错，{pros}，但是{cons}",
    "肤质是{skin_type}，这款{product}对我{effect}",
    "成分很良心，含有{ingredient}，{effect}",
    "{time}用了{amount}，感觉{effect}，性价比{price_comment}"
]


def generate_product_attributes(product_name: str, category: str = "精华") -> Dict[str, Any]:
    """
    为商品生成深度属性

    Args:
        product_name: 商品名称
        category: 类别

    Returns:
        商品属性字典
    """
    # 随机选择主要功效
    primary_effects = random.sample(list(INGREDIENTS_DB.keys()), k=random.randint(1, 2))

    # 生成成分列表
    ingredients = []
    for effect in primary_effects:
        ingredients.extend(random.sample(INGREDIENTS_DB[effect], k=2))

    # 去重
    ingredients = list(set(ingredients))

    # 生成肤质匹配度
    skin_match = {}
    for skin_type in SKIN_TYPE_MATCH:
        scores = [SKIN_TYPE_MATCH[skin_type].get(e, 0.7) for e in primary_effects]
        skin_match[skin_type] = round(sum(scores) / len(scores), 2)

    # 生成规格
    specs = {
        "容量": random.choice(["30ml", "50ml", "100ml", "15ml×2"]),
        "保质期": f"{random.randint(2, 4)}年",
        "产地": random.choice(["法国", "日本", "韩国", "中国", "美国"])
    }

    return {
        "product_name": product_name,
        "category": category,
        "ingredients": ingredients,
        "primary_effects": primary_effects,
        "skin_type_match": skin_match,
        "specifications": specs,
        "price_tier": random.choice(["平价", "中端", "高端", "奢侈"]),
        "texture": random.choice(["清爽水润", "轻薄乳液", "滋润乳霜", "精华油状"]),
        "scent": random.choice(["无香", "淡淡花香", "草本清香", "无味"])
    }


def generate_user_reviews(
    product_name: str,
    count: int = 20,
    skin_types: List[str] = None
) -> List[Dict[str, Any]]:
    """
    生成模拟用户评价

    Args:
        product_name: 商品名称
        count: 生成数量
        skin_types: 用户肤质分布

    Returns:
        用户评价列表
    """
    if skin_types is None:
        skin_types = ["干性", "油性", "混合", "敏感"]

    reviews = []

    for i in range(count):
        skin_type = random.choice(skin_types)

        # 评价时间（最近3个月）
        days_ago = random.randint(1, 90)
        review_date = datetime.now() - timedelta(days=days_ago)

        # 评分（倾向正面）
        rating = random.choices([5, 4, 3, 2, 1], weights=[60, 25, 10, 4, 1])[0]

        # 填充模板
        template = random.choice(REVIEW_TEMPLATES)
        content = template.format(
            time=random.choice(["一周", "半个月", "一个月", "两个月", "三个月"]),
            effect=random.choice(["效果明显", "还不错", "一般", "很好", "有改善"]),
            texture=random.choice(["清爽", "有点黏", "好吸收", "厚重", "轻薄"]),
            brand=random.choice(["兰蔻", "雅诗兰黛", "SK-II", "欧莱雅"]),
            pros=random.choice(["吸收快", "不油腻", "温和", "保湿好", "性价比高"]),
            cons=random.choice(["味道不好闻", "有点贵", "效果慢", "量少", "包装差"]),
            skin_type=skin_type,
            product=product_name,
            ingredient=random.choice(["烟酰胺", "玻尿酸", "视黄醇"]),
            amount=random.choice(["半瓶", "三分之一", "一大半", "快用完了"]),
            price_comment=random.choice(["不错", "有点贵", "还可以", "很划算"])
        )

        reviews.append({
            "id": f"review_{i}",
            "product_name": product_name,
            "user_id": f"user_{random.randint(1000, 9999)}",
            "skin_type": skin_type,
            "rating": rating,
            "content": content,
            "created_at": review_date.isoformat(),
            "helpful_count": random.randint(0, 50),
            "verified_purchase": random.random() > 0.3  # 70%真实购买
        })

    return reviews


def analyze_reviews(reviews: List[Dict]) -> Dict[str, Any]:
    """
    分析用户评价，提取洞察

    Args:
        reviews: 用户评价列表

    Returns:
        分析结果
    """
    if not reviews:
        return {}

    # 按肤质统计
    skin_type_stats = {}
    for r in reviews:
        st = r["skin_type"]
        if st not in skin_type_stats:
            skin_type_stats[st] = {"count": 0, "total_rating": 0}
        skin_type_stats[st]["count"] += 1
        skin_type_stats[st]["total_rating"] += r["rating"]

    for st in skin_type_stats:
        skin_type_stats[st]["avg_rating"] = round(
            skin_type_stats[st]["total_rating"] / skin_type_stats[st]["count"], 1
        )

    # 提取关键词
    all_words = []
    for r in reviews:
        # 简单分词（实际用jieba等）
        words = r["content"].replace("，", " ").replace("。", " ").split()
        all_words.extend([w for w in words if len(w) >= 2])

    # 统计高频词
    from collections import Counter
    top_keywords = [w for w, c in Counter(all_words).most_common(10)]

    # 正负面评价比例
    positive = sum(1 for r in reviews if r["rating"] >= 4)
    negative = sum(1 for r in reviews if r["rating"] <= 2)

    return {
        "total_reviews": len(reviews),
        "average_rating": round(sum(r["rating"] for r in reviews) / len(reviews), 1),
        "positive_rate": f"{positive / len(reviews) * 100:.1f}%",
        "skin_type_analysis": skin_type_stats,
        "top_keywords": top_keywords[:10],
        "verified_rate": f"{sum(1 for r in reviews if r['verified_purchase']) / len(reviews) * 100:.1f}%"
    }


def generate_rag_knowledge(product_name: str, attributes: Dict, reviews: List[Dict]) -> List[Dict]:
    """
    生成用于RAG的知识条目

    Args:
        product_name: 商品名称
        attributes: 商品属性
        reviews: 用户评价

    Returns:
        知识条目列表
    """
    knowledge = []

    # 条目1: 成分解析
    knowledge.append({
        "title": f"{product_name}成分解析",
        "content": f"""
主要成分：{', '.join(attributes['ingredients'][:5])}
主要功效：{', '.join(attributes['primary_effects'])}
适合肤质：{max(attributes['skin_type_match'].items(), key=lambda x: x[1])[0]}（匹配度{max(attributes['skin_type_match'].values())}）
质地：{attributes['texture']}
        """.strip(),
        "type": "ingredient_analysis",
        "metadata": {"product": product_name}
    })

    # 条目2: 用户反馈总结
    review_analysis = analyze_reviews(reviews)
    knowledge.append({
        "title": f"{product_name}用户反馈总结",
        "content": f"""
基于{review_analysis['total_reviews']}条真实评价：
平均评分：{review_analysis['average_rating']}/5
好评率：{review_analysis['positive_rate']}
高频关键词：{', '.join(review_analysis['top_keywords'][:5])}
        """.strip(),
        "type": "user_feedback",
        "metadata": {"product": product_name}
    })

    # 条目3: 肤质适配建议
    skin_analysis = attributes['skin_type_match']
    best_skin = max(skin_analysis.items(), key=lambda x: x[1])
    knowledge.append({
        "title": f"{product_name}肤质适配建议",
        "content": f"""
最适合：{best_skin[0]}肌（匹配度{best_skin[1]}）
其他肤质表现：
- 干性肌：{skin_analysis['干性']}分
- 油性肌：{skin_analysis['油性']}分
- 混合肌：{skin_analysis['混合']}分
- 敏感肌：{skin_analysis['敏感']}分
        """.strip(),
        "type": "skin_match",
        "metadata": {"product": product_name}
    })

    return knowledge


# ==================== 使用示例 ====================

if __name__ == "__main__":
    # 为商品生成完整数据
    product = "兰蔻小黑瓶精华"

    # 1. 生成商品属性
    attrs = generate_product_attributes(product)
    print(f"=== {product} 商品属性 ===")
    print(f"成分: {attrs['ingredients']}")
    print(f"功效: {attrs['primary_effects']}")
    print(f"肤质匹配: {attrs['skin_type_match']}")
    print()

    # 2. 生成用户评价
    reviews = generate_user_reviews(product, count=50)
    print(f"=== 用户评价 (前3条) ===")
    for r in reviews[:3]:
        print(f"[{r['rating']}★] {r['content']}")
    print()

    # 3. 分析评价
    analysis = analyze_reviews(reviews)
    print(f"=== 评价分析 ===")
    print(f"平均评分: {analysis['average_rating']}")
    print(f"好评率: {analysis['positive_rate']}")
    print()

    # 4. 生成RAG知识
    knowledge = generate_rag_knowledge(product, attrs, reviews)
    print(f"=== RAG知识条目 ===")
    for k in knowledge:
        print(f"- {k['title']}")
        print(f"  {k['content'][:50]}...")
        print()
