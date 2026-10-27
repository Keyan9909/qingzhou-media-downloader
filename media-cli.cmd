@echo off
setlocal
cd /d "%~dp0"
python "%~dp0media_cli.py" %*
exit /b %errorlevel%
