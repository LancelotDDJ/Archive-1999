# -*- coding: utf-8 -*-
"""等待灰机 wiki WAF 封锁解除，解除后自动触发标准增量更新。

探测逻辑：每 5 分钟以 wikiclient UA 请求一次 recentchanges（单次轻量请求）；
连续得到 200 即视为封锁解除 → 调用 update_runner.trigger 登记标准增量任务
（任务台账/日志/有界重试全部沿用正式任务系统）。超过 --max-hours 未解除则退出。
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import urllib.request

PROBE_URL = ("https://res1999.huijiwiki.com/api.php?action=query"
             "&list=recentchanges&rclimit=1&format=json&formatversion=2")
UA = "Archive1999KB/2.0 (contact: local)"


def probe() -> bool:
    req = urllib.request.Request(PROBE_URL, headers={"User-Agent": UA})
    try:
        r = urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=20)
        return r.status == 200
    except Exception:
        return False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-hours", type=float, default=6.0)
    ap.add_argument("--interval-min", type=float, default=5.0)
    args = ap.parse_args()

    deadline = time.time() + args.max_hours * 3600
    print(f"[waiter] 开始监视灰机 wiki 封锁状态（每 {args.interval_min:.0f} 分钟探测一次，"
          f"最长等待 {args.max_hours:.0f} 小时）", flush=True)
    n = 0
    while time.time() < deadline:
        n += 1
        blocked = not probe()
        ts = time.strftime("%H:%M:%S")
        if blocked:
            print(f"[waiter] #{n} {ts} 仍被 403 拦截，继续等待…", flush=True)
            time.sleep(args.interval_min * 60)   # 探测间隔（修复：避免高频轰击延长封锁）
        else:
            print(f"[waiter] #{n} {ts} 封锁已解除！触发标准增量更新…", flush=True)
            from server.app.infra import update_runner
            r = update_runner.trigger("incremental", "manual", "兰斯洛特")
            print(f"[waiter] 触发结果: {r}", flush=True)
            if r.get("accepted"):
                print(f"[waiter] 增量任务 #{r['job_id']} 已进入正式任务系统，"
                      f"可在后台「更新任务台账」与 artifacts/logs/update-{r['job_id']}.log 跟踪。", flush=True)
            return
    print(f"[waiter] 等待超过 {args.max_hours:.0f} 小时仍未解除，退出"
          f"（可随时重新运行本脚本，或在后台重新点击「立即增量更新」）", flush=True)


if __name__ == "__main__":
    main()
