@echo off
chcp 65001 >nul
title Studio Suite 网页面板
cd /d "%~dp0"

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
  where python >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo 没有找到 Python。请安装 Python 3，或在本目录 config.local.json 里填写 pythonPath。
  pause
  exit /b 1
)

%PY% -B "%~dp0studio_suite_launcher.py"
set "RC=%ERRORLEVEL%"
echo.
echo 启动器退出（代码 %RC%）。按任意键关闭。
pause >nul
exit /b %RC%
