# -*- coding: utf-8 -*-
"""配置中心：路径、实例配置加载与校验（单例）。

优先级：环境变量 > config.json（项目根） > 默认值。
config.json 为实例配置（含 LLM Key），仅服务端持有，不下发前端。
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

# ---- 路径常量（项目内唯一真源） ----
ROOT = Path(__file__).resolve().parents[2]          # server/
PROJECT = ROOT.parent                                # F:/Archive-1999
WEBUI = PROJECT / "webui"
DATA = PROJECT / "data"
RAW = DATA / "raw"
KB = DATA / "kb"
KB_INDEX = KB / "index"
MODELS = DATA / "models"
ARTIFACTS = PROJECT / "artifacts"
LOGS = ARTIFACTS / "logs"
REPORTS = ARTIFACTS / "reports"
TEST_OUTPUT = ARTIFACTS / "test-output"
DB_PATH = DATA / "app.db"
CONFIG_PATH = PROJECT / "config.json"

for _d in (DATA, RAW, KB, KB_INDEX, MODELS, LOGS, REPORTS, TEST_OUTPUT):
    _d.mkdir(parents=True, exist_ok=True)

_DEFAULTS: dict[str, Any] = {
    "server": {"host": "127.0.0.1", "port": 8765},
    "security": {
        "jwt_secret": "",                 # 空则首启自动生成并持久化到 data/.jwt_secret
        "access_ttl_min": 720,            # 12h
        "refresh_ttl_days": 30,
        "pbkdf2_rounds": 260_000,
    },
    "llm": {
        "provider": "auto",               # auto | openai | none
        "base_url": "https://api.deepseek.com",
        "api_key": "",
        "model": "deepseek-chat",
        "max_tokens": 10000,
        "temperature": 0.25,
        "timeout_s": 150,
    },
    "qa": {"cache_ttl_s": 3600, "rate_limit": 30, "rate_window_s": 300},
    "kb": {
        "wiki_api": "https://res1999.huijiwiki.com/api.php",
        "wiki_base": "https://res1999.huijiwiki.com/wiki/",
        "content_ns": [0, 3500],
        "auto_update_enabled": True,
        "auto_update_day": 1,             # 每月 1 日
        "auto_update_hour": 3,            # 03:00 起
    },
    "admin": {"bootstrap_user": "admin", "bootstrap_password": ""},  # 空=随机生成
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


class Settings:
    """进程级配置单例；reload() 供热更新（改 LLM 配置无需重启）。"""

    _inst: "Settings | None" = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._cfg: dict[str, Any] = {}
        self.reload()

    @classmethod
    def get(cls) -> "Settings":
        with cls._lock:
            if cls._inst is None:
                cls._inst = cls()
            return cls._inst

    def reload(self) -> None:
        file_cfg: dict = {}
        if CONFIG_PATH.exists():
            try:
                file_cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                file_cfg = {}
        cfg = _deep_merge(_DEFAULTS, file_cfg)
        # 环境变量覆盖
        cfg["server"]["host"] = os.environ.get("ARCHIVE_HOST", cfg["server"]["host"])
        cfg["server"]["port"] = int(os.environ.get("ARCHIVE_PORT", cfg["server"]["port"]))
        if os.environ.get("ARCHIVE_JWT_SECRET"):
            cfg["security"]["jwt_secret"] = os.environ["ARCHIVE_JWT_SECRET"]
        if os.environ.get("ARCHIVE_ADMIN_PASSWORD"):
            cfg["admin"]["bootstrap_password"] = os.environ["ARCHIVE_ADMIN_PASSWORD"]
        self._cfg = cfg

    # ---- 访问器 ----
    @property
    def raw(self) -> dict:
        return self._cfg

    def section(self, name: str) -> dict:
        return self._cfg.get(name, {})

    def jwt_secret(self) -> str:
        sec = self._cfg["security"].get("jwt_secret") or ""
        if not sec:
            key_file = DATA / ".jwt_secret"
            if key_file.exists():
                sec = key_file.read_text(encoding="utf-8").strip()
            else:
                import secrets
                sec = secrets.token_hex(32)
                key_file.write_text(sec, encoding="utf-8")
            self._cfg["security"]["jwt_secret"] = sec
        return sec

    def save_instance(self, patch: dict) -> None:
        """把实例级修改（目前仅 llm 段）写回 config.json。"""
        file_cfg: dict = {}
        if CONFIG_PATH.exists():
            try:
                file_cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                file_cfg = {}
        merged = _deep_merge(file_cfg, patch)
        CONFIG_PATH.write_text(
            json.dumps(merged, ensure_ascii=False, indent=1), encoding="utf-8")
        self.reload()


def settings() -> Settings:
    return Settings.get()
