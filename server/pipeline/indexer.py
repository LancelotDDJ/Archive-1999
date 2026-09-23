# -*- coding: utf-8 -*-
"""索引构建器：chunks.jsonl → 向量(embeddings.npy) + BM25(bm25.pkl) + meta.json。

- 向量：BAAI/bge-small-zh-v1.5（fastembed ONNX，本地权重，离线）；
- 嵌入文本：含页面/章节上下文的包装格式（与查询侧指令前缀配套）；
- 存储 float16（加载时归一化转 float32）。
"""
from __future__ import annotations

import json
import pickle
import time
from pathlib import Path

import numpy as np

from server.app.core.config import KB, KB_INDEX

MODEL = "BAAI/bge-small-zh-v1.5"
EMBED_PREFIX = "《重返未来1999》"


def embed_text(c: dict) -> str:
    return (f"{EMBED_PREFIX}[{c.get('type_name', '')}] {c.get('page', '')}"
            f" · {c.get('section', '')}\n{c['text']}")


def load_chunks() -> list[dict]:
    chunks: list[dict] = []
    with open(KB / "chunks.jsonl", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                chunks.append(json.loads(line))
    return chunks


def build_all(progress=print) -> dict:
    t0 = time.time()
    chunks = load_chunks()
    progress(f"chunks loaded: {len(chunks)}")

    from fastembed import TextEmbedding
    from server.app.core.config import MODELS
    model = TextEmbedding(MODEL, cache_dir=str(MODELS), local_files_only=True)
    texts = [embed_text(c)[:2000] for c in chunks]
    vecs = np.empty((len(texts), 512), dtype=np.float16)
    t1 = time.time()
    done = 0
    B = 128
    for arr in model.embed(texts, batch_size=B):
        vecs[done] = arr.astype(np.float16)
        done += 1
        if done % 4096 < B:
            rate = done / max(time.time() - t1, 1)
            eta = (len(texts) - done) / max(rate, 1)
            progress(f"embed {done}/{len(texts)} ({rate:.0f}/s, ETA {eta/60:.1f}min)")
    KB_INDEX.mkdir(parents=True, exist_ok=True)
    np.save(KB_INDEX / "embeddings.npy", vecs)
    progress(f"embeddings saved: {vecs.shape}, {time.time()-t1:.0f}s")

    import jieba
    jieba.initialize()
    progress("tokenizing for BM25 ...")
    t2 = time.time()
    corpus = [[t for t in jieba.cut_for_search(embed_text(c)[:1200]) if t.strip()]
              for c in chunks]
    from rank_bm25 import BM25Okapi
    bm25 = BM25Okapi(corpus)
    with open(KB_INDEX / "bm25.pkl", "wb") as f:
        pickle.dump(bm25, f, protocol=4)
    progress(f"bm25 built: {time.time()-t2:.0f}s")

    meta = {"model": MODEL, "dim": int(vecs.shape[1]), "count": len(chunks),
            "created": time.strftime("%Y-%m-%d %H:%M:%S"),
            "embed_seconds": round(time.time() - t1, 1)}
    (KB_INDEX / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    progress(f"index done: {meta}, total {time.time()-t0:.0f}s")
    return meta
