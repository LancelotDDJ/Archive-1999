# -*- coding: utf-8 -*-
"""应用组装：中间件、路由、静态资源、启动钩子。

只做「挂载与装配」，不含业务逻辑。
"""
from __future__ import annotations

import logging
import os
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api.v1 import auth as v1_auth
from .api.v1 import feedback as v1_feedback
from .api.v1 import conversations as v1_conversations
from .api.v1 import qa as v1_qa
from .api.v1 import system as v1_system
from .api.v1 import users as v1_users
from .core.config import LOGS, WEBUI, settings
from .core.errors import AppError
from .core.retrieval.store import KBNotReadyError, KBStore
from .infra import scheduler
from .infra.db import db
from .services import auth_service

API = "/api/v1"

# ---------- 公网访问口令门禁（环境变量 ARCHIVE_TOKEN 启用；留空则不校验） ----------
_ACCESS_TOKEN = os.environ.get("ARCHIVE_TOKEN", "").strip()

_GATE_401_HTML = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>需要访问口令</title>
<style>
  body { margin: 0; min-height: 100vh; display: flex; align-items: center; justify-content: center;
    font-family: Georgia, 'Noto Serif SC', serif; color: #e8dfc8;
    background: radial-gradient(ellipse at 50% 30%, #17160f, #0b0a07); }
  .card { text-align: center; border: 1px solid rgba(201,165,94,.45); border-radius: 3px;
    padding: 40px 48px; background: linear-gradient(180deg, #14130f, #0c0b09);
    box-shadow: 0 24px 80px rgba(0,0,0,.65), inset 0 0 0 1px rgba(201,165,94,.12); max-width: 92vw; }
  .crest { color: #C9A55E; letter-spacing: .5em; text-indent: .5em; font-size: 18px; margin-bottom: 14px; }
  h1 { font-size: 20px; letter-spacing: .3em; margin: 0 0 16px; }
  p { font-size: 13.5px; line-height: 2; color: rgba(234,227,198,.75); margin: 0; }
  code { font-family: monospace; color: #C9A55E; background: rgba(201,165,94,.08);
    border: 1px solid rgba(201,165,94,.3); border-radius: 2px; padding: 1px 8px; }
</style></head>
<body><div class="card">
  <div class="crest">✦ ✦ ✦</div>
  <h1>需 要 访 问 口 令</h1>
  <p>该档案库未对公开网络直接开放。<br>
  请向档案管理员获取访问链接（形如 <code>?k=口令</code>），<br>
  首次访问后 30 天内本设备将自动记住授权。</p>
</div></body></html>"""


def _setup_logging() -> None:
    LOGS.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(LOGS / "server.log", maxBytes=5_000_000,
                                  backupCount=3, encoding="utf-8")
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    handler.setFormatter(fmt)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)


def create_app() -> FastAPI:
    _setup_logging()
    app = FastAPI(title="重返未来：1999知识库", version="2.0.0",
                  docs_url=None, redoc_url=None, openapi_url=None)

    # ---------- 统一异常 ----------
    @app.exception_handler(AppError)
    async def app_error_handler(_req: Request, exc: AppError):
        return JSONResponse(exc.payload(), status_code=exc.http_status)

    @app.exception_handler(KBNotReadyError)
    async def kb_not_ready_handler(_req: Request, exc: KBNotReadyError):
        return JSONResponse(
            {"error": {"code": "kb_not_ready", "message": str(exc)}},
            status_code=503)

    @app.exception_handler(Exception)
    async def unhandled_handler(_req: Request, exc: Exception):
        logging.getLogger("server").exception("unhandled: %s", exc)
        return JSONResponse(
            {"error": {"code": "internal_error", "message": "服务器内部错误"}},
            status_code=500)

    # ---------- 访问日志（精简） ----------
    @app.middleware("http")
    async def access_log(request: Request, call_next):
        t0 = time.time()
        resp = await call_next(request)
        if request.url.path.startswith(API):
            logging.getLogger("access").info(
                "%s %s -> %s %.0fms", request.method, request.url.path,
                resp.status_code, (time.time() - t0) * 1000)
        return resp

    # ---------- 公网访问口令门禁 ----------
    @app.middleware("http")
    async def token_guard(request: Request, call_next):
        """ARCHIVE_TOKEN 非空时启用：经隧道访客首访 ?k=口令 → 种 30 天 Cookie；
        本机直连（回环地址且未经隧道转发）自动豁免，方便管理员本机使用。"""
        if _ACCESS_TOKEN:
            client = request.client.host if request.client else ""
            via_tunnel = any(h in request.headers
                             for h in ("cf-connecting-ip", "x-forwarded-for"))
            if not (client in ("127.0.0.1", "::1") and not via_tunnel):
                k = request.query_params.get("k", "")
                if k == _ACCESS_TOKEN or request.cookies.get("kb_token", "") == _ACCESS_TOKEN:
                    resp = await call_next(request)
                    if k == _ACCESS_TOKEN:
                        resp.set_cookie("kb_token", _ACCESS_TOKEN, max_age=2592000,
                                        httponly=True, samesite="lax")
                    return resp
                return HTMLResponse(_GATE_401_HTML, status_code=401)
        return await call_next(request)

    # ---------- API 路由 ----------
    app.include_router(v1_auth.router, prefix=API)
    app.include_router(v1_users.router, prefix=API)
    app.include_router(v1_qa.router, prefix=API)
    app.include_router(v1_conversations.router, prefix=API)
    app.include_router(v1_system.router, prefix=API)
    app.include_router(v1_feedback.router, prefix=API)

    # ---------- 页面与静态资源 ----------
    @app.get("/", include_in_schema=False)
    def home():
        return FileResponse(WEBUI / "index.html")

    @app.get("/login", include_in_schema=False)
    def login_page():
        return FileResponse(WEBUI / "login.html")

    @app.get("/admin", include_in_schema=False)
    def admin_page():
        return FileResponse(WEBUI / "admin.html")

    @app.get("/admin/accounts", include_in_schema=False)
    def admin_accounts_page():
        return FileResponse(WEBUI / "admin_accounts.html")

    app.mount("/css", StaticFiles(directory=WEBUI / "css"), name="css")
    app.mount("/js", StaticFiles(directory=WEBUI / "js"), name="js")
    app.mount("/assets", StaticFiles(directory=WEBUI / "assets"), name="assets")

    # ---------- 启动钩子 ----------
    @app.on_event("startup")
    def startup() -> None:
        db()                                  # 建库建表
        auth_service.bootstrap_admin()        # 首启管理员引导

        # KB 冷加载（约 300MB）移入后台线程：端口立即就绪，登录/注册/会话切换
        # 不再被装载阻塞；装载完成前到达的知识库类请求会在 KBStore 锁上排队。
        def _warm_kb() -> None:
            t0 = time.time()
            try:
                st = KBStore.get().status()
                print(f"[startup][后台装载完成 {time.time() - t0:.1f}s] "
                      f"KB ready={st['ready']} chunks={st['chunks']} "
                      f"entities={st['entities']}", flush=True)
            except KBNotReadyError as e:
                print(f"[startup][后台装载] {e}", flush=True)

        threading.Thread(target=_warm_kb, name="kb-prewarm", daemon=True).start()
        from .infra import update_runner
        update_runner.recover_stale()         # 清扫重启前遗留的 running 任务
        scheduler.start()                     # 每月 1 日自动增量更新

    return app


app = create_app()


def main() -> None:
    import uvicorn
    cfg = settings().section("server")
    host, port = cfg.get("host", "127.0.0.1"), int(cfg.get("port", 8765))
    if host not in ("127.0.0.1", "localhost"):
        print(f"[LAN] 监听 {host}:{port} — 局域网设备访问 http://<本机IPv4>:{port}",
              flush=True)
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
