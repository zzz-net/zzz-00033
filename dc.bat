@echo off
chcp 65001 >nul
setlocal
set "HERE=%~dp0"
set "PYTHONPATH=%HERE%src;%PYTHONPATH%"
set "PYTHONIOENCODING=utf-8"
python -m delivery_checker %*
endlocal
