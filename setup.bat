@echo off
setlocal

cd /d "%~dp0"
echo Installing project-local runtimes and dependencies. No models are downloaded by setup.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup.ps1" %*
exit /b %ERRORLEVEL%
