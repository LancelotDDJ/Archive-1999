# -*- coding: utf-8 -*-
"""用户管理路由（管理员）：用户列表/角色/状态/重置密码/删除/账户浏览/查看用户会话。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ...schemas import ResetPasswordBody, SetRoleBody, SetStatusBody
from ...services import conversation_service, user_service
from ..deps import require_admin

router = APIRouter(prefix="/admin/users", tags=["admin-users"])


@router.get("")
def list_users(admin: dict = Depends(require_admin)):
    return {"users": user_service.list_users_as_admin()}


@router.get("/accounts")
def list_accounts(search: str = Query(default="", max_length=64),
                  page: int = Query(default=1, ge=1),
                  page_size: int = Query(default=20, ge=1, le=100),
                  admin: dict = Depends(require_admin)):
    """账户列表页数据：搜索 + 分页。"""
    return user_service.list_accounts_as_admin(search, page, page_size)


@router.get("/accounts/{uid}")
def account_detail(uid: int, admin: dict = Depends(require_admin)):
    """账户详情：完整注册信息（密码为单向哈希，不可还原）+ 会话清单。"""
    return user_service.account_detail_as_admin(uid)


@router.get("/accounts/{uid}/chats")
def account_chats(uid: int, page: int = Query(default=1, ge=1),
                  page_size: int = Query(default=50, ge=1, le=200),
                  cid: str | None = Query(default=None),
                  admin: dict = Depends(require_admin)):
    """该账户全部聊天数据：时间正序，含消息内容/发送时间/收发双方。"""
    return user_service.account_chats_as_admin(uid, page, page_size, cid)


@router.patch("/{uid}/role")
def set_role(uid: int, body: SetRoleBody, admin: dict = Depends(require_admin)):
    user_service.set_role_as_admin(admin, uid, body.role)
    return {"ok": True}


@router.patch("/{uid}/status")
def set_status(uid: int, body: SetStatusBody, admin: dict = Depends(require_admin)):
    user_service.set_status_as_admin(admin, uid, body.status)
    return {"ok": True}


@router.post("/{uid}/reset-password")
def reset_password(uid: int, body: ResetPasswordBody,
                   admin: dict = Depends(require_admin)):
    user_service.reset_password_as_admin(admin, uid, body.new_password)
    return {"ok": True}


@router.delete("/{uid}")
def delete_user(uid: int, admin: dict = Depends(require_admin)):
    user_service.delete_user_as_admin(admin, uid)
    return {"ok": True}


@router.get("/{uid}/conversations")
def user_conversations(uid: int, admin: dict = Depends(require_admin)):
    return {"conversations": user_service.user_conversations_as_admin(uid)}


@router.get("/conversations/{cid}")
def get_conversation(cid: str, admin: dict = Depends(require_admin)):
    return conversation_service.get_conversation_as_admin(cid)
