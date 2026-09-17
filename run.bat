@echo off
setlocal
set "HERE=%~dp0"
if not exist "%HERE%env\python.exe" ( echo Run setup.bat first. & pause & exit /b 1 )
set "PYTHONPATH=%HERE%src"
"%HERE%env\python.exe" -m step2aas %*
