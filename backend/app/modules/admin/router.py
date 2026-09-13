"""管理员路由（仅 admin）：用户管理 + 统计。"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import require_admin
from app.core import observability
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


@router.get("/metrics")
async def metrics(_: User = Depends(require_admin)):
    """运行时可观测性指标：LLM 调用、耗时分位、token 成本、失败步骤归因。

    放在 admin 下而不是公开路径，是因为 token 成本属于运营信息。
    指标是**单进程内存聚合**：uvicorn 多 worker 时每个 worker 各算各的，
    要全局数字需要外部聚合（见 README「已知限制」）。
    """
    return ok(observability.snapshot())
