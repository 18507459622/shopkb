"""初始化管理员账号（admin/123456），幂等。运行：python -m app.seed"""
from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.core.database import get_session_factory, init_models
from app.core.security import hash_password
from app.models import User


async def seed_admin() -> None:
    factory = get_session_factory()
    async with factory() as db:
        res = await db.execute(select(User).where(User.username == "admin"))
        if res.scalar_one_or_none() is None:
            db.add(
                User(
                    username="admin",
                    password_hash=hash_password("123456"),
                    role="admin",
                    nickname="管理员",
                )
            )
            await db.commit()
            print("[seed] created admin / 123456")
        else:
            print("[seed] admin already exists")


async def main() -> None:
    await init_models()
    await seed_admin()


if __name__ == "__main__":
    asyncio.run(main())
