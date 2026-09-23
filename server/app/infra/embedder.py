# -*- coding: utf-8 -*-
"""中文向量嵌入器：BAAI/bge-small-zh-v1.5（ONNX，本地权重，离线加载）。

- 懒加载单例；线程安全；
- 查询侧加 bge 检索指令前缀（官方推荐用法）。
"""
from __future__ import annotations

import os
import threading

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

import numpy as np

from ..core.config import MODELS

MODEL_DIR = MODELS / "bge-small-zh-v1.5"
QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："


class Embedder:
    _inst: "Embedder | None" = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        from fastembed import TextEmbedding
        self._model = TextEmbedding(
            "BAAI/bge-small-zh-v1.5", cache_dir=str(MODELS),
            local_files_only=True)

    @classmethod
    def get(cls) -> "Embedder":
        with cls._lock:
            if cls._inst is None:
                cls._inst = cls()
            return cls._inst

    def embed_texts(self, texts: list[str], batch_size: int = 64) -> np.ndarray:
        vecs = list(self._model.embed(texts, batch_size=batch_size))
        arr = np.asarray(vecs, dtype=np.float32)
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return arr / norms

    def embed_query(self, q: str) -> np.ndarray:
        vec = np.asarray(
            list(self._model.embed([QUERY_INSTRUCTION + q]))[0],
            dtype=np.float32)
        n = np.linalg.norm(vec)
        return vec / (n or 1.0)


def available() -> bool:
    """模型权重是否在位。"""
    return (MODEL_DIR / "model_optimized.onnx").exists()
