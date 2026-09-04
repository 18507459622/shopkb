"""FastAPI 应用入口：中间件 + CORS + 异常处理 + 路由 + 健康检查。"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select, text

from app.core.config import get_settings
from app.core.database import get_session_factory, init_models
from app.core.exceptions import DomainError
from app.core.logging_conf import configure_logging, get_logger
from app.core.middleware import RequestIDMiddleware, request_id_var
from app.core.schemas import ErrorEnvelope
from app.core.security import hash_password
from app.models import User
from app.modules.admin.router import router as admin_router
from app.modules.auth.router import router as auth_router
from app.modules.chat.router import router as chat_router
from app.modules.kb.router import router as kb_router

logger = get_logger("shopkb")


async def _seed_admin() -> None:
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
            logger.info("seeded admin/123456")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.app_debug)
    # 确保 data/ 目录存在（SQLite / Milvus Lite / 上传文件都需要）
    os.makedirs(settings.upload_dir, exist_ok=True)
    # 依赖初始化：失败不阻断启动，/readyz 会反映降级
    try:
        await init_models()
        await _seed_admin()
    except Exception as exc:  # noqa: BLE001
        logger.warning("db init skipped", error=str(exc))
    try:
        from app.rag.vectorstore import get_vectorstore

        get_vectorstore().ensure_collection()
    except Exception as exc:  # noqa: BLE001
        logger.warning("milvus init skipped", error=str(exc))
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )

    app.add_middleware(RequestIDMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.app_cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(DomainError)
    async def _domain_error(request: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.http_status,
            content=ErrorEnvelope(
                code=exc.code, message=exc.message, detail=exc.detail, request_id=request_id_var.get()
            ).model_dump(),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # 只回传可序列化且不敏感的字段：loc/msg/type。
        # exc.errors() 里的 input/ctx/url 可能含原始输入或异常对象（如 ValueError），
        # 直接序列化会 TypeError，也不该回传给客户端。
        errors = [
            {"loc": e.get("loc", []), "msg": e.get("msg", ""), "type": e.get("type", "")}
            for e in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content=ErrorEnvelope(
                code="VALIDATION_ERROR",
                message="参数校验失败",
                detail=errors,
                request_id=request_id_var.get(),
            ).model_dump(),
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.error("unhandled error", error=repr(exc))
        return JSONResponse(
            status_code=500,
            content=ErrorEnvelope(
                code="INTERNAL", message="服务器内部错误", request_id=request_id_var.get()
            ).model_dump(),
        )

    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(kb_router, prefix="/api/v1")
    app.include_router(chat_router, prefix="/api/v1")
    app.include_router(admin_router, prefix="/api/v1")

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz() -> JSONResponse:
        checks: dict[str, str] = {}
        try:
            async with get_session_factory()() as db:
                await db.execute(text("SELECT 1"))
            checks["database"] = "ok"
        except Exception as exc:  # noqa: BLE001
            checks["database"] = f"error: {exc}"
        try:
            from app.rag.vectorstore import get_vectorstore

            checks["milvus"] = "ok" if get_vectorstore().ready() else "error"
        except Exception as exc:  # noqa: BLE001
            checks["milvus"] = f"error: {exc}"
        healthy = all(v == "ok" for v in checks.values())
        return JSONResponse(
            status_code=200 if healthy else 503,
            content={"status": "ok" if healthy else "degraded", "checks": checks},
        )

    return app


app = create_app()
