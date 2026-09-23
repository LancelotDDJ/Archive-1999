# -*- coding: utf-8 -*-
"""一键启动入口：python run.py

环境自检 → 启动 Uvicorn 服务。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def check() -> list[str]:
    problems: list[str] = []
    from server.app.core.config import KB, KB_INDEX, MODELS
    if not (KB / "chunks.jsonl").exists():
        problems.append("知识库未构建：python -m server.pipeline build")
    if not (KB_INDEX / "embeddings.npy").exists():
        problems.append("向量索引未构建：python -m server.pipeline index")
    if not (MODELS / "models--Qdrant--bge-small-zh-v1.5").exists():
        problems.append("向量模型权重缺失：data/models/")
    return problems


def main() -> None:
    problems = check()
    for p in problems:
        print(f"[自检] {p}", flush=True)
    from server.app.main import main as serve
    serve()


if __name__ == "__main__":
    main()
