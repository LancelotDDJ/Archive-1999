@echo off
rem Echo 双端通信测试工具 - 一键启动两端
cd /d %~dp0
set PY=python
if not exist "%PY%" set PY=python
start "Echo-Server(8701)" cmd /k "title Echo 服务端 :8701 && %PY% echo_server.py"
timeout /t 1 >nul
start "Echo-Client(8702)" cmd /k "title Echo 客户端 :8702 && %PY% echo_client.py"
echo 已启动：
echo   服务端界面  http://127.0.0.1:8701
echo   客户端界面  http://127.0.0.1:8702
pause
