# -*- coding: utf-8 -*-
"""SQLite 数据访问层（DAL）。

- WAL 模式 + 外键约束；所有 SQL 参数化；
- 仓储方法显式区分「用户域」（强制 user_id 过滤）与「管理员域」（*_as_admin）；
- 服务层只能通过本模块访问数据库，禁止在别处拼 SQL。
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable

from ..core.config import DB_PATH

_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS users (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
  password_hash TEXT NOT NULL,
  display_name  TEXT NOT NULL DEFAULT '',
  role          TEXT NOT NULL DEFAULT 'user' CHECK(role IN ('user','admin')),
  status        TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','disabled')),
  must_change_pwd INTEGER NOT NULL DEFAULT 0,
  created_at    INTEGER NOT NULL,
  last_login_at INTEGER
);

CREATE TABLE IF NOT EXISTS refresh_tokens (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  token_hash  TEXT NOT NULL UNIQUE,
  expires_at  INTEGER NOT NULL,
  revoked     INTEGER NOT NULL DEFAULT 0,
  created_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_refresh_user ON refresh_tokens(user_id);

CREATE TABLE IF NOT EXISTS conversations (
  id          TEXT PRIMARY KEY,
  user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  title       TEXT NOT NULL DEFAULT '新对话',
  kind        TEXT NOT NULL DEFAULT 'qa' CHECK(kind IN ('qa','story')),
  created_at  INTEGER NOT NULL,
  updated_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_conv_user ON conversations(user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS messages (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  role            TEXT NOT NULL CHECK(role IN ('user','assistant')),
  content         TEXT NOT NULL,
  meta_json       TEXT NOT NULL DEFAULT '{}',
  created_at      INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_msg_conv ON messages(conversation_id, id);

CREATE TABLE IF NOT EXISTS qa_logs (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  question    TEXT NOT NULL,
  mode        TEXT NOT NULL DEFAULT '',
  model       TEXT NOT NULL DEFAULT '',
  cached      INTEGER NOT NULL DEFAULT 0,
  latency_ms  INTEGER NOT NULL DEFAULT 0,
  created_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_qalog_user ON qa_logs(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_qalog_time ON qa_logs(created_at);

CREATE TABLE IF NOT EXISTS update_jobs (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  kind        TEXT NOT NULL CHECK(kind IN ('full','incremental')),
  status      TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','running','ok','fail')),
  trigger_by  TEXT NOT NULL DEFAULT 'manual' CHECK(trigger_by IN ('schedule','manual')),
  actor       TEXT NOT NULL DEFAULT '',
  started_at  INTEGER,
  finished_at INTEGER,
  detail_json TEXT NOT NULL DEFAULT '{}',
  log_path    TEXT NOT NULL DEFAULT '',
  created_at  INTEGER NOT NULL
);
"""

_USERNAME_RE = re.compile(r"^[A-Za-z0-9_一-龥.-]{2,24}$")


def valid_username(name: str) -> bool:
    return bool(_USERNAME_RE.fullmatch(name or ""))


class Database:
    """线程安全的 SQLite 门面：每线程一连接，写操作串行化。"""

    _inst: "Database | None" = None
    _inst_lock = threading.Lock()

    def __init__(self, path: Path = DB_PATH) -> None:
        self.path = path
        self._local = threading.local()
        self._write_lock = threading.Lock()
        self.migrate()

    @classmethod
    def get(cls) -> "Database":
        with cls._inst_lock:
            if cls._inst is None:
                cls._inst = cls()
            return cls._inst

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(str(self.path), timeout=30)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=30000")
            self._local.conn = conn
        return conn

    def migrate(self) -> None:
        conn = sqlite3.connect(str(self.path), timeout=30)
        try:
            conn.executescript(_SCHEMA)
            conn.commit()
        finally:
            conn.close()

    # ---- 基础执行器 ----
    def query(self, sql: str, params: Iterable = ()) -> list[dict]:
        cur = self._conn().execute(sql, tuple(params))
        return [dict(r) for r in cur.fetchall()]

    def query_one(self, sql: str, params: Iterable = ()) -> dict | None:
        cur = self._conn().execute(sql, tuple(params))
        row = cur.fetchone()
        return dict(row) if row else None

    def execute(self, sql: str, params: Iterable = ()) -> int:
        with self._write_lock:
            conn = self._conn()
            cur = conn.execute(sql, tuple(params))
            conn.commit()
            return cur.lastrowid or cur.rowcount

    # ================= users =================
    def user_create(self, username: str, password_hash: str, *,
                    display_name: str = "", role: str = "user",
                    must_change_pwd: int = 0) -> int:
        now = int(time.time())
        return self.execute(
            "INSERT INTO users(username,password_hash,display_name,role,"
            "must_change_pwd,created_at) VALUES(?,?,?,?,?,?)",
            (username, password_hash, display_name or username, role,
             must_change_pwd, now))

    def user_by_name(self, username: str) -> dict | None:
        return self.query_one("SELECT * FROM users WHERE username=?", (username,))

    def user_by_id(self, uid: int) -> dict | None:
        return self.query_one("SELECT * FROM users WHERE id=?", (uid,))

    def user_count(self) -> int:
        row = self.query_one("SELECT COUNT(*) AS n FROM users")
        return int(row["n"]) if row else 0

    def user_touch_login(self, uid: int) -> None:
        self.execute("UPDATE users SET last_login_at=? WHERE id=?",
                     (int(time.time()), uid))

    def user_set_password(self, uid: int, password_hash: str, *,
                          clear_must_change: bool = True) -> None:
        self.execute(
            "UPDATE users SET password_hash=?, must_change_pwd=? WHERE id=?",
            (password_hash, 0 if clear_must_change else 1, uid))

    def user_update_profile(self, uid: int, display_name: str) -> None:
        self.execute("UPDATE users SET display_name=? WHERE id=?",
                     (display_name, uid))

    # ---- admin 域 ----
    def users_list_as_admin(self) -> list[dict]:
        return self.query(
            "SELECT id,username,display_name,role,status,must_change_pwd,"
            "created_at,last_login_at,"
            "(SELECT COUNT(*) FROM conversations c WHERE c.user_id=users.id) AS conv_count,"
            "(SELECT COUNT(*) FROM qa_logs q WHERE q.user_id=users.id) AS qa_count "
            "FROM users ORDER BY id")

    _USER_COUNTS = ("(SELECT COUNT(*) FROM conversations c WHERE c.user_id=users.id) AS conv_count,"
                    "(SELECT COUNT(*) FROM messages m JOIN conversations c2 ON m.conversation_id=c2.id"
                    " WHERE c2.user_id=users.id) AS msg_count,"
                    "(SELECT COUNT(*) FROM qa_logs q WHERE q.user_id=users.id) AS qa_count")

    def users_page_as_admin(self, search: str, page: int, page_size: int) -> dict:
        """账户列表（搜索 + 分页）：search 匹配用户名/显示名。"""
        search = (search or "").strip()
        cond, params = "", []
        if search:
            cond = "WHERE username LIKE ? OR display_name LIKE ?"
            params = [f"%{search}%", f"%{search}%"]
        total = int((self.query_one(
            f"SELECT COUNT(*) AS n FROM users {cond}", params) or {"n": 0})["n"])
        items = self.query(
            f"SELECT id,username,display_name,role,status,must_change_pwd,"
            f"created_at,last_login_at,{self._USER_COUNTS} "
            f"FROM users {cond} ORDER BY id LIMIT ? OFFSET ?",
            [*params, page_size, (page - 1) * page_size])
        return {"total": total, "page": page, "page_size": page_size, "items": items}

    def user_full_as_admin(self, uid: int) -> dict | None:
        """账户完整注册信息（含 password_hash —— 单向哈希，仅供核对/重置）。"""
        return self.query_one(
            f"SELECT id,username,display_name,password_hash,role,status,"
            f"must_change_pwd,created_at,last_login_at,{self._USER_COUNTS} "
            f"FROM users WHERE id=?", (uid,))

    def user_conv_options_as_admin(self, uid: int) -> list[dict]:
        return self.query(
            "SELECT id,title,kind,created_at,updated_at,"
            "(SELECT COUNT(*) FROM messages m WHERE m.conversation_id=conversations.id)"
            " AS msg_count FROM conversations WHERE user_id=? ORDER BY created_at",
            (uid,))

    def user_messages_page_as_admin(self, uid: int, page: int, page_size: int,
                                    cid: str | None = None) -> dict:
        """某账户全部聊天消息（时间正序，可按会话过滤，分页）。"""
        cond, params = "WHERE c.user_id=?", [uid]
        if cid:
            cond += " AND m.conversation_id=?"
            params.append(cid)
        total = int((self.query_one(
            f"SELECT COUNT(*) AS n FROM messages m JOIN conversations c"
            f" ON m.conversation_id=c.id {cond}", params) or {"n": 0})["n"])
        items = self.query(
            f"SELECT m.id, m.conversation_id, c.title AS conv_title, c.kind AS conv_kind,"
            f" m.role, m.content, m.meta_json, m.created_at"
            f" FROM messages m JOIN conversations c ON m.conversation_id=c.id"
            f" {cond} ORDER BY m.created_at ASC, m.id ASC LIMIT ? OFFSET ?",
            [*params, page_size, (page - 1) * page_size])
        for it in items:
            it["sender"] = "用户" if it["role"] == "user" else "助手（档案馆）"
        return {"total": total, "page": page, "page_size": page_size, "items": items}

    def user_set_role_as_admin(self, uid: int, role: str) -> None:
        self.execute("UPDATE users SET role=? WHERE id=?", (role, uid))

    def user_set_status_as_admin(self, uid: int, status: str) -> None:
        self.execute("UPDATE users SET status=? WHERE id=?", (status, uid))
        if status == "disabled":
            self.execute("UPDATE refresh_tokens SET revoked=1 WHERE user_id=?", (uid,))

    def user_delete_as_admin(self, uid: int) -> None:
        self.execute("DELETE FROM users WHERE id=?", (uid,))

    # ================= refresh tokens =================
    def refresh_store(self, user_id: int, token_hash: str, expires_at: int) -> None:
        self.execute(
            "INSERT INTO refresh_tokens(user_id,token_hash,expires_at,created_at)"
            " VALUES(?,?,?,?)",
            (user_id, token_hash, expires_at, int(time.time())))

    def refresh_lookup(self, token_hash: str) -> dict | None:
        return self.query_one(
            "SELECT * FROM refresh_tokens WHERE token_hash=? AND revoked=0",
            (token_hash,))

    def refresh_revoke(self, token_hash: str) -> None:
        self.execute("UPDATE refresh_tokens SET revoked=1 WHERE token_hash=?",
                     (token_hash,))

    def refresh_revoke_all(self, user_id: int) -> None:
        self.execute("UPDATE refresh_tokens SET revoked=1 WHERE user_id=?",
                     (user_id,))

    def refresh_gc(self) -> None:
        self.execute("DELETE FROM refresh_tokens WHERE expires_at<?",
                     (int(time.time()) - 86400,))

    # ================= conversations =================
    def conv_create(self, cid: str, user_id: int, title: str, kind: str) -> None:
        now = int(time.time())
        self.execute(
            "INSERT INTO conversations(id,user_id,title,kind,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?)", (cid, user_id, title[:60], kind, now, now))

    def conv_get_owned(self, cid: str, user_id: int) -> dict | None:
        return self.query_one(
            "SELECT * FROM conversations WHERE id=? AND user_id=?", (cid, user_id))

    def conv_get_as_admin(self, cid: str) -> dict | None:
        return self.query_one("SELECT * FROM conversations WHERE id=?", (cid,))

    def conv_list(self, user_id: int) -> list[dict]:
        return self.query(
            "SELECT c.id,c.title,c.kind,c.created_at,c.updated_at,"
            "(SELECT COUNT(*) FROM messages m WHERE m.conversation_id=c.id) AS msg_count "
            "FROM conversations c WHERE c.user_id=? ORDER BY c.updated_at DESC LIMIT 200",
            (user_id,))

    def conv_list_as_admin(self, user_id: int) -> list[dict]:
        """管理员查看指定用户的会话清单。"""
        return self.conv_list(user_id)

    def conv_touch(self, cid: str, title: str | None = None) -> None:
        if title:
            self.execute("UPDATE conversations SET updated_at=?, title=? WHERE id=?",
                         (int(time.time()), title[:60], cid))
        else:
            self.execute("UPDATE conversations SET updated_at=? WHERE id=?",
                         (int(time.time()), cid))

    def conv_delete(self, cid: str, user_id: int) -> None:
        self.execute("DELETE FROM conversations WHERE id=? AND user_id=?",
                     (cid, user_id))

    # ================= messages =================
    def msg_add(self, cid: str, role: str, content: str, meta: dict) -> None:
        self.execute(
            "INSERT INTO messages(conversation_id,role,content,meta_json,created_at)"
            " VALUES(?,?,?,?,?)",
            (cid, role, content, json.dumps(meta or {}, ensure_ascii=False),
             int(time.time())))

    def msg_list(self, cid: str, limit: int = 300) -> list[dict]:
        rows = self.query(
            "SELECT role,content,meta_json,created_at FROM messages"
            " WHERE conversation_id=? ORDER BY id DESC LIMIT ?", (cid, limit))
        out = []
        for r in reversed(rows):
            try:
                meta = json.loads(r["meta_json"] or "{}")
            except ValueError:
                meta = {}
            out.append({"role": r["role"], "content": r["content"],
                        "meta": meta, "created_at": r["created_at"]})
        return out

    # ================= qa logs =================
    def qa_log_add(self, user_id: int, question: str, mode: str, model: str,
                   cached: bool, latency_ms: int) -> None:
        self.execute(
            "INSERT INTO qa_logs(user_id,question,mode,model,cached,latency_ms,created_at)"
            " VALUES(?,?,?,?,?,?,?)",
            (user_id, question[:500], mode, model, 1 if cached else 0,
             latency_ms, int(time.time())))

    def qa_activity_as_admin(self, days: int = 30) -> list[dict]:
        since = int(time.time()) - days * 86400
        return self.query(
            "SELECT date(created_at,'unixepoch','localtime') AS day,"
            " COUNT(*) AS total, SUM(cached) AS cached,"
            " COUNT(DISTINCT user_id) AS users "
            "FROM qa_logs WHERE created_at>=? GROUP BY day ORDER BY day", (since,))

    def qa_recent_as_admin(self, limit: int = 50) -> list[dict]:
        return self.query(
            "SELECT q.id,q.question,q.mode,q.cached,q.latency_ms,q.created_at,"
            "u.username FROM qa_logs q JOIN users u ON u.id=q.user_id"
            " ORDER BY q.id DESC LIMIT ?", (limit,))

    # ================= update jobs =================
    def job_create(self, kind: str, trigger_by: str, actor: str) -> int:
        return self.execute(
            "INSERT INTO update_jobs(kind,status,trigger_by,actor,created_at)"
            " VALUES(?,?,?,?,?)",
            (kind, "queued", trigger_by, actor, int(time.time())))

    def job_start(self, job_id: int) -> None:
        self.execute("UPDATE update_jobs SET status='running', started_at=? WHERE id=?",
                     (int(time.time()), job_id))

    def job_finish(self, job_id: int, ok: bool, detail: dict, log_path: str) -> None:
        self.execute(
            "UPDATE update_jobs SET status=?, finished_at=?, detail_json=?, log_path=?"
            " WHERE id=?",
            ("ok" if ok else "fail", int(time.time()),
             json.dumps(detail, ensure_ascii=False), log_path, job_id))

    def job_list(self, limit: int = 30) -> list[dict]:
        return self.query(
            "SELECT * FROM update_jobs ORDER BY id DESC LIMIT ?", (limit,))

    def job_running(self) -> dict | None:
        return self.query_one(
            "SELECT * FROM update_jobs WHERE status IN ('queued','running')"
            " ORDER BY id DESC LIMIT 1")

    def job_last_success(self, kind: str = "incremental") -> dict | None:
        return self.query_one(
            "SELECT * FROM update_jobs WHERE kind=? AND status='ok'"
            " ORDER BY id DESC LIMIT 1", (kind,))

    def job_success_this_month(self) -> dict | None:
        return self.query_one(
            "SELECT * FROM update_jobs WHERE status='ok'"
            " AND strftime('%Y-%m', datetime(finished_at,'unixepoch','localtime'))"
            " = strftime('%Y-%m','now','localtime') ORDER BY id DESC LIMIT 1")


def db() -> Database:
    return Database.get()
