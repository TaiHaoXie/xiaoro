"""
密码验证工具

提供密码强度检查和验证功能
"""

import re
from typing import List, Optional, Tuple
from enum import Enum


class PasswordStrength(str, Enum):
    """密码强度等级"""
    WEAK = "weak"
    MEDIUM = "medium"
    STRONG = "strong"
    VERY_STRONG = "very_strong"


class PasswordValidator:
    """
    密码验证器

    检查密码强度和安全性
    """

    # 默认配置
    MIN_LENGTH = 8
    MAX_LENGTH = 128
    REQUIRE_UPPERCASE = True
    REQUIRE_LOWERCASE = True
    REQUIRE_DIGIT = True
    REQUIRE_SPECIAL = True
    FORBIDDEN_PATTERNS = [
        r"123456",  # 常见数字序列
        r"abcdef",  # 常见字母序列
        r"password",  # 常见密码
        r"qwerty",  # 键盘序列
    ]

    def __init__(
        self,
        min_length: int = MIN_LENGTH,
        max_length: int = MAX_LENGTH,
        require_uppercase: bool = REQUIRE_UPPERCASE,
        require_lowercase: bool = REQUIRE_LOWERCASE,
        require_digit: bool = REQUIRE_DIGIT,
        require_special: bool = REQUIRE_SPECIAL,
        forbidden_patterns=None
    ):
        """
        初始化密码验证器

        Args:
            min_length: 最小长度
            max_length: 最大长度
            require_uppercase: 是否要求大写字母
            require_lowercase: 是否要求小写字母
            require_digit: 是否要求数字
            require_special: 是否要求特殊字符
            forbidden_patterns: 禁止的密码模式
        """
        self.min_length = min_length
        self.max_length = max_length
        self.require_uppercase = require_uppercase
        self.require_lowercase = require_lowercase
        self.require_digit = require_digit
        self.require_special = require_special
        self.forbidden_patterns = forbidden_patterns or self.FORBIDDEN_PATTERNS

    def validate(self, password: str):
        """
        验证密码

        Args:
            password: 待验证的密码

        Returns:
            (是否通过, 错误信息列表)
        """
        # Python 3.8 兼容性：使用 Tuple 而非 tuple[]
        from typing import Tuple, List
        errors: List[str] = []

        # 检查长度
        if len(password) < self.min_length:
            errors.append(f"密码长度至少{self.min_length}位")
        if len(password) > self.max_length:
            errors.append(f"密码长度最多{self.max_length}位")

        # 检查大写字母
        if self.require_uppercase and not re.search(r'[A-Z]', password):
            errors.append("密码必须包含至少一个大写字母")

        # 检查小写字母
        if self.require_lowercase and not re.search(r'[a-z]', password):
            errors.append("密码必须包含至少一个小写字母")

        # 检查数字
        if self.require_digit and not re.search(r'\d', password):
            errors.append("密码必须包含至少一个数字")

        # 检查特殊字符
        if self.require_special:
            special_chars = r'[!@#$%^&*(),.?":{}|<>_\[\]~`+=\-]'
            if not re.search(special_chars, password):
                errors.append("密码必须包含至少一个特殊字符")

        # 检查禁止模式
        password_lower = password.lower()
        for pattern in self.forbidden_patterns:
            if pattern in password_lower:
                errors.append(f"密码包含禁止的模式: {pattern}")

        return len(errors) == 0, errors

    def get_strength(self, password: str) -> PasswordStrength:
        """
        获取密码强度

        Args:
            password: 密码

        Returns:
            密码强度等级
        """
        score = 0

        # 长度评分
        if len(password) >= 8:
            score += 1
        if len(password) >= 12:
            score += 1
        if len(password) >= 16:
            score += 1

        # 字符类型评分
        has_upper = bool(re.search(r'[A-Z]', password))
        has_lower = bool(re.search(r'[a-z]', password))
        has_digit = bool(re.search(r'\d', password))
        has_special = bool(re.search(r'[!@#$%^&*(),.?":{}|<>_\[\]~`+=\-]', password))

        char_types = sum([has_upper, has_lower, has_digit, has_special])
        score += char_types

        # 复杂度评分
        if char_types >= 3 and len(password) >= 10:
            score += 1

        # 判断强度
        if score <= 2:
            return PasswordStrength.WEAK
        elif score <= 4:
            return PasswordStrength.MEDIUM
        elif score <= 5:
            return PasswordStrength.STRONG
        else:
            return PasswordStrength.VERY_STRONG


# 全局默认验证器
_default_validator = PasswordValidator()


def validate_password(password: str):
    """
    验证密码（使用默认验证器）

    Args:
        password: 待验证的密码

    Returns:
        (是否通过, 错误信息列表)
    """
    return _default_validator.validate(password)


def get_password_strength(password: str) -> PasswordStrength:
    """
    获取密码强度（使用默认验证器）

    Args:
        password: 密码

    Returns:
        密码强度等级
    """
    return _default_validator.get_strength(password)


# ==================== Pydantic 验证器 ====================

from pydantic import field_validator


def validate_password_strength(value: str) -> str:
    """
    Pydantic 密码强度验证器

    用法:
        class UserCreate(BaseModel):
            password: str

            @field_validator("password")
            @classmethod
            def validate_password(cls, v: str) -> str:
                return validate_password_strength(v)
    """
    is_valid, errors = validate_password(value)

    if not is_valid:
        raise ValueError("; ".join(errors))

    return value


# ==================== 使用示例 ====================

"""
# 直接使用
from app.security.password import validate_password, get_password_strength

password = "MyP@ssw0rd123"
is_valid, errors = validate_password(password)
if is_valid:
    strength = get_password_strength(password)
    print(f"密码强度: {strength}")
else:
    print(f"密码验证失败: {errors}")


# 在Pydantic模型中使用
from pydantic import BaseModel, Field, field_validator
from app.security.password import validate_password_strength

class UserCreate(BaseModel):
    password: str = Field(..., min_length=8)

    @field_validator("password")
    @classmethod
    def validate_password_strength(cls, v: str) -> str:
        return validate_password_strength(v)
"""
