"""SQLAlchemy 2.0 async 引擎与会话工厂（进程级惰性单例）。"""
from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""


_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            settings.database_url,
            echo=settings.database_echo,
            pool_pre_ping=True,
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _session_factory


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖：每个请求一个 AsyncSession，Service 层负责 commit/rollback。"""
    factory = get_session_factory()
    async with factory() as session:
        yield session


async def init_models() -> None:
    """开发/测试用建表（生产走 Alembic）。"""
    from app import models  # noqa: F401  确保模型已注册

    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
