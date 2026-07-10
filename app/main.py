"""
FastAPI 主入口文件

启动应用的主要入口点，包含：
- 应用初始化
- 中间件配置
- 路由注册
- 异常处理
- CORS配置
"""

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from fastapi.responses import FileResponse
from fastapi.responses import Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from slowapi.errors import RateLimitExceeded
from contextlib import asynccontextmanager
import logging
from datetime import datetime
import os

from app.config import settings
from app.core.rate_limit import limiter, rate_limit_handler

# 配置日志
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


# ==================== 应用生命周期管理 ====================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    应用生命周期管理

    启动时：初始化数据库连接、加载模型等
    关闭时：清理资源、关闭连接等
    """
    # ========== 启动时执行 ==========
    logger.info("🚀 应用启动中...")
    logger.info(f"📦 {settings.APP_NAME} v{settings.APP_VERSION}")
    logger.info(f"🌍 环境: {settings.ENVIRONMENT}")

    # 初始化数据库连接池
    try:
        from app.database.postgres import init_postgres_pool
        await init_postgres_pool()
        logger.info("✅ PostgreSQL连接池初始化成功")
    except Exception as e:
        logger.warning(f"⚠️  PostgreSQL连接池初始化失败: {e}")
        logger.info("将继续运行，但数据库相关功能可能不可用")

    # 初始化基础表结构，确保首次运行可用
    try:
        from app.database.postgres import init_database_tables
        await init_database_tables()
        logger.info("✅ 基础数据库表初始化成功")
    except Exception as e:
        logger.warning(f"⚠️  基础数据库表初始化失败: {e}")

    # 初始化缓存表（依赖PostgreSQL）
    try:
        from app.services.llm_cache import init_cache_table
        await init_cache_table()
        logger.info("✅ LLM缓存表初始化成功")
    except Exception as e:
        logger.warning(f"⚠️  LLM缓存表初始化失败: {e}")

    # 初始化Milvus连接（确保collection存在）
    try:
        from app.database.milvus import get_milvus_manager, init_collections
        get_milvus_manager()
        init_collections()
        logger.info("✅ Milvus连接和索引初始化完成")
    except Exception as e:
        logger.warning(f"⚠️  Milvus初始化失败（可能影响识图）: {e}")

    # 预热CLIP模型（优化首次响应时间）
    try:
        from app.services.image_embedding import get_image_embedding_service
        embedding_service = get_image_embedding_service()
        logger.info("✅ CLIP模型预热完成")
    except Exception as e:
        logger.warning(f"⚠️  CLIP模型预热失败: {e}")

    logger.info("✅ 应用启动完成！")

    # ========== 应用运行中 ==========
    yield

    # ========== 关闭时执行 ==========
    logger.info("⏹️  应用关闭中...")

    # 清理数据库连接
    try:
        from app.database.postgres import close_postgres_pool
        await close_postgres_pool()
        logger.info("✅ 数据库连接已关闭")
    except Exception as e:
        logger.warning(f"⚠️  数据库关闭时出错: {e}")

    logger.info("✅ 应用已安全关闭")


# ==================== 创建FastAPI应用 ====================

# 创建应用实例
app = FastAPI(
    title=settings.APP_NAME,
    description="基于RAG的多模态电商智能导购Agent",
    version=settings.APP_VERSION,
    docs_url="/docs",           # Swagger UI地址
    redoc_url="/redoc",         # ReDoc地址
    openapi_url="/openapi.json", # OpenAPI JSON地址
    lifespan=lifespan,          # 生命周期管理
)

# 设置速率限制器
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_handler)


# ==================== 中间件配置 ====================

# 1. CORS中间件（允许跨域请求）
app.add_middleware(
    CORSMiddleware,
    allow_origins=[            # 允许的源（生产环境要限制！）
        "http://localhost:3000",
        "http://localhost:5173",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,    # 允许携带Cookie
    allow_methods=["*"],        # 允许所有HTTP方法
    allow_headers=["*"],        # 允许所有请求头
)

# 2. GZip压缩中间件（压缩响应，节省带宽）
app.add_middleware(GZipMiddleware, minimum_size=1000)

static_dir = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=static_dir), name="static")


# ==================== 全局异常处理 ====================

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """
    全局异常处理器

    捕获所有未处理的异常，返回统一的错误格式
    """
    logger.error(f"未处理的异常: {exc}", exc_info=True)

    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "success": False,
            "message": "服务器内部错误",
            "detail": str(exc) if settings.DEBUG else "请联系管理员",
            "timestamp": datetime.now().isoformat()
        }
    )


# ==================== 根路由（健康检查）====================

@app.get("/", tags=["根路由"])
async def root():
    """
    根路径

    返回应用基本信息，用于健康检查
    """
    return {
        "success": True,
        "message": f"欢迎使用{settings.APP_NAME}",
        "data": {
            "name": settings.APP_NAME,
            "version": settings.APP_VERSION,
            "environment": settings.ENVIRONMENT,
            "docs_url": "/docs",
            "pages": {
                "chat": "/chat",
                "quality": "/quality",
                "knowledge": "/knowledge",
                "stream_test": "/test",
                "image_search": "/image-search",
                "rag_test": "/rag",
                "decision": "/decision"
            },
            "status": "running"
        },
        "timestamp": datetime.now().isoformat()
    }


@app.get("/health", tags=["根路由"])
async def health_check():
    """
    健康检查接口

    用于容器编排系统（如K8s）的健康检查
    """
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat()
    }


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    """
    轻量 favicon，避免浏览器重复请求 404。
    """
    svg = """
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
        <rect width="64" height="64" rx="18" fill="#B98E9C"/>
        <text x="32" y="39" text-anchor="middle" font-size="22" font-family="Arial, sans-serif" fill="#FFFFFF">Lu</text>
    </svg>
    """
    return Response(content=svg, media_type="image/svg+xml")


@app.get("/test", tags=["根路由"])
async def test_page():
    """
    流式输出测试页面
    """
    static_path = os.path.join(os.path.dirname(__file__), "..", "static")
    return FileResponse(os.path.join(static_path, "stream-test.html"))


@app.get("/image-search", tags=["根路由"])
async def image_search_page():
    """
    以图搜图测试页面
    """
    static_path = os.path.join(os.path.dirname(__file__), "..", "static")
    return FileResponse(os.path.join(static_path, "image-search.html"))


@app.get("/rag", tags=["根路由"])
async def rag_page():
    """
    RAG智能检索测试页面
    """
    static_path = os.path.join(os.path.dirname(__file__), "..", "static")
    return FileResponse(os.path.join(static_path, "rag-test.html"))


@app.get("/decision", tags=["根路由"])
async def decision_page():
    """
    决策辅助测试页面
    """
    static_path = os.path.join(os.path.dirname(__file__), "..", "static")
    return FileResponse(os.path.join(static_path, "decision-test.html"))


@app.get("/chat", tags=["根路由"])
async def chat_page():
    """
    智能对话页面
    """
    static_path = os.path.join(os.path.dirname(__file__), "static")
    return FileResponse(os.path.join(static_path, "chat.html"))


@app.get("/quality", tags=["根路由"])
async def quality_page():
    """
    质量评测后台页面
    """
    static_path = os.path.join(os.path.dirname(__file__), "static")
    return FileResponse(os.path.join(static_path, "quality.html"))


@app.get("/knowledge", tags=["根路由"])
async def knowledge_page():
    """
    知识库工作台页面
    """
    static_path = os.path.join(os.path.dirname(__file__), "static")
    return FileResponse(os.path.join(static_path, "knowledge.html"))


# ==================== 注册路由模块 ====================

# 导入路由模块
from app.api.v1 import chat, upload, search, image_search, rag, decision, evaluation, documents, admin, auth

# 注册路由
app.include_router(auth.router, prefix="/api/v1", tags=["认证"])
app.include_router(chat.router, prefix="/api/v1", tags=["对话"])
app.include_router(upload.router, prefix="/api/v1", tags=["上传"])
app.include_router(search.router, prefix="/api/v1", tags=["搜索"])
app.include_router(image_search.router, prefix="/api/v1", tags=["图片搜索"])
app.include_router(rag.router, prefix="/api/v1", tags=["RAG检索"])
app.include_router(decision.router, prefix="/api/v1", tags=["决策辅助"])
app.include_router(evaluation.router, prefix="/api/v1", tags=["质量评测"])
app.include_router(documents.router, prefix="/api/v1", tags=["文档管理"])
app.include_router(admin.router, prefix="/api/v1", tags=["管理"])


# ==================== 启动命令 ====================

"""
开发环境启动命令：
    uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

生产环境启动命令：
    uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4

使用配置文件启动：
    uvicorn app.main:app --config uvicorn_config.py
"""


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,  # 开发环境自动重载
        log_level=settings.LOG_LEVEL.lower()
    )
