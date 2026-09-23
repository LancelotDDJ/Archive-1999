# -*- coding: utf-8 -*-
"""认证路由：注册/登录/刷新/登出/改密/当前用户。"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from ...core.errors import AppError
from ...schemas import (ChangePasswordBody, LoginBody, RefreshBody,
                        RegisterBody, UpdateProfileBody)
from ...services import auth_service, user_service
from ..deps import current_user

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register")
def register(body: RegisterBody):
    return auth_service.register(body.username, body.password, body.display_name)


@router.post("/login")
def login(body: LoginBody):
    return auth_service.login(body.username, body.password)


@router.post("/refresh")
def refresh(body: RefreshBody):
    return auth_service.refresh(body.refresh_token)


@router.post("/logout")
def logout(body: RefreshBody):
    auth_service.logout(body.refresh_token)
    return {"ok": True}


@router.get("/me")
def me(user: dict = Depends(current_user)):
    return {"user": user_service.get_profile(user)}


@router.post("/change-password")
def change_password(body: ChangePasswordBody, user: dict = Depends(current_user)):
    auth_service.change_password(user, body.old_password, body.new_password)
    return {"ok": True, "note": "密码已修改，请重新登录"}


@router.patch("/me")
def update_me(body: UpdateProfileBody, user: dict = Depends(current_user)):
    return {"user": user_service.update_profile(user, body.display_name)}
