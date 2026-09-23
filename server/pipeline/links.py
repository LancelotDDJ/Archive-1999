# -*- coding: utf-8 -*-
"""内链反向映射：目标实体 → 链接到它的页面清单（links_map.json）。

数据源：渲染 HTML 中的 /wiki/ 内链；目标经别名归一，仅保留已知实体。
支持全量构建与增量同步。
"""
from __future__ import annotations

import json
from collections import defaultdict
from urllib.parse import unquote

from bs4 import BeautifulSoup

from server.app.core.config import KB, RAW

LINKS_PATH = KB / "links_map.json"


def make_canon(entities: dict, aliases: dict):
    types = {t: m.get("type_name", "百科") for t, m in entities.items()}

    def canon(t: str) -> str | None:
        t = (t or "").strip()
        t = aliases.get(t, t)
        return t if t in types else None

    return canon, types


def extract_outgoing(html: str, title: str, canon) -> set[str]:
    """页面渲染 HTML → 出链实体集合（别名归一后）。"""
    targets: set[str] = set()
    soup = BeautifulSoup(html, "lxml")
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not href.startswith("/wiki/"):
            continue
        target = unquote(href[len("/wiki/"):]).split("#")[0].split("?")[0]
        if not target or target == title:
            continue
        t = canon(target)
        if t and t != title:
            targets.add(t)
    return targets


def load_links() -> dict:
    if LINKS_PATH.exists():
        try:
            return json.loads(LINKS_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    return {}


def save_links(links: dict) -> None:
    LINKS_PATH.write_text(json.dumps(links, ensure_ascii=False), encoding="utf-8")


def sync_pages(page_links: dict, links: dict) -> dict:
    """增量同步：page_links = {title: (targets:set, type_name:str)}，原地更新 links。"""
    changed = set(page_links.keys())
    for t in list(links.keys()):
        lst = [x for x in links[t] if x["page"] not in changed]
        if lst:
            links[t] = lst
        else:
            del links[t]
    for title, (targets, ptype) in page_links.items():
        for t in targets:
            lst = links.setdefault(t, [])
            if not any(x["page"] == title for x in lst):
                lst.append({"page": title, "type": ptype})
    return links


def build_all(progress=print) -> dict:
    entities = json.loads((KB / "entities.json").read_text(encoding="utf-8"))
    aliases = json.loads((KB / "aliases.json").read_text(encoding="utf-8"))
    canon, types = make_canon(entities, aliases)

    out: dict[str, dict[str, set]] = defaultdict(lambda: defaultdict(set))
    files = sorted((RAW / "render").glob("*.json"))
    progress(f"scanning {len(files)} pages for internal links ...")
    for idx, path in enumerate(files):
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
            title = d["parse"]["title"]
            html = d["parse"].get("text") or ""
        except (OSError, ValueError, KeyError):
            continue
        for t in extract_outgoing(html, title, canon):
            out[t][title].add(types.get(title, "百科"))
        if idx % 1000 == 0 and idx:
            progress(f"  {idx}/{len(files)}")

    links = {target: [{"page": p, "type": next(iter(ts))}
                      for p, ts in sorted(pages.items())]
             for target, pages in out.items()}
    save_links(links)
    progress(f"links_map saved: {len(links)} targets")
    return {"targets": len(links)}
