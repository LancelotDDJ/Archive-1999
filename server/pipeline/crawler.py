# -*- coding: utf-8 -*-
"""全量爬虫：res1999.huijiwiki.com（MediaWiki + HuijiWiki）。

四阶段：
  A 页面清单（ns0 内容页/重定向、ns3500 Data 页）
  B Data 命名空间 JSON（50/批）
  C ns0 wikitext（50/批，含重定向 → 别名表）
  D ns0 内容页渲染 HTML（逐页，服务端已展开 Lua 数据）

断点续传以磁盘为准；Cloudflare 对抗见 wikiclient。
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from server.app.core.config import RAW
from .wikiclient import ApiClient

RENDER_DIR = RAW / "render"
DATA_NS_DIR = RAW / "data_ns"
WT_PATH = RAW / "wikitext.jsonl"
LIST_PATH = RAW / "page_list.json"
TS_PATH = RAW / "last_update.txt"


def _safe_name(title: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", title)


def phase_lists(api: ApiClient, progress=print) -> dict:
    """A：收集页面清单。"""
    progress("[A] fetching page lists ...")
    out: dict[str, list] = {"ns0": [], "ns0_redirect": [], "data": []}
    for apfilterredir, key in (("nonredirects", "ns0"), ("redirects", "ns0_redirect")):
        apcontinue = None
        while True:
            params = {"action": "query", "list": "allpages", "apnamespace": 0,
                      "apfilterredir": apfilterredir, "aplimit": "500",
                      "format": "json", "formatversion": "2"}
            if apcontinue:
                params["apcontinue"] = apcontinue
            d = api.get(params)
            out[key] += [p["title"] for p in d.get("query", {}).get("allpages", [])]
            apcontinue = (d.get("continue") or {}).get("apcontinue")
            if not apcontinue:
                break
        progress(f"  {key}: {len(out[key])}")
    apcontinue = None
    while True:
        params = {"action": "query", "list": "allpages", "apnamespace": 3500,
                  "aplimit": "500", "format": "json", "formatversion": "2"}
        if apcontinue:
            params["apcontinue"] = apcontinue
        d = api.get(params)
        out["data"] += [p["title"] for p in d.get("query", {}).get("allpages", [])]
        apcontinue = (d.get("continue") or {}).get("apcontinue")
        if not apcontinue:
            break
    progress(f"  data: {len(out['data'])}")
    LIST_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=1),
                         encoding="utf-8")
    return out


def phase_data_ns(api: ApiClient, titles: list[str], progress=print) -> int:
    """B：Data 页 JSON 内容（批量 50/请求），断点以磁盘文件为准。"""
    DATA_NS_DIR.mkdir(parents=True, exist_ok=True)
    todo = [t for t in titles
            if not (DATA_NS_DIR / (_safe_name(t.replace("Data:", "")) + ".json.json")).exists()]
    progress(f"[B] data_ns: {len(todo)} to fetch / {len(titles)} total")
    done = 0
    for i in range(0, len(todo), 50):
        batch = todo[i:i + 50]
        d = api.get({"action": "query", "prop": "revisions", "rvprop": "content",
                     "rvslots": "main", "titles": "|".join(batch),
                     "format": "json", "formatversion": "2"})
        for p in d.get("query", {}).get("pages", []):
            title = p.get("title", "")
            try:
                content = p["revisions"][0]["slots"]["main"]["content"]
            except (KeyError, IndexError, TypeError):
                continue
            name = _safe_name(title.replace("Data:", ""))
            (DATA_NS_DIR / f"{name}.json.json").write_text(content, encoding="utf-8")
            done += 1
        if i % 5000 == 0 and i:
            progress(f"  data_ns {i}/{len(todo)}")
    return done


def phase_wikitext(api: ApiClient, titles: list[str], progress=print) -> int:
    """C：ns0 全部 wikitext（含重定向），追加写入 wikitext.jsonl，磁盘去重续传。"""
    have: set[str] = set()
    if WT_PATH.exists():
        for line in open(WT_PATH, encoding="utf-8"):
            try:
                have.add(json.loads(line)["title"])
            except (ValueError, KeyError):
                continue
    todo = [t for t in titles if t not in have]
    progress(f"[C] wikitext: {len(todo)} to fetch / {len(titles)} total")
    done = 0
    with open(WT_PATH, "a", encoding="utf-8") as f:
        for i in range(0, len(todo), 50):
            batch = todo[i:i + 50]
            d = api.get({"action": "query", "prop": "revisions", "rvprop": "content",
                         "rvslots": "main", "titles": "|".join(batch),
                         "format": "json", "formatversion": "2"})
            for p in d.get("query", {}).get("pages", []):
                try:
                    content = p["revisions"][0]["slots"]["main"]["content"]
                except (KeyError, IndexError, TypeError):
                    content = ""
                f.write(json.dumps({"title": p.get("title", ""), "content": content},
                                   ensure_ascii=False) + "\n")
                done += 1
            f.flush()
    return done


def phase_render(api: ApiClient, titles: list[str], progress=print) -> int:
    """D：ns0 内容页渲染 HTML（逐页）。断点以 render/{pageid}.json 为准。"""
    RENDER_DIR.mkdir(parents=True, exist_ok=True)
    # title -> pageid
    id_of: dict[str, int] = {}
    for i in range(0, len(titles), 50):
        d = api.get({"action": "query", "titles": "|".join(titles[i:i + 50]),
                     "format": "json", "formatversion": "2"})
        for p in d.get("query", {}).get("pages", []):
            if p.get("pageid"):
                id_of[p["title"]] = p["pageid"]
    todo = [t for t in titles if t in id_of
            and not (RENDER_DIR / f"{id_of[t]}.json").exists()]
    progress(f"[D] render: {len(todo)} to fetch / {len(titles)} total")
    done = 0
    for idx, title in enumerate(todo):
        d = api.get({"action": "parse", "page": title,
                     "prop": "text|categories|displaytitle",
                     "disablelimitreport": 1, "disableeditsection": 1,
                     "disabletoc": 1, "format": "json", "formatversion": "2"})
        (RENDER_DIR / f"{id_of[title]}.json").write_text(
            json.dumps(d, ensure_ascii=False), encoding="utf-8")
        done += 1
        if done % 200 == 0:
            progress(f"  render {done}/{len(todo)}")
    return done


def crawl_all(progress=print) -> dict:
    api = ApiClient("https://res1999.huijiwiki.com/api.php")
    RAW.mkdir(parents=True, exist_ok=True)
    lists = phase_lists(api, progress)
    stats = {"ns0": len(lists["ns0"]), "redirects": len(lists["ns0_redirect"]),
             "data": len(lists["data"])}
    stats["data_fetched"] = phase_data_ns(api, lists["data"], progress)
    stats["wt_fetched"] = phase_wikitext(
        api, lists["ns0"] + lists["ns0_redirect"], progress)
    stats["render_fetched"] = phase_render(api, lists["ns0"], progress)
    TS_PATH.write_text(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                       encoding="utf-8")
    progress(f"crawl done: {stats}")
    return stats
