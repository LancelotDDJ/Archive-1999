# -*- coding: utf-8 -*-
"""问答服务：严格/推理双模式编排、NDJSON 流式输出、答案缓存、多轮指代消解。

事件协议（与前端约定）：
  meta(来源/实体卡/模式) → delta*(文本增量) → done
  聚合类问题转入 agent_service（首事件 agent_mode）。
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time

from ..core.retrieval import prompts
from ..core.retrieval.engine import (AGENT_STYLE_RE, REASON_STYLE_RE,
                                     RetrievalEngine)
from ..core.textutil import sanitize_answer
from ..infra import llm_client
from ..infra.db import db
from . import conversation_service

_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_LOCK = threading.Lock()

_TAB_LINE = re.compile(r"^\s*(初始|洞悉|衣着\d?|L2D|立绘|头像|背景图|导航目录)\s*$", re.M)
_MENU_LINE = re.compile(r"^\s*\d{2}\.[^\s]{1,10}\s*$", re.M)


def clip(s: str, n: int = 260) -> str:
    s = s.strip().replace("\n\n", "\n")
    return s if len(s) <= n else s[:n] + "……"


def clean_for_display(s: str) -> str:
    s = _TAB_LINE.sub("", s)
    s = _MENU_LINE.sub("", s)
    lines, out, buf = s.split("\n"), [], []
    for ln in lines:
        t = ln.strip()
        if len(t) == 1 and t:
            buf.append(t)
        else:
            if buf:
                out.append("".join(buf))
                buf = []
            out.append(ln)
    if buf:
        out.append("".join(buf))
    s = "\n".join(out)
    return re.sub(r"\n{3,}", "\n\n", s).strip()


def _sources_of(contexts: list[dict]) -> list[dict]:
    return [{
        "n": i, "page": c["page"], "section": c["section"],
        "type": c.get("type", ""), "url": c.get("url", ""),
        "score": c["score"], "snippet": clip(clean_for_display(c["text"]), 420),
    } for i, c in enumerate(contexts, 1)]


def _extractive_answer(r: RetrievalEngine, question: str, contexts: list[dict],
                       ents: list[str], intents) -> str:
    """无 LLM 兜底：实体卡 + 证据摘录。"""
    parts: list[str] = []
    if intents:
        secs = [s for _, secs, _t in intents for s in secs]
        contexts = sorted(contexts, key=lambda c: 0 if any(
            x in c.get("section", "") for x in secs) else 1)
    if ents:
        meta = r.kb.entities.get(ents[0])
        if meta:
            parts.append(f"### {meta['title']}（{meta.get('type_name', '百科')}）")
            if meta.get("summary"):
                parts.append(clean_for_display(meta["summary"]))
            if meta.get("sections"):
                parts.append("页面章节：" + "、".join(meta["sections"][:12]))
            parts.append("")
    parts.append("根据维基资料，最相关的信息如下：")
    for i, c in enumerate(contexts[:5], 1):
        parts.append(f"[{i}] 《{c['page']}》· {c['section']}\n"
                     f"{clip(clean_for_display(c['text']), 320)}")
    return "\n\n".join(parts)


def _history_of(body) -> list[dict]:
    return [h for h in (body.history or [])[-3:]
            if isinstance(h, dict) and (h.get("q") or "").strip()]


def _followup_and_query(q: str, history: list[dict], r: RetrievalEngine):
    """判别是否追问：代词/短问开头、实体重叠、无实体短问。
    返回 (followup, 检索查询, 历史实体, 生效历史问句)。"""
    if not history:
        return False, q, [], ""
    hist_qs = " ".join(h["q"] for h in history)
    if len(q) <= 12 or re.match(r"^(他|她|它|这|那|他们|她们|它们)", q):
        return True, (history[-1]["q"].strip() + " " + q).strip(), \
            r.detect_entities(hist_qs), hist_qs
    q_ents = set(r.detect_entities(q))
    hist_ents = set(r.detect_entities(hist_qs))
    if q_ents & hist_ents:
        return True, (history[-1]["q"].strip() + " " + q).strip(), \
            list(q_ents & hist_ents), hist_qs
    if not q_ents and len(q) <= 25:
        return True, (history[-1]["q"].strip() + " " + q).strip(), [], hist_qs
    return False, q, [], ""


def _build_messages(question: str, contexts: list[dict], style: str,
                    history: list[dict] | None) -> list[dict]:
    limit = 4000 if style == "reason" else 900
    blocks = [f"[{i}] 《{c['page']}》· {c['section']}\n{c['text'][:limit]}"
              for i, c in enumerate(contexts, 1)]
    sys_p = prompts.REASON_SYSTEM if style == "reason" else prompts.STRICT_SYSTEM
    msgs = [{"role": "system", "content": sys_p}]
    for h in (history or [])[-3:]:
        hq = (h.get("q") or "").strip()[:200]
        ha = (h.get("a") or "").strip()[:400]
        if hq:
            msgs.append({"role": "user", "content": hq})
            msgs.append({"role": "assistant", "content": ha or "（略）"})
    tail = ("请结合此前对话与资料回答；若问题中的代词指向此前话题，请先自行确认指代。"
            if history else
            "请依据资料回答。回答要像一篇有深度的分析文章，自然流畅，不要机械分段。")
    msgs.append({"role": "user", "content":
                 "【资料】\n" + "\n\n".join(blocks) + f"\n\n【问题】{question}\n" + tail})
    return msgs


def _cache_key(q: str, top_k: int, history: list[dict]) -> str:
    sig = json.dumps([{"q": h.get("q", ""), "a": (h.get("a") or "")[:200]}
                      for h in history], ensure_ascii=False)
    return hashlib.md5(f"{q}|{top_k}|{sig}".encode("utf-8")).hexdigest()


def _cache_get(key: str) -> dict | None:
    ttl = 3600
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit and time.time() - hit[0] < ttl:
            resp = dict(hit[1])
            resp["cached"] = True
            return resp
    return None


def _cache_put(key: str, resp: dict) -> None:
    with _CACHE_LOCK:
        _CACHE[key] = (time.time(), dict(resp))
        if len(_CACHE) > 500:
            _CACHE.pop(next(iter(_CACHE)))


def clear_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


def _prep(body, r: RetrievalEngine) -> dict | None:
    """共享预处理：校验/历史/缓存/检索/多跳/意图。"""
    q = (body.question or "").strip()
    if not q:
        return None
    history = _history_of(body)
    ckey = _cache_key(q, body.top_k, history)
    hit = _cache_get(ckey)
    if hit:
        return {"q": q, "cache_key": ckey, "cached": hit}
    followup, q_eff, ents_hist, hist_qs_eff = _followup_and_query(q, history, r)
    style = "reason" if (REASON_STYLE_RE.search(q)
                         or (followup and REASON_STYLE_RE.search(hist_qs_eff[-60:]))) \
        else "strict"
    top_k_eff = min(max(3, body.top_k or 8) + (6 if style == "reason" else 0), 16)
    contexts, ents = r.search(q_eff, top_k=top_k_eff, extra_ents=ents_hist,
                              entity_recall=4 if style == "reason" else 12)
    if style == "reason":
        article = [c for c in contexts if c.get("type") != "数据页"]
        data_c = [c for c in contexts if c.get("type") == "数据页"]
        contexts = article + data_c
        contexts = contexts + r.expand_context(contexts, exclude=set(ents))
    intents = r.detect_intent(q)
    return {"q": q, "contexts": contexts, "ents": ents, "intents": intents,
            "style": style, "history": history, "followup": followup,
            "cache_key": ckey, "cached": None}


def ask_events(body, user: dict, agent_runner):
    """生成器：产出 NDJSON 事件 dict（api 层负责序列化）。
    agent_runner: (body, user, engine) -> generator，延迟注入避免循环依赖。"""
    t0 = time.time()
    q = (body.question or "").strip()
    if not q:
        yield {"type": "error", "error": "empty question"}
        return

    r = RetrievalEngine.get()          # 先查 KB 变更（热重载）
    if not r.kb.ready():
        yield {"type": "error", "error": "知识库尚未就绪，请稍后再试"}
        return

    use_agent = bool(body.agent) or bool(AGENT_STYLE_RE.search(q))
    if use_agent:
        yield {"type": "agent_mode"}
        final_answer = None
        from_cache = False
        for ev in agent_runner(body, user, r):
            if ev.get("type") == "__final__":
                final_answer = ev.get("answer")
                from_cache = bool(ev.get("cached"))
                continue
            yield ev
        _log_qa(user, q, "agent", llm_client.current_model(), from_cache, t0)
        _persist(user, body, q, final_answer, {"mode": "agent"})
        return

    try:
        p = _prep(body, r)
    except Exception as e:
        yield {"type": "error", "error": str(e)}
        return
    if p is None:
        yield {"type": "error", "error": "empty question"}
        return
    if p["cached"]:
        c = p["cached"]
        yield {"type": "meta", "mode": c["mode"], "model": c.get("model", ""),
               "style": c.get("style", "strict"), "entity": c.get("entity"),
               "entity_meta": c.get("entity_meta"), "sources": c.get("sources", [])}
        yield {"type": "delta", "text": c["answer"]}
        yield {"type": "done", "cached": True}
        _log_qa(user, q, c["mode"], c.get("model", ""), True, t0)
        _persist(user, body, q, c["answer"],
                 {"mode": c["mode"], "style": c.get("style"),
                  "sources": c.get("sources", [])})
        return

    provider, info = llm_client.resolve_provider()
    sources = _sources_of(p["contexts"])
    em = r.entity_meta(p["ents"][0] if p["ents"] else None)
    yield {"type": "meta", "mode": provider, "model": info.get("model", ""),
           "style": p["style"], "followup": p["followup"],
           "entity": p["ents"][0] if p["ents"] else None,
           "entity_meta": em, "sources": sources}

    parts: list[str] | None = None
    mode = provider
    if provider == "openai":
        try:
            parts = []
            max_tk = 10000 if p["style"] == "reason" else None
            for piece in llm_client.chat_stream(
                    _build_messages(p["q"], p["contexts"], p["style"],
                                    p["history"] if p["followup"] else None),
                    max_tokens=max_tk):
                parts.append(piece)
                yield {"type": "delta", "text": piece}
        except Exception as e:
            print(f"[qa] stream error: {e}", flush=True)
            parts = None
    if parts:
        full = "".join(parts)
    else:
        mode = "extractive"
        full = _extractive_answer(r, p["q"], p["contexts"], p["ents"], p["intents"])
        yield {"type": "delta", "text": full}

    # 缓存/落库前净化：思考过程（英文规划行、think 块）不进存储
    full = sanitize_answer(full) or full

    resp = {"mode": mode, "model": info.get("model", ""), "answer": full,
            "sources": sources, "entity": p["ents"][0] if p["ents"] else None,
            "entity_meta": em, "style": p["style"], "cached": False}
    _cache_put(p["cache_key"], resp)
    yield {"type": "done", "mode": mode}
    _log_qa(user, p["q"], mode, info.get("model", ""), False, t0)
    _persist(user, body, p["q"], full,
             {"mode": mode, "style": p["style"], "sources": sources,
              "entity_meta": em})


def _log_qa(user: dict, q: str, mode: str, model: str, cached: bool, t0: float) -> None:
    try:
        db().qa_log_add(user["id"], q, mode, model, cached,
                        int((time.time() - t0) * 1000))
    except Exception:
        pass


def _persist(user: dict, body, q: str, answer: str | None, meta: dict) -> None:
    """把本轮问答追加到用户会话（前端随后整体拉取）。"""
    if not answer:
        return
    cid = getattr(body, "conversation_id", None)
    if not cid:
        return
    try:
        conversation_service.record_message(
            user, cid, "user", q, {}, title_hint=q[:24])
        conversation_service.record_message(user, cid, "assistant", answer, meta)
    except Exception as e:
        print(f"[qa] persist failed: {e}", flush=True)


def kb_status() -> dict:
    from ..core.retrieval.store import KBStore
    return KBStore.get().status()
