"""
对话上下文管理服务

负责保存和加载对话历史、提取用户偏好
"""

from typing import List, Dict, Any, Optional
from datetime import datetime
import logging
import json

from app.database.postgres import execute_query
from app.services.intent import IntentResult

logger = logging.getLogger(__name__)


def _ensure_list(value):
    """确保值是列表类型"""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _convert_decimal_to_float(obj):
    """将不可JSON序列化的类型转换为可序列化类型"""
    from decimal import Decimal
    if isinstance(obj, Decimal):
        return float(obj)
    elif isinstance(obj, datetime):
        return obj.isoformat()
    elif isinstance(obj, dict):
        return {k: _convert_decimal_to_float(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_convert_decimal_to_float(item) for item in obj]
    return obj


class ConversationContext:
    """
    对话上下文

    包含：
    - 历史消息
    - 用户偏好（预算、品牌、类别等）
    - 之前提到的商品
    - 对话统计
    """

    def __init__(
        self,
        session_id: str,
        messages: Optional[List[Dict]] = None,
        user_profile: Optional[Dict] = None,
        mentioned_products: Optional[List[Dict]] = None
    ):
        self.session_id = session_id
        self.messages = _ensure_list(messages) if messages else []
        self.user_profile = user_profile or self._default_profile()
        self.mentioned_products = _ensure_list(mentioned_products) if mentioned_products else []
        self.updated_at = datetime.now()

    def _default_profile(self) -> Dict:
        """默认用户画像"""
        return {
            "budget": None,           # 预算
            "preferred_brands": [],    # 偏好品牌
            "preferred_categories": [], # 偏好类别
            "skin_types": [],         # 肤质标签
            "skin_concerns": [],      # 肌肤诉求
            "use_case": None,          # 使用场景
            "price_sensitivity": "medium",  # 价格敏感度: low/medium/high
            "interaction_count": 0,    # 互动次数
            "last_intent": None        # 上次意图
        }

    def add_message(self, role: str, content: str, intent: Optional[str] = None, **metadata):
        """添加消息到历史"""
        self.messages.append({
            "role": role,
            "content": content,
            "intent": intent,
            "timestamp": datetime.now().isoformat(),
            **metadata
        })
        self.updated_at = datetime.now()

    def update_profile(self, intent_result: IntentResult):
        """
        根据意图识别结果更新用户画像

        提取规则：
        - 如果用户提到预算，记录预算
        - 如果用户提到品牌，添加到偏好品牌
        - 如果用户提到类别，添加到偏好类别
        """
        entities = intent_result.entities

        # 确保列表类型
        if not isinstance(self.user_profile["preferred_brands"], list):
            self.user_profile["preferred_brands"] = []
        if not isinstance(self.user_profile["preferred_categories"], list):
            self.user_profile["preferred_categories"] = []
        if not isinstance(self.user_profile.get("skin_types"), list):
            self.user_profile["skin_types"] = []
        if not isinstance(self.user_profile.get("skin_concerns"), list):
            self.user_profile["skin_concerns"] = []

        # 更新预算
        if "budget" in entities:
            self.user_profile["budget"] = entities["budget"]
            logger.info(f"更新用户预算: {entities['budget']}")

        # 更新品牌偏好
        if "brand" in entities:
            brand = entities["brand"]
            if brand not in self.user_profile["preferred_brands"]:
                self.user_profile["preferred_brands"].append(brand)
            logger.info(f"添加品牌偏好: {brand}")

        # 更新类别偏好
        if "category" in entities:
            category = entities["category"]
            if category not in self.user_profile["preferred_categories"]:
                self.user_profile["preferred_categories"].append(category)
            logger.info(f"添加类别偏好: {category}")

        if "skin_types" in entities:
            for skin_type in _ensure_list(entities["skin_types"]):
                if skin_type not in self.user_profile["skin_types"]:
                    self.user_profile["skin_types"].append(skin_type)

        if "skin_concerns" in entities:
            for concern in _ensure_list(entities["skin_concerns"]):
                if concern not in self.user_profile["skin_concerns"]:
                    self.user_profile["skin_concerns"].append(concern)

        # 更新使用场景
        if "use_case" in entities:
            self.user_profile["use_case"] = entities["use_case"]

        # 更新价格敏感度
        if "budget" in entities:
            budget = entities["budget"]
            if budget < 1000:
                self.user_profile["price_sensitivity"] = "high"
            elif budget > 5000:
                self.user_profile["price_sensitivity"] = "low"

        # 更新互动统计
        self.user_profile["interaction_count"] = self.user_profile.get("interaction_count", 0) + 1
        self.user_profile["last_intent"] = intent_result.intent

    def get_context_summary(self, max_messages: int = 10) -> str:
        """
        获取对话历史摘要（用于LLM提示）

        Returns:
            对话摘要字符串
        """
        if not self.messages:
            return ""

        recent_messages = self.messages[-max_messages:]

        summary_parts = []
        for msg in recent_messages:
            role = "用户" if msg["role"] == "user" else "助手"
            summary_parts.append(f"{role}: {msg['content']}")

        return "\n".join(summary_parts)

    def get_profile_hint(self) -> str:
        """获取用户画像提示（用于LLM）"""
        profile = self.user_profile
        hints = []

        if profile["budget"]:
            hints.append(f"预算约{profile['budget']}元")

        if profile["preferred_brands"]:
            hints.append(f"偏好品牌: {', '.join(profile['preferred_brands'])}")

        if profile["preferred_categories"]:
            hints.append(f"关注类别: {', '.join(profile['preferred_categories'])}")

        if profile.get("skin_types"):
            hints.append(f"肤质: {', '.join(profile['skin_types'])}")

        if profile.get("skin_concerns"):
            hints.append(f"诉求: {', '.join(profile['skin_concerns'])}")

        if profile["use_case"]:
            hints.append(f"主要用途: {profile['use_case']}")

        if profile["price_sensitivity"] == "high":
            hints.append("注重性价比")
        elif profile["price_sensitivity"] == "low":
            hints.append("追求高品质")

        return " | ".join(hints) if hints else "新用户"


class ConversationService:
    """
    对话服务

    管理对话上下文的存储和检索
    """

    async def get_context(self, session_id: str) -> ConversationContext:
        """
        获取对话上下文

        Args:
            session_id: 会话ID

        Returns:
            对话上下文对象
        """
        # 从数据库加载
        result = await execute_query(
            "SELECT * FROM conversations WHERE session_id = $1",
            session_id,
            fetch="one"
        )

        if result:
            # 已存在的会话 - 解析JSON字段
            messages_raw = result.get("messages")
            context_raw = result.get("context")

            # 处理messages字段
            if isinstance(messages_raw, str):
                messages = json.loads(messages_raw)
            else:
                messages = _ensure_list(messages_raw)

            # 处理context字段
            if isinstance(context_raw, str):
                context_data = json.loads(context_raw)
            else:
                context_data = context_raw or {}

            user_profile = context_data.get("user_profile", {})
            mentioned_products = context_data.get("mentioned_products", [])

            # 确保列表类型
            if not isinstance(user_profile.get("preferred_brands"), list):
                user_profile["preferred_brands"] = []
            if not isinstance(user_profile.get("preferred_categories"), list):
                user_profile["preferred_categories"] = []
            mentioned_products = _ensure_list(mentioned_products)

            logger.info(f"加载会话 {session_id}, 消息数: {len(messages)}")

            return ConversationContext(
                session_id=session_id,
                messages=messages,
                user_profile=user_profile,
                mentioned_products=mentioned_products
            )
        else:
            # 新会话
            logger.info(f"创建新会话 {session_id}")
            return ConversationContext(session_id=session_id)

    async def save_context(self, context: ConversationContext):
        """
        保存对话上下文

        Args:
            context: 对话上下文对象
        """
        # 分别准备messages和context数据
        messages_json = json.dumps(_convert_decimal_to_float(context.messages))
        context_data = {
            "user_profile": context.user_profile,
            "mentioned_products": _convert_decimal_to_float(context.mentioned_products)
        }
        context_json = json.dumps(context_data)

        # 先尝试更新
        from app.database.postgres import get_postgres_pool
        pool = get_postgres_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                # 尝试更新
                result = await conn.execute(
                    """
                    UPDATE conversations
                    SET messages = $2, context = $3, updated_at = NOW()
                    WHERE session_id = $1
                    """,
                    context.session_id,
                    messages_json,
                    context_json
                )

                # 如果没有更新任何行（UPDATE返回 "UPDATE 0"），则插入
                if result == "UPDATE 0":
                    await conn.execute(
                        """
                        INSERT INTO conversations (session_id, messages, context, created_at, updated_at)
                        VALUES ($1, $2, $3, NOW(), NOW())
                        """,
                        context.session_id,
                        messages_json,
                        context_json
                    )

        logger.debug(f"保存会话 {context.session_id}, 消息数: {len(context.messages)}")

    async def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        intent: Optional[str] = None,
        **metadata
    ):
        """
        添加单条消息并保存

        Args:
            session_id: 会话ID
            role: 角色 (user/assistant)
            content: 消息内容
            intent: 意图类型
            **metadata: 其他元数据
        """
        context = await self.get_context(session_id)
        context.add_message(role, content, intent, **metadata)
        await self.save_context(context)

    async def update_profile(self, session_id: str, intent_result: IntentResult):
        """
        更新用户画像

        Args:
            session_id: 会话ID
            intent_result: 意图识别结果
        """
        context = await self.get_context(session_id)
        context.update_profile(intent_result)
        await self.save_context(context)

    async def get_history(
        self,
        session_id: str,
        limit: int = 20
    ) -> List[Dict]:
        """
        获取对话历史

        Args:
            session_id: 会话ID
            limit: 返回消息数量

        Returns:
            消息列表
        """
        context = await self.get_context(session_id)
        return context.messages[-limit:]

    async def clear_history(self, session_id: str) -> bool:
        """
        清空对话历史

        Args:
            session_id: 会话ID

        Returns:
            是否成功
        """
        await execute_query(
            "DELETE FROM conversations WHERE session_id = $1",
            session_id,
            fetch="none"
        )
        logger.info(f"清空会话 {session_id}")
        return True


# ==================== 全局实例 ====================

_conversation_service: Optional[ConversationService] = None


def get_conversation_service() -> ConversationService:
    """获取对话服务实例"""
    global _conversation_service
    if _conversation_service is None:
        _conversation_service = ConversationService()
    return _conversation_service
