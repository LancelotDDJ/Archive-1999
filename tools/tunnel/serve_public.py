# -*- coding: utf-8 -*-
"""公网发布编排器：一条命令把本机知识库发布为公网可访问的网址。

链路：访客 → Cloudflare 边缘（HTTPS 随机域名）→ cloudflared 隧道 → 本机 8765
保护：访问口令门禁（ARCHIVE_TOKEN，首访 ?k=口令 → 30 天 Cookie 记忆）

用法：
    python tools/tunnel/serve_public.py            # 前台运行（Ctrl+C 一键回收）
    python tools/tunnel/serve_public.py --stop     # 停止后台常驻实例

产物（tools/tunnel/ 下）：
    .tunnel_secret  访问口令（首次自动生成，此后固定，链接不变）
    .tunnel_url     当前公网地址（每次启动可能变化）
"""
from __future__ import annotations

import os
import re
import secrets
import subprocess
import sys
import threading
import time
from functools import partial
from pathlib import Path

print = partial(print, flush=True)  # noqa: A001 — 后台重定向时保证日志即时可见

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
SECRET_FILE = HERE / ".tunnel_secret"
URL_FILE = HERE / ".tunnel_url"
LOG_FILE = Path(os.environ.get("TUNNEL_LOG", str(ROOT / "artifacts/logs/tunnel.log")))
CLOUDFLARED = HERE / "bin" / "cloudflared.exe"
LOCAL_PORT = 8765
LOCAL_URL = f"http://127.0.0.1:{LOCAL_PORT}"

PY = sys.executable


LOCK_FILE = HERE / ".serve_lock"


def _pid_alive(pid: int) -> bool:
    try:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"],
                             capture_output=True, text=True,
                             encoding="utf-8", errors="replace").stdout
        return str(pid) in (out or "")
    except Exception:
        return False


def acquire_lock() -> None:
    """单实例锁：防止多个编排器互相杀服务/抢隧道（本次变慢的根源之一）。"""
    if LOCK_FILE.exists():
        try:
            pid = int(LOCK_FILE.read_text(encoding="utf-8").strip())
            if _pid_alive(pid):
                print(f"[×] 已有编排器在运行（PID {pid}）。请先关闭其窗口再启动新实例。")
                sys.exit(1)
        except ValueError:
            pass
    LOCK_FILE.write_text(str(os.getpid()), encoding="utf-8")


def read_or_create_secret() -> str:
    if SECRET_FILE.exists():
        s = SECRET_FILE.read_text(encoding="utf-8").strip()
        if len(s) >= 16:
            return s
    s = secrets.token_urlsafe(16)[:16]
    SECRET_FILE.write_text(s, encoding="utf-8")
    return s


def port_up(port: int) -> bool:
    """TCP 直连探测（禁用系统代理，代理会让 127.0.0.1 探测永远失败）。"""
    import socket
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            return True
    except OSError:
        return False


def pick_python() -> str:
    """挑选能 import fastapi/uvicorn 的解释器来拉起主服务。

    教训：`python` 可能指向未装依赖的基础解释器（脚本自身只用标准库所以能启动，
    但子服务会 ModuleNotFoundError 崩溃 → 表现为"启动超时"）。
    """
    def ok(exe: str) -> bool:
        try:
            r = subprocess.run([exe, "-c", "import fastapi,uvicorn"],
                               capture_output=True, timeout=30)
            return r.returncode == 0
        except Exception:
            return False

    if ok(sys.executable):
        return sys.executable
    candidates = [
        Path(r"C:\Users\Lance\.workbuddy\binaries\python\envs\default\Scripts\python.exe"),
        ROOT / ".venv" / "Scripts" / "python.exe",
        ROOT / "venv" / "Scripts" / "python.exe",
    ]
    for c in candidates:
        if c.exists() and ok(str(c)):
            print(f"[i] 当前 python 缺少依赖，改用虚拟环境解释器：{c}")
            return str(c)
    print("[×] 未找到带 FastAPI 依赖的 Python 解释器。")
    print("    请安装依赖或直接用项目虚拟环境运行，例如：")
    print('    C:\\Users\\Lance\\.workbuddy\\binaries\\python\\envs\\default\\Scripts\\python.exe tools/tunnel/serve_public.py')
    sys.exit(1)


def main() -> None:
    args = sys.argv[1:]
    named = None
    if "--named" in args:
        i = args.index("--named")
        named = args[i + 1] if i + 1 < len(args) else None
        args = args[:i] + args[i + 2:]
    acquire_lock()
    if "--stop" in sys.argv:
        URL_FILE.unlink(missing_ok=True)
        print("已请求停止：请结束 serve_public/cloudflared/python 进程窗口（或用任务管理器）。")
        return

    if not CLOUDFLARED.exists():
        print(f"[×] 未找到 cloudflared：{CLOUDFLARED}")
        sys.exit(1)

    token = read_or_create_secret()
    PY = pick_python()

    # 1) 主服务：为注入访问口令，先结束旧实例再以 ARCHIVE_TOKEN 拉起（保持 127.0.0.1）
    if port_up(LOCAL_PORT):
        print("[…] 检测到旧实例，正在重启以注入访问口令…")
        # 中文 Windows 的 netstat 输出为 GBK——必须容错解码，否则 UnicodeDecodeError
        out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True,
                             encoding="utf-8", errors="replace").stdout or ""
        pids = {ln.split()[-1] for ln in out.splitlines()
                if f":{LOCAL_PORT} " in ln and "LISTENING" in ln}
        for pid in pids:
            subprocess.run(["taskkill", "/PID", pid, "/F"], capture_output=True)
            print(f"    已停止旧进程 PID={pid}")
        for _ in range(10):
            if not port_up(LOCAL_PORT):
                break
            time.sleep(1)
        svc = None
    else:
        svc = None
    env = {**os.environ, "ARCHIVE_TOKEN": token}
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    svc = subprocess.Popen(
        [PY, str(ROOT / "run.py")],
        cwd=str(ROOT), env=env,
        stdout=open(LOG_FILE, "ab"), stderr=subprocess.STDOUT)
    print(f"[…] 正在启动主服务 {LOCAL_URL}（已启用访问口令 · 日志 {LOG_FILE.name}）")
    for _ in range(30):
        if port_up(LOCAL_PORT):
            break
        time.sleep(1)
    else:
        print("[×] 主服务启动超时 —— 最近日志（tunnel.log）：")
        if LOG_FILE.exists():
            tail = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()[-8:]
            for ln in tail:
                print("   |", ln)
        svc.terminate(); sys.exit(1)
    print("[✓] 主服务已就绪（口令门禁已激活）")

    if named:
        home_cf = Path.home() / ".cloudflared"
        cred, cert = home_cf / f"{named}.json", home_cf / "cert.pem"
        missing = [q.name for q in (cred, cert) if not q.exists()]
        if missing:
            print(f"[×] 具名隧道凭据不全，缺少：{', '.join(missing)}")
            print(f"    请先在 tools/tunnel 目录执行：")
            print(f"      {CLOUDFLARED} tunnel login")
            print(f"      {CLOUDFLARED} tunnel create {named}")
            print(f"    并将绑定域名写入 tools/tunnel/.named_hostname")
            LOCK_FILE.unlink(missing_ok=True); sys.exit(1)
        named_host = (NAMED_HOST_FILE.read_text(encoding="utf-8").strip()
                      if NAMED_HOST_FILE.exists() else "")
        if not named_host:
            print("[×] 缺少 tools/tunnel/.named_hostname（未写入固定域名）")
            LOCK_FILE.unlink(missing_ok=True); sys.exit(1)

    # 2) Cloudflare 隧道（快速模式 / 具名固定地址模式）+ 看门狗自愈
    NAMED_HOST_FILE = HERE / ".named_hostname"
    state = {'url': '', 'proc': None}

    def start_tunnel():
        URL_FILE.unlink(missing_ok=True)
        got = threading.Event()
        if named:
            host = (NAMED_HOST_FILE.read_text(encoding="utf-8").strip()
                    if NAMED_HOST_FILE.exists() else "")
            if host:
                fixed = f"https://{host}"
                URL_FILE.write_text(fixed, encoding="utf-8")
                state['url'] = fixed
                got.set()
            cmd = [str(CLOUDFLARED), "tunnel", "run", named]
        else:
            cmd = [str(CLOUDFLARED), "tunnel", "--url", LOCAL_URL, "--no-autoupdate"]
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            encoding="utf-8", errors="replace")
        url_re = re.compile(r"https://(?!api\.)[a-z0-9-]+\.trycloudflare\.com")

        def pump():
            for line in proc.stdout or []:
                if not named:
                    m = url_re.search(line)
                    if m and not got.is_set():
                        URL_FILE.write_text(m.group(0), encoding="utf-8")
                        state['url'] = m.group(0)
                        got.set()
                LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
                with open(LOG_FILE, "a", encoding="utf-8") as f:
                    f.write(line)

        threading.Thread(target=pump, daemon=True).start()
        for _ in range(30):
            if got.is_set():
                break
            time.sleep(1)
        state['proc'] = proc
        return got.is_set()

    def watchdog(token: str):
        """每 120s 经公网域名探测健康接口；连续 2 次失败 → 重建隧道（地址会变）。"""
        import http.client
        fails = 0
        while True:
            time.sleep(120)
            if not port_up(LOCAL_PORT):
                print('[watchdog] 本机主服务失联，正在重启…', flush=True)
                env = {**os.environ, "ARCHIVE_TOKEN": token}
                try:
                    subprocess.Popen([PY, str(ROOT / "run.py")], cwd=str(ROOT), env=env,
                                     stdout=open(LOG_FILE, "ab"), stderr=subprocess.STDOUT)
                    for _ in range(30):
                        if port_up(LOCAL_PORT):
                            print('[watchdog] 主服务已恢复', flush=True)
                            break
                        time.sleep(1)
                except Exception as e:
                    print(f'[watchdog] 服务重启失败: {e}', flush=True)
                continue
            url = state.get('url')
            if not url:
                continue
            host = url.split('//', 1)[1]
            try:
                conn = http.client.HTTPSConnection(host, timeout=20)
                conn.request('GET', f'/api/v1/system/health?k={token}')
                r = conn.getresponse()
                r.read()
                ok = r.status == 200
                conn.close()
            except Exception:
                ok = False
            if ok:
                fails = 0
                continue
            fails += 1
            print(f'[watchdog] 隧道探测失败 {fails}/2', flush=True)
            if fails >= 2:
                print('[watchdog] 隧道失联，正在重建…（公网地址将变化，见 .tunnel_url）', flush=True)
                try:
                    state['proc'].terminate()
                except Exception:
                    pass
                time.sleep(3)
                if start_tunnel():
                    fails = 0
                    print(f"[watchdog] 隧道已重建：{state['url']}?k={token}", flush=True)
                else:
                    print('[watchdog] 重建失败，将继续重试', flush=True)

    if start_tunnel():
        url = state['url']
        print("\n" + "=" * 62)
        print(f"[✓] 公网地址：  {url}?k={token}")
        print("[✓] 完整链接（发给朋友即用，首次访问自动记住授权 30 天）：")
        print(f"      {url}?k={token}")
        print(f"[i] 口令本体存于 {SECRET_FILE.name}；地址存于 {URL_FILE.name}")
        print("[i] 看门狗每 2 分钟探测公网链路，连续失败自动重建隧道")
        print("[i] 快速隧道地址每次重启会变化；需固定域名请注册 Cloudflare 具名隧道")
        print("=" * 62 + "\n按 Ctrl+C 停止并回收全部进程\n")
    else:
        print("[×] 未能获取公网地址（网络受限？），隧道日志见 tunnel.log")

    threading.Thread(target=watchdog, args=(token,), daemon=True).start()

    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("\n[←] 正在回收…")
    finally:
        try:
            state['proc'].terminate()
        except Exception:
            pass
        if svc:
            svc.terminate()
        URL_FILE.unlink(missing_ok=True)
        LOCK_FILE.unlink(missing_ok=True)
        print("[✓] 已停止隧道与主服务")


if __name__ == "__main__":
    main()
