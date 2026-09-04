"""密码哈希（argon2id）与 JWT / refresh token 编解码。"""
from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from app.core.config import get_settings

_password_hasher = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1)

# 用于未知用户名时的时序拉平：模块加载时预计算一次，避免每次登录都重算。
_DUMMY_HASH = _password_hasher.hash("dummy-password-for-timing-equalization")


def hash_password(password: str) -> str:
    return _password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _password_hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False


def dummy_verify() -> None:
    """未知用户名时也做一次等价校验，抹平枚举时序差。"""
    try:
        _password_hasher.verify(_DUMMY_HASH, "not-the-password")
    except VerifyMismatchError:
        pass


def create_access_token(user_id: int, role: str, jti: str | None = None) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "role": role,
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_expire_minutes),
        "jti": jti or secrets.token_hex(16),
        "iss": settings.app_name,
        "aud": settings.app_name,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_access_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    return jwt.decode(
        token, settings.jwt_secret, algorithms=["HS256"], audience=settings.app_name
    )


def generate_refresh_token() -> str:
    """refresh token 是 256-bit 随机 opaque 串（非 JWT）。"""
    return secrets.token_urlsafe(48)


def hash_refresh_token(token: str) -> str:
    """数据库只存 refresh token 的 sha256。"""
    return hashlib.sha256(token.encode()).hexdigest()
