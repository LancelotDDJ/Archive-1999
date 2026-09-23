# -*- coding: utf-8 -*-
"""知识库存储：chunks / entities / aliases / links / 向量 / BM25 的装载与热重载。

- 单例 + mtime 监视：索引文件变更后自动重建（月度更新落盘即生效，无需重启）；
- 只暴露只读视图，检索引擎通过本模块访问全部 KB 资产。
"""
from __future__ import annotations

import json
import pickle
import threading
from pathlib import Path
from typing import Any

import numpy as np

from ..config import KB, KB_INDEX

_WATCH = ("chunks.jsonl", "entities.json", "aliases.json", "links_map.json")


class KBNotReadyError(RuntimeError):
    pass


class KBStore:
    _inst: "KBStore | None" = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self.chunks: list[dict] = []
        self.entities: dict[str, dict] = {}
        self.aliases: dict[str, str] = {}
        self.links_map: dict[str, list] = {}
        self.emb: np.ndarray | None = None
        self.bm25: Any = None
        self.meta: dict = {}
        self.page_index: dict[str, list[int]] = {}
        self._mtime: float = 0.0

    @classmethod
    def get(cls) -> "KBStore":
        with cls._lock:
            if cls._inst is None:
                cls._inst = cls()
                cls._inst._load()
            return cls._inst

    @classmethod
    def maybe_reload(cls) -> "KBStore":
        """每次请求前调用：磁盘变更则重建（先于答案缓存查询）。"""
        inst = cls.get()
        with cls._lock:
            mt = inst._current_mtime()
            if mt != inst._mtime:
                print("[kb] index changed on disk -> hot reload", flush=True)
                inst._load()
        return inst

    def _current_mtime(self) -> float:
        mt = 0.0
        for name in _WATCH + ("index/embeddings.npy", "index/bm25.pkl", "index/meta.json"):
            p = KB / name
            try:
                mt = max(mt, p.stat().st_mtime)
            except OSError:
                pass
        return mt

    def _load(self) -> None:
        chunks_path = KB / "chunks.jsonl"
        if not chunks_path.exists():
            raise KBNotReadyError(
                "知识库尚未构建：请先运行 python -m server.pipeline parse && index")
        self.chunks = []
        with open(chunks_path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    self.chunks.append(json.loads(line))
        self.entities = self._read_json(KB / "entities.json", {})
        self.aliases = self._read_json(KB / "aliases.json", {})
        self.links_map = self._read_json(KB / "links_map.json", {})
        emb_path = KB_INDEX / "embeddings.npy"
        if emb_path.exists():
            emb = np.load(emb_path).astype(np.float32)
            norms = np.linalg.norm(emb, axis=1, keepdims=True)
            norms[norms == 0] = 1.0
            self.emb = emb / norms
        else:
            self.emb = None
        bm_path = KB_INDEX / "bm25.pkl"
        self.bm25 = pickle.loads(bm_path.read_bytes()) if bm_path.exists() else None
        self.meta = self._read_json(KB_INDEX / "meta.json", {})
        self.page_index = {}
        for i, c in enumerate(self.chunks):
            self.page_index.setdefault(c["page"], []).append(i)
        self._mtime = self._current_mtime()

    @staticmethod
    def _read_json(path: Path, default: Any) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return default

    # ---- 只读视图 ----
    def ready(self) -> bool:
        return bool(self.chunks) and self.emb is not None

    def status(self) -> dict:
        from collections import Counter
        types = Counter(c.get("type_name", "") for c in self.chunks)
        return {
            "ready": self.ready(),
            "chunks": len(self.chunks),
            "entities": len(self.entities),
            "aliases": len(self.aliases),
            "type_dist": dict(types.most_common()),
            "index_meta": self.meta,
        }


def store() -> KBStore:
    return KBStore.get()
