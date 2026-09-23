# RUNBOOK · 运维手册

## 启动 / 停止

```bash
python run.py                    # 前台启动（Ctrl+C 停止）
ARCHIVE_HOST=0.0.0.0 python run.py   # 局域网模式
ARCHIVE_PORT=9000 python run.py      # 改端口
```

首启自动：建表（SQLite WAL）→ 创建管理员（随机密码见控制台与 `artifacts/logs/bootstrap.txt`）→
加载知识库 → 启动月度更新调度线程。

## 管理员职责

| 操作 | 入口 |
|------|------|
| 用户管理（角色/禁用/重置密码/删除） | 后台 `/admin` → 用户管理 |
| 手动增量/全量更新 | 后台 → 知识库状态 |
| LLM 配置（provider/base_url/key/model） | 后台 → 生成端配置 |
| 审计最近提问 | 后台 → 最近提问记录 |

## 知识库更新

- **自动**：内置调度器每月 1 日 03:00 执行增量更新（`update_jobs` 台账可查，
  日志 `artifacts/logs/update-<job_id>.log`）。当月已有成功记录则跳过；服务重启不会重复执行。
- **手动**：`python -m server.pipeline update` 或后台按钮。
- **全量**：`python -m server.pipeline crawl`（重抓整站，断点续传）→ `parse` → `links` → `index`；
  或后台「全量重抓」按钮（一条任务链自动跑完）。
- **热重载**：索引文件变更后，下一个问答请求自动重建检索器并清空答案缓存，无需重启服务。

## 常见问题

| 症状 | 处理 |
|------|------|
| 启动提示「知识库未构建」 | 执行 `python -m server.pipeline build` |
| 回答变成纯证据摘录 | LLM Key 未配置或调用失败；后台检查生成端配置，或接受证据模式 |
| 爬虫大量 403 | wiki 站 Cloudflare 分钟级封锁；爬虫会自适应降速与退避，静待恢复即可 |
| 忘记管理员密码 | 以另一管理员在后台重置；或删除 `data/app.db` 重启重新引导（数据丢失） |
| 索引体积 | `data/kb/index/` ≈ 200MB（向量 float16 + BM25）；`data/raw/` ≈ 1GB 抓取存档 |

## 回归验证

```bash
python -m server.tests.regression
# 覆盖：注册/登录/令牌旋转/权限隔离/会话隔离/KB 状态/三类问答/统计接口
# 报告输出：artifacts/test-output/regression-*.json
```

## 安全基线

- 密码 PBKDF2-HMAC-SHA256（26 万轮，逐用户盐）；JWT access 12h + refresh 30d（旋转吊销）。
- 普通用户数据（会话/问答日志）仓储层强制按 `user_id` 隔离；管理端全部 `require_admin`。
- LLM Key 仅存在于服务端 `config.json`，任何接口不回显明文。
- `/qa/*` 每用户滑动窗口限流（默认 30 次/5 分钟，`config.json` 可调）。

## 部署与公网访问（本机当服务器）

### 方案 A：公网网址（异地访问 · Cloudflare 快速隧道，推荐）

```bash
python tools/tunnel/serve_public.py
```

一条命令完成：自动生成访问口令 → 以口令门禁启动主服务(8765) → 拉起 cloudflared 隧道
→ 打印 `https://<随机名>.trycloudflare.com?k=口令` 完整链接。把该链接发给访问者即可
（首次访问自动种 30 天 Cookie，之后无需再带口令）。

- 口令存于 `tools/tunnel/.tunnel_secret`（勿外泄；泄露后删除该文件重启即换新口令）；
- 当前公网地址存于 `tools/tunnel/.tunnel_url`；隧道日志 `artifacts/logs/tunnel.log`；
- **快速隧道地址每次重启会变**；需固定域名：注册 Cloudflare → 具名隧道
  （`cloudflared tunnel login` → `tunnel create` → 配置路由）；
- 停止：Ctrl+C（自动回收隧道与主服务）；后台残留可在任务管理器结束
  `cloudflared.exe` / `python.exe`。

### 方案 B：局域网直连（同一 WiFi）

`ARCHIVE_HOST=0.0.0.0 python run.py`，防火墙放行（管理员命令行）：

```
netsh advfirewall firewall add rule name="Archive1999" dir=in action=allow protocol=TCP localport=8765
```

访问 `http://<本机IPv4>:8765`。局域网模式建议不设 ARCHIVE_TOKEN（内网可信），
但账户体系仍要求登录。

### 方案 C：开机自启（长期常驻）

双击 `tools/tunnel/autostart-install.bat`（自提权计划任务：开机以最高权限静默运行
serve_public.py），卸载运行 `tools/tunnel/autostart-uninstall.bat`。

### 公网安全基线（暴露前必读）

- 双层防护：外层访问口令门禁（「需要访问口令」页）+ 内层 JWT 账户体系；
- 访问口令存于 `tools/tunnel/.tunnel_secret`，任何接口不回显；
- 注意 LLM API 额度消耗由使用者分摊；回收方式 = 结束 serve_public 进程。
