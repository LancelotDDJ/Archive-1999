# -*- coding: utf-8 -*-
"""解析器：原始抓取数据 → 结构化知识块。

三通道：
  1. 渲染 HTML（render/*.json）→ 按 h2/h3 章节切块；表格抽离为行级文本；实体卡（含主图）；
  2. wikitext（wikitext.jsonl）→ 重定向别名表；
  3. Data 命名空间 JSON（data_ns/*.json）→ 键路径扁平化文本块。

输出：data/kb/{chunks.jsonl, entities.json, aliases.json}
"""
from __future__ import annotations

import html as htmllib
import json
import re
from pathlib import Path
from urllib.parse import quote

from bs4 import BeautifulSoup, Tag

from server.app.core.config import KB, RAW

CHUNK_MAX = 1500
TABLE_CHUNK_MAX = 1200
MIN_LEN = 25

CAT_RULES = [
    ("character", ("角色",)),
    ("psychube", ("心相",)),
    ("collectible", ("收藏品",)),
    ("building", ("建筑",)),
    ("item", ("道具", "物品")),
    ("activity", ("活动", "庆典", "演绎")),
    ("chapter", ("章节", "关卡")),
    ("story", ("剧情",)),
]

TYPE_NAME = {
    "character": "角色", "psychube": "心相", "collectible": "收藏品",
    "building": "建筑", "item": "道具", "activity": "活动",
    "chapter": "关卡章节", "story": "剧情", "general": "百科",
}

WIKI_BASE = "https://res1999.huijiwiki.com/wiki/"


def classify(title: str, cats: list[str], wikitext: str = "") -> str:
    cset = "|".join(cats)
    for key, words in CAT_RULES:
        if any(w in cset for w in words):
            return key
    head = wikitext[:400] if wikitext else ""
    if "{{角色数据" in head or "{{角色" in head:
        return "character"
    if "{{心相" in head:
        return "psychube"
    if "{{收藏品" in head or "{{古董" in head:
        return "collectible"
    if re.match(r"^[0-9A-Za-z一-龥]{1,12}-\d{1,3}$", title):
        return "chapter"
    if "活动" in title:
        return "activity"
    return "general"


def clean_text(s: str) -> str:
    s = htmllib.unescape(s)
    s = re.sub(r"[ \t　]+", " ", s)
    s = re.sub(r"^\s*导航目录\s*$", "", s, flags=re.M)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


# ---------------- 渲染 HTML 通道 ----------------
def _read_render(path: Path) -> tuple[str, str, list[str]] | None:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    parse = d.get("parse") or {}
    title = parse.get("title") or path.stem
    body = parse.get("text") or ""
    cats: list[str] = []
    for c in (parse.get("categories") or []):
        if isinstance(c, str):
            name = c
        elif isinstance(c, dict):
            name = c.get("category") or c.get("*") or c.get("title") or ""
        else:
            name = ""
        name = str(name).replace("_", " ").strip()
        if name:
            cats.append(name)
    return title, body, cats


_NOISE_SELECTORS = (
    ".mw-editsection", ".toc", ".navbox", ".catlinks", ".mw-references-wrap",
    ".printfooter", "script", "style", ".mw-empty-elt", ".mw-jump-link",
    ".reference", ".mbox-small", ".hatnote .mw-collapsible-toggle", ".char-menu",
)

_DECOR_RE = re.compile(
    r"icon|logo|button|sprite|\.svg|dungeonmap|img_star|herorare|belonging|"
    r"summonpool|charactercareer|resonate|tupo_|bg_|beijingzhuangshi|"
    r"zhujiemian|tu\d", re.I)


def _img_url(img: Tag) -> tuple[str, int] | None:
    src = img.get("src") or img.get("data-src") or ""
    src = htmllib.unescape(src)
    if src.startswith("//"):
        src = "https:" + src
    if not src.startswith("http"):
        return None
    if _DECOR_RE.search(src) or _DECOR_RE.search(img.get("alt") or ""):
        return None
    w = img.get("data-file-width") or img.get("width") or 0
    try:
        w = int(re.sub(r"\D", "", str(w)) or 0)
    except ValueError:
        w = 0
    return src, w


def _pick_image(root: Tag, lead_els: list) -> str | None:
    """页面主图：角色立绘命名 > 立姿图 > 导语区大图。"""
    for pat in ("destiny-", "spine_static-", "l2d_static-"):
        for img in root.find_all("img"):
            r = _img_url(img)
            if r and pat in r[0].lower():
                return r[0]
    for el in lead_els:
        if not isinstance(el, Tag):
            continue
        for img in el.find_all("img"):
            r = _img_url(img)
            if r and (not r[1] or r[1] >= 200):
                return r[0]
    return None


def _table_lines(tb: Tag) -> list[str]:
    headers = [th.get_text(" ", strip=True) for th in tb.find_all("th")]
    lines: list[str] = []
    for tr in tb.find_all("tr"):
        ths = tr.find_all("th", recursive=False)
        tds = tr.find_all("td", recursive=False)
        cells = ths + tds
        if not cells:
            continue
        vals = [clean_text(c.get_text(" ", strip=True)) for c in cells]
        if not any(vals):
            continue
        if headers and tds and not ths and len(vals) <= len(headers):
            pairs = [f"{headers[i]}: {v}" for i, v in enumerate(vals) if v]
            lines.append(" | ".join(pairs))
        else:
            lines.append(" | ".join(v for v in vals if v))
    return lines


def _emit(base: dict, text: str, out: list) -> None:
    if len(text) <= CHUNK_MAX:
        out.append({**base, "text": text})
        return
    buf = ""
    for p in (x for x in text.split("\n") if x.strip()):
        if len(buf) + len(p) + 1 > CHUNK_MAX:
            if buf.strip():
                out.append({**base, "text": buf.strip()})
            buf = p
        else:
            buf = (buf + "\n" + p) if buf else p
    if buf.strip():
        out.append({**base, "text": buf.strip()})


def chunk_page(path: Path, wt_map: dict[str, str]) -> tuple[list[dict], dict | None]:
    """单页渲染 HTML → (知识块, 实体卡)。全量与增量更新共用。"""
    r = _read_render(path)
    if not r:
        return [], None
    title, body_html, cats = r
    soup = BeautifulSoup(body_html, "lxml")
    root = (soup.find("div", class_="mw-parser-output")
            or soup.find("body") or soup)
    for sel in _NOISE_SELECTORS:
        for el in root.select(sel):
            el.decompose()
    for el in root.find_all(class_=re.compile(r"^(wiki-)?(nav|navigation|toc)$", re.I)):
        el.decompose()

    def headline(el: Tag) -> str:
        sp = el.find(class_="mw-headline")
        return clean_text((sp or el).get_text(" ", strip=True))

    sec2, sec3 = "", ""
    lead_parts: list = []
    sections: list[str] = []
    bucket: dict[tuple[str, str], list] = {}
    order: list[tuple[str, str]] = []
    for el in root.children:
        if not isinstance(el, Tag):
            continue
        if el.name in ("h1", "h2"):
            sec2, sec3 = headline(el), ""
            sections.append(sec2)
        elif el.name in ("h3", "h4", "h5"):
            sec3 = headline(el)
            sections.append(f"{sec2}/{sec3}" if sec2 else sec3)
        else:
            key = (sec2, sec3)
            if key not in bucket:
                bucket[key] = []
                order.append(key)
            bucket[key].append(el)

    page_chunks: list[dict] = []
    for key in order:
        s2, s3 = key
        sec_label = "/".join(x for x in (s2, s3) if x) or "概述"
        els = bucket[key]
        if not s2:
            lead_parts = els
        # 表格优先抽离为行级文本
        for el in els:
            tables = [t for t in el.find_all("table") if not t.find_parent("table")]
            for tb in tables:
                lines = _table_lines(tb)
                tb.decompose()
                if not lines:
                    continue
                buf: list[str] = []
                n = 0
                header_line = lines[0] if len(lines) > 1 and " | " in lines[0] else ""
                body_lines = lines[1:] if header_line else lines
                if header_line:
                    buf.append(f"表头: {header_line}")
                for ln in body_lines:
                    buf.append(ln)
                    n += len(ln)
                    if n > TABLE_CHUNK_MAX:
                        page_chunks.append({
                            "section": f"{sec_label}·表格",
                            "text": f"[表格] {sec_label}\n" + "\n".join(buf)})
                        buf = [f"表头: {header_line}"] if header_line else []
                        n = 0
                if len(buf) > (1 if header_line else 0):
                    page_chunks.append({
                        "section": f"{sec_label}·表格",
                        "text": f"[表格] {sec_label}\n" + "\n".join(buf)})
        txt = "\n".join(el.get_text("\n", strip=True) for el in els if isinstance(el, Tag))
        if txt.strip():
            page_chunks.append({"section": sec_label, "text": txt})

    lead_text = clean_text("\n".join(
        el.get_text("\n", strip=True) for el in lead_parts if isinstance(el, Tag)))[:600]
    url = WIKI_BASE + quote(title)
    page_type = classify(title, cats, wt_map.get(title, ""))
    entity = {
        "title": title, "type": page_type, "type_name": TYPE_NAME[page_type],
        "cats": cats, "url": url, "summary": lead_text,
        "sections": sections, "image": _pick_image(root, lead_parts),
    }

    out: list[dict] = []
    for pc in page_chunks:
        text_body = clean_text(pc["text"])
        if len(text_body) < MIN_LEN:
            continue
        base = {
            "page": title, "pageid": path.stem, "type": page_type,
            "type_name": TYPE_NAME[page_type], "section": pc["section"], "url": url,
        }
        _emit(base, text_body, out)
    return out, entity


# ---------------- wikitext 通道 ----------------
_REDIRECT_RE = re.compile(r"^#(?:REDIRECT|重定向|轉址)\s*:?\s*\[\[([^\]|#]+)", re.I)


def build_aliases() -> dict[str, str]:
    aliases: dict[str, str] = {}
    wt = RAW / "wikitext.jsonl"
    if not wt.exists():
        return aliases
    for line in open(wt, encoding="utf-8"):
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        content = (rec.get("content") or "").strip()
        m = _REDIRECT_RE.match(content)
        if m:
            aliases[rec["title"]] = m.group(1).strip().lstrip(":")
    return aliases


def load_wt_map() -> dict[str, str]:
    wt_map: dict[str, str] = {}
    wt = RAW / "wikitext.jsonl"
    if not wt.exists():
        return wt_map
    for line in open(wt, encoding="utf-8"):
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        c = rec.get("content") or ""
        if c.strip():
            wt_map[rec["title"]] = c[:500]
    return wt_map


# ---------------- Data JSON 通道 ----------------
_SKIP_KEY_RE = re.compile(
    r"icon|img|image|path|file|url|sprite|pic|avatar|svg|png|webp|jpg"
    r"|bool|show_?name|l2d|flag|switch|isShow|isTimeShow", re.I)
_SKIP_VAL_RE = re.compile(r"\.(png|webp|jpg|jpeg|gif|svg|mp3|ogg)$", re.I)
_BOOL_WORDS = {"true", "false", "null", "none"}
_NUMERIC_ONLY = re.compile(r"^[\d\s#.,%:|()\[\]\-]*$")


def _flatten(obj, prefix: str, out: list[str], budget: list[int]) -> None:
    if budget[0] <= 0:
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            if _SKIP_KEY_RE.search(str(k)):
                continue
            _flatten(v, f"{prefix}/{k}" if prefix else str(k), out, budget)
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:400]):
            _flatten(v, f"{prefix}[{i}]" if prefix else f"[{i}]", out, budget)
    else:
        v = str(obj).strip()
        v = re.sub(r"<[^>]+>", "", v)
        v = re.sub(r"\{\{[^{}|]*\|([^{}]*)\}\}", r"\1", v)
        v = re.sub(r"[{}]", "", v)
        v = re.sub(r"\s+", " ", v).strip()
        if not v or v.lower() in _BOOL_WORDS:
            return
        if len(v) < 2 or _NUMERIC_ONLY.match(v):
            return
        if _SKIP_VAL_RE.search(v):
            return
        out.append(f"{prefix}: {v}" if prefix else v)
        budget[0] -= 1


def chunk_data_file(path: Path) -> list[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    name = path.stem
    if name.endswith(".json"):
        name = name[:-5]
    lines: list[str] = []
    # 剧情文本页保留大预算，其余数据页 80 行
    budget = [2500 if name.startswith(("Story", "Episode")) else 80]
    _flatten(data, "", lines, budget)
    if not lines:
        return []
    out: list[dict] = []
    buf, size = [], 0
    for ln in lines:
        buf.append(ln)
        size += len(ln)
        if size > CHUNK_MAX:
            out.append({"page": f"Data:{name}", "pageid": "data", "type": "data",
                        "type_name": "数据页", "section": name, "url": "",
                        "text": "\n".join(buf)})
            buf, size = [], 0
    if buf:
        out.append({"page": f"Data:{name}", "pageid": "data", "type": "data",
                    "type_name": "数据页", "section": name, "url": "",
                    "text": "\n".join(buf)})
    return out


# ---------------- 全量入口 ----------------
def parse_all(progress=print) -> dict:
    wt_map = load_wt_map()
    files = sorted((RAW / "render").glob("*.json"))
    progress(f"rendered pages: {len(files)}")
    chunks: list[dict] = []
    entities: dict[str, dict] = {}
    for idx, path in enumerate(files):
        page_chunks, entity = chunk_page(path, wt_map)
        if not entity or entity["title"] in entities:
            continue
        entities[entity["title"]] = entity
        chunks.extend(page_chunks)
        if idx % 500 == 0:
            progress(f"  parsed {idx}/{len(files)} pages, chunks={len(chunks)}")
    for i, c in enumerate(chunks):
        c["id"] = f"c{i:06d}"

    aliases = build_aliases()
    progress(f"aliases: {len(aliases)}")

    data_files = sorted((RAW / "data_ns").glob("*.json"))
    progress(f"data ns pages: {len(data_files)}")
    data_chunks: list[dict] = []
    for i, path in enumerate(data_files):
        data_chunks.extend(chunk_data_file(path))
        if i % 20000 == 0 and i:
            progress(f"  data {i}/{len(data_files)}, chunks={len(data_chunks)}")
    for i, c in enumerate(data_chunks):
        c["id"] = f"d{i:06d}"

    KB.mkdir(parents=True, exist_ok=True)
    with open(KB / "chunks.jsonl", "w", encoding="utf-8") as f:
        for c in chunks + data_chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    (KB / "entities.json").write_text(
        json.dumps(entities, ensure_ascii=False, indent=1), encoding="utf-8")
    (KB / "aliases.json").write_text(
        json.dumps(aliases, ensure_ascii=False, indent=1), encoding="utf-8")
    progress(f"TOTAL chunks: {len(chunks) + len(data_chunks)} "
             f"(article {len(chunks)} + data {len(data_chunks)})")
    progress(f"entities: {len(entities)}, aliases: {len(aliases)}")
    return {"article_chunks": len(chunks), "data_chunks": len(data_chunks),
            "entities": len(entities), "aliases": len(aliases)}


if __name__ == "__main__":
    parse_all()
