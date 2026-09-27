@echo off
rem 《重返未来：1999》知识库 - 一键启动
cd /d %~dp0
set PY=C:\Users\Lance\.workbuddy\binaries\python\envs\default\Scripts\python.exe
if not exist "%PY%" set PY=python
"%PY%" run.py
pause
