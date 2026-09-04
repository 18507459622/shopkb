"""认证业务逻辑：注册/登录/refresh 旋转/登出/改密。事务边界在本层。"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import AuthenticationError, InvalidCredentialsError, UsernameTakenError
from app.core.security import (
    create_access_token,
    dummy_verify,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)
from app.models import User

from .repositories import RefreshTokenRepository, UserRepository


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class AuthService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.users = UserRepository(db)
        self.refresh_tokens = RefreshTokenRepository(db)

    async def register(self, username: str, password: str, nickname: str | None = None) -> User:
        if await self.users.get_by_username(username):
            raise UsernameTakenError()
        user = await self.users.create(username, hash_password(password), nickname)
        await self.db.commit()
        return user

    async def login(
        self,
        username: str,
        password: str,
        ip: str | None = None,
        device_label: str | None = None,
    ) -> tuple[str, str, User]:
        user = await self.users.get_by_username(username)
        if user is None:
            dummy_verify()  # 时序拉平，防用户名枚举
            raise InvalidCredentialsError()
        if not verify_password(password, user.password_hash):
            raise InvalidCredentialsError()
        if not user.is_active:
            raise AuthenticationError("账号已被禁用")

        access_token = create_access_token(user.id, user.role)
        refresh_token = generate_refresh_token()
        settings = get_settings()
        expires_at = utcnow() + timedelta(days=settings.refresh_token_expire_days)
        await self.refresh_tokens.create(
            user.id,
            hash_refresh_token(refresh_token),
            str(uuid.uuid4()),
            expires_at,
            ip,
            device_label,
        )
        user.last_login_at = utcnow()
        await self.db.commit()
        return access_token, refresh_token, user

    async def refresh(
        self, refresh_token: str, ip: str | None = None, device_label: str | None = None
    ) -> tuple[str, str, User]:
        record = await self.refresh_tokens.get_by_hash(hash_refresh_token(refresh_token))
        if record is None:
            raise AuthenticationError("刷新凭证无效")
        now = utcnow()
        if record.revoked_at is not None:
            # 已吊销 token 被再次使用 → 判定令牌盗窃，吊销整个 family
            await self.refresh_tokens.revoke_family(record.family_id, now)
            await self.db.commit()
            raise AuthenticationError("检测到异常登录，请重新登录")
        if record.expires_at <= now:
            raise AuthenticationError("刷新凭证已过期，请重新登录")

        user = await self.users.get_by_id(record.user_id)
        if user is None or not user.is_active:
            raise AuthenticationError()

        # 旋转：签发新 token（同 family），吊销旧 token
        settings = get_settings()
        new_token = generate_refresh_token()
        new_record = await self.refresh_tokens.create(
            user.id,
            hash_refresh_token(new_token),
            record.family_id,
            now + timedelta(days=settings.refresh_token_expire_days),
            ip,
            device_label,
        )
        record.revoked_at = now
        record.replaced_by_id = new_record.id
        await self.db.commit()
        return create_access_token(user.id, user.role), new_token, user

    async def logout(self, refresh_token: str) -> None:
        record = await self.refresh_tokens.get_by_hash(hash_refresh_token(refresh_token))
        if record is not None:
            await self.refresh_tokens.revoke_family(record.family_id, utcnow())
            await self.db.commit()

    async def change_password(self, user: User, old_password: str, new_password: str) -> None:
        if not verify_password(old_password, user.password_hash):
            raise InvalidCredentialsError("原密码错误")
        user.password_hash = hash_password(new_password)
        user.password_changed_at = utcnow()
        # 改密后吊销该用户全部 refresh（登出所有设备）
        await self.refresh_tokens.revoke_all_for_user(user.id, utcnow())
        await self.db.commit()
