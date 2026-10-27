@echo off
setlocal
cd /d "%~dp0"
pythonw "%~dp0app.py"
if errorlevel 1 python "%~dp0app.py"
