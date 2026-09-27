# -*- coding: utf-8 -*-
"""用户反馈接口：回答评价 / 一般反馈提交 + 管理端查询。"""
from typing import Optional

from fastapi import APIRouter, Depends, Query

from ...core.errors import ValidationError
from ..deps import current_user, require_admin
from ...schemas import FeedbackBody
from ...services import feedback_service

router = APIRouter(prefix="/feedback", tags=["feedback"])


@router.post("")
def submit_feedback(body: FeedbackBody, user: dict = Depends(current_user)):
    return feedback_service.submit(user, body)


@router.get("/rated")
def rated_keys(conv_id: str = Query(...), user: dict = Depends(current_user)):
    return feedback_service.rated_keys(user, conv_id)


@router.get("/admin")
def admin_list(kind: Optional[str] = Query(None),
               from_date: Optional[str] = Query(None, alias="from"),
               to_date: Optional[str] = Query(None, alias="to"),
               limit: int = Query(200),
               admin: dict = Depends(require_admin)):
    import datetime as _dt
    ts_from = ts_to = None
    try:
        if from_date:
            ts_from = int(_dt.datetime.strptime(from_date, "%Y-%m-%d")
                          .replace(tzinfo=_dt.timezone.utc).timestamp())
        if to_date:
            ts_to = int((_dt.datetime.strptime(to_date, "%Y-%m-%d")
                         + _dt.timedelta(days=1)).replace(tzinfo=_dt.timezone.utc).timestamp()) - 1
    except ValueError:
        raise ValidationError("日期格式应为 YYYY-MM-DD")
    return feedback_service.list_admin(kind, ts_from, ts_to, limit)
