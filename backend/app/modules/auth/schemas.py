"""认证模块 Pydantic 请求/响应模型。

输入校验在 API 边界完成（防注入/防畸形输入的第一道防线）：
- 用户名白名单正则：仅允许字母/数字/下划线/常用汉字，3-64 位，
  从源头拒绝 SQL 注入载荷（' ; -- /*）与 XSS（< > "）所需的特殊字符。
- 密码：6-128 位，禁止控制字符，不能全为空白。

说明：SQL 注入的主防线是 SQLAlchemy 参数化查询（见 repositories.py），
此处白名单校验是"纵深防御"的第二道防线。
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict
from pydantic_core import PydanticCustomError

# 用户名白名单：字母（含大小写）、数字、下划线、常用汉字（一-龥）。
# re.fullmatch 要求"整串匹配"，杜绝首尾/中间夹带非法字符。
_USERNAME_RE = re.compile(r"[A-Za-z0-9_一-龥]{3,64}")

# 密码规则：禁止 ASCII 控制字符与 DEL（\x00-\x1f、\x7f）。
_PASSWORD_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")


def _check_username(v: str) -> str:
    if not _USERNAME_RE.fullmatch(v):
        raise PydanticCustomError(
            "username_invalid", "用户名只能包含字母、数字、下划线或中文，长度 3-64"
        )
    return v


def _check_password(v: str) -> str:
    if not (6 <= len(v) <= 128) or not v.strip() or _PASSWORD_CTRL_RE.search(v):
        raise PydanticCustomError(
            "password_invalid", "密码长度需 6-128 位，且不能全为空白或含控制字符"
        )
    return v


# 可复用的"带校验类型"：三处请求模型共用同一套规则，避免规则漂移。
Username = Annotated[str, AfterValidator(_check_username)]
Password = Annotated[str, AfterValidator(_check_password)]


class RegisterRequest(BaseModel):
    username: Username
    password: Password


class LoginRequest(BaseModel):
    username: Username
    password: Password


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: Password


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    role: str
    nickname: str | None = None
    created_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut
