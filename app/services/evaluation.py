"""
质量评测服务模块

实现端到端的质量评测与反馈闭环
包括：回答准确率、检索精度、响应时间、用户满意度等指标
"""

from typing import Dict, List, Any, Optional
from datetime import datetime, timedelta
import logging
import json

from app.database.postgres import execute_query, init_postgres_pool

logger = logging.getLogger(__name__)


class EvaluationService:
    """
    质量评测服务类

    功能：
    1. 收集用户反馈（点赞/点踩/评分）
    2. 计算评估指标（准确率、召回率、响应时间）
    3. 生成质量报告
    4. 提供优化建议
    """

    def __init__(self):
        """初始化评测服务"""
        self._initialized = False

    async def _ensure_initialized(self):
        """确保数据库表已创建"""
        if self._initialized:
            return

        await init_postgres_pool()

        # 创建评测相关表
        await self._create_tables()
        self._initialized = True
        logger.info("✅ 质量评测服务初始化成功")

    async def _create_tables(self):
        """创建评测相关数据表"""
        # 1. 对话评测表
        await execute_query("""
            CREATE TABLE IF NOT EXISTS conversation_evaluations (
                id SERIAL PRIMARY KEY,
                session_id VARCHAR(100) NOT NULL,
                message_id VARCHAR(100) NOT NULL,
                user_message TEXT NOT NULL,
                ai_response TEXT NOT NULL,
                rating INTEGER CHECK (rating >= 1 AND rating <= 5),
                feedback_type VARCHAR(20),
                feedback_text TEXT,
                response_time_ms INTEGER,
                retrieval_sources JSONB,
                product_clicks INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT NOW(),
                is_bad_sample BOOLEAN DEFAULT FALSE,
                reviewed_at TIMESTAMP
            )
        """)
        await execute_query("CREATE INDEX IF NOT EXISTS idx_eval_session ON conversation_evaluations(session_id)")
        await execute_query("CREATE INDEX IF NOT EXISTS idx_eval_rating ON conversation_evaluations(rating)")
        await execute_query("CREATE INDEX IF NOT EXISTS idx_eval_created ON conversation_evaluations(created_at)")

        # 2. 评测统计表
        await execute_query("""
            CREATE TABLE IF NOT EXISTS evaluation_stats (
                id SERIAL PRIMARY KEY,
                stat_date DATE NOT NULL UNIQUE,
                total_conversations INTEGER DEFAULT 0,
                total_messages INTEGER DEFAULT 0,
                total_ratings INTEGER DEFAULT 0,
                avg_rating DECIMAL(3,2),
                thumbs_up_count INTEGER DEFAULT 0,
                thumbs_down_count INTEGER DEFAULT 0,
                avg_response_time_ms INTEGER,
                satisfaction_rate DECIMAL(5,2),
                retrieval_accuracy DECIMAL(5,2),
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW()
            )
        """)

        # 3. 优化建议表
        await execute_query("""
            CREATE TABLE IF NOT EXISTS optimization_suggestions (
                id SERIAL PRIMARY KEY,
                suggestion_type VARCHAR(50) NOT NULL,
                title TEXT NOT NULL,
                description TEXT,
                priority INTEGER CHECK (priority >= 1 AND priority <= 5),
                status VARCHAR(20) DEFAULT 'pending',
                related_eval_ids INTEGER[],
                created_at TIMESTAMP DEFAULT NOW(),
                resolved_at TIMESTAMP
            )
        """)

    async def record_feedback(
        self,
        session_id: str,
        message_id: str,
        user_message: str,
        ai_response: str,
        feedback_type: str,
        rating: Optional[int] = None,
        feedback_text: Optional[str] = None,
        response_time_ms: Optional[int] = None,
        retrieval_sources: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        记录用户反馈

        Args:
            session_id: 会话ID
            message_id: 消息ID
            user_message: 用户消息
            ai_response: AI回复
            feedback_type: 反馈类型 (thumbs_up/thumbs_down/report)
            rating: 评分 (1-5)
            feedback_text: 反馈文本
            response_time_ms: 响应时间(毫秒)
            retrieval_sources: 检索来源列表

        Returns:
            记录结果
        """
        await self._ensure_initialized()

        try:
            result = await execute_query("""
                INSERT INTO conversation_evaluations
                (session_id, message_id, user_message, ai_response,
                 rating, feedback_type, feedback_text, response_time_ms, retrieval_sources)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                RETURNING id
            """,
                session_id, message_id, user_message, ai_response,
                rating, feedback_type, feedback_text, response_time_ms,
                json.dumps(retrieval_sources) if retrieval_sources else None,
                fetch="one"
            )

            logger.info(f"✅ 反馈记录成功: {feedback_type}")

            return {"success": True, "evaluation_id": result["id"]}

        except Exception as e:
            logger.error(f"❌ 反馈记录失败: {e}")
            return {"success": False, "error": str(e)}

    async def get_daily_stats(self, days: int = 7) -> Dict[str, Any]:
        """
        获取每日统计数据

        Args:
            days: 统计天数

        Returns:
            统计数据
        """
        await self._ensure_initialized()

        try:
            # 获取最近N天的数据
            # 注意：retrieval_sources 存储的是 citation 标题数组（字符串），如 ["商品名", "lumi-doc · 第 1 段", "FAQ标题"]
            # 文档命中率通过检查标题中是否包含 "lumi-doc" 来判断
            stats = await execute_query(f"""
                SELECT
                    DATE(created_at) as date,
                    COUNT(*) as total_evaluations,
                    COUNT(rating) as total_ratings,
                    COALESCE(AVG(rating), 0) as avg_rating,
                    SUM(CASE WHEN feedback_type = 'thumbs_up' THEN 1 ELSE 0 END) as thumbs_up,
                    SUM(CASE WHEN feedback_type = 'thumbs_down' THEN 1 ELSE 0 END) as thumbs_down,
                    COALESCE(AVG(response_time_ms), 0) as avg_response_time,
                    COUNT(CASE WHEN jsonb_array_length(retrieval_sources) > 0 THEN 1 END) as has_citations_count,
                    SUM(COALESCE(jsonb_array_length(retrieval_sources), 0)) as total_citations,
                    COUNT(CASE WHEN EXISTS (
                        SELECT 1 FROM jsonb_array_elements_text(retrieval_sources) AS elem
                        WHERE elem LIKE '%lumi-doc%'
                    ) THEN 1 END) as has_document_hit_count
                FROM conversation_evaluations
                WHERE created_at >= NOW() - INTERVAL '{days} days'
                GROUP BY DATE(created_at)
                ORDER BY date DESC
            """, fetch="all")

            # 计算总体满意度
            total_up = sum(s["thumbs_up"] for s in stats)
            total_down = sum(s["thumbs_down"] for s in stats)
            satisfaction_rate = (total_up / (total_up + total_down) * 100) if (total_up + total_down) > 0 else 0

            # 计算 citation 命中率（有来源的回复占比）
            total_evals = sum(s["total_evaluations"] for s in stats)
            has_citations = sum(s["has_citations_count"] for s in stats)
            citation_hit_rate = (has_citations / total_evals * 100) if total_evals > 0 else 0

            # 计算文档命中率（命中文档来源的回复占比）
            has_document_hits = sum(s["has_document_hit_count"] for s in stats)
            document_hit_rate = (has_document_hits / total_evals * 100) if total_evals > 0 else 0

            return {
                "success": True,
                "period_days": days,
                "daily_stats": stats,
                "summary": {
                    "total_evaluations": sum(s["total_evaluations"] for s in stats),
                    "total_ratings": sum(s["total_ratings"] for s in stats),
                    "avg_rating": sum(s["avg_rating"] for s in stats) / len(stats) if stats else 0,
                    "thumbs_up": total_up,
                    "thumbs_down": total_down,
                    "satisfaction_rate": round(satisfaction_rate, 2),
                    "avg_response_time_ms": sum(s["avg_response_time"] for s in stats) / len(stats) if stats else 0,
                    "citation_hit_rate": round(citation_hit_rate, 2),
                    "document_hit_rate": round(document_hit_rate, 2)
                }
            }

        except Exception as e:
            logger.error(f"❌ 获取统计数据失败: {e}")
            return {"success": False, "error": str(e)}

    async def get_quality_report(self) -> Dict[str, Any]:
        """
        生成质量报告

        Returns:
            质量报告
        """
        await self._ensure_initialized()

        try:
            stats = await self.get_daily_stats(30)
            summary = stats.get("summary", {})

            # 计算各项指标
            report = {
                "overall_quality": "good" if summary.get("satisfaction_rate", 0) >= 70 else "needs_improvement",
                "metrics": {
                    "user_satisfaction": {
                        "score": summary.get("satisfaction_rate", 0),
                        "level": "高" if summary.get("satisfaction_rate", 0) >= 80 else
                                "中" if summary.get("satisfaction_rate", 0) >= 60 else "低",
                        "trend": "稳定"  # 可计算趋势
                    },
                    "response_speed": {
                        "avg_ms": summary.get("avg_response_time_ms", 0),
                        "level": "快" if summary.get("avg_response_time_ms", 0) < 2000 else
                                "中" if summary.get("avg_response_time_ms", 0) < 5000 else "慢"
                    },
                    "user_engagement": {
                        "avg_rating": round(summary.get("avg_rating", 0), 2),
                        "total_ratings": summary.get("total_ratings", 0)
                    }
                },
                "recommendations": []
            }

            # 根据指标生成建议
            if summary.get("satisfaction_rate", 0) < 60:
                report["recommendations"].append({
                    "type": "urgent",
                    "title": "满意度偏低",
                    "description": "建议检查常见问题回复质量，优化Prompt模板"
                })

            if summary.get("avg_response_time_ms", 0) > 3000:
                report["recommendations"].append({
                    "type": "optimization",
                    "title": "响应速度偏慢",
                    "description": "建议优化检索逻辑或增加缓存"
                })

            thumbs_up = summary.get("thumbs_up", 0)
            thumbs_down = summary.get("thumbs_down", 0)
            if thumbs_down > thumbs_up:
                report["recommendations"].append({
                    "type": "critical",
                    "title": "负面反馈过多",
                    "description": "建议分析负面反馈样本，找出问题模式"
                })

            return {"success": True, "report": report}

        except Exception as e:
            logger.error(f"❌ 生成质量报告失败: {e}")
            return {"success": False, "error": str(e)}

    async def get_bad_samples(self, limit: int = 10) -> List[Dict[str, Any]]:
        """
        获取需要人工审查的负面样本

        Args:
            limit: 返回数量

        Returns:
            负面样本列表
        """
        await self._ensure_initialized()

        try:
            samples = await execute_query("""
                SELECT
                    id, session_id, user_message, ai_response,
                    rating, feedback_type, feedback_text,
                    response_time_ms, retrieval_sources,
                    created_at, reviewed_at
                FROM conversation_evaluations
                WHERE feedback_type = 'thumbs_down' OR rating <= 2
                ORDER BY created_at DESC
                LIMIT $1
            """, limit, fetch="all")

            return {"success": True, "samples": samples}

        except Exception as e:
            logger.error(f"❌ 获取负面样本失败: {e}")
            return {"success": False, "error": str(e)}

    async def mark_reviewed(self, evaluation_id: int, is_bad_sample: bool = True):
        """
        标记样本已审查

        Args:
            evaluation_id: 评测ID
            is_bad_sample: 是否为问题样本
        """
        await self._ensure_initialized()

        try:
            await execute_query("""
                UPDATE conversation_evaluations
                SET is_bad_sample = $1, reviewed_at = NOW()
                WHERE id = $2
            """, is_bad_sample, evaluation_id, fetch="none")

            return {"success": True}

        except Exception as e:
            logger.error(f"❌ 标记审查失败: {e}")
            return {"success": False, "error": str(e)}


# ==================== 全局实例 ====================

_evaluation_service: Optional[EvaluationService] = None


def get_evaluation_service() -> EvaluationService:
    """获取评测服务单例"""
    global _evaluation_service
    if _evaluation_service is None:
        _evaluation_service = EvaluationService()
    return _evaluation_service
