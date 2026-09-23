# -*- coding: utf-8 -*-
"""LLM 客户端（OpenAI 兼容协议）。

- provider: auto | openai | none（auto=有 Key 即启用）；
- chat / chat_stream / chat_tools 三接口；失败返回 None / 空迭代，由上层兜底；
- 配置每次调用时读取（Settings 单例已缓存，支持管理端改配置后即时生效）。
"""
from __future__ import annotations

import json
from typing import Any, Iterable, Iterator

import requests

from ..core.config import settings


def resolve_provider() -> tuple[str, dict]:
    cfg = settings().section("llm")
    p = cfg.get("provider", "auto")
    if p == "none":
        return "none", {}
    if p == "auto" and not (cfg.get("api_key") or "").strip():
        return "none", {}
    return "openai", {"model": cfg.get("model", "")}


def _post(path: str, payload: dict, *, stream: bool = False,
          timeout: int | None = None) -> requests.Response | None:
    cfg = settings().section("llm")
    url = f"{cfg.get('base_url', '').rstrip('/')}{path}"
    try:
        r = requests.post(
            url,
            headers={"Authorization": f"Bearer {cfg.get('api_key', '')}",
                     "Content-Type": "application/json"},
            json=payload, timeout=timeout or int(cfg.get("timeout_s", 150)),
            stream=stream)
        r.raise_for_status()
        return r
    except requests.RequestException as e:
        print(f"[llm] request failed: {e}", flush=True)
        return None


def _base_payload(messages: list[dict], max_tokens: int | None) -> dict:
    cfg = settings().section("llm")
    return {
        "model": cfg.get("model", "deepseek-chat"),
        "messages": messages,
        "temperature": float(cfg.get("temperature", 0.25)),
        "max_tokens": int(max_tokens or cfg.get("max_tokens", 10000)),
    }


def chat(messages: list[dict], *, max_tokens: int | None = None) -> str | None:
    provider, _ = resolve_provider()
    if provider != "openai":
        return None
    r = _post("/chat/completions", _base_payload(messages, max_tokens))
    if r is None:
        return None
    try:
        return r.json()["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError):
        return None


def chat_tools(messages: list[dict], tools: list[dict] | None,
               *, max_tokens: int | None = None) -> dict | None:
    """工具调用轮：返回原始 assistant message（含 content 与/或 tool_calls）。"""
    provider, _ = resolve_provider()
    if provider != "openai":
        return None
    payload = _base_payload(messages, max_tokens)
    if tools:
        payload["tools"] = tools
    r = _post("/chat/completions", payload)
    if r is None:
        return None
    try:
        return r.json()["choices"][0]["message"]
    except (ValueError, KeyError, IndexError):
        return None


def chat_stream(messages: list[dict], *, max_tokens: int | None = None) -> Iterator[str]:
    """流式生成：逐段产出文本；失败时不产出（上层兜底）。"""
    provider, _ = resolve_provider()
    if provider != "openai":
        return
    payload = _base_payload(messages, max_tokens)
    payload["stream"] = True
    r = _post("/chat/completions", payload, stream=True, timeout=240)
    if r is None:
        return
    try:
        for raw in r.iter_lines():
            line = raw.decode("utf-8", "ignore").strip() if raw else ""
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                j = json.loads(data)
            except ValueError:
                continue
            piece = ((j.get("choices") or [{}])[0].get("delta") or {}).get("content")
            if piece:
                yield piece
    except requests.RequestException:
        return


def current_model() -> str:
    return settings().section("llm").get("model", "")


def masked_config() -> dict:
    """供管理端展示：不回显完整 Key。"""
    cfg = settings().section("llm")
    key = cfg.get("api_key", "") or ""
    return {
        "provider": cfg.get("provider", "auto"),
        "base_url": cfg.get("base_url", ""),
        "model": cfg.get("model", ""),
        "max_tokens": cfg.get("max_tokens", 10000),
        "temperature": cfg.get("temperature", 0.25),
        "has_key": bool(key.strip()),
        "api_key_masked": (key[:6] + "***") if len(key) > 6 else ("***" if key else ""),
    }


def update_config(patch: dict) -> None:
    """管理端更新 LLM 配置（api_key 传 '***' 视为不修改；clear_key 清空）。"""
    llm_patch: dict[str, Any] = {}
    for k in ("provider", "base_url", "model"):
        if patch.get(k) is not None:
            llm_patch[k] = str(patch[k]).strip()
    if patch.get("clear_key"):
        llm_patch["api_key"] = ""
    elif patch.get("api_key") and "***" not in str(patch["api_key"]):
        llm_patch["api_key"] = str(patch["api_key"]).strip()
    if patch.get("max_tokens"):
        llm_patch["max_tokens"] = int(patch["max_tokens"])
    if patch.get("temperature") is not None:
        llm_patch["temperature"] = float(patch["temperature"])
    settings().save_instance({"llm": llm_patch})
