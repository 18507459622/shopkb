"""FastAPI 依赖：当前用户 / 角色守卫。授权关键判断重读 DB（角色降级即时生效）。"""
from __future__ import annotations

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.exceptions import AuthenticationError, PermissionDeniedError
from app.core.security import decode_access_token
from app.models import User

_bearer = HTTPBearer(auto_error=False)


async def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
) -> User:
    if creds is None:
        raise AuthenticationError()
    try:
        payload = decode_access_token(creds.credentials)
    except Exception:
        raise AuthenticationError("凭证无效或已过期") from None
    if payload.get("type") != "access":
        raise AuthenticationError()
    user = await db.get(User, int(payload["sub"]))
    if user is None or not user.is_active:
        raise AuthenticationError()
    return user


def require_role(role: str):
    async def _require(user: User = Depends(get_current_user)) -> User:
        if user.role != role:
            raise PermissionDeniedError()
        return user

    return _require


require_admin = require_role("admin")
