# -*- coding: utf-8 -*-
"""内置调度器：每月 1 日自动增量更新知识库。

- 守护线程每小时检查一次：当日为配置日（默认每月 1 日）且时间已过配置时刻、
  且本月尚无成功任务、且无在跑任务 → 触发增量更新；
- 台账在 update_jobs 表（job_success_this_month 判定），重启服务不会重复触发；
- 可用 kb.auto_update_enabled 关闭。
"""
from __future__ import annotations

import threading
import time
from datetime import datetime

from ..core.config import settings
from . import update_runner
from .db import db

_CHECK_INTERVAL_S = 3600


def _due(now: datetime) -> bool:
    kb_cfg = settings().section("kb")
    if not kb_cfg.get("auto_update_enabled", True):
        return False
    if now.day != int(kb_cfg.get("auto_update_day", 1)):
        return False
    if now.hour < int(kb_cfg.get("auto_update_hour", 3)):
        return False
    if db().job_success_this_month():
        return False
    if db().job_running():
        return False
    return True


def _loop() -> None:
    # 启动后延迟首轮检查，避免与首启引导抢资源
    time.sleep(90)
    while True:
        try:
            now = datetime.now()
            if _due(now):
                print(f"[scheduler] 月度更新触发 {now:%Y-%m-%d %H:%M}", flush=True)
                update_runner.trigger("incremental", "schedule", "scheduler")
        except Exception as e:
            print(f"[scheduler] check failed: {e}", flush=True)
        time.sleep(_CHECK_INTERVAL_S)


def start() -> None:
    t = threading.Thread(target=_loop, daemon=True, name="kb-scheduler")
    t.start()
    print("[scheduler] 月度自动更新调度已启动（每月 1 日 03:00 增量更新）", flush=True)
