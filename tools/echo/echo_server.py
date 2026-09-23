# -*- coding: utf-8 -*-
"""Echo 服务端 —— 双端通信测试之「服务端」。

职责：接收客户端服务转发的数据 → 按模式处理 → 返回结构化结果；
维护请求台账（内存，最近 200 条）并提供服务端自检界面。

端口 8701；界面：GET /（server_ui.html）
"""
from __future__ import annotations

import hashlib
import re
import time
import uuid
from datetime import datetime
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

HERE = Path(__file__).resolve().parent
STATIC = HERE / "static"
PORT = 8701
STARTED_AT = time.time()
LOG: list[dict] = []          # 最近请求台账（新在前）
_MAX_LOG = 200

app = FastAPI(title="Echo Server", docs_url=None, redoc_url=None, openapi_url=None)

PROCESSORS = {
    "echo": lambda s: s,
    "upper": lambda s: s.upper(),
    "reverse": lambda s: s[::-1],
    "stats": None,               # 结构化统计，单独处理
}


class EchoIn(BaseModel):
    message: str = Field(min_length=1, max_length=20000)
    mode: str = "echo"


def _stats(s: str) -> dict:
    words = re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fff]", s)
    return {
        "chars": len(s),
        "chars_no_space": len(re.sub(r"\s", "", s)),
        "tokens": len(words),
        "lines": s.count("\n") + 1,
        "md5": hashlib.md5(s.encode("utf-8")).hexdigest()[:16],
    }


@app.post("/api/echo")
def api_echo(body: EchoIn):
    t0 = time.perf_counter()
    mode = body.mode if body.mode in PROCESSORS else "echo"
    if mode == "stats":
        result = _stats(body.message)
    else:
        result = PROCESSORS[mode](body.message)
    ms = round((time.perf_counter() - t0) * 1000, 2)
    entry = {
        "ok": True,
        "id": f"REQ-{uuid.uuid4().hex[:8].upper()}",
        "seq": len(LOG) + 1,
        "received_at": datetime.now().isoformat(timespec="milliseconds"),
        "mode": mode,
        "original": body.message,
        "result": result,
        "processing_ms": ms,
        "server": f"echo-server@{PORT}",
    }
    LOG.insert(0, entry)
    del LOG[_MAX_LOG:]
    return entry


@app.get("/api/health")
def api_health():
    return {
        "ok": True,
        "service": "echo-server",
        "port": PORT,
        "uptime_s": round(time.time() - STARTED_AT, 1),
        "total_processed": len(LOG),
        "started_at": datetime.fromtimestamp(STARTED_AT).isoformat(timespec="seconds"),
        "now": datetime.now().isoformat(timespec="seconds"),
    }


@app.get("/api/log")
def api_log(limit: int = 50):
    return {"ok": True, "total": len(LOG), "items": LOG[:max(1, min(limit, _MAX_LOG))]}


@app.post("/api/log/clear")
def api_log_clear():
    n = len(LOG)
    LOG.clear()
    return {"ok": True, "cleared": n}


@app.get("/favicon.ico")
def favicon():
    return JSONResponse({"ok": True})


@app.get("/")
def ui():
    return FileResponse(STATIC / "server_ui.html")


if __name__ == "__main__":
    print(f"[echo-server] listening on http://127.0.0.1:{PORT} (UI: /)", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
