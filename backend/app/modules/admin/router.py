"""管理员路由（仅 admin）：用户管理 + 统计。"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import require_admin
from app.core.schemas import ok
from app.models import User

from .schemas import AdminStats, UserAdminOut, UserUpdateRequest
from .service import AdminService

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/users")
async def list_users(
    page: int = 1,
    size: int = 20,
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    items, total = await AdminService(db).list_users(page, size)
    return ok(
        {
            "items": [UserAdminOut.model_validate(u) for u in items],
            "total": total,
            "page": page,
            "size": size,
        }
    )


@router.patch("/users/{user_id}")
async def update_user(
    user_id: int,
    body: UserUpdateRequest,
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    user = await AdminService(db).update_user(user_id, body.is_active, body.role)
    return ok(UserAdminOut.model_validate(user))


@router.get("/stats")
async def stats(_: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    data = await AdminService(db).stats()
    return ok(AdminStats(**data))
