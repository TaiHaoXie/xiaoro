"""
Celery异步任务模块

处理耗时的后台任务：
- 商品推荐计算
- 价格对比爬取
- 评测数据更新
- 批量数据导入
"""

from celery import Celery, Task
from typing import Dict, Any, List
import logging
import asyncio
from datetime import datetime

logger = logging.getLogger(__name__)

# ==================== Celery应用配置 ====================

# 创建Celery应用
celery_app = Celery(
    'ecommerce_tasks',
    broker='redis://localhost:6379/0',
    backend='redis://localhost:6379/1',
    include=['app.tasks.product.tasks', 'app.tasks.evaluation.tasks', 'app.tasks.rag.tasks', 'app.tasks.image_tasks']
)

# Celery配置
celery_app.conf.update(
    # 任务结果过期时间（1天）
    result_expires=86400,

    # 任务执行时间限制
    task_time_limit=300,      # 硬限制5分钟
    task_soft_time_limit=240, # 软限制4分钟

    # 任务预取（每个worker预取4个任务）
    worker_prefetch_multiplier=4,

    # 任务结果序列化
    task_serializer='json',
    result_serializer='json',
    accept_content=['json'],

    # 时区
    timezone='Asia/Shanghai',
    enable_utc=True,

    # 任务路由
    task_routes={
        'app.tasks.product.tasks.*': {'queue': 'product'},
        'app.tasks.evaluation.tasks.*': {'queue': 'evaluation'},
        'app.tasks.rag.tasks.*': {'queue': 'rag'},
        'tasks.image.*': {'queue': 'image'},
    },

    # 任务重试配置
    task_autoretry_for=(Exception,),
    task_retry_kwargs={'max_retries': 3, 'countdown': 60},

    # Worker配置
    worker_max_tasks_per_child=1000,
)


# ==================== 基础任务类 ====================

class DatabaseTask(Task):
    """需要数据库连接的任务基类"""

    _db = None

    @property
    def db(self):
        """懒加载数据库连接"""
        if self._db is None:
            from app.database.postgres import get_postgres_pool
            self._db = get_postgres_pool()
        return self._db

    def after_return(self, *args, **kwargs):
        """任务结束后清理连接"""
        if self._db is not None:
            self._db.close()
            self._db = None
        super().after_return(*args, **kwargs)


# ==================== 商品推荐任务 ====================

@celery_app.task(bind=True, base=DatabaseTask, name='tasks.product.recommend')
def recommend_products(
    self,
    user_id: str,
    session_id: str,
    query: str,
    skin_type: str = None,
    budget_range: tuple = None,
    limit: int = 10
) -> Dict[str, Any]:
    """
    异步商品推荐任务

    Args:
        user_id: 用户ID
        session_id: 会话ID
        query: 搜索查询
        skin_type: 肤质类型
        budget_range: 预算范围 (min, max)
        limit: 返回数量

    Returns:
        推荐结果
    """
    try:
        logger.info(f"开始推荐任务: session={session_id}, query={query}")

        # 执行推荐逻辑
        from app.services.agent import ShoppingAgent
        from app.services.skincare_analyzer import get_skincare_analyzer

        agent = ShoppingAgent()
        analyzer = get_skincare_analyzer()

        # 1. 意图识别
        intent = agent.detect_intent(query)

        # 2. 检索商品
        products = asyncio.run(agent.retrieve_products(
            query=intent.get('product_type', ''),
            filters={
                'skin_type': skin_type,
                'price_range': budget_range
            },
            limit=limit * 3  # 多检索一些，后面筛选
        ))

        # 3. 匹配分析
        if skin_type and products:
            for product in products:
                match_result = analyzer.match_skin_concerns(
                    product=product,
                    skin_type=skin_type,
                    concerns=intent.get('concerns', [])
                )
                product['match_score'] = match_result['overall_match']
                product['match_reasons'] = match_result['reasons']

        # 4. 排序
        products.sort(key=lambda x: x.get('match_score', 0), reverse=True)

        # 5. 返回Top N
        result = {
            'success': True,
            'products': products[:limit],
            'total_candidates': len(products),
            'intent': intent,
            'timestamp': datetime.now().isoformat()
        }

        logger.info(f"推荐完成: 返回{len(result['products'])}个商品")
        return result

    except Exception as e:
        logger.error(f"推荐任务失败: {e}", exc_info=True)
        raise


# ==================== 价格对比任务 ====================

@celery_app.task(bind=True, name='tasks.product.compare_prices')
def compare_prices(self, product_ids: List[int]) -> Dict[str, Any]:
    """
    异步价格对比任务

    从多个平台爬取价格信息

    Args:
        product_ids: 商品ID列表

    Returns:
        价格对比结果
    """
    try:
        logger.info(f"开始价格对比: {len(product_ids)}个商品")

        # 模拟价格对比（实际项目中对接真实API）
        results = []
        for pid in product_ids:
            results.append({
                'product_id': pid,
                'platforms': [
                    {'platform': '天猫', 'price': 1080, 'url': 'https://tmall.com/...'},
                    {'platform': '京东', 'price': 1050, 'url': 'https://jd.com/...'},
                    {'platform': '拼多多', 'price': 980, 'url': 'https://pinduoduo.com/...'},
                ],
                'lowest': 980,
                'highest': 1080
            })

        return {
            'success': True,
            'comparisons': results,
            'timestamp': datetime.now().isoformat()
        }

    except Exception as e:
        logger.error(f"价格对比失败: {e}", exc_info=True)
        raise


# ==================== 评测数据更新任务 ====================

@celery_app.task(bind=True, base=DatabaseTask, name='tasks.evaluation.update_stats')
def update_evaluation_stats(self) -> Dict[str, Any]:
    """
    更新评测统计数据

    计算并缓存：
    - 平均响应时间
    - 好评率
    - 使用频率统计

    Returns:
        更新结果
    """
    try:
        logger.info("开始更新评测统计")

        async def _update():
            pool = await self.db.acquire()

            # 计算平均响应时间
            result = await pool.execute("""
                INSERT INTO evaluation_stats (stat_date, metric_name, metric_value)
                SELECT
                    CURRENT_DATE,
                    'avg_response_time',
                    COALESCE(AVG(response_time), 0)
                FROM conversation_evaluations
                WHERE created_at >= CURRENT_DATE
                ON CONFLICT (stat_date, metric_name)
                DO UPDATE SET metric_value = EXCLUDED.metric_value
            """)

            # 计算好评率
            await pool.execute("""
                INSERT INTO evaluation_stats (stat_date, metric_name, metric_value)
                SELECT
                    CURRENT_DATE,
                    'positive_rate',
                    COALESCE(
                        SUM(CASE WHEN feedback_type = 'thumbs_up' THEN 1 ELSE 0 END) * 100.0 / NULLIF(COUNT(*), 0),
                        0
                    )
                FROM conversation_evaluations
                WHERE created_at >= CURRENT_DATE
                ON CONFLICT (stat_date, metric_name)
                DO UPDATE SET metric_value = EXCLUDED.metric_value
            """)

            await self.db.release(pool)
            return True

        asyncio.run(_update())

        logger.info("评测统计更新完成")
        return {
            'success': True,
            'timestamp': datetime.now().isoformat()
        }

    except Exception as e:
        logger.error(f"更新评测统计失败: {e}", exc_info=True)
        raise


# ==================== 批量数据导入任务 ====================

@celery_app.task(bind=True, base=DatabaseTask, name='tasks.rag.import_documents')
def import_documents_to_vectorstore(
    self,
    documents: List[Dict[str, Any]],
    batch_size: int = 100
) -> Dict[str, Any]:
    """
    批量导入文档到向量库

    Args:
        documents: 文档列表
        batch_size: 批处理大小

    Returns:
        导入结果
    """
    try:
        logger.info(f"开始导入{len(documents)}个文档到向量库")

        from app.services.embedding import get_embedding_service

        embedding_service = get_embedding_service()

        imported = 0
        failed = 0

        # 批量处理
        for i in range(0, len(documents), batch_size):
            batch = documents[i:i+batch_size]

            for doc in batch:
                try:
                    # 生成embedding
                    text = doc.get('content', '')
                    embedding = embedding_service.encode(text)

                    # 存储到向量库（这里简化处理）
                    # 实际项目中会调用Milvus/Pinecone等

                    imported += 1

                except Exception as e:
                    logger.warning(f"文档导入失败: {e}")
                    failed += 1

        logger.info(f"向量库导入完成: 成功{imported}, 失败{failed}")

        return {
            'success': True,
            'imported': imported,
            'failed': failed,
            'timestamp': datetime.now().isoformat()
        }

    except Exception as e:
        logger.error(f"向量库导入失败: {e}", exc_info=True)
        raise


# ==================== 批量图片索引任务 ====================

@celery_app.task(bind=True, name='tasks.image.batch_index')
def batch_index_images(
    self,
    product_ids: List[int] = None,
    batch_size: int = 50,
    overwrite: bool = False
) -> Dict[str, Any]:
    """
    批量建立商品图片向量索引

    Args:
        product_ids: 商品ID列表（为空则处理所有商品）
        batch_size: 每批处理数量
        overwrite: 是否覆盖已有索引

    Returns:
        索引结果统计
    """
    try:
        from app.tasks.image_tasks import batch_index_product_images, index_all_products

        if product_ids is None:
            # 全量索引
            result = index_all_products(batch_size=batch_size, overwrite=overwrite)
        else:
            # 指定商品索引
            result = batch_index_product_images(product_ids, overwrite=overwrite)

        return {
            'success': True,
            **result,
            'timestamp': datetime.now().isoformat()
        }

    except Exception as e:
        logger.error(f"批量图片索引失败: {e}", exc_info=True)
        raise


# ==================== 定时任务 ====================

from celery.schedules import crontab

@celery_app.task(name='tasks.daily_cleanup')
def daily_cleanup():
    """每日清理任务"""
    logger.info("执行每日清理任务")
    # 清理过期数据、缓存等
    return {'cleaned': True}


@celery_app.task(name='tasks.weekly_report')
def weekly_report():
    """每周报告生成"""
    logger.info("生成每周报告")
    # 生成使用统计报告
    return {'report_generated': True}


# 配置定时任务
celery_app.conf.beat_schedule = {
    'daily-cleanup-at-2am': {
        'task': 'tasks.daily_cleanup',
        'schedule': crontab(hour=2, minute=0),
    },
    'weekly-report-every-monday': {
        'task': 'tasks.weekly_report',
        'schedule': crontab(hour=9, minute=0, day_of_week=1),
    },
}


# ==================== Worker入口 ====================

if __name__ == '__main__':
    celery_app.start()
