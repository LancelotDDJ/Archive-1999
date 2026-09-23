# -*- coding: utf-8 -*-
"""安全原语：密码哈希（PBKDF2-HMAC-SHA256）与 JWT 签发/校验。

无第三方加密依赖：密码用 stdlib hashlib.pbkdf2_hmac；
JWT 用 PyJWT（HS256）。令牌载荷：sub(用户id) / role / type(access|refresh) / jti。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
import uuid

import jwt

from .config import settings
from .errors import AuthError

_ALGO = "HS256"


# ---------------- 密码 ----------------
def hash_password(password: str, *, rounds: int | None = None) -> str:
    rounds = rounds or int(settings().section("security").get("pbkdf2_rounds", 260_000))
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds)
    return "pbkdf2$%d$%s$%s" % (
        rounds,
        base64.b64encode(salt).decode(),
        base64.b64encode(dk).decode(),
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, rounds_s, salt_b64, dk_b64 = stored.split("$", 3)
        if scheme != "pbkdf2":
            return False
        salt = base64.b64decode(salt_b64)
        expect = base64.b64decode(dk_b64)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(rounds_s))
        return hmac.compare_digest(dk, expect)
    except (ValueError, TypeError):
        return False


def validate_password_strength(pw: str) -> str | None:
    """返回 None=合格，否则为错误说明。"""
    if len(pw or "") < 8:
        return "密码至少 8 位"
    if len(pw) > 128:
        return "密码过长"
    if pw.isdigit() or pw.isalpha():
        return "密码需同时包含字母与数字"
    return None


# ---------------- JWT ----------------
def _issue(user_id: int, role: str, typ: str, ttl_s: int) -> tuple[str, int, str]:
    now = int(time.time())
    jti = uuid.uuid4().hex
    payload = {
        "sub": str(user_id), "role": role, "type": typ, "jti": jti,
        "iat": now, "exp": now + ttl_s,
    }
    token = jwt.encode(payload, settings().jwt_secret(), algorithm=_ALGO)
    return token, now + ttl_s, jti


def issue_access(user_id: int, role: str) -> tuple[str, int]:
    ttl = int(settings().section("security").get("access_ttl_min", 720)) * 60
    token, exp, _ = _issue(user_id, role, "access", ttl)
    return token, exp


def issue_refresh(user_id: int, role: str) -> tuple[str, int, str]:
    ttl = int(settings().section("security").get("refresh_ttl_days", 30)) * 86400
    return _issue(user_id, role, "refresh", ttl)


def decode_token(token: str, *, expect_type: str = "access") -> dict:
    try:
        payload = jwt.decode(token, settings().jwt_secret(), algorithms=[_ALGO])
    except jwt.ExpiredSignatureError:
        raise AuthError("登录已过期，请重新登录", code="token_expired")
    except jwt.InvalidTokenError:
        raise AuthError("无效的登录凭证", code="token_invalid")
    if payload.get("type") != expect_type:
        raise AuthError("凭证类型不符", code="token_wrong_type")
    return payload


def hash_token(token: str) -> str:
    """刷新令牌只存哈希，泄露数据库也无法冒用。"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
