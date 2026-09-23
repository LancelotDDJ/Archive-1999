@echo off
rem Archive1999 tunnel autostart uninstaller (requires admin, UAC prompt will appear)
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting administrator privileges...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)
schtasks /Delete /F /TN "Archive1999-Tunnel"
echo [OK] Autostart task removed
pause
