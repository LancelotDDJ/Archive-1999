# -*- coding: utf-8 -*-
"""KB 管理服务：索引状态、手动触发更新、任务台账、LLM 配置与全站问答活跃（管理员）。"""
from __future__ import annotations

from ..core.retrieval.store import KBStore
from ..infra import llm_client, update_runner
from ..infra.db import db


def kb_status() -> dict:
    st = KBStore.get().status()
    jobs = db().job_list(10)
    return {**st, "recent_jobs": jobs,
            "running_job": db().job_running()}


def trigger_update(kind: str, actor: str) -> dict:
    return update_runner.trigger(kind, "manual", actor)


def list_jobs(limit: int = 30) -> list[dict]:
    return db().job_list(limit)


def get_llm_config() -> dict:
    return llm_client.masked_config()


def set_llm_config(patch: dict) -> dict:
    llm_client.update_config(patch)
    return llm_client.masked_config()


def _fill_days(rows: list[dict], days: int) -> list[dict]:
    """把稀疏的按日记录补全为连续 days 天时间轴（缺失日补 0）。"""
    import datetime as _dt
    by_day = {r["day"]: r for r in rows}
    today = _dt.date.today()
    out = []
    for i in range(days - 1, -1, -1):
        d = (today - _dt.timedelta(days=i)).isoformat()
        r = by_day.get(d)
        out.append({"day": d, "total": r["total"] if r else 0,
                    "cached": (r.get("cached") or 0) if r else 0,
                    "users": (r.get("users") or 0) if r else 0})
    return out


def qa_activity(days: int = 30) -> dict:
    """全站问答活跃（管理员）：按日聚合 + 最近提问审计。"""
    return {"days": days, "series": _fill_days(db().qa_activity_as_admin(days), days),
            "recent": db().qa_recent_as_admin(50)}
