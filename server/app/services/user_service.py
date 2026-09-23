# -*- coding: utf-8 -*-
"""用户服务：个人资料 + 管理员用户管理。

数据可见性规则：
- 普通用户：仅能读写本人资料（api 层注入的 current_user 即本人）；
- 管理员：本模块 *_as_admin 系列方法可管理任意用户；
- 管理员不可降级/禁用/删除自己（防自锁）。
"""
from __future__ import annotations

from ..core.errors import ForbiddenError, NotFoundError, ValidationError
from ..core.security import hash_password, validate_password_strength
from ..infra.db import db


def get_profile(user: dict) -> dict:
    u = db().user_by_id(user["id"])
    if not u:
        raise NotFoundError("用户不存在")
    return _public(u)


def update_profile(user: dict, display_name: str) -> dict:
    display_name = (display_name or "").strip()[:32]
    if not display_name:
        raise ValidationError("显示名不能为空")
    db().user_update_profile(user["id"], display_name)
    return get_profile(user)


def _public(u: dict) -> dict:
    return {"id": u["id"], "username": u["username"],
            "display_name": u.get("display_name") or u["username"],
            "role": u["role"], "status": u["status"],
            "must_change_pwd": bool(u.get("must_change_pwd")),
            "created_at": u["created_at"], "last_login_at": u.get("last_login_at")}


# ================= 管理员域 =================
def list_users_as_admin() -> list[dict]:
    return db().users_list_as_admin()


def _target(uid: int) -> dict:
    u = db().user_by_id(uid)
    if not u:
        raise NotFoundError("用户不存在")
    return u


def set_role_as_admin(actor: dict, uid: int, role: str) -> None:
    if actor["id"] == uid and role != "admin":
        raise ForbiddenError("不能降级自己的管理员权限", code="self_demote")
    _target(uid)
    db().user_set_role_as_admin(uid, role)


def set_status_as_admin(actor: dict, uid: int, status: str) -> None:
    if actor["id"] == uid and status == "disabled":
        raise ForbiddenError("不能禁用自己的账号", code="self_disable")
    _target(uid)
    db().user_set_status_as_admin(uid, status)


def reset_password_as_admin(actor: dict, uid: int, new_password: str) -> None:
    _target(uid)
    msg = validate_password_strength(new_password)
    if msg:
        raise ValidationError(msg, code="weak_password")
    db().user_set_password(uid, hash_password(new_password), clear_must_change=False)
    db().refresh_revoke_all(uid)


def delete_user_as_admin(actor: dict, uid: int) -> None:
    if actor["id"] == uid:
        raise ForbiddenError("不能删除自己的账号", code="self_delete")
    _target(uid)
    db().user_delete_as_admin(uid)


def user_conversations_as_admin(uid: int) -> list[dict]:
    _target(uid)
    return db().conv_list_as_admin(uid)


# ================= 账户浏览（管理员：列表 + 详情 + 聊天数据） =================
def list_accounts_as_admin(search: str, page: int, page_size: int) -> dict:
    page = max(1, page)
    page_size = max(1, min(page_size, 100))
    data = db().users_page_as_admin(search, page, page_size)
    data["items"] = [_account_info(u) for u in data["items"]]
    return data


def account_detail_as_admin(uid: int) -> dict:
    """账户完整注册信息。

    密码安全说明：系统只存 PBKDF2-SHA256 单向哈希（逐用户随机盐），
    原始密码任何人都不可还原；详情页展示哈希值仅供核对，
    管理员如需恢复访问请使用「重置密码」。
    """
    u = db().user_full_as_admin(uid)
    if not u:
        raise NotFoundError("用户不存在")
    info = _account_info(u)
    pw = u.get("password_hash") or ""
    algo, _, digest = pw.partition("$")
    info["password"] = {
        "algorithm": algo or "unknown",
        "hash": pw,
        "hash_preview": digest[:32] + "…" if digest else pw[:32] + "…",
        "reversible": False,
        "note": "单向哈希不可还原；如需重新获得访问权请使用「重置密码」",
    }
    info["conversations"] = db().user_conv_options_as_admin(uid)
    return info


def account_chats_as_admin(uid: int, page: int, page_size: int,
                           cid: str | None = None) -> dict:
    """某账户全部聊天消息：时间正序、含收发双方与所属会话，分页。"""
    _target(uid)
    if cid:
        conv = db().conv_get_as_admin(cid)
        if not conv or conv["user_id"] != uid:
            raise ValidationError("会话不属于该账户", code="conv_mismatch")
    page = max(1, page)
    page_size = max(1, min(page_size, 200))
    data = db().user_messages_page_as_admin(uid, page, page_size, cid)
    import json as _json
    for it in data["items"]:
        try:
            it["meta"] = _json.loads(it.pop("meta_json") or "{}")
        except ValueError:
            it["meta"] = {}
        it["time"] = it.pop("created_at")
    return data


def _account_info(u: dict) -> dict:
    return {"id": u["id"], "username": u["username"],
            "display_name": u.get("display_name") or u["username"],
            "role": u["role"], "status": u["status"],
            "must_change_pwd": bool(u.get("must_change_pwd")),
            "created_at": u["created_at"], "last_login_at": u.get("last_login_at"),
            "conv_count": u.get("conv_count", 0),
            "msg_count": u.get("msg_count", 0), "qa_count": u.get("qa_count", 0)}
