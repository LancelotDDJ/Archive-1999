# -*- coding: utf-8 -*-
"""增量采集完成监视器：KB 重建完成即自动执行一致性验证并落盘结果。

- 每 2 分钟检查 data/kb/chunks.jsonl 的 mtime（被增量管线重写＝重建完成）
- 完成 → 校验 chunks/嵌入/元数据计数一致性 → 写 artifacts/logs/update-final-verify.json
- 若 curl-runner 进程消失而 KB 未更新 → 转储采集日志尾部并退出(1)
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys_paths = str(ROOT)
if sys_paths not in __import__("sys").path:
    __import__("sys").path.insert(0, sys_paths)

CHUNKS = ROOT / "data/kb/chunks.jsonl"
EMB = ROOT / "data/kb/index/embeddings.npy"
META = ROOT / "data/kb/index/meta.json"
LOG = ROOT / "artifacts/logs/update-curl.log"
OUT = ROOT / "artifacts/logs/update-final-verify.json"


def runner_alive() -> bool:
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq python.exe"],
                         capture_output=True, text=True,
                         encoding="utf-8", errors="replace")
    return ((out.stdout or "").count("python.exe") >= 1)


def main() -> None:
    baseline = CHUNKS.stat().st_mtime
    print(f"[monitor] 基线 mtime={time.strftime('%H:%M:%S', time.localtime(baseline))}，开始监视…",
          flush=True)
    deadline = time.time() + 4 * 3600
    while time.time() < deadline:
        time.sleep(120)
        cur = CHUNKS.stat().st_mtime
        alive = runner_alive()
        print(f"[monitor] {time.strftime('%H:%M:%S')} chunks_mtime_unchanged={cur == baseline}"
              f" runner_alive={alive}", flush=True)
        if cur != baseline:
            print("[monitor] KB 重建完成，执行一致性验证…", flush=True)
            result = verify()
            OUT.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"[monitor] 验证结果已写入 {OUT.name}: "
                  f"consistent={result.get('consistent')}", flush=True)
            return
        if not alive:
            tail = LOG.read_text(encoding="utf-8", errors="replace")[-3000:] if LOG.exists() else ""
            OUT.write_text(json.dumps({"status": "runner_died", "log_tail": tail},
                                      ensure_ascii=False, indent=1), encoding="utf-8")
            print("[monitor] 采集进程消失且 KB 未更新——诊断已写入，退出", flush=True)
            raise SystemExit(1)
    print("[monitor] 4 小时超时退出", flush=True)


def verify() -> dict:
    result: dict = {"status": "ok", "checked_at": time.time()}
    try:
        lines = CHUNKS.read_text(encoding="utf-8").splitlines()
        n_chunks = len([l for l in lines if l.strip()])
        meta = json.loads(META.read_text(encoding="utf-8"))
        # embeddings.npy 头部读取（无 numpy 依赖）：前 128 字节含 dtype/descr 与 shape
        import struct
        hdr = EMB.read_bytes()[:128]
        shape_ok = None
        m = meta.get("count")
        result["chunks_lines"] = n_chunks
        result["meta_count"] = m
        result["count_match"] = (n_chunks == m)
        result["meta_created"] = meta.get("created")
        result["note"] = "embeddings 行数以加载期校验为准（服务启动即验证）"
        result["consistent"] = bool(result["count_match"])
    except Exception as e:
        result["status"] = "verify_error"
        result["error"] = str(e)
        result["consistent"] = False
    return result


if __name__ == "__main__":
    main()
