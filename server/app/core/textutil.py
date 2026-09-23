# -*- coding: utf-8 -*-
"""回答文本净化：思考过程与最终回答的分离工具。

两种典型泄露形态：
1. 开头的英文主导行——模型把工作笔记/英文导语当正文吐出。
   注意：即使句中夹带「香调」「木质」等中文术语，整行仍是英文行
   （历史教训：仅凭"行内无中文"判定会被这类行绕过）；
2. <think>...</think> 内联推理块（部分模型的思考协议）。
面向用户的正文不应包含任何一类。
"""
from __future__ import annotations

import re

_THINK_BLOCK_RE = re.compile(r"<think>[\s\S]*?</think>", re.I)
_THINK_OPEN_RE = re.compile(r"^\s*<think>[\s\S]*$", re.I)
_CJK_RE = re.compile(r"[一-鿿]")
_LATIN_WORD_RE = re.compile(r"[A-Za-z]{2,}")


def strip_think_blocks(text: str) -> str:
    """移除 <think> 推理块（含未闭合的前置块）。"""
    if not text:
        return ""
    t = _THINK_BLOCK_RE.sub("", text)
    t = _THINK_OPEN_RE.sub("", t)
    return t


def _is_english_dominant(s: str) -> bool:
    """英文主导行判定（容忍句中夹带的少量中文术语）。

    规则：拉丁词（≥2 字母）≥3 个，且拉丁字母总数 > 中文字数 × 3。
    实测校准样本：
    - 'The complete list of characters whose scent notes (香调) contain "木质"(wood):'
      → 拉丁词 10、拉丁字母 45 > 4×3 → 英文行 ✓
    - 'APPLe 的技能如下'                    → 拉丁词 1 → 非英文行 ✓
    - '"La unua cirklo"意为"最初的网"'      → 拉丁词 3、字母 12 ≤ 8×3 → 非英文行 ✓
    - '| 角色 | 香调 |'                     → 拉丁词 0 → 非英文行 ✓
    """
    words = _LATIN_WORD_RE.findall(s)
    if len(words) < 3:
        return False
    latin_chars = sum(len(w) for w in words)
    cjk_chars = len(_CJK_RE.findall(s))
    return latin_chars > cjk_chars * 3


def strip_leading_english_lines(text: str, max_drop: int = 3) -> str:
    """剥离开头英文主导行（保留中英混排正文），最多剥离 max_drop 行。"""
    if not text:
        return ""
    lines = text.split("\n")
    kept, dropped = [], 0
    for ln in lines:
        s = ln.strip()
        if dropped < max_drop and s and _is_english_dominant(s):
            dropped += 1
            continue
        kept.append(ln)
    return "\n".join(kept).lstrip()


def sanitize_answer(text: str) -> str:
    """面向用户正文的最终净化：先 think 块，后英文行。"""
    return strip_leading_english_lines(strip_think_blocks(text))
