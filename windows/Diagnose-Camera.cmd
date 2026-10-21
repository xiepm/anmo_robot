@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Diagnose-Camera.ps1" %*
set "REHAB_EXIT=%ERRORLEVEL%"
if not "%REHAB_EXIT%"=="0" pause
exit /b %REHAB_EXIT%
