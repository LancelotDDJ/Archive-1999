# -*- coding: utf-8 -*-
"""用户反馈服务：回答评价（answer）与一般反馈（general）的提交与后台查询。"""
from __future__ import annotations

from server.app.core.errors import ConflictError, NotFoundError, ValidationError
from server.app.infra.db import db

MAX_CONTENT = 2000
MAX_SNIPPET = 400


def submit(user: dict, body) -> dict:
    kind = body.kind
    content = (body.content or "").strip()
    if len(content) > MAX_CONTENT:
        raise ValidationError(f"反馈内容过长（最多 {MAX_CONTENT} 字）")

    if kind == "answer":
        conv_id = (body.conv_id or "").strip()
        answer_key = (body.answer_key or "").strip()
        if not conv_id or not answer_key:
            raise ValidationError("回答评价缺少关联的问答信息")
        if body.rating not in (1, -1):
            raise ValidationError("评价必须是 好评 或 差评")
        snippet = (body.answer_snippet or "").strip()[:MAX_SNIPPET]
        try:
            fid = db().feedback_create(user["id"], "answer", conv_id,
                                       answer_key, snippet, body.rating, content)
        except Exception as e:
            # SQLite 对部分唯一索引的报错为列名形式：
            # "UNIQUE constraint failed: feedback.user_id, feedback.conv_id, feedback.answer_key"
            msg = str(e)
            if "UNIQUE constraint failed" in msg and "feedback" in msg:
                raise ConflictError("该回答已评价过")
            raise
        return {"ok": True, "id": fid, "kind": "answer"}

    # general
    if not content:
        raise ValidationError("请填写反馈内容")
    fid = db().feedback_create(user["id"], "general", None, None, "", None, content)
    return {"ok": True, "id": fid, "kind": "general"}


def rated_keys(user: dict, conv_id: str) -> dict:
    if not conv_id:
        raise ValidationError("缺少会话 ID")
    return {"keys": db().feedback_rated_keys(user["id"], conv_id)}


def list_admin(kind: str | None = None, ts_from: int | None = None,
               ts_to: int | None = None, limit: int = 200) -> dict:
    items = db().feedback_list(kind or None, ts_from, ts_to, min(limit, 500))
    for it in items:
        it["kind_label"] = "回答反馈" if it["kind"] == "answer" else "一般反馈"
        it["rating_label"] = ("好评" if it["rating"] == 1
                              else "差评" if it["rating"] == -1 else "—")
    return {"items": items, "total": len(items)}
