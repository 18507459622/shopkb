"""认证路由：REST，返回统一 envelope；refresh token 走 HttpOnly cookie。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.exceptions import AuthenticationError
from app.core.schemas import ok
from app.models import User

from .schemas import ChangePasswordRequest, LoginRequest, RegisterRequest, TokenResponse, UserOut
from .service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


def _set_refresh_cookie(response: Response, refresh_token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        max_age=settings.refresh_token_expire_days * 24 * 3600,
        httponly=True,
        secure=settings.app_env == "prod",
        samesite="lax",
        path="/api/v1/auth",
    )


@router.post("/register")
async def register(body: RegisterRequest, db: AsyncSession = Depends(get_db)):
    service = AuthService(db)
    user = await service.register(body.username, body.password)
    return ok(UserOut.model_validate(user))


@router.post("/login")
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    service = AuthService(db)
    ip = request.client.host if request.client else None
    access_token, refresh_token, user = await service.login(body.username, body.password, ip=ip)
    _set_refresh_cookie(response, refresh_token)
    response.headers["Cache-Control"] = "no-store"
    return ok(TokenResponse(access_token=access_token, user=UserOut.model_validate(user)))


@router.post("/refresh")
async def refresh(request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    refresh_token = request.cookies.get("refresh_token")
    if not refresh_token:
        raise AuthenticationError("缺少刷新凭证")
    service = AuthService(db)
    ip = request.client.host if request.client else None
    access_token, new_refresh, user = await service.refresh(refresh_token, ip=ip)
    _set_refresh_cookie(response, new_refresh)
    response.headers["Cache-Control"] = "no-store"
    return ok(TokenResponse(access_token=access_token, user=UserOut.model_validate(user)))


@router.post("/logout")
async def logout(request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    refresh_token = request.cookies.get("refresh_token")
    if refresh_token:
        await AuthService(db).logout(refresh_token)
    response.delete_cookie("refresh_token", path="/api/v1/auth")
    return ok()


@router.post("/change-password")
async def change_password(
    body: ChangePasswordRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await AuthService(db).change_password(user, body.old_password, body.new_password)
    return ok()


@router.get("/me")
async def me(user: User = Depends(get_current_user)):
    return ok(UserOut.model_validate(user))
