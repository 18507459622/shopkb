"""管理员模块 Pydantic 模型。"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class UserAdminOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    role: str
    nickname: str | None = None
    is_active: bool
    last_login_at: datetime | None = None
    created_at: datetime


class UserUpdateRequest(BaseModel):
    is_active: bool | None = None
    role: str | None = Field(default=None, pattern="^(admin|user)$")


class AdminStats(BaseModel):
    total_users: int
    active_users: int
    admin_count: int
    total_documents: int
    total_chunks: int
