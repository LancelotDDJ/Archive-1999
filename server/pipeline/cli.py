# -*- coding: utf-8 -*-
"""数据管线 CLI：

  python -m server.pipeline crawl      # 全量抓取（断点续传）
  python -m server.pipeline parse      # 解析 → chunks/entities/aliases
  python -m server.pipeline links      # 内链反向映射
  python -m server.pipeline index      # 向量 + BM25 索引
  python -m server.pipeline build      # parse + links + index（首建一条龙）
  python -m server.pipeline update     # 增量更新（recentchanges）
"""
from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    cmd = argv[0] if argv else ""
    if cmd == "crawl":
        from .crawler import crawl_all
        crawl_all()
    elif cmd == "parse":
        from .parser import parse_all
        parse_all()
    elif cmd == "links":
        from .links import build_all
        build_all()
    elif cmd == "index":
        from .indexer import build_all as index_all
        index_all()
    elif cmd == "build":
        from .links import build_all as links_all
        from .indexer import build_all as index_all
        from .parser import parse_all
        parse_all()
        links_all()
        index_all()
    elif cmd == "update":
        from .updater import run_incremental
        force = argv[1].split(",") if len(argv) > 1 else None
        run_incremental(force=force)
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
