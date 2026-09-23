# -*- coding: utf-8 -*-
"""会话服务：对话与消息的持久化（严格按用户隔离）。

- 普通用户：全部操作强制限定本人会话（读取不存在即 404，不暴露存在性）；
- 管理员：可读任意用户会话（审计用途）；
- 消息整体覆盖式保存（前端每次问答后同步全量），服务端裁剪至最近 300 条。
"""
from __future__ import annotations

import re
import uuid

from ..core.errors import NotFoundError, ValidationError
from ..infra.db import db

_ID_RE = re.compile(r"^[a-f0-9]{32}$")


def list_conversations(user: dict) -> list[dict]:
    return db().conv_list(user["id"])


def get_conversation(user: dict, cid: str) -> dict:
    conv = db().conv_get_owned(cid, user["id"])
    if not conv:
        raise NotFoundError("会话不存在", code="conversation_not_found")
    conv["messages"] = db().msg_list(cid)
    return conv


def save_conversation(user: dict, body) -> dict:
    messages = (body.messages or [])[-300:]
    for m in messages:
        if m.get("role") not in ("user", "assistant"):
            raise ValidationError("消息角色非法")
    now_cid = body.id if (body.id and _ID_RE.fullmatch(body.id)) else uuid.uuid4().hex
    existing = db().conv_get_owned(now_cid, user["id"])
    title = (body.title or "新对话").strip()[:60] or "新对话"
    if existing is None:
        if body.id and not _ID_RE.fullmatch(body.id or ""):
            raise ValidationError("会话 ID 非法")
        db().conv_create(now_cid, user["id"], title, body.kind)
    else:
        db().conv_touch(now_cid, title)
        # 覆盖式：清空旧消息重写（保持前端单一真源语义）
        db().execute("DELETE FROM messages WHERE conversation_id=?", (now_cid,))
    for m in messages:
        db().msg_add(now_cid, m["role"], str(m.get("content", "")),
                     m.get("meta") or {})
    return {"id": now_cid, "msg_count": len(messages)}


def delete_conversation(user: dict, cid: str) -> None:
    conv = db().conv_get_owned(cid, user["id"])
    if not conv:
        raise NotFoundError("会话不存在", code="conversation_not_found")
    db().conv_delete(cid, user["id"])


# ---------------- 管理员域 ----------------
def get_conversation_as_admin(cid: str) -> dict:
    conv = db().conv_get_as_admin(cid)
    if not conv:
        raise NotFoundError("会话不存在", code="conversation_not_found")
    conv["messages"] = db().msg_list(cid)
    return conv


def record_message(user: dict, cid: str | None, role: str, content: str,
                   meta: dict, kind: str = "qa", title_hint: str = "") -> str | None:
    """问答服务回调：把单条消息追加到会话（会话不存在则按 hint 创建）。"""
    if not cid or not _ID_RE.fullmatch(cid):
        return None
    conv = db().conv_get_owned(cid, user["id"])
    if conv is None:
        title = (title_hint or "新对话").strip()[:60] or "新对话"
        db().conv_create(cid, user["id"], title, kind)
    db().msg_add(cid, role, content, meta or {})
    db().conv_touch(cid)
    return cid
