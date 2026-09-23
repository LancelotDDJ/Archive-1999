# -*- coding: utf-8 -*-
"""统一异常与错误码：api 层捕获 AppError 并转为一致的错误响应。"""
from __future__ import annotations


class AppError(Exception):
    """业务异常基类。http_status + 机器可读 code + 人类可读 message。"""

    http_status = 400
    code = "bad_request"

    def __init__(self, message: str = "", *, code: str | None = None,
                 http_status: int | None = None) -> None:
        super().__init__(message or self.code)
        self.message = message or self.code
        if code:
            self.code = code
        if http_status:
            self.http_status = http_status

    def payload(self) -> dict:
        return {"error": {"code": self.code, "message": self.message}}


class AuthError(AppError):
    http_status = 401
    code = "unauthorized"


class ForbiddenError(AppError):
    http_status = 403
    code = "forbidden"


class NotFoundError(AppError):
    http_status = 404
    code = "not_found"


class ConflictError(AppError):
    http_status = 409
    code = "conflict"


class RateLimitError(AppError):
    http_status = 429
    code = "rate_limited"


class ValidationError(AppError):
    http_status = 422
    code = "validation_failed"


class ServiceUnavailableError(AppError):
    http_status = 503
    code = "service_unavailable"
