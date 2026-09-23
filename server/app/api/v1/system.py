# -*- coding: utf-8 -*-
"""系统路由：公开状态 / 管理员 KB、LLM 配置与全站统计。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ...schemas import LLMConfigBody, UpdateTriggerBody
from ...services import kb_admin_service
from ..deps import current_user, require_admin

router = APIRouter(tags=["system"])


@router.get("/system/health")
def health():
    return {"ok": True, "service": "archive1999"}


@router.get("/kb/status")
def kb_status(user: dict = Depends(current_user)):
    return kb_admin_service.kb_status()


@router.post("/admin/kb/update")
def trigger_update(body: UpdateTriggerBody, admin: dict = Depends(require_admin)):
    return kb_admin_service.trigger_update(body.kind, admin["username"])


@router.get("/admin/kb/jobs")
def list_jobs(admin: dict = Depends(require_admin)):
    return {"jobs": kb_admin_service.list_jobs()}


@router.get("/admin/llm/config")
def get_llm_config(admin: dict = Depends(require_admin)):
    return kb_admin_service.get_llm_config()


@router.post("/admin/llm/config")
def set_llm_config(body: LLMConfigBody, admin: dict = Depends(require_admin)):
    return kb_admin_service.set_llm_config(body.model_dump(exclude_unset=True))


@router.get("/admin/stats/qa-activity")
def qa_activity_admin(days: int = Query(default=30, ge=7, le=365),
                      admin: dict = Depends(require_admin)):
    return kb_admin_service.qa_activity(days)
