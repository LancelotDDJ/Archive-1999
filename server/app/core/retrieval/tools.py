# -*- coding: utf-8 -*-
"""检索工具集（LLM function-calling 契约 + 执行器）。

挂在检索引擎上的 5 个工具，agent_service 与 story_service 共用——
避免服务层互相依赖。
"""
from __future__ import annotations

import json

from .engine import RetrievalEngine

TOOLS_SCHEMA = [
    {"type": "function", "function": {
        "name": "search_chunks",
        "description": "混合检索知识块（向量+关键词）。适合查具体信息：技能效果、数值、台词、活动规则等。",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "检索查询词"},
            "top_k": {"type": "integer", "description": "返回条数，默认 6"}},
            "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "scan_mentions",
        "description": "全库精确扫描关键词：返回【全部】命中页面的紧凑清单（按 页面类型+主线系列 分组，可直接用于穷尽统计），另附前几条原文摘录。统计/计数/列举类问题必用。",
        "parameters": {"type": "object", "properties": {
            "keyword": {"type": "string", "description": "要精确匹配的关键词"},
            "type_filter": {"type": "string", "description": "可选页面类型过滤（角色/心相/道具/活动/关卡章节/剧情/建筑/收藏品/百科）"},
            "max_excerpts": {"type": "integer", "description": "摘录条数，默认 8"}},
            "required": ["keyword"]}}},
    {"type": "function", "function": {
        "name": "find_backlinks",
        "description": "列出通过维基内链指向某页面的全部页面（内链是官方编目关联）。「某角色在哪些章节/版本出现/登场」类问题优先用此工具（type_filter=关卡章节），再结合 scan_mentions 交叉验证。",
        "parameters": {"type": "object", "properties": {
            "title": {"type": "string", "description": "目标页面标题"},
            "type_filter": {"type": "string", "description": "可选页面类型过滤"}},
            "required": ["title"]}}},
    {"type": "function", "function": {
        "name": "get_page",
        "description": "读取指定页面的完整分块内容。支持批量：传 titles 数组（至多 12 个）一次读取多个页面——穷尽名单/统计类任务必须用批量模式（每页返回前 max_blocks 块，批量时默认 3 块）。",
        "parameters": {"type": "object", "properties": {
            "title": {"type": "string", "description": "单个页面标题"},
            "titles": {"type": "array", "items": {"type": "string"},
                       "description": "批量页面标题数组（至多 12 个）"},
            "max_blocks": {"type": "integer", "description": "每页最多返回块数，单页默认 10，批量默认 3"}}}}},
    {"type": "function", "function": {
        "name": "list_entities",
        "description": "列出知识库实体清单，可按类型或名称关键词过滤。",
        "parameters": {"type": "object", "properties": {
            "type_filter": {"type": "string"},
            "keyword": {"type": "string", "description": "标题包含的关键词"},
            "max_results": {"type": "integer", "default": 80}}}}},
]


class ToolExecutor:
    """在检索引擎上执行工具调用，返回 JSON 可序列化结果。"""

    def __init__(self, r: RetrievalEngine) -> None:
        self.r = r

    def execute(self, name: str, args: dict):
        try:
            if name == "search_chunks":
                res, _ = self.r.search(args.get("query", ""),
                                       top_k=int(args.get("top_k", 6)))
                return [{"page": c["page"], "section": c["section"],
                         "text": c["text"][:400]} for c in res]
            if name == "scan_mentions":
                res = self.r.scan_mentions(
                    args.get("keyword", ""),
                    max_excerpts=int(args.get("max_excerpts", 8)),
                    type_filter=args.get("type_filter"))
                groups: dict[str, list] = {}
                for pg in res.get("pages", []):
                    key = f"{pg.get('type', '百科')}/{pg.get('series') or '其他'}"
                    groups.setdefault(key, []).append(pg["page"])
                res["grouped_pages"] = {k: "、".join(sorted(v))
                                        for k, v in sorted(groups.items())}
                res.pop("pages", None)
                return res
            if name == "find_backlinks":
                return self.r.backlinks(args.get("title", ""), args.get("type_filter"))
            if name == "get_page":
                titles = args.get("titles")
                if isinstance(titles, list) and titles:
                    # 批量读取：每页精简到 max_blocks(默认3) 块 × 350 字，防上下文膨胀
                    mb = int(args.get("max_blocks", 3))
                    out = []
                    for t in titles[:12]:
                        blocks = self.r.page_chunks(str(t), mb)
                        if blocks:
                            out.append({"title": t, "blocks": [
                                {"section": b["section"], "text": b["text"][:350]}
                                for b in blocks]})
                        else:
                            out.append({"title": t, "blocks": [],
                                        "note": "页面不存在或无内容"})
                    return {"batch": True, "pages": out,
                            "page_count": len(out)}
                title = args.get("title", "")
                mb = int(args.get("max_blocks", 10))
                blocks = self.r.page_chunks(title, mb)
                return [{"section": b["section"], "text": b["text"][:600]}
                        for b in blocks]
            if name == "list_entities":
                return self.r.list_entities(args.get("type_filter"),
                                            args.get("keyword"),
                                            int(args.get("max_results", 80)))
            return {"error": f"unknown tool {name}"}
        except Exception as e:
            return {"error": str(e)}

    @staticmethod
    def progress_note(name: str, result) -> str:
        """给 UI 的简短进度描述。"""
        if isinstance(result, dict):
            if result.get("batch"):
                return f"批量读取 {result.get('page_count', 0)} 个页面"
            if "total_chunks" in result:
                return (f"全库扫描「{result.get('keyword', '')}」→ 命中 "
                        f"{result['total_chunks']} 块 / {result['page_count']} 页"
                        + ("（已穷尽）" if not result.get("truncated") else "（超上限截断）"))
            if "count" in result and "pages" in result:
                return f"内链反查「{result.get('title', '')}」→ {result['count']} 页"
            if "count" in result and "entities" in result:
                return f"实体清单 → {result['count']} 个"
            if "error" in result:
                return f"{name} 失败: {result['error']}"
        if isinstance(result, list):
            return f"{name} → {len(result)} 条结果"
        return f"{name} 完成"


def extract_json(text: str):
    """从 LLM 输出中提取第一个平衡的 JSON 对象/数组。"""
    if not text:
        return None
    for start_ch, end_ch in (("{", "}"), ("[", "]")):
        start = text.find(start_ch)
        if start < 0:
            continue
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == start_ch:
                depth += 1
            elif ch == end_ch:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except ValueError:
                        break
    return None


def normalize_worldview(data):
    """形态归一化：模型可能把世界观输出成数组而非对象。"""
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and item.get("agents"):
                return item
        if data and all(isinstance(x, dict) and x.get("name") for x in data):
            return {"agents": data}
    return data
