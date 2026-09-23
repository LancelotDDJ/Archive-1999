# -*- coding: utf-8 -*-
"""Echo 客户端 —— 双端通信测试之「用户端」。

职责：提供用户操作界面；把用户请求转发给服务端(8701)并附加链路信息；
聚合两端健康状态供界面显示通信链路是否正常。

端口 8702；界面：GET /（client_ui.html）
"""
from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

import requests
import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

HERE = Path(__file__).resolve().parent
STATIC = HERE / "static"
PORT = 8702
SERVER = "http://127.0.0.1:8701"
STARTED_AT = time.time()

app = FastAPI(title="Echo Client", docs_url=None, redoc_url=None, openapi_url=None)


class SendIn(BaseModel):
    message: str = Field(min_length=1, max_length=20000)
    mode: str = "echo"


@app.post("/api/send")
def api_send(body: SendIn):
    """浏览器 → 客户端服务 → 服务端 的完整链路。"""
    t0 = time.perf_counter()
    try:
        r = requests.post(f"{SERVER}/api/echo",
                          json={"message": body.message, "mode": body.mode},
                          timeout=5)
        client_latency = round((time.perf_counter() - t0) * 1000, 2)
        data = r.json()
        data["link"] = {
            "hops": [f"browser → echo-client@{PORT}", f"echo-client@{PORT} → echo-server@8701"],
            "client_latency_ms": client_latency,
            "server_http_status": r.status_code,
            "sent_at": datetime.now().isoformat(timespec="milliseconds"),
        }
        return data
    except requests.exceptions.ConnectionError:
        return {"ok": False, "error": "无法连接服务端(8701)：服务未启动或已停止",
                "client_latency_ms": round((time.perf_counter() - t0) * 1000, 2)}
    except requests.exceptions.Timeout:
        return {"ok": False, "error": "服务端响应超时(>5s)",
                "client_latency_ms": round((time.perf_counter() - t0) * 1000, 2)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"链路异常: {e}",
                "client_latency_ms": round((time.perf_counter() - t0) * 1000, 2)}


@app.get("/api/health")
def api_health():
    """聚合两端状态：客户端自身 + 服务端可达性。"""
    server_ok, server_info = False, {}
    try:
        r = requests.get(f"{SERVER}/api/health", timeout=2)
        server_ok = r.ok
        server_info = r.json()
    except Exception:  # noqa: BLE001
        pass
    return {
        "ok": True,
        "service": "echo-client",
        "port": PORT,
        "uptime_s": round(time.time() - STARTED_AT, 1),
        "link_ok": server_ok,
        "server": server_info,
    }


@app.get("/")
def ui():
    return FileResponse(STATIC / "client_ui.html")


if __name__ == "__main__":
    print(f"[echo-client] listening on http://127.0.0.1:{PORT} → server {SERVER}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
