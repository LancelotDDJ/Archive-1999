# -*- coding: utf-8 -*-
"""WAF 受限环境下的增量采集执行器：curl(完整浏览器头) 作为 API 传输层。

原理：灰机 WAF 按 TLS/头指纹拦截 python-requests，但放行携带完整浏览器头
（sec-ch-ua / Sec-Fetch-* 等）的 curl 请求。本脚本用 curl 子进程实现 ApiClient
传输层，替换 updater.ApiClient 后运行标准增量管线（哈希去重/热更新逻辑全部复用）。
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

API = "https://res1999.huijiwiki.com/api.php"

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": "https://res1999.huijiwiki.com/wiki/%E9%A6%96%E9%A1%B5",
    "sec-ch-ua": '"Chromium";v="131", "Not_A Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}

_last_call = 0.0
MIN_GAP = 1.2          # 礼貌间隔（秒），命中挑战时自适应放大
_gap = MIN_GAP


def _gate() -> None:
    global _last_call, _gap
    wait = _gap - (time.time() - _last_call)
    if wait > 0:
        time.sleep(wait)
    _last_call = time.time()


def curl_api_get(params: dict, max_retry: int = 3, timeout: int = 90) -> dict:
    """curl 传输层：完整浏览器头 + JSON 解析 + 挑战自适应退避。"""
    global _gap
    url = f"{API}?{urlencode(params)}"
    argv = ["curl", "-s", "--compressed", "--max-time", str(timeout)]
    for k, v in BROWSER_HEADERS.items():
        argv += ["-H", f"{k}: {v}"]
    argv.append(url)
    last = None
    for attempt in range(1, max_retry + 1):
        _gate()
        r = subprocess.run(argv, capture_output=True, timeout=timeout + 10)
        body = r.stdout or b""
        text = body.decode("utf-8", "replace")
        if r.returncode == 0 and body:
            if "请稍候" in text[:500] or "Just a moment" in text[:500]:
                last = "challenge"
            else:
                try:
                    return json.loads(text)
                except Exception as e:
                    last = f"json: {e}"
        else:
            last = f"curl rc={r.returncode}"
        wait = min(8 * attempt, 30)
        print(f"  [curl] 第{attempt}次失败({last})，{wait}s 后重试", flush=True)
        _gap = min(_gap * 2, 20)
        time.sleep(wait)
    raise RuntimeError(f"curl API 连续 {max_retry} 次失败: {last}")


class CurlApiClient:
    """替换 updater.ApiClient 的最小桩：仅实现 get()。"""

    def __init__(self, api_url: str, **kw) -> None:
        self.api_url = api_url

    def get(self, params: dict, **kw) -> dict:
        return curl_api_get(params)


def main() -> None:
    import server.pipeline.updater as up
    up.ApiClient = CurlApiClient
    print("[runner] curl 传输层已注入，开始标准增量管线…", flush=True)
    t0 = time.time()
    result = up.run_incremental(progress=print)
    print(f"[runner] 完成（{time.time() - t0:.0f}s）: {json.dumps(result, ensure_ascii=False)}",
          flush=True)


if __name__ == "__main__":
    main()
