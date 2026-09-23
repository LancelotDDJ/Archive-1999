# -*- coding: utf-8 -*-
"""MediaWiki API 客户端：Cloudflare 挑战检测 + 自适应全局限速 + 指数退避重试。

设计要点：
- 全局令牌门（线程共享）：起始 1.2s/请求；遇 403/挑战页 ×1.45（上限 3.5s）；成功 ×0.97（下限 1.0s）；
- HTTP 200 但 content-type 非 JSON 且 body 含挑战特征 → 视同 403；
- 单次请求最多重试 10 次，退避 50→400s 随机抖动（分钟级封锁）。
"""
from __future__ import annotations

import json
import random
import threading
import time

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

_CHALLENGE_MARKS = ("challenges.cloudflare", "请稍候", "Just a moment",
                    "cf-browser-verification", "cf_chl")


class CFBlockError(Exception):
    """Cloudflare 封锁/挑战。"""


class ApiClient:
    def __init__(self, api_url: str, *, start_interval: float = 1.2,
                 floor: float = 1.0, cap: float = 3.5) -> None:
        self.api_url = api_url
        self._interval = start_interval
        self._floor = floor
        self._cap = cap
        self._last_ts = 0.0
        self._gate_lock = threading.Lock()
        self._tls = threading.local()
        self.stats = {"ok": 0, "blocked": 0, "retry": 0}

    @property
    def session(self) -> requests.Session:
        s = getattr(self._tls, "s", None)
        if s is None:
            s = requests.Session()
            s.headers.update({
                "User-Agent": UA,
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Referer": "https://res1999.huijiwiki.com/wiki/%E9%A6%96%E9%A1%B5",
            })
            self._tls.s = s
        return s

    def _gate(self) -> None:
        with self._gate_lock:
            wait = self._interval - (time.time() - self._last_ts)
            self._last_ts = time.time() + max(wait, 0.0)
        if wait > 0:
            time.sleep(wait)

    def _penalize(self) -> None:
        with self._gate_lock:
            self._interval = min(self._interval * 1.45, self._cap)

    def _reward(self) -> None:
        with self._gate_lock:
            self._interval = max(self._floor, self._interval * 0.97)

    @staticmethod
    def _looks_like_challenge(resp: requests.Response) -> bool:
        ctype = (resp.headers.get("Content-Type") or "").lower()
        if "json" in ctype:
            return False
        head = resp.text[:2000] if resp.text else ""
        return any(m in head for m in _CHALLENGE_MARKS)

    def get(self, params: dict, *, max_retry: int = 10, timeout: int = 40) -> dict:
        last_err: Exception | None = None
        for attempt in range(max_retry):
            self._gate()
            try:
                r = self.session.get(self.api_url, params=params, timeout=timeout)
            except requests.RequestException as e:
                last_err = e
                self._penalize()
                self.stats["retry"] += 1
                time.sleep(min(5 * (attempt + 1), 30))
                continue
            if r.status_code in (403, 429, 503) or self._looks_like_challenge(r):
                self.stats["blocked"] += 1
                self._penalize()
                wait = random.uniform(50, 400) * (1 + attempt * 0.25)
                time.sleep(min(wait, 400))
                continue
            try:
                data = r.json()
            except ValueError:
                last_err = ValueError(f"non-json response: {r.text[:120]!r}")
                self.stats["retry"] += 1
                time.sleep(3 + attempt * 2)
                continue
            self._reward()
            self.stats["ok"] += 1
            return data
        raise CFBlockError(f"API 连续 {max_retry} 次失败: {last_err}")


def client(api_url: str | None = None) -> ApiClient:
    from server.app.core.config import settings
    url = api_url or settings().section("kb").get("wiki_api")
    return ApiClient(url)
