# -*- coding: utf-8 -*-
"""剧情推演服务（MiroFish 五阶段 + BettaFish 多立场辩论）。

阶段：信息收集（Agent 全库）→ 回合制多角色推演 → 分支辩论 → 多段式报告。
全程事件流（生成器），产物存档 artifacts/reports/simulations/sim_<ts>/。
"""
from __future__ import annotations

import json
import time

from ..core.config import REPORTS
from ..core.retrieval import prompts
from ..core.retrieval.engine import RetrievalEngine
from ..core.retrieval.tools import (TOOLS_SCHEMA, ToolExecutor, extract_json,
                                    normalize_worldview)
from ..infra import llm_client
from ..infra.db import db
from . import conversation_service

SIM_DIR = REPORTS / "simulations"


class StorySimEngine:
    def __init__(self, r: RetrievalEngine) -> None:
        self.r = r
        self.tools = ToolExecutor(r)

    # ---------- Phase 1: 信息收集 ----------
    def collect_worldview(self, theme: str, max_iter: int = 8):
        sys_prompt = (
            "你是《重返未来：1999》剧情推演的信息收集 Agent。"
            "玩家会给出一个【推演主题】（通常是一个假设/分歧点）。"
            "你的任务是用工具收集推演所需的全部信息：\n"
            "1. 涉及角色的设定（性格/目标/能力/当前处境/语言风格）——用 search_chunks + get_page 深挖角色页\n"
            "2. 关键设定/组织（如暴雨、圣洛夫基金会、重塑之手）——用 search_chunks\n"
            "3. 时间线与已知事件（含分歧点前后的原作剧情）——用 get_page 读剧情章节页与「剧情时间线」页\n"
            "4. 人物关系——从剧情文本中提炼\n"
            "证据不足时换关键词、用 scan_mentions 全库扫描。信息越完整越好。")
        user_prompt = (
            f"【推演主题】{theme}\n\n"
            "请调用工具收集上述四类信息。收集完成后，直接输出一个 JSON 对象（不要输出其他文字），"
            "结构如下：\n"
            '{"agents": [{"name": "角色名", "role": "身份", "persona": "性格与行为逻辑", '
            '"goal": "当前目标", "state": "当前处境(含伤势/资源)", "style": "语言风格", '
            '"facts": ["原作事实1"], "citations": [资料编号]}], '
            '"settings": [{"name": "设定名", "summary": "机制说明", "citations": []}], '
            '"timeline": [{"time": "时间", "event": "事件", "citations": []}], '
            '"events": [{"name": "关键事件/章节", "summary": "原作剧情概要", "citations": []}], '
            '"relations": [["角色A", "角色B", "关系说明"]], '
            '"divergence": "推演分歧点(原作节点+假设改变)"}\n'
            "agents 最多 5 个（选与推演最相关的角色）。所有内容必须来自工具收集的资料。")
        msgs = [{"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_prompt}]
        steps = 0
        for it in range(max_iter):
            yield {"type": "collect_round", "round": it + 1}
            msg = llm_client.chat_tools(msgs, TOOLS_SCHEMA)
            if msg is None:
                yield {"type": "collect_note", "note": "LLM 调用失败"}
                return None
            tool_calls = msg.get("tool_calls")
            if not tool_calls:
                content = (msg.get("content") or "").strip()
                data = normalize_worldview(extract_json(content))
                if isinstance(data, dict) and data.get("agents"):
                    return data
                msgs.append({"role": "assistant", "content": content or "（继续）"})
                msgs.append({"role": "user", "content":
                             "请基于已收集的资料输出世界观 JSON（严格按要求结构，不要其他文字）。"})
                continue
            msgs.append({"role": "assistant", "content": msg.get("content") or "",
                         "tool_calls": tool_calls})
            for tc in tool_calls:
                fn = tc.get("function", {})
                name = fn.get("name", "")
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except (ValueError, TypeError):
                    args = {}
                result = self.tools.execute(name, args)
                note = self.tools.progress_note(name, result)
                steps += 1
                yield {"type": "collect_step", "step": steps, "tool": name, "note": note}
                msgs.append({"role": "tool", "tool_call_id": tc.get("id", ""),
                             "content": json.dumps(result, ensure_ascii=False)[:12000]})
        msgs.append({"role": "user", "content":
                     "工具调用轮次已用完。请立即基于已收集的全部资料输出世界观 JSON"
                     "（严格按要求结构，agents 最多 5 个，不要其他文字，使用简体中文）。"})
        msg = llm_client.chat_tools(msgs, None, max_tokens=3000)
        if msg:
            return normalize_worldview(extract_json((msg.get("content") or "").strip()))
        return None

    # ---------- Phase 2: 推演执行 ----------
    def _agent_turn_prompt(self, agent, world_state, turn, others_actions, theme):
        facts = "\n".join(
            f"- {f} [资料{c}]" for f, c in zip(
                agent.get("facts", []),
                agent.get("citations", [1] * len(agent.get("facts", [])))))
        others = "\n".join(f"- {a}：{t[:200]}" for a, t in others_actions) or "（本回合你第一个行动）"
        return (
            f"【你是】{agent.get('name')}（{agent.get('role', '')}）\n"
            f"【性格与行为逻辑】{agent.get('persona', '')}\n"
            f"【当前目标】{agent.get('goal', '')}\n"
            f"【你的处境】{agent.get('state', '')}\n"
            f"【语言风格】{agent.get('style', '')}\n"
            f"【你的原作事实】\n{facts}\n\n"
            f"【推演主题】{theme}\n"
            f"【当前世界状态（第 {turn} 回合开始时）】\n{world_state[:1500]}\n\n"
            f"【本回合其他角色的行动】\n{others}\n\n"
            "请以该角色的身份决定本回合的行动。输出 JSON（不要其他文字）：\n"
            '{"action": "你采取的行动/说的话（第一人称，符合语言风格）", '
            '"reasoning": "内在动机与推理依据（引用你的原作事实编号如 资料[1]）", '
            '"impact": "该行动对局势的影响"}')

    def _narrator_prompt(self, world_state, turn, actions, theme):
        acts = "\n\n".join(f"【{a}】{t[:400]}" for a, t in actions)
        return (
            "你是一场《重返未来：1999》剧情推演的叙述者（导演视角）。\n"
            f"【推演主题】{theme}\n"
            f"【上一回合结束时的世界状态】\n{world_state[:1500]}\n\n"
            f"【第 {turn} 回合各角色行动】\n{acts}\n\n"
            "请输出本回合的剧情叙述与新的世界状态，要求：\n"
            "1. 剧情叙述要自然、有张力，符合各角色性格与世界观（暴雨/回溯/时间旅行等设定）；\n"
            "2. 结尾输出【世界状态更新】段：局势变化、角色状态变化、新的紧张点；\n"
            "3. 若出现重大分歧点（多种合理走向），显式标注【分歧点】并简述各走向。")

    def run_simulation(self, theme, worldview, turns=3):
        agents = worldview.get("agents", [])[:5]
        if not agents:
            return None
        world_state = (
            f"推演分歧点：{worldview.get('divergence', theme)}\n"
            f"初始局势：{theme} 假设成立后的瞬间。\n"
            f"关键设定：{json.dumps(worldview.get('settings', []), ensure_ascii=False)[:600]}\n"
            f"当前时间线位置：{json.dumps(worldview.get('timeline', [])[-2:], ensure_ascii=False)[:400]}")
        process_log = []
        for t in range(1, turns + 1):
            yield {"type": "turn_start", "turn": t}
            actions = []
            for agent in agents:
                prompt = self._agent_turn_prompt(agent, world_state, t, actions, theme)
                msgs = [{"role": "system", "content":
                         "你正在参演一场基于《重返未来：1999》原作设定的剧情推演。"
                         "严格保持角色人设（in-character），不得出戏。输出 JSON。"},
                        {"role": "user", "content": prompt}]
                raw = llm_client.chat(msgs) or ""
                data = extract_json(raw)
                if not isinstance(data, dict) or not data.get("action"):
                    action, reasoning, impact = raw[:500], "（自由发挥）", ""
                else:
                    action = data.get("action", "")
                    reasoning = data.get("reasoning", "")
                    impact = data.get("impact", "")
                actions.append((agent.get("name", "?"), action))
                process_log.append({"turn": t, "agent": agent.get("name"),
                                    "action": action, "reasoning": reasoning,
                                    "impact": impact})
                yield {"type": "action", "turn": t,
                       "agent": agent.get("name", "?"), "action": action,
                       "reasoning": reasoning, "impact": impact}
            narrator_out = llm_client.chat(
                [{"role": "system", "content": "你是剧情推演的叙述者。"},
                 {"role": "user", "content":
                  self._narrator_prompt(world_state, t, actions, theme)}]) or ""
            world_state = narrator_out
            process_log.append({"turn": t, "agent": "__narrator__",
                                "action": narrator_out, "reasoning": "世界状态合成",
                                "impact": ""})
            yield {"type": "narrator", "turn": t, "content": narrator_out}
        return {"world_state": world_state, "process_log": process_log,
                "agents": agents}

    # ---------- Phase 3: 分支辩论 ----------
    def branch_debate(self, turns_summary):
        stances = [
            ("原作逻辑派", "严格依据《重返未来：1999》原作设定与已发生剧情推演走向"),
            ("角色心理派", "以角色性格、动机与心理变化为核心推演走向"),
            ("剧情张力派", "以戏剧冲突与叙事张力为核心，探索更有张力的走向"),
        ]
        ctx = turns_summary[:3000]
        branches = []
        for name, stance in stances:
            out = llm_client.chat([
                {"role": "system", "content":
                 f"你是剧情推演论坛中的「{name}」分析家。立场：{stance}。"
                 "基于推演过程，给出 1 条你认为最合理的后续剧情走向，"
                 "包含：走向标题、推演依据（引用推演中的具体事件）、合理性评分(1-5)。"
                 "用简体中文。JSON 输出：{\"title\":..., \"reasoning\":..., \"score\":...}"},
                {"role": "user", "content": ctx}]) or ""
            data = extract_json(out) or {}
            b = {"stance": name, "title": data.get("title", ""),
                 "reasoning": data.get("reasoning", ""),
                 "score": data.get("score", 3)}
            branches.append(b)
            yield {"type": "branch", "data": b}
        return branches

    # ---------- 主入口 ----------
    def run(self, theme: str, turns: int = 3, max_collect_iter: int = 8):
        yield {"type": "phase", "phase": "collect",
               "note": "全库收集推演所需信息（角色/设定/时间线/事件）"}
        worldview = None
        collector = self.collect_worldview(theme, max_iter=max_collect_iter)
        while True:
            try:
                ev = next(collector)
            except StopIteration as si:
                worldview = si.value
                break
            yield ev
        worldview = normalize_worldview(worldview)
        if not isinstance(worldview, dict) or not worldview.get("agents"):
            yield {"type": "error",
                   "error": "信息收集失败：未能构建世界观。请稍后重试或更换主题表述。"}
            return None
        yield {"type": "worldview", "data": worldview}

        agents = worldview.get("agents", [])[:5]
        yield {"type": "phase", "phase": "simulate",
               "agents": [a.get("name") for a in agents], "turns": turns}
        sim = None
        sim_gen = self.run_simulation(theme, worldview, turns=turns)
        while True:
            try:
                ev = next(sim_gen)
            except StopIteration as si:
                sim = si.value
                break
            yield ev
        if not sim:
            yield {"type": "error", "error": "推演执行失败。"}
            return None

        yield {"type": "phase", "phase": "debate",
               "note": "多立场分析家辩论剧情走向分支"}
        turns_summary = "\n\n".join(
            f"【回合 {p['turn']} · {p['agent']}】{p['action'][:300]}"
            for p in sim["process_log"] if p["agent"] != "__narrator__")
        branches = []
        debater = self.branch_debate(turns_summary)
        while True:
            try:
                ev = next(debater)
            except StopIteration as si:
                branches = si.value or []
                break
            yield ev

        yield {"type": "phase", "phase": "report", "note": "撰写结构化推演报告"}
        proc_digest = "\n".join(
            f"- 回合 {p['turn']} · {p['agent']}：{p['action'][:120]}"
            + (f"（依据: {p['reasoning'][:80]}）"
               if p.get("reasoning") and p["agent"] != "__narrator__" else "")
            for p in sim["process_log"])
        proc_narrative = "\n\n".join(
            (f"### 回合 {p['turn']} · {p['agent']}\n{p['action'][:500]}")
            for p in sim["process_log"])

        report_part1 = [
            {"role": "system", "content":
             "你是《重返未来：1999》剧情推演的报告撰写者。撰写推演报告的【前三章】（Markdown）：\n"
             "## 一、推演主题与假设\n## 二、推演输入信息（角色/设定/时间线，标注来源）\n"
             "## 三、推演过程记录（逐回合紧凑列表：每回合每角色一行，含行动与依据）\n\n"
             "用简体中文；不要写结论与分支（后续章节另写）。"},
            {"role": "user", "content":
             f"【推演主题】{theme}\n\n"
             f"【世界观】\n{json.dumps(worldview, ensure_ascii=False, indent=1)[:2500]}\n\n"
             f"【推演过程记录】\n{json.dumps(sim['process_log'], ensure_ascii=False, indent=1)[:10000]}\n\n"
             "请撰写前三章。"}]
        report_main_story = [
            {"role": "system", "content":
             "你是一位擅长剧情推演的小说作者。基于以下推演过程记录，"
             "撰写一篇完整、连贯且精彩的推演故事（Markdown）。要求：\n"
             "1. 将所有回合中角色的行动、对话与决断串联成连贯叙事；\n"
             "2. 涵盖起因、发展、高潮与结局四个阶段，节奏张弛有度；\n"
             "3. 每个角色的行动与对话必须符合其人设与原作设定；\n"
             "4. 用自然、有文学性的简体中文写作——有画面感、有情绪、有张力；\n"
             "5. 不要标注资料编号——这是故事，不是分析报告；\n"
             "6. 使用 Markdown 格式，可用分隔线和标题划分阶段。"},
            {"role": "user", "content":
             f"【推演主题】{theme}\n\n"
             f"【推演过程记录】\n{proc_narrative[:12000]}\n\n"
             f"【最终世界状态】\n{sim['world_state'][:1500]}\n\n"
             "请撰写完整的推演故事。"}]
        report_branch_stories = [
            {"role": "system", "content":
             "你是一位擅长剧情推演的小说作者。基于推演过程记录与走向分析结果，"
             "为以下每个分支各撰写一篇完整、连贯且精彩的分支故事（Markdown）。要求：\n"
             "1. 每篇故事涵盖起因、发展、高潮与结局；\n"
             "2. 故事内容必须与推演过程中的角色行动、对话一致；\n"
             "3. 故事之间要有明确差异——体现不同走向带来的不同结局；\n"
             "4. 用自然、有文学性的简体中文写作；\n"
             "5. 不要标注资料编号。"},
            {"role": "user", "content":
             f"【推演主题】{theme}\n\n"
             f"【走向分析】\n{json.dumps(branches, ensure_ascii=False, indent=1)[:2500]}\n\n"
             f"【推演过程记录】\n{proc_narrative[:8000]}\n\n"
             f"【最终世界状态】\n{sim['world_state'][:1000]}\n\n"
             "请为每个分支各撰写一篇完整的分支故事。每个故事用 `## 分支 X · 标题` 作为标题。"}]
        report_part2 = [
            {"role": "system", "content":
             "你是《重返未来：1999》剧情推演的报告撰写者。推演报告的【前三章】已完成，"
             "现在撰写【后三章】（Markdown）：\n"
             "## 四、核心结论（推演揭示的剧情逻辑与关键转折）\n"
             "## 五、可能的剧情走向分支（2-4 个，含合理性评分 1-5）\n"
             "## 六、与原作一致性校验（吻合点与分歧点）\n\n"
             "用简体中文；每章内容充实但精炼。"},
            {"role": "user", "content":
             f"【推演主题】{theme}\n\n"
             f"【过程摘要】\n{proc_digest[:3000]}\n\n"
             f"【最终世界状态】\n{sim['world_state'][:1800]}\n\n"
             f"【走向分支辩论结果】\n{json.dumps(branches, ensure_ascii=False, indent=1)[:1800]}\n\n"
             "请撰写后三章。"}]

        return {"report_part1": report_part1, "report_part2": report_part2,
                "report_main_story": report_main_story,
                "report_branch_stories": report_branch_stories,
                "worldview": worldview, "process_log": sim["process_log"],
                "branches": branches, "world_state": sim["world_state"],
                "theme": theme, "agents": sim["agents"],
                "settings": worldview.get("settings", []),
                "timeline": worldview.get("timeline", [])}


def _report_fallback(theme, worldview, process_log, branches, world_state) -> str:
    lines = ["# 剧情推演报告（兜底版）", f"\n## 一、推演主题与假设\n{theme}"]
    lines.append("\n## 二、推演输入信息\n")
    for a in (worldview or {}).get("agents", []):
        lines.append(f"- **{a.get('name')}**（{a.get('role', '')}）：{a.get('persona', '')[:120]}")
    lines.append("\n## 三、推演过程记录\n")
    for p in process_log:
        who = "叙述者" if p["agent"] == "__narrator__" else p["agent"]
        lines.append(f"**回合 {p['turn']} · {who}**：{p['action'][:300]}")
    lines.append("\n## 四、核心结论\n")
    lines.append((world_state or "")[-800:])
    lines.append("\n## 五、可能的剧情走向分支\n")
    for b in branches:
        lines.append(f"- **{b.get('title', '')}**（{b.get('stance', '')}，合理性 "
                     f"{b.get('score', 3)}/5）：{b.get('reasoning', '')[:200]}")
    return "\n".join(lines)


def run_simulation_stream(body, user: dict, r: RetrievalEngine):
    """事件生成器（api 层序列化为 NDJSON）。"""
    theme = (body.theme or "").strip()
    if not theme:
        yield {"type": "error", "error": "empty theme"}
        return
    engine = StorySimEngine(r)
    result = None
    try:
        sim_gen = engine.run(theme, turns=max(2, min(body.turns, 5)),
                             max_collect_iter=body.max_collect_iter)
        while True:
            try:
                ev = next(sim_gen)
            except StopIteration as si:
                result = si.value
                break
            yield ev
    except Exception as e:
        yield {"type": "error", "error": f"推演异常: {e}"}
        return
    if result is None:
        return

    model = llm_client.current_model()
    yield {"type": "meta", "mode": "simulate", "model": model,
           "style": "reason", "followup": False, "entity": None, "entity_meta": None,
           "sources": [{"n": i, "page": s.get("name", ""), "section": "推演设定",
                        "url": "", "score": 0}
                       for i, s in enumerate(result.get("settings", []), 1)],
           "turns": len({p["turn"] for p in result["process_log"]})}

    parts: list[str] = []
    full = ""
    sections = [
        (result["report_part1"], 5000, None),
        (result["report_main_story"], 4000, "完整推演故事"),
        (result["report_branch_stories"], 4000, "分支故事"),
    ]
    for msgs, max_tk, section_name in sections:
        try:
            if section_name:
                yield {"type": "story_section", "section": section_name}
            for piece in llm_client.chat_stream(msgs, max_tokens=max_tk):
                parts.append(piece)
                full += piece
                yield {"type": "delta", "text": piece}
        except Exception as e:
            print(f"[story] report section stream error: {e}", flush=True)
    # 后三章（携带前文尾部续写）
    p2_msgs = result["report_part2"] + [
        {"role": "assistant", "content": (full or "")[-1500:]},
        {"role": "user", "content":
         "前三章、推演故事与分支故事已写完。请撰写最后部分："
         "一致性校验与核心结论，不要重复已有内容，使用简体中文。"}]
    try:
        for piece in llm_client.chat_stream(p2_msgs, max_tokens=4500):
            parts.append(piece)
            full += piece
            yield {"type": "delta", "text": piece}
    except Exception as e:
        print(f"[story] report p2 stream error: {e}", flush=True)

    if not full.strip():
        full = _report_fallback(theme, result.get("worldview"),
                                result["process_log"], result["branches"],
                                result["world_state"])
        yield {"type": "delta", "text": full}

    # 存档（产物归 artifacts/reports/simulations/，与源码严格分离）
    ts = time.strftime("%Y%m%d_%H%M%S")
    sim_dir = SIM_DIR / f"sim_{ts}"
    report_path = ""
    try:
        sim_dir.mkdir(parents=True, exist_ok=True)
        (sim_dir / "report.md").write_text(full, encoding="utf-8")
        (sim_dir / "process.json").write_text(json.dumps(
            {"theme": theme, "worldview": result.get("worldview"),
             "process_log": result["process_log"], "branches": result["branches"],
             "world_state": result["world_state"]},
            ensure_ascii=False, indent=1), encoding="utf-8")
        report_path = str(sim_dir / "report.md")
    except OSError as e:
        report_path = f"存档失败: {e}"
    yield {"type": "done", "mode": "simulate", "report_path": report_path}

    # 会话留痕
    cid = getattr(body, "conversation_id", None)
    if cid:
        try:
            conversation_service.record_message(
                user, cid, "user", f"【剧情推演】{theme}", {}, title_hint=f"推演·{theme[:18]}")
            conversation_service.record_message(
                user, cid, "assistant", full,
                {"mode": "simulate", "report_path": report_path})
        except Exception:
            pass
    try:
        db().qa_log_add(user["id"], f"[推演]{theme}", "simulate", model, False, 0)
    except Exception:
        pass
