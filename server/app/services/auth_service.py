# -*- coding: utf-8 -*-
"""认证服务：注册、登录、令牌签发/刷新/吊销、改密、首启管理员引导。

职责单一：只处理「身份」。用户资料管理归 user_service。
"""
from __future__ import annotations

import secrets
import time

from ..core.config import LOGS, settings
from ..core.errors import AuthError, ConflictError, ValidationError
from ..core.security import (decode_token, hash_password, hash_token,
                             issue_access, issue_refresh, validate_password_strength,
                             verify_password)
from ..infra.db import db, valid_username


def _public_user(u: dict) -> dict:
    return {"id": u["id"], "username": u["username"],
            "display_name": u.get("display_name") or u["username"],
            "role": u["role"], "must_change_pwd": bool(u.get("must_change_pwd")),
            "created_at": u["created_at"], "last_login_at": u.get("last_login_at")}


def _issue_pair(u: dict) -> dict:
    access, aexp = issue_access(u["id"], u["role"])
    refresh, rexp, _jti = issue_refresh(u["id"], u["role"])
    db().refresh_store(u["id"], hash_token(refresh), rexp)
    return {"access_token": access, "access_expires": aexp,
            "refresh_token": refresh, "refresh_expires": rexp,
            "user": _public_user(u)}


def register(username: str, password: str, display_name: str | None = None) -> dict:
    username = (username or "").strip()
    if not valid_username(username):
        raise ValidationError("用户名需为 2-24 位中文/字母/数字/._-", code="bad_username")
    msg = validate_password_strength(password)
    if msg:
        raise ValidationError(msg, code="weak_password")
    if db().user_by_name(username):
        raise ConflictError("用户名已被注册", code="username_taken")
    uid = db().user_create(username, hash_password(password),
                           display_name=display_name or username)
    u = db().user_by_id(uid)
    db().user_touch_login(uid)
    return _issue_pair(u)


def login(username: str, password: str) -> dict:
    u = db().user_by_name((username or "").strip())
    if not u or not verify_password(password or "", u["password_hash"]):
        raise AuthError("用户名或密码错误", code="bad_credentials")
    if u["status"] != "active":
        raise AuthError("账号已被禁用，请联系管理员", code="account_disabled")
    db().user_touch_login(u["id"])
    db().refresh_gc()
    return _issue_pair(u)


def refresh(refresh_token: str) -> dict:
    payload = decode_token(refresh_token, expect_type="refresh")
    uid = int(payload["sub"])
    old_hash = hash_token(refresh_token)
    rec = db().refresh_lookup(old_hash)
    if not rec or rec["user_id"] != uid:
        raise AuthError("刷新令牌无效或已吊销", code="refresh_invalid")
    if rec["expires_at"] < int(time.time()):
        raise AuthError("刷新令牌已过期，请重新登录", code="refresh_expired")
    u = db().user_by_id(uid)
    if not u or u["status"] != "active":
        raise AuthError("账号不可用", code="account_disabled")
    # 旋转：旧令牌作废，签发新对
    db().refresh_revoke(old_hash)
    return _issue_pair(u)


def logout(refresh_token: str | None) -> None:
    if refresh_token:
        db().refresh_revoke(hash_token(refresh_token))


def change_password(user: dict, old_password: str, new_password: str) -> None:
    u = db().user_by_id(user["id"])
    if not u or not verify_password(old_password or "", u["password_hash"]):
        raise AuthError("原密码错误", code="bad_credentials")
    msg = validate_password_strength(new_password)
    if msg:
        raise ValidationError(msg, code="weak_password")
    db().user_set_password(u["id"], hash_password(new_password))
    db().refresh_revoke_all(u["id"])     # 改密后全端重新登录


def bootstrap_admin() -> None:
    """users 为空时创建初始管理员。密码优先级：环境变量 > config > 随机生成。"""
    if db().user_count() > 0:
        return
    admin_name = settings().section("admin").get("bootstrap_user", "admin")
    pw = (settings().section("admin").get("bootstrap_password") or "").strip()
    generated = False
    if not pw:
        pw = secrets.token_urlsafe(9) + "A1"
        generated = True
    uid = db().user_create(admin_name, hash_password(pw),
                           display_name="档案管理员", role="admin",
                           must_change_pwd=1)
    note = (
        f"[bootstrap] 初始管理员已创建 username={admin_name} "
        f"password={pw} （请登录后立即修改）\n")
    try:
        with open(LOGS / "bootstrap.txt", "a", encoding="utf-8") as f:
            f.write(note)
    except OSError:
        pass
    print("=" * 60, flush=True)
    print(note.strip(), flush=True)
    print("=" * 60, flush=True)
