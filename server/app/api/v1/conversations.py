# -*- coding: utf-8 -*-
"""会话路由（用户域，严格按用户隔离）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from ...schemas import ConversationSaveBody
from ...services import conversation_service
from ..deps import current_user

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.get("")
def list_conversations(user: dict = Depends(current_user)):
    return {"conversations": conversation_service.list_conversations(user)}


@router.post("")
def save_conversation(body: ConversationSaveBody, user: dict = Depends(current_user)):
    return conversation_service.save_conversation(user, body)


@router.get("/{cid}")
def get_conversation(cid: str, user: dict = Depends(current_user)):
    return conversation_service.get_conversation(user, cid)


@router.delete("/{cid}")
def delete_conversation(cid: str, user: dict = Depends(current_user)):
    conversation_service.delete_conversation(user, cid)
    return {"ok": True}
