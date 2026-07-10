"""
对话API模块

处理用户对话请求，支持流式输出
集成意图识别、RAG检索、LLM生成
"""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any, AsyncGenerator, Tuple
from datetime import datetime
import logging
import json

from app.services.llm import get_llm_service
from app.services.intent import get_intent_service, IntentResult
from app.services.agent import get_shopping_agent
from app.services.conversation import _convert_decimal_to_float
from app.database.postgres import execute_query
from app.config import settings

logger = logging.getLogger(__name__)

# 创建路由器
router = APIRouter(prefix="/chat", tags=["对话"])


def _sse_json(payload: Dict[str, Any]) -> str:
    """Serialize SSE payloads safely, including Decimal values from DB rows."""
    return json.dumps(_convert_decimal_to_float(payload), ensure_ascii=False)

# 懒加载服务实例（使用时再初始化）
def get_services():
    """获取服务实例（懒加载）"""
    return {
        "agent": get_shopping_agent(),
    }


# ==================== 数据模型 ====================

class ChatMessage(BaseModel):
    """聊天消息"""
    role: str = Field(..., description="角色：user/assistant/system")
    content: str = Field(..., description="消息内容")
    timestamp: Optional[datetime] = None


class ChatRequest(BaseModel):
    """聊天请求"""
    model_config = {"extra": "ignore"}

    message: str = Field(..., description="用户消息", min_length=1)
    session_id: Optional[str] = Field(None, description="会话ID")
    conversation_history: Optional[List[ChatMessage]] = Field(
        default_factory=list,
        description="历史对话记录"
    )
    stream: bool = Field(default=False, description="是否流式输出")
    image_context: Optional[Dict[str, Any]] = Field(default=None, description="图片上下文（识图场景）")
    images: Optional[List[str]] = Field(default=None, description="上传图片文件名列表（兼容）")


class ChatResponse(BaseModel):
    """聊天响应（增强版：包含来源引用和避坑提示）"""
    response: str
    intent: Dict[str, Any] = Field(default_factory=dict, description="意图识别结果（旧系统）")
    scenario_intent: Dict[str, Any] = Field(default_factory=dict, description="场景意图（新系统）")
    sources: List[Dict] = Field(default_factory=list, description="来源列表（旧格式，保留兼容）")
    citations: List[Dict] = Field(default_factory=list, description="增强的来源引用（新格式）")
    pitfalls: List[Dict] = Field(default_factory=list, description="避坑提示")
    products: List[Dict] = Field(default_factory=list)
    comparison_data: Optional[Dict] = None
    decision_process: Optional[Dict] = None  # 决策过程可视化
    metadata: Dict[str, Any] = Field(default_factory=dict)
    session_id: str


class TestIntentRequest(BaseModel):
    """测试意图识别请求"""
    message: str = Field(..., description="测试消息")


# ==================== 对话接口 ====================

@router.post("/message", response_model=ChatResponse)
async def chat_message(request: Request, payload: ChatRequest):
    """
    智能对话接口（非流式）

    整合意图识别、RAG检索、商品搜索、决策辅助
    """
    try:
        session_id = payload.session_id or f"session_{datetime.now().timestamp()}"
        history = [m.model_dump() for m in (payload.conversation_history or [])]

        if settings.USE_V2_AGENT:
            from app.services.v2.agent import get_v2_shopping_agent
            agent = get_v2_shopping_agent()

            text_parts: List[str] = []
            products: List[Dict[str, Any]] = []
            pitfalls: List[Dict[str, Any]] = []
            citations: List[Dict[str, Any]] = []
            comparison: Optional[Dict[str, Any]] = None
            decision_process: Optional[Dict[str, Any]] = None
            answer_contract: Optional[Dict[str, Any]] = None
            intent_data: Optional[Dict[str, Any]] = None

            async for item in agent.chat_stream_events(
                message=payload.message,
                session_id=session_id,
                conversation_history=history,
                image_context=payload.image_context,
            ):
                event_name = item.get("event", "message")
                event_data = item.get("data", {})
                if event_name == "message" and isinstance(event_data, dict):
                    content = event_data.get("content") or ""
                    if content:
                        text_parts.append(content)
                elif event_name == "products" and isinstance(event_data, dict):
                    products = event_data.get("products") or []
                elif event_name == "pitfalls" and isinstance(event_data, dict):
                    pitfalls = event_data.get("pitfalls") or []
                elif event_name == "citations" and isinstance(event_data, dict):
                    citations = event_data.get("citations") or []
                elif event_name == "comparison" and isinstance(event_data, dict):
                    comparison = event_data
                elif event_name == "decision_process" and isinstance(event_data, dict):
                    decision_process = event_data.get("decision_process")
                elif event_name == "answer_contract" and isinstance(event_data, dict):
                    answer_contract = event_data.get("answer_contract")
                elif event_name == "intent" and isinstance(event_data, dict):
                    intent_data = event_data

            full_text = "".join(text_parts)
            return {
                "response": full_text,
                "intent": intent_data or {},
                "scenario_intent": {},
                "sources": [],
                "citations": citations,
                "pitfalls": pitfalls,
                "products": products,
                "comparison_data": comparison,
                "decision_process": decision_process,
                "metadata": {
                    "v2": True,
                    "answer_contract": answer_contract,
                },
                "session_id": session_id,
            }

        agent = get_shopping_agent()

        result = await agent.chat(
            message=payload.message,
            session_id=session_id,
            context={"history": payload.conversation_history}
        )

        result["session_id"] = session_id

        if "scenario_intent" in result:
            result["scenario_intent"] = {
                "intent": result.get("scenario_intent"),
                "priority": result.get("priority", "中"),
                "prompt": result.get("scenario_prompt", "")[:200] + "..." if len(result.get("scenario_prompt", "")) > 200 else result.get("scenario_prompt", "")
            }

        return result

    except Exception as e:
        logger.error(f"对话处理错误: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/stream")
async def chat_stream(request: Request, payload: ChatRequest):
    """
    智能流式对话接口（SSE）

    实时流式返回回复，整合所有Agent能力

    请求示例：
    ```json
    {
        "message": "我想买个手机打游戏，5000左右",
        "stream": true
    }
    ```
    """
    async def generate() -> AsyncGenerator[str, None]:
        """生成SSE事件流"""
        try:
            history = [m.model_dump() for m in (payload.conversation_history or [])]

            if settings.USE_V2_AGENT:
                from app.services.v2.agent import get_v2_shopping_agent
                agent = get_v2_shopping_agent()

                yield f"event: start\ndata: {_sse_json({'session_id': payload.session_id})}\n\n"

                async for item in agent.chat_stream_events(
                    message=payload.message,
                    session_id=payload.session_id,
                    conversation_history=history,
                    image_context=payload.image_context,
                ):
                    event_name = item.get("event", "message")
                    event_data = item.get("data", {})
                    yield f"event: {event_name}\ndata: {_sse_json(event_data)}\n\n"

                yield f"event: message\ndata: {_sse_json({'content': '', 'done': True})}\n\n"

            else:
                agent = get_shopping_agent()

                yield f"event: start\ndata: {_sse_json({'message': '小 ro 正在理解你的需求'})}\n\n"

                intent_service = get_intent_service()
                intent_result = await intent_service.recognize(payload.message)

                async for item in agent.chat_stream_events(
                    message=payload.message,
                    session_id=payload.session_id,
                    context={"history": payload.conversation_history},
                    intent_result=intent_result
                ):
                    event_name = item.get("event", "message")
                    event_data = item.get("data", {})
                    yield f"event: {event_name}\ndata: {_sse_json(event_data)}\n\n"

                yield f"event: message\ndata: {_sse_json({'content': '', 'done': True})}\n\n"

        except Exception as e:
            logger.error(f"流式对话错误: {e}", exc_info=True)
            yield f"event: error\ndata: {_sse_json({'error': str(e), 'message': f'处理请求时出错：{e}'})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )


@router.get("/sessions/{session_id}")
async def get_session(session_id: str):
    """
    获取会话历史

    Args:
        session_id: 会话ID
    """
    # TODO: 从数据库获取会话历史
    try:
        messages = await execute_query(
            "SELECT * FROM conversations WHERE session_id = $1 ORDER BY created_at",
            session_id,
            fetch="all"
        )
    except Exception as e:
        logger.error(f"获取会话历史失败: {e}")
        messages = []

    return {
        "session_id": session_id,
        "messages": messages,
        "created_at": datetime.now().isoformat()
    }


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    """
    删除会话

    Args:
        session_id: 会话ID
    """
    # TODO: 从数据库删除会话
    try:
        await execute_query(
            "DELETE FROM conversations WHERE session_id = $1",
            session_id,
            fetch="none"
        )
    except Exception as e:
        logger.error(f"删除会话失败: {e}")

    return {
        "message": f"会话 {session_id} 已删除"
    }


# ==================== 测试接口 ====================

@router.post("/test/intent")
async def test_intent(request: TestIntentRequest):
    """
    测试意图识别

    Args:
        request: 测试请求

    Returns:
        意图识别结果
    """
    intent_service = get_intent_service()
    result = await intent_service.recognize(request.message, use_llm=False)
    return {
        "query": request.message,
        "intent": result.intent,
        "confidence": result.confidence,
        "entities": result.entities
    }
