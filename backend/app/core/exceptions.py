"""领域错误模型：typed DomainError 由全局 handler 映射为统一 envelope。"""
from __future__ import annotations

from typing import Any


class DomainError(Exception):
    code: str = "DOMAIN_ERROR"
    message: str = "发生错误"
    http_status: int = 400

    def __init__(self, message: str | None = None, detail: Any = None) -> None:
        self.message = message or self.message
        self.detail = detail
        super().__init__(self.message)


class NotFoundError(DomainError):
    code = "NOT_FOUND"
    message = "资源不存在"
    http_status = 404


class AuthenticationError(DomainError):
    code = "UNAUTHORIZED"
    message = "未认证或凭证已失效"
    http_status = 401


class PermissionDeniedError(DomainError):
    code = "FORBIDDEN"
    message = "无权限执行该操作"
    http_status = 403


class ConflictError(DomainError):
    code = "CONFLICT"
    message = "资源冲突"
    http_status = 409


class ValidationError(DomainError):
    code = "VALIDATION_ERROR"
    message = "参数校验失败"
    http_status = 422


class ServiceUnavailableError(DomainError):
    code = "SERVICE_UNAVAILABLE"
    message = "依赖服务暂不可用"
    http_status = 503


# ---- 具体领域错误 ----
class UserNotFoundError(NotFoundError):
    code = "USER_NOT_FOUND"


class ConversationNotFoundError(NotFoundError):
    code = "CONVERSATION_NOT_FOUND"


class DocumentNotFoundError(NotFoundError):
    code = "DOCUMENT_NOT_FOUND"


class UsernameTakenError(ConflictError):
    code = "USERNAME_TAKEN"
    message = "用户名已被占用"


class InvalidCredentialsError(AuthenticationError):
    code = "INVALID_CREDENTIALS"
    message = "用户名或密码错误"


class DocumentBusyError(ConflictError):
    code = "DOCUMENT_BUSY"
    message = "文档正在处理中，请稍后再试"
