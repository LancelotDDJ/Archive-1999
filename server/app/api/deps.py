# -*- coding: utf-8 -*-
"""API 依赖注入：当前用户 / 管理员 / 限流。"""
from __future__ import annotations

import threading
import time

from fastapi import Depends, Header, Request

from ..core.config import settings
from ..core.errors import AuthError, ForbiddenError, RateLimitError
from ..core.security import decode_token
from ..infra.db import db


def _bearer(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise AuthError("缺少登录凭证", code="token_missing")
    return authorization[7:].strip()


def current_user(authorization: str | None = Header(default=None)) -> dict:
    token = _bearer(authorization)
    payload = decode_token(token, expect_type="access")
    try:
        uid = int(payload["sub"])
    except (KeyError, ValueError):
        raise AuthError("凭证内容非法", code="token_invalid")
    u = db().user_by_id(uid)
    if not u:
        raise AuthError("账号不存在", code="user_gone")
    if u["status"] != "active":
        raise AuthError("账号已被禁用", code="account_disabled")
    return u


def require_admin(user: dict = Depends(current_user)) -> dict:
    if user["role"] != "admin":
        raise ForbiddenError("需要管理员权限", code="admin_required")
    return user


# ---------------- 限流（每用户滑动窗口） ----------------
class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[int, list[float]] = {}
        self._lock = threading.Lock()

    def check(self, user_id: int) -> None:
        cfg = settings().section("qa")
        limit = int(cfg.get("rate_limit", 30))
        window = int(cfg.get("rate_window_s", 300))
        now = time.time()
        with self._lock:
            hits = [t for t in self._hits.get(user_id, []) if now - t < window]
            if len(hits) >= limit:
                raise RateLimitError(
                    f"提问过于频繁（{limit} 次/{window//60} 分钟），请稍后再试",
                    code="qa_rate_limited")
            hits.append(now)
            self._hits[user_id] = hits


qa_limiter = RateLimiter()


def rate_limited_user(user: dict = Depends(current_user)) -> dict:
    qa_limiter.check(user["id"])
    return user
