# -*- coding: utf-8 -*-
"""增量更新器：MediaWiki recentchanges → 变更页重抓重切 → 向量哈希复用 → BM25 重建。

流程：
  1. 读 data/raw/last_update.txt 为时间基准（缺省用建库快照时间）；
  2. recentchanges（ns0|3500, edit|new）拉取变更；
  3. ns0 重抓渲染 → 重分块 → 整页替换；ns3500 重抓 JSON → 重分块 → 整页替换；
  4. 未变更块按 md5(page|section|text) 复用旧向量行，仅编码新块；
  5. BM25 全量重建；同步 entities/aliases/links；写回时间戳；
  6. 服务端靠 mtime 热重载，无需重启。
限制：仅跟踪 edit|new，页面删除不自动移除块。
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

import numpy as np

from server.app.core.config import KB, KB_INDEX, RAW
from . import links as links_mod
from .indexer import embed_text
from .parser import (build_aliases, chunk_data_file, chunk_page, load_wt_map)
from .wikiclient import ApiClient

TS_PATH = RAW / "last_update.txt"
FALLBACK_EPOCH = "2026-09-12T13:00:00Z"


def _chunk_hash(c: dict) -> str:
    return hashlib.md5(
        f"{c['page']}\x00{c['section']}\x00{c['text']}".encode("utf-8")).hexdigest()


def _fetch_changed_titles(api: ApiClient, since: str) -> tuple[set, set]:
    ns0: set[str] = set()
    ns3500: set[str] = set()
    rccontinue = None
    while True:
        params = {"action": "query", "list": "recentchanges", "rcstart": since,
                  "rcdir": "newer", "rctype": "edit|new", "rcnamespace": "0|3500",
                  "rclimit": "500", "format": "json", "formatversion": "2"}
        if rccontinue:
            params["rccontinue"] = rccontinue
        d = api.get(params, max_retry=API_RETRY)
        for rc in d.get("query", {}).get("recentchanges", []):
            (ns0 if rc.get("ns") == 0 else ns3500).add(rc["title"])
        rccontinue = (d.get("continue") or {}).get("rccontinue")
        if not rccontinue:
            break
    return ns0, ns3500


def _resolve_pageids(api: ApiClient, titles: set[str]) -> dict[str, int | None]:
    out: dict[str, int | None] = {}
    tl = sorted(titles)
    for i in range(0, len(tl), 50):
        d = api.get({"action": "query", "titles": "|".join(tl[i:i + 50]),
                     "format": "json", "formatversion": "2"}, max_retry=API_RETRY)
        for p in d.get("query", {}).get("pages", []):
            out[p["title"]] = p.get("pageid")
        time.sleep(0.2)
    return out


def _fetch_render(api: ApiClient, pageid: int, title: str) -> Path:
    d = api.get({"action": "parse", "page": title,
                 "prop": "text|categories|displaytitle",
                 "disablelimitreport": 1, "disableeditsection": 1,
                 "disabletoc": 1, "format": "json", "formatversion": "2"}, max_retry=API_RETRY)
    path = RAW / "render" / f"{pageid}.json"
    path.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    return path


def _fetch_data_content(api: ApiClient, titles: set[str]) -> dict[str, str | None]:
    got: dict[str, str | None] = {}
    tl = sorted(titles)
    for i in range(0, len(tl), 50):
        d = api.get({"action": "query", "prop": "revisions", "rvprop": "content",
                     "rvslots": "main", "format": "json", "formatversion": "2",
                     "titles": "|".join(tl[i:i + 50])}, max_retry=API_RETRY)
        for p in d.get("query", {}).get("pages", []):
            try:
                got[p["title"]] = p["revisions"][0]["slots"]["main"]["content"]
            except (KeyError, IndexError, TypeError):
                got[p["title"]] = None
    return got


API_RETRY = 3   # 更新路径的 API 失败重试预算（默认 10 次×40s 在网络故障时会让任务假死 30+ 分钟）


def run_incremental(force: list[str] | None = None, progress=print) -> dict:
    api = ApiClient("https://res1999.huijiwiki.com/api.php")
    since = TS_PATH.read_text(encoding="utf-8").strip() if TS_PATH.exists() else FALLBACK_EPOCH
    progress(f"incremental update since: {since}")

    ns0, ns3500 = _fetch_changed_titles(api, since)
    for t in (force or []):
        (ns3500 if t.startswith("Data:") else ns0).add(t)
    progress(f"changed: ns0={len(ns0)}, ns3500={len(ns3500)}")
    if not ns0 and not ns3500:
        TS_PATH.write_text(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                           encoding="utf-8")
        return {"changed_pages": 0, "note": "no changes"}

    old_chunks = [json.loads(l) for l in open(KB / "chunks.jsonl", encoding="utf-8")]
    old_emb = np.load(KB_INDEX / "embeddings.npy")
    row_of_hash: dict[str, int] = {}
    for i, c in enumerate(old_chunks):
        row_of_hash.setdefault(_chunk_hash(c), i)

    changed_pages: set[str] = set()
    to_embed: list[dict] = []

    # ---- ns0 文章页 ----
    if ns0:
        ids = _resolve_pageids(api, ns0)
        wt_map = load_wt_map()
        ents = json.loads((KB / "entities.json").read_text(encoding="utf-8"))
        aliases_now = json.loads((KB / "aliases.json").read_text(encoding="utf-8"))
        canon_fn, _types = links_mod.make_canon(ents, aliases_now)
        page_links: dict[str, tuple[set, str]] = {}
        failed_renders: list[str] = []
        for title in sorted(ns0):
            pid = ids.get(title)
            if pid is None:
                changed_pages.add(title)
                continue
            try:
                path = _fetch_render(api, pid, title)
            except Exception as e:
                progress(f"  render failed {title}: {e}")
                failed_renders.append(title)
                continue
            new_cs, entity = chunk_page(path, wt_map)
            changed_pages.add(title)
            to_embed.extend(new_cs)
            if entity:
                ents[title] = entity
            try:
                render_html = json.loads(path.read_text(encoding="utf-8"))["parse"].get("text") or ""
                page_links[title] = (
                    links_mod.extract_outgoing(render_html, title, canon_fn),
                    entity.get("type_name", "百科") if entity else "百科")
            except Exception as e:
                progress(f"  links extract failed {title}: {e}")
        # 失败页统一重试一轮（网络抖动常见；仍失败则计入 result.report 供人工跟进）
        if failed_renders:
            progress(f"retrying {len(failed_renders)} failed renders once")
            still_failed = []
            for title in list(failed_renders):
                pid = ids.get(title)
                try:
                    path = _fetch_render(api, pid, title)
                    new_cs, entity = chunk_page(path, wt_map)
                    changed_pages.add(title)
                    to_embed.extend(new_cs)
                    if entity:
                        ents[title] = entity
                except Exception as e:
                    progress(f"  retry still failed {title}: {e}")
                    still_failed.append(title)
            failed_renders = still_failed
        (KB / "entities.json").write_text(
            json.dumps(ents, ensure_ascii=False, indent=1), encoding="utf-8")
        try:
            aliases = build_aliases()
            (KB / "aliases.json").write_text(
                json.dumps(aliases, ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception as e:
            progress(f"  alias rebuild skipped: {e}")
        if page_links:
            try:
                links = links_mod.load_links()
                links_mod.sync_pages(page_links, links)
                links_mod.save_links(links)
                progress(f"links_map synced for {len(page_links)} pages")
            except Exception as e:
                progress(f"  links sync skipped: {e}")

    # ---- ns3500 数据页 ----
    if ns3500:
        got = _fetch_data_content(api, ns3500)
        for title, content in got.items():
            safe = re.sub(r'[\\/:*?"<>|]', "_", title.replace("Data:", ""))
            path = RAW / "data_ns" / (safe + ".json.json")
            if content is None:
                if path.exists():
                    path.unlink()
            else:
                path.write_text(content, encoding="utf-8")
            name = path.stem
            if name.endswith(".json"):
                name = name[:-5]
            changed_pages.add(f"Data:{name}")
            if content is not None:
                to_embed.extend(chunk_data_file(path))

    # ---- 组装新块列表 ----
    kept = [c for c in old_chunks if c["page"] not in changed_pages]
    new_all = kept + to_embed
    for i, c in enumerate(new_all):
        c["id"] = f"u{i:06d}"

    reuse_rows, fresh = [], []
    for pos, c in enumerate(new_all):
        h = _chunk_hash(c)
        if h in row_of_hash:
            reuse_rows.append((pos, row_of_hash[h]))
        else:
            fresh.append((pos, c))
    progress(f"chunks: {len(reuse_rows)} reused, {len(fresh)} to embed, total {len(new_all)}")

    emb = np.empty((len(new_all), 512), dtype=np.float16)
    for pos, row in reuse_rows:
        emb[pos] = old_emb[row]
    if fresh:
        from fastembed import TextEmbedding
        from server.app.core.config import MODELS
        model = TextEmbedding("BAAI/bge-small-zh-v1.5", cache_dir=str(MODELS),
                              local_files_only=True)
        texts = [embed_text(c)[:2000] for _, c in fresh]
        n = 0
        for arr in model.embed(texts, batch_size=128):
            emb[fresh[n][0]] = arr.astype(np.float16)
            n += 1
            if n % 1024 < 128:
                progress(f"embed {n}/{len(fresh)}")

    KB_INDEX.mkdir(parents=True, exist_ok=True)
    np.save(KB_INDEX / "embeddings.npy", emb)
    with open(KB / "chunks.jsonl", "w", encoding="utf-8") as f:
        for c in new_all:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")

    import jieba
    jieba.initialize()
    from rank_bm25 import BM25Okapi
    corpus = [[t for t in jieba.cut_for_search(embed_text(c)[:1200]) if t.strip()]
              for c in new_all]
    import pickle
    with open(KB_INDEX / "bm25.pkl", "wb") as f:
        pickle.dump(BM25Okapi(corpus), f, protocol=4)

    (KB_INDEX / "meta.json").write_text(json.dumps({
        "model": "BAAI/bge-small-zh-v1.5", "dim": 512, "count": len(new_all),
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "note": f"incremental update since {since}",
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    TS_PATH.write_text(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                       encoding="utf-8")
    result = {"changed_pages": len(changed_pages), "total_chunks": len(new_all),
              "embedded_new": len(fresh)}
    if failed_renders:
        result["failed_pages"] = sorted(failed_renders)
    progress(f"UPDATE DONE: {result}")
    return result
