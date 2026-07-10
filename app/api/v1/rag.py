"""
RAG检索API模块

提供增强的检索和问答接口
"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime
import logging

from app.services.rag import get_rag_retriever
from app.services.knowledge_base import get_knowledge_service

logger = logging.getLogger(__name__)

# 创建路由器
router = APIRouter(prefix="/rag", tags=["RAG检索"])


# ==================== 数据模型 ====================

class RAGQueryRequest(BaseModel):
    """RAG查询请求"""
    query: str = Field(..., description="查询问题", min_length=1)
    category: Optional[str] = Field(None, description="商品类别过滤")
    brand: Optional[str] = Field(None, description="品牌过滤")
    price_min: Optional[float] = Field(None, description="最低价格")
    price_max: Optional[float] = Field(None, description="最高价格")
    top_k: int = Field(5, description="返回结果数量", ge=1, le=20)


class RAGQueryResponse(BaseModel):
    """RAG查询响应"""
    query: str
    response: str
    sources: List[Dict] = Field(default_factory=list)
    products: List[Dict] = Field(default_factory=list)
    knowledge: List[Dict] = Field(default_factory=list)


class KnowledgeAddRequest(BaseModel):
    """添加知识请求"""
    title: str = Field(..., description="知识标题")
    content: str = Field(..., description="知识内容")
    type: str = Field(..., description="知识类型")
    product_id: Optional[int] = Field(None, description="关联商品ID")
    metadata: Optional[Dict] = Field(None, description="额外元数据")


# ==================== RAG接口 ====================

@router.post("/query", response_model=RAGQueryResponse)
async def rag_query(request: RAGQueryRequest):
    """
    RAG增强查询

    综合使用向量检索和知识库搜索，生成高质量回答

    请求示例：
    ```json
    {
        "query": "iPhone 15 和华为 Mate 60 哪个更好？",
        "top_k": 5
    }
    ```
    """
    try:
        # 构建过滤条件
        filters = {}
        if request.category:
            filters["category"] = request.category
        if request.brand:
            filters["brand"] = request.brand
        if request.price_min:
            filters["price_min"] = request.price_min
        if request.price_max:
            filters["price_max"] = request.price_max

        # 执行RAG查询
        retriever = get_rag_retriever()
        result = await retriever.rag_query(
            query=request.query,
            filters=filters if filters else None,
            top_k=request.top_k
        )

        return RAGQueryResponse(**result)

    except Exception as e:
        logger.error(f"RAG查询错误: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/search")
async def rag_search(
    query: str = Query(..., description="搜索关键词"),
    category: Optional[str] = Query(None, description="类别过滤"),
    top_k: int = Query(5, description="返回数量")
):
    """
    快速检索（不生成回答）

    仅返回检索结果，不调用LLM生成回答
    """
    try:
        filters = {}
        if category:
            filters["category"] = category

        retriever = get_rag_retriever()
        results = await retriever.retrieve(
            query=query,
            filters=filters if filters else None,
            top_k=top_k
        )

        return {
            "query": query,
            "total_products": len(results["products"]),
            "total_knowledge": len(results["knowledge"]),
            "products": results["products"],
            "knowledge": results["knowledge"]
        }

    except Exception as e:
        logger.error(f"RAG检索错误: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 知识库管理接口 ====================

@router.post("/knowledge")
async def add_knowledge(request: KnowledgeAddRequest):
    """
    添加知识条目

    用于构建和维护知识库
    """
    try:
        knowledge_service = get_knowledge_service()
        knowledge_id = await knowledge_service.add_knowledge(
            title=request.title,
            content=request.content,
            knowledge_type=request.type,
            metadata=request.metadata,
            product_id=request.product_id
        )

        return {
            "success": True,
            "knowledge_id": knowledge_id,
            "message": "知识条目添加成功"
        }

    except Exception as e:
        logger.error(f"添加知识失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/knowledge")
async def list_knowledge(
    type: Optional[str] = Query(None, description="知识类型"),
    product_id: Optional[int] = Query(None, description="商品ID"),
    limit: int = Query(20, description="返回数量")
):
    """
    获取知识库列表
    """
    try:
        knowledge_service = get_knowledge_service()

        if product_id:
            results = await knowledge_service.get_product_knowledge(
                product_id=product_id,
                knowledge_type=type
            )
        elif type == "faq":
            results = await knowledge_service.get_faq()
        else:
            # 搜索全部知识
            results = await knowledge_service.search_knowledge(
                query="",
                knowledge_type=type,
                top_k=limit
            )

        return {
            "total": len(results),
            "results": results[:limit]
        }

    except Exception as e:
        logger.error(f"获取知识库失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/knowledge/faq")
async def get_faq(
    category: Optional[str] = Query(None, description="问题分类")
):
    """
    获取常见问题
    """
    try:
        knowledge_service = get_knowledge_service()
        faqs = await knowledge_service.get_faq(category=category)

        return {
            "total": len(faqs),
            "results": faqs
        }

    except Exception as e:
        logger.error(f"获取FAQ失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/knowledge/{knowledge_id}")
async def delete_knowledge(knowledge_id: int):
    """
    删除知识条目
    """
    try:
        knowledge_service = get_knowledge_service()
        success = await knowledge_service.delete_knowledge(knowledge_id)

        return {
            "success": success,
            "message": f"知识条目 {knowledge_id} 已删除"
        }

    except Exception as e:
        logger.error(f"删除知识失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 测试接口 ====================

@router.post("/test/init-sample-data")
async def init_sample_data():
    """
    初始化示例数据

    用于测试RAG功能
    """
    try:
        knowledge_service = get_knowledge_service()

        # 添加示例FAQ
        faqs = [
            {
                "title": "如何选择合适的手机？",
                "content": "选择手机时需要考虑：1）预算范围；2）使用需求（游戏/拍照/办公）；3）品牌偏好；4）系统选择（iOS/Android）。建议根据实际需求平衡性能和价格。",
                "type": "faq",
                "metadata": {"category": "选购指南", "tags": ["手机", "选购"]}
            },
            {
                "title": "什么是降噪耳机？",
                "content": "降噪耳机通过主动降噪技术，利用麦克风采集外界噪音，然后产生反向声波抵消噪音。适合在嘈杂环境（如地铁、飞机）中使用，可以提供更安静的听音体验。",
                "type": "faq",
                "metadata": {"category": "产品知识", "tags": ["耳机", "降噪"]}
            },
            {
                "title": "商品支持退换货吗？",
                "content": "支持！7天无理由退货，15天免费换货。商品需保持完好状态，配件齐全。定制商品、生鲜商品等特殊品类除外。",
                "type": "faq",
                "metadata": {"category": "售后", "tags": ["退换货", "售后政策"]}
            }
        ]

        added = []
        for faq in faqs:
            try:
                kid = await knowledge_service.add_knowledge(**faq)
                added.append(kid)
            except:
                pass  # 可能已存在

        # 添加商品知识
        product_knowledge = [
            {
                "title": "iPhone 15 Pro 产品亮点",
                "content": "iPhone 15 Pro 采用钛金属边框，搭载A17 Pro芯片，支持USB-C接口。相机系统升级为48MP主摄，支持5倍光学变焦。屏幕支持120Hz ProMotion自适应刷新率。",
                "type": "product_desc",
                "product_id": 1,
                "metadata": {"features": ["钛金属", "A17 Pro", "USB-C", "48MP相机"]}
            },
            {
                "title": "AirPods Pro 使用技巧",
                "content": "1）佩戴检测：取下单只耳机会自动暂停播放；2）空间音频：支持动态头部追踪；3）降噪模式：长按耳柄切换降噪/通透模式；4）查找耳机：通过查找App定位。",
                "type": "user_guide",
                "product_id": 4,
                "metadata": {"product": "AirPods Pro"}
            }
        ]

        for pk in product_knowledge:
            try:
                kid = await knowledge_service.add_knowledge(**pk)
                added.append(kid)
            except:
                pass

        return {
            "success": True,
            "message": f"已添加 {len(added)} 条示例知识",
            "added_ids": added
        }

    except Exception as e:
        logger.error(f"初始化示例数据失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/test/demo-query")
async def demo_query():
    """
    演示RAG查询效果
    """
    try:
        # 先初始化示例数据
        await init_sample_data()

        # 执行演示查询
        retriever = get_rag_retriever()

        demo_queries = [
            "如何选择合适的手机？",
            "iPhone 15 Pro 有什么特点？",
            "降噪耳机是什么？"
        ]

        results = []
        for query in demo_queries:
            result = await retriever.rag_query(query, top_k=3)
            results.append({
                "query": query,
                "response": result["response"][:200] + "...",
                "sources_count": len(result["sources"])
            })

        return {
            "demo_results": results
        }

    except Exception as e:
        logger.error(f"演示查询失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))
