# -*- coding: utf-8 -*-
"""Agent 深度分析服务：ReAct 工具循环（聚合/统计/跨页推理类问题）。

工具契约与执行在 core.retrieval.tools（与推演服务共享）；
本服务专注「规划-执行-观察-汇总」循环与回答质量保障：
混合检索种子注入、共引二跳关联、英文规划文本拦截、
DSML 工具标记抢救、证据强制、迭代上限与末轮强制汇总。
"""
from __future__ import annotations

import json
import re
from collections import Counter

from ..core.retrieval import prompts
from ..core.retrieval.engine import RetrievalEngine
from ..core.retrieval.tools import TOOLS_SCHEMA, ToolExecutor
from ..core.textutil import _is_english_dominant, sanitize_answer
from ..infra import llm_client

_DSML_INVOKE = re.compile(
    r"<｜｜DSML｜｜\s*invoke\s+name=\"([^\"]+)\">(.*?)</｜｜DSML｜｜\s*invoke>", re.S)
_DSML_PARAM = re.compile(r"parameter\s+name=\"([^\"]+)\"[^>]*>(.*?)<", re.S)
_DSML_ANY = re.compile(r"</?｜｜DSML｜｜[^>]*>")

_PLAN_OPENERS = ("let me", "i'll", "i will", "i found", "let's", "i need to",
                 "now i", "first,", "i should", "the user asked", "i can see")
_TOOL_NAMES = ("scan_mentions", "get_page", "find_backlinks",
               "search_chunks", "list_entities")


def _parse_dsml(text: str) -> list[dict]:
    calls = []
    for i, m in enumerate(_DSML_INVOKE.finditer(text)):
        name = m.group(1).strip()
        args = {p.group(1): p.group(2).strip()
                for p in _DSML_PARAM.finditer(m.group(2))}
        if name:
            calls.append({"id": f"dsml_{i}", "type": "function",
                          "function": {"name": name,
                                       "arguments": json.dumps(args, ensure_ascii=False)}})
    return calls


def _strip_dsml(text: str) -> str:
    return _DSML_ANY.sub("", text or "").strip()


def _looks_like_planning(content: str) -> bool:
    if not content:
        return False
    # 英文主导的第一行（即使夹带中文术语）＝ 导语泄露，视同规划文本退回
    for ln in content.split("\n"):
        s = ln.strip()
        if s and _is_english_dominant(s):
            return True
        if s:
            break
    head = content[:240]
    low = head.lower()
    if any(low.lstrip().startswith(p) for p in _PLAN_OPENERS):
        return True
    if sum(c.isascii() and c.isalpha() for c in head) > 130:
        return True
    if any(t in content for t in _TOOL_NAMES):
        return True
    return False


class AgentEngine:
    def __init__(self, r: RetrievalEngine) -> None:
        self.r = r
        self.tools = ToolExecutor(r)

    def run_collect(self, question: str, history: list[dict] | None = None,
                    max_iter: int = 10):
        """证据收集循环（生成器）：逐步产出进度事件；
        StopIteration.value = 最终回答（或 None=失败）。"""
        r = self.r
        try:
            ents = r.list_entities(type_filter="活动", max_results=400).get("entities", [])
            names = sorted({(e.get("title") or "").strip() for e in ents if e.get("title")})
            activity_index = "、".join(names)
        except Exception:
            activity_index = ""
        sys_prompt = prompts.AGENT_SYSTEM_BASE + (
            f"\n【知识库活动/版本页索引】（名字可能相关的页面请 get_page 读内容验证）：\n{activity_index}"
            if activity_index else "")
        msgs = [{"role": "system", "content": sys_prompt}]
        for h in (history or [])[-2:]:
            if h.get("q"):
                msgs.append({"role": "user", "content": h["q"][:200]})
                msgs.append({"role": "assistant",
                             "content": (h.get("a") or "")[:300] or "（略）"})
        msgs.append({"role": "user", "content":
                     prompts.AGENT_USER_KICKOFF.format(question=question)})

        tools_used = 1          # 种子检索视为已有证据基础
        planning_pushes = 0

        # 种子检索 + 共引二跳关联（确定性，不依赖模型自觉）
        try:
            seed, _ = r.search(question, top_k=8)
            seed_pages, seen = [], set()
            for c in seed:
                pg = c.get("page")
                if pg and pg not in seen:
                    seen.add(pg)
                    seed_pages.append(pg)
            cite: Counter = Counter()
            for pg in seed_pages[:3]:
                for p in r.backlinks(pg).get("pages", []):
                    nm = p.get("page")
                    if nm and nm not in seen:
                        cite[nm] += 1
            strong = [nm for nm, c in cite.most_common(18) if c >= 2]
            weak = [nm for nm, c in cite.most_common(60) if c == 1]
            blob = ""
            if strong:
                blob = "强共引（被多个首轮关联页共同引用，大概率与主题直接相关）：" + "、".join(strong)
            if weak:
                blob += ("\n弱关联：" + "、".join(weak[:30])) if blob else "弱关联：" + "、".join(weak[:30])
            if blob:
                msgs.append({"role": "user", "content":
                             "【共引关联候选（书目耦合：与首轮命中页被相同来源页引用，"
                             "按强度排序，请 get_page 验证后纳入）】\n" + blob})
        except Exception:
            pass

        for it in range(max_iter):
            allow_tools = it < max_iter - 1
            msg = llm_client.chat_tools(msgs, TOOLS_SCHEMA if allow_tools else None)
            if msg is None:
                yield {"kind": "error", "note": "LLM 调用失败"}
                return None
            tool_calls = msg.get("tool_calls")
            content = _strip_dsml(msg.get("content") or "")
            if not tool_calls and content:
                rescued = _parse_dsml(content)
                if rescued:
                    tool_calls = rescued
                    content = _strip_dsml(_DSML_INVOKE.sub("", content))
                    yield {"kind": "tool", "tool": "dsml-rescue",
                           "note": f"工具标记泄漏已抢救（{len(rescued)} 个调用）"}
            if not tool_calls:
                if content and tools_used > 0 and not _looks_like_planning(content):
                    return content
                if content and tools_used > 0 and _looks_like_planning(content) \
                        and (not allow_tools or planning_pushes >= 2):
                    break
                if _looks_like_planning(content):
                    planning_pushes += 1
                msgs.append({"role": "assistant", "content": content or "（好的）"})
                msgs.append({"role": "user", "content":
                             prompts.AGENT_FORCED_NOTE if tools_used == 0 else
                             (prompts.AGENT_PLANNING_NOTE if _looks_like_planning(content)
                              else "证据仍不足：请继续用工具补充检索（联想扩展），"
                                   "或基于已收集证据输出最终回答。")})
                continue
            if tools_used >= 48:
                break
            msgs.append({"role": "assistant", "content": content,
                         "tool_calls": tool_calls})
            for tc in tool_calls:
                fn = tc.get("function", {})
                name = fn.get("name", "")
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except (ValueError, TypeError):
                    args = {}
                result = self.tools.execute(name, args)
                tools_used += 1
                note = self.tools.progress_note(name, result)
                yield {"kind": "tool", "tool": name, "note": note}
                msgs.append({"role": "tool", "tool_call_id": tc.get("id", ""),
                             "content": json.dumps(result, ensure_ascii=False)[:12000]})
        msgs.append({"role": "user", "content": prompts.AGENT_FINAL_NOTE})
        msg = llm_client.chat_tools(msgs, None)
        content = _strip_dsml((msg or {}).get("content") or "")
        return content or None


def run_agent_stream(body, user: dict, r: RetrievalEngine):
    """供 qa_service 注入的事件生成器：agent 步骤事件 + __final__ 终事件。
    Agent 结果走与常规问答相同的 1h 缓存（重复问题秒回，节省 LLM 费用）。"""
    from .qa_service import (_cache_get, _cache_key, _cache_put,
                             _followup_and_query, _history_of)
    q = (body.question or "").strip()
    history = _history_of(body)
    ckey = "agent|" + _cache_key(q, body.top_k, history)
    hit = _cache_get(ckey)
    if hit:
        yield {"type": "meta", "mode": "agent",
               "model": hit.get("model", ""), "style": "reason",
               "followup": hit.get("followup", False),
               "entity": None, "entity_meta": hit.get("entity_meta"),
               "sources": [], "steps": hit.get("steps", [])}
        yield {"type": "delta", "text": hit["answer"]}
        yield {"type": "done", "mode": "agent", "cached": True}
        yield {"type": "__final__", "answer": hit["answer"], "cached": True}
        return
    followup, _q_eff, _ents_hist, _hq = _followup_and_query(q, history, r)
    engine = AgentEngine(r)
    steps_log: list[str] = []
    try:
        collector = engine.run_collect(q, history if followup else None, max_iter=16)
        final = None
        while True:
            try:
                ev = next(collector)
            except StopIteration as si:
                final = si.value
                break
            if ev.get("kind") == "tool":
                steps_log.append(ev.get("note", ""))
            yield {"type": "agent", "step": ev.get("step"),
                   "tool": ev.get("tool", ""), "note": ev.get("note", "")}
    except Exception as e:
        yield {"type": "error", "error": f"Agent 异常: {e}"}
        return
    if not final:
        yield {"type": "delta",
               "text": "Agent 证据收集未能完成（模型调用失败或超时）。"
                       "请稍后重试，或关闭 Agent 模式使用常规问答。"}
        yield {"type": "done", "mode": "agent"}
        return
    # 思考过程与最终回答分离：剥离 think 推理块与开头纯英文规划行
    final = sanitize_answer(final) or final
    em = None
    ents = r.detect_entities(" ".join(h.get("q", "") for h in history) + " " + q)
    if ents:
        em = r.entity_meta(ents[0])
    model = llm_client.current_model()
    _cache_put(ckey, {"mode": "agent", "model": model, "answer": final,
                      "entity_meta": em, "followup": followup, "steps": steps_log})
    yield {"type": "meta", "mode": "agent", "model": model,
           "style": "reason", "followup": followup, "entity": None,
           "entity_meta": em, "sources": [], "steps": steps_log}
    yield {"type": "delta", "text": final}
    yield {"type": "done", "mode": "agent"}
    yield {"type": "__final__", "answer": final, "cached": False}
