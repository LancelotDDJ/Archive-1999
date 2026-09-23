@echo off
rem Archive1999 tunnel autostart installer (requires admin, UAC prompt will appear)
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting administrator privileges...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)
set PY=python
if not exist "%PY%" set PY=python
schtasks /Create /F /SC ONSTART /RL HIGHEST /TN "Archive1999-Tunnel" /TR "cmd /c cd /d %~dp0 && %PY% serve_public.py"
if %errorlevel% equ 0 (echo [OK] Autostart task created: Archive1999-Tunnel) else (echo [FAIL] create failed)
pause
