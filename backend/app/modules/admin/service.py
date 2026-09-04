"""管理员业务逻辑：用户管理 + 统计看板。"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, ValidationError
from app.models import Document, User


class AdminService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list_users(self, page: int = 1, size: int = 20) -> tuple[list[User], int]:
        total = (await self.db.execute(select(func.count()).select_from(User))).scalar_one()
        stmt = select(User).order_by(User.id.asc()).offset((page - 1) * size).limit(size)
        items = list((await self.db.execute(stmt)).scalars().all())
        return items, total

    async def update_user(self, user_id: int, is_active: bool | None, role: str | None) -> User:
        user = await self.db.get(User, user_id)
        if user is None:
            raise NotFoundError("用户不存在")
        if user.username == "admin" and is_active is False:
            raise ValidationError("不能禁用内置管理员账号")
        if is_active is not None:
            user.is_active = is_active
        if role is not None:
            user.role = role
        await self.db.commit()
        return user

    async def stats(self) -> dict:
        total_users = (await self.db.execute(select(func.count()).select_from(User))).scalar_one()
        active_users = (
            await self.db.execute(
                select(func.count()).select_from(User).where(User.is_active.is_(True))
            )
        ).scalar_one()
        admin_count = (
            await self.db.execute(select(func.count()).select_from(User).where(User.role == "admin"))
        ).scalar_one()
        total_documents = (
            await self.db.execute(
                select(func.count()).select_from(Document).where(Document.deleted_at.is_(None))
            )
        ).scalar_one()
        total_chunks = (
            await self.db.execute(
                select(func.coalesce(func.sum(Document.chunk_count), 0)).where(
                    Document.deleted_at.is_(None)
                )
            )
        ).scalar_one()
        return {
            "total_users": total_users,
            "active_users": active_users,
            "admin_count": admin_count,
            "total_documents": total_documents,
            "total_chunks": total_chunks,
        }
