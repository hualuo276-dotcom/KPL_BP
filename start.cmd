@echo off
setlocal
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8

if not exist "data\kpl.db" (
  echo [1/2] 首次运行，抓取 KPL 数据 ...
  python -m kplbp.ingest
  if errorlevel 1 goto :fail
)

echo [2/2] 启动服务 ...
echo.
python -m kplbp.server %*
if errorlevel 3 (
  echo.
  echo 端口被占用，试试： start.cmd --port 8781
)
goto :eof

:fail
echo 数据抓取失败，请检查网络后重试。
pause
