"""统一响应 envelope 与分页模型。"""
from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class Envelope(BaseModel, Generic[T]):
    code: str = "OK"
    message: str = ""
    data: T | None = None
    request_id: str = ""


class ErrorEnvelope(BaseModel):
    code: str
    message: str
    detail: Any = None
    request_id: str = ""


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    size: int


def ok(data: Any = None, message: str = "") -> Envelope:
    """构造成功响应 envelope（注入当前 request_id）。"""
    from app.core.middleware import request_id_var

    return Envelope(code="OK", message=message, data=data, request_id=request_id_var.get())
