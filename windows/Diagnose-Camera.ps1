$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
$python = Join-Path $root '.venv-vision-windows\Scripts\python.exe'
if (-not (Test-Path $python)) { throw 'Run windows\Install-Native.cmd first.' }
Write-Host 'Close the native service launcher and RealSense Viewer before this camera-only diagnostic.'
& $python -X utf8 -u (Join-Path $PSScriptRoot 'diagnose_camera.py')
exit $LASTEXITCODE
