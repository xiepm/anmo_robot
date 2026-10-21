[CmdletBinding()]
param([switch]$Check)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
$python = Join-Path $root '.venv-control-windows\Scripts\python.exe'
if (-not (Test-Path $python)) { throw 'Run windows\Install-Native.cmd first.' }
$arguments = @('-X','utf8','-u',(Join-Path $PSScriptRoot 'native_launcher.py'))
if ($Check) { $arguments += '--check' }
& $python @arguments
exit $LASTEXITCODE
