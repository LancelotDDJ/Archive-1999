# -*- coding: utf-8 -*-
"""混合检索引擎（框架无关的领域核心）。

召回：bge 向量（0.55）+ BM25（0.45，min-max 归一融合）；
加权：实体页保底召回 +0.35、实体页/相关页加权、意图章节/类型加权；
辅助：实体识别（别名归一 + 分词边界）、意图识别、多跳扩展、全库扫描、
      内链反查、实体清单。
"""
from __future__ import annotations

import re
import threading

import numpy as np

from ...infra.embedder import Embedder
from .store import KBStore

# 意图 -> (正则, 章节关键词, 类型过滤)
INTENT_RULES: list[tuple[str, re.Pattern, tuple, tuple]] = [
    ("voice",    re.compile(r"台词|语音|配音|cv|声线|说话|说些什么"), ("语音",), ()),
    ("skill",    re.compile(r"技能|神秘术|大招|洞悉一|洞悉二|洞悉三|移情|传承|塑造|共鸣"),
     ("神秘术", "传承", "塑造", "共鸣", "技能"), ("character",)),
    ("insight",  re.compile(r"洞悉|养成|突破|升级|素材|材料|掉落"),
     ("素材", "洞悉", "养成", "掉落"), ()),
    ("attr",     re.compile(r"属性|面板|生命|攻击|防御|灵感|定位|星级|稀有度"),
     ("属性", "灵感"), ()),
    ("story",    re.compile(r"剧情|故事|背景|设定|文化|生平|经历|身份|是谁|介绍"),
     ("文化", "故事", "剧情", "概述", "简介"), ("story",)),
    ("obtain",   re.compile(r"获取|获得|怎么得|哪里|商店|兑换|抽取|卡池|复刻"),
     ("获取", "单品", "商店", "兑换", "实装"), ()),
    ("strategy", re.compile(r"攻略|配队|阵容|怎么样|厉害|强度|推荐|评价|搭配"),
     ("定位", "概述", "灵感"), ()),
    ("reason",   re.compile(r"为什么|为何|怎么会|结局|命运|活下来|死了|死亡|牺牲|意味着|原因|如何看待|推测|分析一下"),
     ("文化", "故事", "剧情", "概述", "背景", "设定"), ()),
]

REASON_STYLE_RE = re.compile(
    r"为什么|为何|怎么会|结局|命运|活下来|死了|死亡|牺牲|意味着|原因|如何看待|推测|分析一下|伤势|受伤|重伤|怎么死")
AGENT_STYLE_RE = re.compile(
    r"多少|几个|数一数|统计|盘点|汇总|列出|名单|排名|全库|出现过|登场过|有哪些版本|出场")

GENERIC_ALIAS_BLACKLIST = {
    "角色", "心相", "道具", "物品", "活动", "收藏品", "建筑", "关卡",
    "章节", "剧情", "台词", "语音", "技能", "材料", "素材", "轶事",
    "皮肤", "立绘", "武器", "装备", "介绍", "角色一览", "主题活动",
    "常驻活动", "大型活动", "版本活动", "活动章节", "主线剧情活动",
}

_SERIES_RE = re.compile(r"^(\d{1,2})(ST|ND|RD|TH)-", re.I)


def chapter_series(title: str) -> str | None:
    m = _SERIES_RE.match(title or "")
    return (m.group(1) + m.group(2)).upper() if m else None


class RetrievalEngine:
    """单例；KB 热重载（mtime 变化）时自动重建实体词表。"""

    _inst: "RetrievalEngine | None" = None
    _lock = threading.Lock()

    def __init__(self, kb: KBStore) -> None:
        self.kb = kb
        self._kb_built_mtime = kb._mtime
        self._alias_map: dict[str, str] = {}
        self._ent_vocab: list[str] = []
        self._tokenizer = None
        self._rebuild_vocab()

    @classmethod
    def get(cls) -> "RetrievalEngine":
        with cls._lock:
            kb = KBStore.maybe_reload()
            if cls._inst is None or cls._inst._kb_built_mtime != kb._mtime:
                cls._inst = cls(kb)
            return cls._inst

    def _rebuild_vocab(self) -> None:
        self._alias_map = {a: t for a, t in self.kb.aliases.items()
                           if a not in GENERIC_ALIAS_BLACKLIST}
        vocab = list(self.kb.entities.keys())
        vocab += [a for a in self._alias_map
                  if a not in self.kb.entities and a not in vocab]
        vocab.sort(key=len, reverse=True)
        self._ent_vocab = vocab

    @property
    def tokenizer(self):
        if self._tokenizer is None:
            import jieba
            jieba.initialize()
            self._tokenizer = lambda s: [t for t in jieba.cut_for_search(s) if t.strip()]
        return self._tokenizer

    # ---------------- 识别 ----------------
    def detect_entities(self, q: str, limit: int = 4) -> list[str]:
        found: list[str] = []
        rest = q
        for name in self._ent_vocab:
            if not name or len(found) >= limit:
                continue
            if name in rest:
                if re.fullmatch(r"[0-9A-Za-z .·]+", name):
                    if not re.search(rf"(?<![0-9A-Za-z.]){re.escape(name)}(?![0-9A-Za-z.])", rest):
                        continue
                canonical = self._alias_map.get(name, name)
                if canonical not in found:
                    found.append(canonical)
                rest = rest.replace(name, "◇")
        return found

    def detect_intent(self, q: str) -> list[tuple[str, tuple, tuple]]:
        return [(name, secs, types) for name, pat, secs, types in INTENT_RULES
                if pat.search(q)]

    # ---------------- 混合检索 ----------------
    def search(self, q: str, top_k: int = 8, *,
               extra_ents: list[str] | None = None,
               entity_recall: int = 12) -> tuple[list[dict], list[str]]:
        kb = self.kb
        K = 150
        fused: dict[int, float] = {}
        if kb.emb is not None:
            qv = Embedder.get().embed_query(q)
            top_v = np.argpartition(-(kb.emb @ qv), K)[:K]
            v_scores = kb.emb[top_v] @ qv
            for i, s in zip(top_v, self._norm(v_scores)):
                fused[int(i)] = 0.55 * float(s)
        if kb.bm25 is not None:
            b_all = kb.bm25.get_scores(self.tokenizer(q))
            top_b = np.argpartition(-b_all, K)[:K]
            for i, s in zip(top_b, self._norm(b_all[top_b])):
                fused[int(i)] = fused.get(int(i), 0.0) + 0.45 * float(s)

        ents = list(dict.fromkeys(self.detect_entities(q) + list(extra_ents or [])))
        for e in ents:
            for i in kb.page_index.get(e, [])[:entity_recall]:
                fused[int(i)] = max(fused.get(int(i), 0.0), 0.45) + 0.35

        intents = self.detect_intent(q)
        results = []
        for i, score in fused.items():
            c = kb.chunks[i]
            s = score
            if ents:
                if c["page"] in ents:
                    s += 0.35
                elif any(c["page"].find(e) >= 0 or e.find(c["page"]) >= 0
                         for e in ents if len(c["page"]) > 1 and len(e) > 1):
                    s += 0.18
            for _, secs, types in intents:
                if any(sec in c.get("section", "") for sec in secs):
                    s += 0.12
                if types and c.get("type") in types:
                    s += 0.10
            results.append((s, i, c))
        results.sort(key=lambda x: -x[0])

        out, seen = [], set()
        for s, i, c in results:
            key = (c["page"], c.get("section", ""))
            if key in seen:
                continue
            seen.add(key)
            out.append({"score": round(float(s), 4), "page": c["page"],
                        "section": c.get("section", ""), "type": c.get("type_name", ""),
                        "url": c.get("url", ""), "text": c["text"]})
            if len(out) >= top_k:
                break
        return out, ents

    @staticmethod
    def _norm(x: np.ndarray) -> np.ndarray:
        lo, hi = float(x.min()), float(x.max())
        return (x - lo) / (hi - lo) if hi > lo else np.zeros_like(x)

    # ---------------- 多跳扩展 ----------------
    def expand_context(self, contexts: list[dict], exclude: set[str] | None = None,
                       max_add: int = 6) -> list[dict]:
        exclude = exclude or set()
        scan = "\n".join(c["text"][:500] for c in contexts[:4])
        found = [e for e in self.detect_entities(scan) if e not in exclude]
        added: list[dict] = []
        seen = {(c["page"], c["section"]) for c in contexts}
        for e in found[:3]:
            for i in self.kb.page_index.get(e, [])[:4]:
                c = self.kb.chunks[i]
                if (c["page"], c["section"]) in seen:
                    continue
                seen.add((c["page"], c["section"]))
                added.append({"score": 0.45, "page": c["page"],
                              "section": c["section"], "type": c.get("type_name", ""),
                              "url": c.get("url", ""), "text": c["text"]})
                if len(added) >= max_add:
                    return added
        return added

    # ---------------- 全库扫描 / 内链 / 清单 ----------------
    def scan_mentions(self, keyword: str, max_excerpts: int = 8,
                      type_filter: str | None = None) -> dict:
        kw = (keyword or "").strip()
        if not kw:
            return {"keyword": kw, "total_chunks": 0, "truncated": False,
                    "page_count": 0, "pages": [], "top_excerpts": []}
        pages: dict[str, dict] = {}
        total = 0
        LIMIT = 6000
        for c in self.kb.chunks:
            text = c["text"]
            i = text.find(kw)
            if i < 0:
                continue
            total += 1
            rec = pages.setdefault(c["page"], {
                "page": c["page"], "type": c.get("type_name", ""),
                "sections": set(), "count": 0,
                "series": chapter_series(c["page"]), "excerpt": None})
            rec["count"] += 1
            rec["sections"].add(c.get("section", ""))
            if rec["excerpt"] is None:
                s = max(0, i - 40)
                rec["excerpt"] = text[s:i + len(kw) + 90].replace("\n", " ")
            if total >= LIMIT:
                break
        entries = [
            {"page": r["page"], "type": r["type"], "count": r["count"],
             "series": r["series"], "sections": sorted(r["sections"])[:4]}
            for r in pages.values() if not type_filter or r["type"] == type_filter]
        entries.sort(key=lambda x: -x["count"])
        excerpts = [{"page": r["page"],
                     "section": sorted(r["sections"])[0] if r["sections"] else "",
                     "excerpt": r["excerpt"]}
                    for r in pages.values() if r["excerpt"]][:max_excerpts]
        return {"keyword": kw, "total_chunks": total,
                "truncated": total >= LIMIT, "page_count": len(entries),
                "pages": entries, "top_excerpts": excerpts}

    def backlinks(self, title: str, type_filter: str | None = None) -> dict:
        lst = list(self.kb.links_map.get(title, []))
        if type_filter:
            lst = [x for x in lst if x.get("type") == type_filter]
        for x in lst:
            x["series"] = chapter_series(x.get("page", ""))
        return {"title": title, "count": len(lst), "pages": lst}

    def list_entities(self, type_filter: str | None = None,
                      keyword: str | None = None, max_results: int = 80) -> dict:
        out = []
        for t, m in self.kb.entities.items():
            if type_filter and m.get("type_name") != type_filter:
                continue
            if keyword and keyword not in t:
                continue
            out.append({"title": t, "type": m.get("type_name"),
                        "sections": len(m.get("sections", []))})
        out.sort(key=lambda x: -x["sections"])
        return {"count": len(out), "entities": out[:max_results]}

    def entity_meta(self, title: str | None) -> dict | None:
        if not title:
            return None
        m = self.kb.entities.get(title)
        if not m:
            return None
        return {"title": m.get("title"), "type_name": m.get("type_name"),
                "image": m.get("image"), "url": m.get("url")}

    def page_chunks(self, title: str, max_blocks: int = 10) -> list[dict]:
        idxs = self.kb.page_index.get(title, [])[:max_blocks]
        return [{"section": self.kb.chunks[i]["section"],
                 "text": self.kb.chunks[i]["text"]} for i in idxs]


def engine() -> RetrievalEngine:
    return RetrievalEngine.get()
