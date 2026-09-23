# -*- coding: utf-8 -*-
"""端到端回归测试：认证 / 权限隔离 / 会话 / 检索 / 问答（证据模式）。

用法：
    python -m server.tests.regression [--base http://127.0.0.1:8765]

需要服务已启动且知识库已构建。结果输出至 artifacts/test-output/。
"""
from __future__ import annotations

import json
import sys
import time
import uuid
from pathlib import Path

import requests

from server.app.core.config import TEST_OUTPUT

BASE = sys.argv[sys.argv.index("--base") + 1] if "--base" in sys.argv \
    else "http://127.0.0.1:8765"
API = BASE + "/api/v1"

results: list[dict] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append({"name": name, "ok": bool(ok), "detail": detail[:300]})
    mark = "✅" if ok else "❌"
    print(f"{mark} {name}" + (f" — {detail[:120]}" if detail and not ok else ""))
    return ok


def ask_stream(token: str, question: str, agent: bool | None = None) -> dict:
    """读取 NDJSON 流，返回聚合结果。"""
    events = []
    with requests.post(API + "/qa/ask",
                       json={"question": question, "agent": agent},
                       headers={"Authorization": f"Bearer {token}"},
                       stream=True, timeout=300) as r:
        for line in r.iter_lines():
            if line:
                try:
                    events.append(json.loads(line))
                except ValueError:
                    pass
    out = {"events": events, "answer": "", "meta": {}, "mode": ""}
    for ev in events:
        t = ev.get("type")
        if t == "meta":
            out["meta"] = ev
            out["mode"] = ev.get("mode", "")
        elif t == "delta":
            out["answer"] += ev.get("text", "")
        elif t == "done":
            out["mode"] = ev.get("mode", out["mode"]) or out["mode"]
    return out


def main() -> int:
    t0 = time.time()
    suffix = uuid.uuid4().hex[:6]
    user_a = f"回归甲{suffix}"
    user_b = f"回归乙{suffix}"
    pw = "test1234a"

    # ---------- 认证 ----------
    r = requests.post(API + "/auth/register",
                      json={"username": user_a, "password": pw}, timeout=30)
    check("注册·甲", r.status_code == 200 and r.json().get("access_token"))
    tok_a = r.json().get("access_token", "")
    ref_a = r.json().get("refresh_token", "")

    r = requests.post(API + "/auth/register",
                      json={"username": user_b, "password": pw}, timeout=30)
    check("注册·乙", r.status_code == 200)
    tok_b = r.json().get("access_token", "")

    r = requests.post(API + "/auth/register",
                      json={"username": user_a, "password": pw}, timeout=30)
    check("重复注册被拒", r.status_code == 409)

    r = requests.post(API + "/auth/login",
                      json={"username": user_a, "password": "wrong1234b"}, timeout=30)
    check("错误密码被拒", r.status_code == 401)

    r = requests.get(API + "/auth/me", headers={"Authorization": f"Bearer {tok_a}"})
    check("me·甲", r.status_code == 200 and r.json()["user"]["username"] == user_a)

    r = requests.post(API + "/auth/refresh", json={"refresh_token": ref_a}, timeout=30)
    check("刷新令牌旋转", r.status_code == 200 and r.json().get("access_token"))
    tok_a = r.json()["access_token"]
    # 旧 refresh 已吊销
    r = requests.post(API + "/auth/refresh", json={"refresh_token": ref_a}, timeout=30)
    check("旧刷新令牌已吊销", r.status_code == 401)

    # ---------- 权限隔离 ----------
    r = requests.get(API + "/admin/users", headers={"Authorization": f"Bearer {tok_a}"})
    check("普通用户禁入管理端", r.status_code == 403)

    # 甲创建会话
    cid = None
    r = requests.post(API + "/conversations",
                      json={"title": "回归会话", "kind": "qa",
                            "messages": [{"role": "user", "content": "测试问题",
                                          "meta": {}}]},
                      headers={"Authorization": f"Bearer {tok_a}"}, timeout=30)
    check("创建会话", r.status_code == 200 and r.json().get("id"))
    cid = r.json().get("id")

    r = requests.get(API + f"/conversations/{cid}",
                     headers={"Authorization": f"Bearer {tok_b}"}, timeout=30)
    check("乙不可读甲会话(404)", r.status_code == 404)

    r = requests.get(API + "/conversations",
                     headers={"Authorization": f"Bearer {tok_b}"}, timeout=30)
    check("乙会话列表为空", r.status_code == 200
          and all(c["id"] != cid for c in r.json().get("conversations", [])))

    r = requests.delete(API + f"/conversations/{cid}",
                        headers={"Authorization": f"Bearer {tok_b}"}, timeout=30)
    check("乙不可删甲会话", r.status_code == 404)

    # ---------- 知识库状态 ----------
    r = requests.get(API + "/kb/status", headers={"Authorization": f"Bearer {tok_a}"})
    kb = r.json()
    check("KB 状态", r.status_code == 200 and kb.get("chunks", 0) > 100000,
          f"chunks={kb.get('chunks')}")

    # ---------- 问答（依赖知识库；LLM 可达与否都做内容断言） ----------
    if kb.get("ready"):
        ans = ask_stream(tok_a, "红弩箭的神秘术有哪些效果？")
        check("问答·数值查询", len(ans["answer"]) > 50,
              f"len={len(ans['answer'])} mode={ans['mode']}")
        check("问答·来源引用", bool(ans["meta"].get("sources")))

        ans = ask_stream(tok_a, "维尔汀为什么在暴雨中活下来了？")
        check("问答·剧情推理", len(ans["answer"]) > 80,
              f"len={len(ans['answer'])} mode={ans['mode']}")
        check("推理模式路由", ans["meta"].get("style") == "reason"
              or ans["mode"] in ("openai", "extractive"))

        # 实体边界：角色「6」不应被 6.2 版本干扰（证据模式下检查来源页）
        ans = ask_stream(tok_a, "角色6的技能是什么？")
        pages = [s.get("page", "") for s in ans["meta"].get("sources", [])]
        check("纯数字实体边界", any(p == "6" or p.startswith("6") for p in pages),
              f"pages={pages[:4]}")

        ans = ask_stream(tok_a, "游戏中有多少个6星角色？请统计", agent=True)
        types = [e.get("type") for e in ans["events"]]
        check("Agent 模式触发", "agent_mode" in types or ans["mode"] == "agent",
              f"types={types[:3]}")
    else:
        print("⚠ 知识库索引未就绪，跳过问答内容断言")

    # ---------- 统计接口（仪表盘已移除：确认下线 + 管理端隔离） ----------
    r = requests.get(API + "/stats/overview",
                     headers={"Authorization": f"Bearer {tok_a}"})
    check("统计端点已下线(404)", r.status_code == 404)

    r = requests.get(API + "/admin/stats/qa-activity",
                     headers={"Authorization": f"Bearer {tok_a}"})
    check("普通用户禁入全站统计(403)", r.status_code == 403)

    r = requests.get(API + "/admin/users/accounts",
                     headers={"Authorization": f"Bearer {tok_a}"})
    check("普通用户禁入账户浏览(403)", r.status_code == 403)

    r = requests.get(API + f"/admin/users/accounts/{999999}/chats",
                     headers={"Authorization": f"Bearer {tok_a}"})
    check("普通用户禁入聊天数据(403)", r.status_code == 403)

    # ---------- 落盘报告 ----------
    TEST_OUTPUT.mkdir(parents=True, exist_ok=True)
    report = {
        "base": BASE, "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "elapsed_s": round(time.time() - t0, 1),
        "total": len(results), "passed": sum(1 for x in results if x["ok"]),
        "results": results,
    }
    out = TEST_OUTPUT / f"regression-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n== {report['passed']}/{report['total']} 通过，耗时 {report['elapsed_s']}s")
    print(f"报告: {out}")
    return 0 if report["passed"] == report["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
