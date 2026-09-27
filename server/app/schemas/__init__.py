# -*- coding: utf-8 -*-
"""pydantic DTO：跨层数据契约（api ↔ services）。"""
from __future__ import annotations

from pydantic import BaseModel, Field


# ---------------- auth ----------------
class RegisterBody(BaseModel):
    username: str = Field(min_length=2, max_length=24)
    password: str = Field(min_length=8, max_length=128)
    display_name: str | None = Field(default=None, max_length=32)


class LoginBody(BaseModel):
    username: str
    password: str


class RefreshBody(BaseModel):
    refresh_token: str


class ChangePasswordBody(BaseModel):
    old_password: str
    new_password: str = Field(min_length=8, max_length=128)


class UpdateProfileBody(BaseModel):
    display_name: str = Field(min_length=1, max_length=32)


class TokenPair(BaseModel):
    access_token: str
    access_expires: int
    refresh_token: str
    refresh_expires: int
    user: dict


# ---------------- admin users ----------------
class SetRoleBody(BaseModel):
    role: str = Field(pattern="^(user|admin)$")


class SetStatusBody(BaseModel):
    status: str = Field(pattern="^(active|disabled)$")


class ResetPasswordBody(BaseModel):
    new_password: str = Field(min_length=8, max_length=128)


# ---------------- qa ----------------
class AskBody(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=8, ge=3, le=16)
    history: list[dict] | None = None
    agent: bool | None = None            # True=强制 Agent；None=自动
    conversation_id: str | None = None


class SimBody(BaseModel):
    theme: str = Field(min_length=2, max_length=500)
    turns: int = Field(default=3, ge=2, le=5)
    max_collect_iter: int = Field(default=8, ge=4, le=12)
    conversation_id: str | None = None


# ---------------- conversations ----------------
class ConversationSaveBody(BaseModel):
    id: str | None = None
    title: str | None = Field(default=None, max_length=60)
    kind: str = Field(default="qa", pattern="^(qa|story)$")
    messages: list[dict] = []


# ---------------- llm config ----------------
class LLMConfigBody(BaseModel):
    provider: str | None = Field(default=None, pattern="^(auto|openai|none)$")
    base_url: str | None = None
    api_key: str | None = None
    clear_key: bool = False
    model: str | None = None
    max_tokens: int | None = None
    temperature: float | None = None


# ---------------- kb admin ----------------
class UpdateTriggerBody(BaseModel):
    kind: str = Field(default="incremental", pattern="^(incremental|full)$")


# ---------------- feedback ----------------
class FeedbackBody(BaseModel):
    kind: str = Field(pattern="^(answer|general)$")
    conv_id: str | None = None
    answer_key: str | None = None
    answer_snippet: str | None = None
    rating: int | None = None
    content: str = ""
