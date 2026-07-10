"""
认证API模块

处理用户注册、登录、Token刷新等
"""

from fastapi import APIRouter, HTTPException, status, Depends
from fastapi.security import HTTPBearer
from pydantic import BaseModel, Field
from typing import Optional
import logging

from app.core.auth import (
    AuthService,
    UserCreate,
    UserLogin,
    UserResponse,
    Token,
    get_current_user,
    get_current_active_user,
    UserInDB,
)
from app.database.postgres import execute_query

logger = logging.getLogger(__name__)

# 创建路由器
router = APIRouter(prefix="/auth", tags=["认证"])


# ==================== 数据模型 ====================

class RegisterResponse(BaseModel):
    """注册响应"""
    user: UserResponse
    token: Token


class RefreshTokenRequest(BaseModel):
    """刷新Token请求"""
    refresh_token: str


# ==================== 注册接口 ====================

@router.post("/register", response_model=RegisterResponse)
async def register(user_data: UserCreate):
    """
    用户注册

    创建新用户账号并返回访问令牌

    请求示例：
    ```json
    {
        "username": "testuser",
        "password": "password123",
        "email": "test@example.com"
    }
    ```
    """
    auth_service = AuthService()

    try:
        # 注册用户
        user = await auth_service.register(
            username=user_data.username,
            password=user_data.password,
            email=user_data.email
        )

        # 创建Token
        from app.core.auth import create_access_token, create_refresh_token
        from app.config import settings

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

        return RegisterResponse(
            user=UserResponse(
                id=user.id,
                username=user.username,
                email=user.email,
                is_active=user.is_active,
                created_at=user.created_at
            ),
            token=token
        )

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"注册失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="注册失败，请稍后重试"
        )


# ==================== 登录接口 ====================

@router.post("/login", response_model=Token)
async def login(login_data: UserLogin):
    """
    用户登录

    使用用户名和密码登录，返回访问令牌

    请求示例：
    ```json
    {
        "username": "testuser",
        "password": "password123"
    }
    ```
    """
    auth_service = AuthService()

    try:
        user, token = await auth_service.login(
            username=login_data.username,
            password=login_data.password
        )

        return token

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"登录失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="登录失败，请稍后重试"
        )


# ==================== 刷新Token ====================

@router.post("/refresh", response_model=Token)
async def refresh_token(request: RefreshTokenRequest):
    """
    刷新访问令牌

    使用刷新令牌获取新的访问令牌

    请求示例：
    ```json
    {
        "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
    }
    ```
    """
    auth_service = AuthService()

    token = await auth_service.refresh_access_token(request.refresh_token)

    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效的刷新令牌"
        )

    return token


# ==================== 获取当前用户 ====================

@router.get("/me", response_model=UserResponse)
async def get_me(
    current_user: UserInDB = Depends(get_current_user)
):
    """
    获取当前用户信息

    需要认证，返回当前登录用户的详细信息
    """
    return UserResponse(
        id=current_user.id,
        username=current_user.username,
        email=current_user.email,
        is_active=current_user.is_active,
        created_at=current_user.created_at,
        last_login=current_user.last_login
    )


# ==================== 登出 ====================

@router.post("/logout")
async def logout(
    current_user: UserInDB = Depends(get_current_user)
):
    """
    用户登出

    注意：JWT是无状态的，客户端只需删除Token即可
    此接口主要用于记录日志或执行清理操作
    """
    logger.info(f"用户登出: {current_user.username}")

    return {
        "message": "登出成功",
        "hint": "客户端应删除存储的Token"
    }


# ==================== 修改密码 ====================

class ChangePasswordRequest(BaseModel):
    """修改密码请求"""
    old_password: str = Field(..., min_length=6)
    new_password: str = Field(..., min_length=6)


@router.post("/change-password")
async def change_password(
    request: ChangePasswordRequest,
    current_user: UserInDB = Depends(get_current_user)
):
    """
    修改密码

    需要提供旧密码验证身份
    """
    from app.core.auth import verify_password, get_password_hash

    # 验证旧密码
    if not verify_password(request.old_password, current_user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="原密码错误"
        )

    # 更新密码
    new_hashed_password = get_password_hash(request.new_password)

    await execute_query(
        "UPDATE users SET hashed_password = $1 WHERE id = $2",
        new_hashed_password,
        current_user.id,
        fetch="none"
    )

    logger.info(f"用户修改密码: {current_user.username}")

    return {"message": "密码修改成功"}


# ==================== 健康检查 ====================

@router.get("/health")
async def auth_health():
    """
    认证服务健康检查
    """
    return {
        "service": "auth",
        "status": "healthy",
        "features": {
            "register": True,
            "login": True,
            "refresh_token": True,
            "change_password": True
        }
    }


# ==================== 使用示例 ====================

"""
# 在 main.py 中注册路由
from app.api.v1 import auth

app.include_router(auth.router, prefix="/api/v1")


# 客户端调用示例

# 1. 注册
POST /api/v1/auth/register
{
    "username": "testuser",
    "password": "password123",
    "email": "test@example.com"
}

# 2. 登录
POST /api/v1/auth/login
{
    "username": "testuser",
    "password": "password123"
}

# 3. 使用Token访问受保护的接口
GET /api/v1/auth/me
Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...

# 4. 刷新Token
POST /api/v1/auth/refresh
{
    "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
}
"""
