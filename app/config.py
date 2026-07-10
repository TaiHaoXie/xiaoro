"""
配置管理模块

读取环境变量，提供统一的配置接口
使用 pydantic-settings 进行类型验证
"""

from pydantic_settings import BaseSettings
from pydantic import Field, field_validator
from typing import Optional
from functools import lru_cache
import secrets


class Settings(BaseSettings):
    """
    应用配置类

    从环境变量读取配置，并提供默认值和类型验证
    """

    # ========== 应用配置 ==========
    APP_NAME: str = "电商智能导购Agent"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = True
    ENVIRONMENT: str = "development"

    # ========== 服务器配置 ==========
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # ========== 数据库配置 ==========
    # PostgreSQL
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "postgres"
    POSTGRES_DB: str = "ecommerce_agent"

    @property
    def postgres_url(self) -> str:
        """PostgreSQL连接URL（同步）"""
        return (
            f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def async_postgres_url(self) -> str:
        """PostgreSQL连接URL（异步）"""
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    # Milvus (向量数据库)
    MILVUS_HOST: str = "localhost"
    MILVUS_PORT: int = 19530
    MILVUS_TOKEN: str = "root:Milvus"  # Milvus认证token

    @property
    def milvus_uri(self) -> str:
        """Milvus连接URI"""
        token_part = f"?token={self.MILVUS_TOKEN}" if self.MILVUS_TOKEN else ""
        return f"http://{self.MILVUS_HOST}:{self.MILVUS_PORT}{token_part}"

    # Redis (缓存)
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_PASSWORD: Optional[str] = None

    @property
    def redis_url(self) -> str:
        """Redis连接URL"""
        if self.REDIS_PASSWORD:
            return f"redis://:{self.REDIS_PASSWORD}@{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"

    # ========== LLM API 配置 ==========
    # Kimi API (主用 - 稳定)
    KIMI_API_KEY: str = ""
    KIMI_API_BASE: str = "https://api.moonshot.cn/v1"
    KIMI_CHAT_MODEL: str = "moonshot-v1-8k"
    KIMI_EMBEDDING_MODEL: str = "embed-v1"

    # 豆包 API (备用 - 需要endpoint ID)
    DOUBAO_API_KEY: str = ""
    DOUBAO_API_BASE: str = "https://ark.cn-beijing.volces.com/api/v3"
    DOUBAO_CHAT_MODEL: str = "ep-20241105174832-wlcc9"  # 示例endpoint ID
    DOUBAO_EMBEDDING_MODEL: str = "ep-20241105175329-xxxxx"
    DOUBAO_VISION_MODEL: str = "ep-20241105175714-xxxxx"

    # Embedding provider:
    # - auto: use Doubao only when a real API key + endpoint are configured
    # - doubao: force remote Doubao embedding
    # - local: deterministic local embedding for offline/demo stability
    EMBEDDING_PROVIDER: str = "auto"
    EMBEDDING_DIMENSION: int = 1024

    # 是否使用 v2 全新核心链路（TurnParser -> Router -> Retriever -> Ranker -> Presenter 全确定性链路）
    USE_V2_AGENT: bool = True

    # Intent classification:
    # Keep rule/scenario classifier as the default for stable demos.
    # Set INTENT_LLM_ENABLED=true when an external classifier model is ready.
    INTENT_LLM_ENABLED: bool = False
    INTENT_LLM_CONFIDENCE_THRESHOLD: float = 0.35

    # 硅基流动 SiliconFlow API (OpenAI 兼容，当前主用 - DeepSeek)
    DEFAULT_LLM_PROVIDER: str = "siliconflow"
    SILICONFLOW_API_KEY: str = ""
    SILICONFLOW_API_BASE: str = "https://api.siliconflow.cn/v1"
    SILICONFLOW_CHAT_MODEL: str = "deepseek-ai/DeepSeek-V3.2"

    # 智谱 API (可选)
    ZHIPU_API_KEY: str = ""
    ZHIPU_API_BASE: str = "https://open.bigmodel.cn/api/paas/v4"

    # ========== V2 Agent 开关 ==========
    # V2 生成式大模型润色开关：默认关闭（保护基线），显式设 V2_DISABLE_LLM=false/0 才开启
    V2_DISABLE_LLM: bool = True
    # V2.2 状态机开关
    V2_STATE_MACHINE_ENABLED: bool = True
    # V2 追问 LLM 裁判开关（影子模式默认关，不改变规则链路）
    V2_FOLLOWUP_LLM_JUDGE_ENABLED: bool = False
    V2_FOLLOWUP_LLM_JUDGE_SHADOW_ENABLED: bool = False
    V2_FOLLOWUP_LLM_JUDGE_MODEL: str = ""
    V2_FOLLOWUP_LLM_JUDGE_MAX_TOKENS: int = 160
    V2_FOLLOWUP_LLM_JUDGE_PROVIDER: str = ""

    # ========== 对象存储配置 ==========
    MINIO_ENDPOINT: str = "localhost:9000"
    MINIO_ACCESS_KEY: str = "minioadmin"
    MINIO_SECRET_KEY: str = "minioadmin"
    MINIO_BUCKET: str = "ecommerce"

    # ========== 安全配置 ==========
    SECRET_KEY: str = Field(
        default="development-secret-key-change-in-production",
        description="JWT签名密钥，生产环境必须使用强密钥"
    )
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    @field_validator("SECRET_KEY")
    @classmethod
    def validate_secret_key(cls, v: str) -> str:
        """
        验证SECRET_KEY强度

        生产环境必须使用安全的密钥
        """
        # 检查是否使用默认密钥
        weak_defaults = [
            "your-secret-key",
            "development-secret-key-change-in-production",
            "secret",
            "key"
        ]

        if v in weak_defaults:
            import os
            if os.getenv("ENVIRONMENT", "development") == "production":
                raise ValueError(
                    "生产环境不能使用默认SECRET_KEY！"
                    "请使用: python -c 'import secrets; print(secrets.token_urlsafe(32))' 生成安全密钥"
                )
            else:
                import warnings
                warnings.warn(
                    "当前使用默认SECRET_KEY，生产环境请更换！",
                    UserWarning
                )

        # 检查密钥长度
        if len(v) < 32:
            raise ValueError(
                f"SECRET_KEY长度不足（当前{len(v)}字符），建议至少32字符"
            )

        return v

    # ========== 缓存配置 ==========
    LLM_CACHE_TTL: int = 86400  # LLM缓存过期时间（秒），默认24小时
    REDIS_FALLBACK_ENABLED: bool = True  # Redis失败时是否降级到PostgreSQL

    # ========== 上传配置 ==========
    MAX_UPLOAD_SIZE: int = 10485760  # 10MB
    # 允许的图片类型（从环境变量读取，需要JSON格式）
    ALLOWED_IMAGE_TYPES_JSON: str = '["image/jpeg", "image/png", "image/webp"]'

    @property
    def ALLOWED_IMAGE_TYPES(self) -> list:
        """允许的图片类型列表"""
        import json
        try:
            return json.loads(self.ALLOWED_IMAGE_TYPES_JSON)
        except:
            return ["image/jpeg", "image/png", "image/webp"]

    # ========== 日志配置 ==========
    LOG_LEVEL: str = "INFO"
    LOG_FILE: str = "logs/app.log"

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": True,
        "extra": "ignore",
    }


@lru_cache()
def get_settings() -> Settings:
    """
    获取配置单例

    使用 lru_cache 确保配置只加载一次
    """
    return Settings()


# 导出配置实例（方便直接导入使用）
settings = get_settings()


# ==================== 使用示例 ====================
"""
from app.config import settings

# 使用配置
print(settings.APP_NAME)
print(settings.postgres_url)
print(settings.DOUBAO_API_KEY)

# 判断环境
if settings.ENVIRONMENT == "development":
    # 开发环境特定逻辑
    pass
"""
