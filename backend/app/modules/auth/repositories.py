"""认证模块数据访问层：owner 无关，只做 ORM 读写，不抛 HTTPException。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import RefreshToken, User


class UserRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_by_username(self, username: str) -> User | None:
        res = await self.db.execute(select(User).where(User.username == username))
        return res.scalar_one_or_none()

    async def get_by_id(self, user_id: int) -> User | None:
        return await self.db.get(User, user_id)

    async def create(
        self, username: str, password_hash: str, nickname: str | None = None
    ) -> User:
        user = User(username=username, password_hash=password_hash, nickname=nickname, role="user")
        self.db.add(user)
        await self.db.flush()
        return user


class RefreshTokenRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create(
        self,
        user_id: int,
        token_hash: str,
        family_id: str,
        expires_at: datetime,
        ip: str | None = None,
        device_label: str | None = None,
    ) -> RefreshToken:
        rec = RefreshToken(
            user_id=user_id,
            token_hash=token_hash,
            family_id=family_id,
            expires_at=expires_at,
            ip=ip,
            device_label=device_label,
        )
        self.db.add(rec)
        await self.db.flush()
        return rec

    async def get_by_hash(self, token_hash: str) -> RefreshToken | None:
        res = await self.db.execute(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        )
        return res.scalar_one_or_none()

    async def revoke_family(self, family_id: str, now: datetime) -> None:
        await self.db.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )

    async def revoke_all_for_user(self, user_id: int, now: datetime) -> None:
        await self.db.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )
