@echo off
setlocal
rem ASCII-only wrapper; the PowerShell script uses UTF-8 with BOM for PS 5.1.
set "REHAB_CONNECT_DIR=%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command "& (Join-Path $env:REHAB_CONNECT_DIR ((-join [char[]](0x8FDE,0x63A5,0x73B0,0x6709,0x8BBE,0x5907)) + '.ps1'))"
set "REHAB_CONNECT_EXIT=%ERRORLEVEL%"
if not "%REHAB_CONNECT_EXIT%"=="0" pause
exit /b %REHAB_CONNECT_EXIT%
