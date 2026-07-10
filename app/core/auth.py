"""
认证授权模块

提供JWT认证和用户管理功能
"""

from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, Tuple
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel, Field, EmailStr, field_validator
import logging

from app.config import settings
from app.security.password import validate_password_strength

logger = logging.getLogger(__name__)


# ==================== 密码加密 ====================

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    验证密码

    Args:
        plain_password: 明文密码
        hashed_password: 哈希密码

    Returns:
        是否匹配
    """
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """
    获取密码哈希

    Args:
        password: 明文密码

    Returns:
        哈希后的密码
    """
    return pwd_context.hash(password)


# ==================== JWT Token ====================

def create_access_token(
    data: Dict[str, Any],
    expires_delta: Optional[timedelta] = None
) -> str:
    """
    创建访问令牌

    Args:
        data: 要编码的数据
        expires_delta: 过期时间增量

    Returns:
        JWT令牌字符串
    """
    to_encode = data.copy()

    # 设置过期时间（使用timezone-aware datetime）
    now = datetime.now(timezone.utc)
    if expires_delta:
        expire = now + expires_delta
    else:
        expire = now + timedelta(
            minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
        )

    to_encode.update({"exp": expire.timestamp(), "iat": now.timestamp()})

    # 编码JWT
    encoded_jwt = jwt.encode(
        to_encode,
        settings.SECRET_KEY,
        algorithm="HS256"
    )

    return encoded_jwt


def create_refresh_token(data: Dict[str, Any]) -> str:
    """
    创建刷新令牌

    Args:
        data: 要编码的数据

    Returns:
        刷新令牌字符串
    """
    to_encode = data.copy()
    now = datetime.now(timezone.utc)
    expire = now + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)

    to_encode.update({
        "exp": expire.timestamp(),
        "iat": now.timestamp(),
        "type": "refresh"
    })

    encoded_jwt = jwt.encode(
        to_encode,
        settings.SECRET_KEY,
        algorithm="HS256"
    )

    return encoded_jwt


def decode_token(token: str) -> Optional[Dict[str, Any]]:
    """
    解码JWT令牌

    Args:
        token: JWT令牌字符串

    Returns:
        解码后的数据，失败返回None
    """
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=["HS256"]
        )
        return payload
    except JWTError as e:
        logger.warning(f"Token解码失败: {e}")
        return None


# ==================== 数据模型 ====================

class Token(BaseModel):
    """Token响应"""
    access_token: str
    refresh_token: Optional[str] = None
    token_type: str = "bearer"
    expires_in: int = Field(..., description="过期时间（秒）")


class TokenData(BaseModel):
    """Token数据"""
    user_id: int
    username: str
    exp: Optional[int] = None


class UserBase(BaseModel):
    """用户基础模型"""
    username: str = Field(..., min_length=3, max_length=50)
    email: Optional[EmailStr] = None


class UserCreate(UserBase):
    """用户创建"""
    password: str = Field(..., min_length=8, max_length=100)

    @field_validator("password")
    @classmethod
    def validate_password_strength(cls, v: str) -> str:
        """密码强度验证"""
        return validate_password_strength(v)


class UserLogin(BaseModel):
    """用户登录"""
    username: str = Field(..., min_length=3)
    password: str = Field(..., min_length=6)


class UserResponse(UserBase):
    """用户响应"""
    id: int
    is_active: bool = True
    created_at: Optional[datetime] = None
    last_login: Optional[datetime] = None


class UserInDB(UserBase):
    """数据库中的用户"""
    id: int
    hashed_password: str
    is_active: bool = True
    created_at: Optional[datetime] = None
    last_login: Optional[datetime] = None

    def verify_password(self, password: str) -> bool:
        """验证密码"""
        return verify_password(password, self.hashed_password)


# ==================== 认证服务 ====================

class AuthService:
    """
    认证服务

    处理用户注册、登录、Token验证等
    """

    async def register(
        self,
        username: str,
        password: str,
        email: Optional[str] = None
    ) -> UserInDB:
        """
        用户注册

        Args:
            username: 用户名
            password: 密码
            email: 邮箱

        Returns:
            创建的用户
        """
        from app.database.postgres import execute_query

        # 检查用户名是否已存在
        existing = await execute_query(
            "SELECT id FROM users WHERE username = $1",
            username,
            fetch="one"
        )

        if existing:
            raise ValueError(f"用户名 '{username}' 已存在")

        # 检查邮箱是否已存在
        if email:
            existing_email = await execute_query(
                "SELECT id FROM users WHERE email = $1",
                email,
                fetch="one"
            )

            if existing_email:
                raise ValueError(f"邮箱 '{email}' 已被注册")

        # 创建用户
        hashed_password = get_password_hash(password)

        result = await execute_query(
            """INSERT INTO users (username, email, hashed_password)
               VALUES ($1, $2, $3)
               RETURNING id, username, email, is_active, created_at""",
            username,
            email,
            hashed_password,
            fetch="one"
        )

        logger.info(f"✅ 用户注册成功: {username}")

        return UserInDB(**result)

    async def login(
        self,
        username: str,
        password: str
    ) -> Tuple[UserInDB, Token]:
        """
        用户登录

        Args:
            username: 用户名
            password: 密码

        Returns:
            用户信息和Token
        """
        from app.database.postgres import execute_query

        # 查询用户
        result = await execute_query(
            "SELECT * FROM users WHERE username = $1",
            username,
            fetch="one"
        )

        if not result:
            raise ValueError("用户名或密码错误")

        user = UserInDB(**result)

        # 验证密码
        if not user.verify_password(password):
            raise ValueError("用户名或密码错误")

        # 检查账户状态
        if not user.is_active:
            raise ValueError("账户已被禁用")

        # 更新最后登录时间
        await execute_query(
            "UPDATE users SET last_login = NOW() WHERE id = $1",
            user.id,
            fetch="none"
        )

        # 创建Token
        access_token = create_access_token(
            data={"sub": str(user.id), "username": user.username}
        )

        refresh_token = create_refresh_token(
            data={"sub": str(user.id)}
        )

        token = Token(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
        )

        logger.info(f"✅ 用户登录成功: {username}")

        return user, token

    async def get_current_user(
        self,
        token: str
    ) -> Optional[UserInDB]:
        """
        获取当前用户

        Args:
            token: JWT令牌

        Returns:
            用户信息，失败返回None
        """
        from app.database.postgres import execute_query

        # 解码Token
        payload = decode_token(token)
        if payload is None:
            return None

        user_id = payload.get("sub")
        if user_id is None:
            return None

        # 查询用户
        try:
            result = await execute_query(
                "SELECT * FROM users WHERE id = $1",
                int(user_id),
                fetch="one"
            )

            if result:
                return UserInDB(**result)
        except Exception as e:
            logger.error(f"获取用户信息失败: {e}")

        return None

    async def refresh_access_token(
        self,
        refresh_token: str
    ) -> Optional[Token]:
        """
        刷新访问令牌

        Args:
            refresh_token: 刷新令牌

        Returns:
            新的Token，失败返回None
        """
        payload = decode_token(refresh_token)
        if payload is None:
            return None

        if payload.get("type") != "refresh":
            return None

        user_id = payload.get("sub")
        if user_id is None:
            return None

        # 创建新的访问令牌
        access_token = create_access_token(
            data={"sub": user_id}
        )

        return Token(
            access_token=access_token,
            expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
        )


# ==================== FastAPI依赖 ====================

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

security = HTTPBearer(auto_error=False)


async def get_optional_user(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> Optional[UserInDB]:
    """
    获取可选的当前用户（允许匿名访问）

    用于需要用户上下文但不强制登录的接口
    """
    if credentials is None:
        return None

    auth_service = AuthService()
    user = await auth_service.get_current_user(credentials.credentials)
    return user


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> UserInDB:
    """
    获取当前用户（必须登录）

    用于需要认证的接口
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未提供认证信息",
            headers={"WWW-Authenticate": "Bearer"},
        )

    auth_service = AuthService()
    user = await auth_service.get_current_user(credentials.credentials)

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效的认证信息",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="账户已被禁用"
        )

    return user


async def get_current_active_user(
    current_user: UserInDB = Depends(get_current_user)
) -> UserInDB:
    """
    获取当前活跃用户（必须登录且未禁用）
    """
    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="账户已被禁用"
        )
    return current_user


# ==================== 数据库表创建 ====================

CREATE_USERS_TABLE_SQL = """
-- 用户表
CREATE TABLE IF NOT EXISTS users (
    id BIGSERIAL PRIMARY KEY,
    username VARCHAR(50) UNIQUE NOT NULL,
    email VARCHAR(255) UNIQUE,
    hashed_password VARCHAR(255) NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW(),
    last_login TIMESTAMP
);

-- 创建索引
CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
"""


# ==================== 全局实例 ====================

_auth_service: Optional[AuthService] = None


def get_auth_service() -> AuthService:
    """获取认证服务单例"""
    global _auth_service
    if _auth_service is None:
        _auth_service = AuthService()
    return _auth_service


# ==================== 使用示例 ====================

"""
# 在API中使用
from app.core.auth import (
    UserLogin,
    UserCreate,
    Token,
    get_current_user,
    get_optional_user,
    get_auth_service
)

@router.post("/register", response_model=UserResponse)
async def register(user_data: UserCreate):
    auth_service = get_auth_service()
    user = await auth_service.register(
        username=user_data.username,
        password=user_data.password,
        email=user_data.email
    )
    return UserResponse(**user.dict())

@router.post("/login", response_model=Token)
async def login(login_data: UserLogin):
    auth_service = get_auth_service()
    _, token = await auth_service.login(
        username=login_data.username,
        password=login_data.password
    )
    return token

@router.get("/me", response_model=UserResponse)
async def get_me(current_user: UserInDB = Depends(get_current_user)):
    return UserResponse(**current_user.dict())

@router.post("/chat")
async def chat(
    request: ChatRequest,
    current_user: Optional[UserInDB] = Depends(get_optional_user)
):
    # current_user 可能为 None（匿名用户）
    user_id = current_user.id if current_user else None
    ...
"""
