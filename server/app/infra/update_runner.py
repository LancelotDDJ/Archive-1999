# -*- coding: utf-8 -*-
"""更新任务执行器：全量/增量更新的排队执行与台账记录。

- 单任务并发（job_running 互斥）；
- 日志落 artifacts/logs/update-<job_id>.log；
- 结束后由 KBStore 的 mtime 监视自动热重载（无需通知）。
"""
from __future__ import annotations

import contextlib
import io
import threading
import time
import traceback
from pathlib import Path

from ..core.config import LOGS
from .db import db

_worker_lock = threading.Lock()


class _Tee(io.StringIO):
    """同时写日志文件与内存缓冲。"""
    def __init__(self, fp):
        super().__init__()
        self._fp = fp

    def write(self, s):
        self._fp.write(s)
        self._fp.flush()
        return super().write(s)


def trigger(kind: str, trigger_by: str, actor: str) -> dict:
    """登记任务并后台执行。已有在跑任务则拒绝。"""
    running = db().job_running()
    if running:
        return {"accepted": False, "reason": f"已有任务在运行（#{running['id']} {running['kind']}）",
                "job": running}
    job_id = db().job_create(kind, trigger_by, actor)
    t = threading.Thread(target=_run, args=(job_id, kind), daemon=True,
                         name=f"kb-update-{job_id}")
    t.start()
    return {"accepted": True, "job_id": job_id, "kind": kind}


def _run(job_id: int, kind: str) -> None:
    if not _worker_lock.acquire(blocking=False):
        return
    log_path = LOGS / f"update-{job_id}.log"
    db().job_start(job_id)
    ok, detail = False, {}
    try:
        with open(log_path, "w", encoding="utf-8") as fp:
            tee = _Tee(fp)
            with contextlib.redirect_stdout(tee):
                print(f"[job #{job_id}] kind={kind} start "
                      f"{time.strftime('%Y-%m-%d %H:%M:%S')}")
                if kind == "full":
                    from server.pipeline.crawler import crawl_all
                    from server.pipeline.links import build_all as links_all
                    from server.pipeline.indexer import build_all as index_all
                    from server.pipeline.parser import parse_all
                    detail["crawl"] = crawl_all()
                    detail["parse"] = parse_all()
                    detail["links"] = links_all()
                    detail["index"] = index_all()
                else:
                    from server.pipeline.updater import run_incremental
                    detail["update"] = run_incremental()
                print(f"[job #{job_id}] done {time.strftime('%Y-%m-%d %H:%M:%S')}")
        ok = True
    except Exception as e:
        detail = {"error": str(e), "trace": traceback.format_exc()[-2000:]}
        try:
            with open(log_path, "a", encoding="utf-8") as fp:
                fp.write("\n[FAIL] " + str(e) + "\n" + traceback.format_exc())
        except OSError:
            pass
    finally:
        db().job_finish(job_id, ok, detail, str(log_path))
        _worker_lock.release()


def list_jobs(limit: int = 30) -> list[dict]:
    return db().job_list(limit)


def current_job() -> dict | None:
    return db().job_running()
