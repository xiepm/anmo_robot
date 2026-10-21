# Native Windows installer. No camera or robot connection is opened here.
[CmdletBinding()]
param([switch]$SkipModel)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
$env:PYTHONUTF8 = '1'
function Find-Python312 {
    $candidates = @((Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'))
    if (Get-Command py.exe -ErrorAction SilentlyContinue) {
        # py may return stderr when 3.12 is not installed; do not treat this
        # expected lookup failure as a terminating PS 5.1 native error.
        $oldPreference = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        $found = & py.exe -3.12 -c 'import sys; print(sys.executable)' 2>$null
        $ok = $LASTEXITCODE -eq 0
        $ErrorActionPreference = $oldPreference
        if ($ok -and $found) { $candidates += "$found" }
    }
    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) {
            & $candidate (Join-Path $PSScriptRoot 'check_python.py')
            if ($LASTEXITCODE -eq 0) { return $candidate }
        }
    }
    return $null
}
$python = Find-Python312
if (-not $python) {
    if (-not (Get-Command winget.exe -ErrorAction SilentlyContinue)) {
        throw 'Install Python 3.12 x64 with the Python launcher, then run this script again: https://www.python.org/downloads/windows/'
    }
    Write-Host 'Installing Python 3.12 x64 using Windows Package Manager...'
    & winget.exe install --id Python.Python.3.12 --exact --source winget --architecture x64 --accept-source-agreements --accept-package-agreements
    if ($LASTEXITCODE -ne 0) { throw 'Python installation failed. Install Python 3.12 x64 manually and retry.' }
    $python = Find-Python312
    if (-not $python) { throw 'Python was not found yet. Reopen this installer after installation.' }
}
# Wheels for OpenCV/ONNX depend on the official VC++ runtime. Do not download
# DLL files from unofficial sites or put a copied DLL in the program directory.
$vc = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64' -ErrorAction SilentlyContinue
if (-not $vc -or $vc.Installed -ne 1) {
    if (-not (Get-Command winget.exe -ErrorAction SilentlyContinue)) {
        throw 'Install the Microsoft Visual C++ 2015-2022 x64 redistributable first: https://aka.ms/vs/17/release/vc_redist.x64.exe'
    }
    & winget.exe install --id Microsoft.VCRedist.2015+.x64 --exact --source winget --accept-source-agreements --accept-package-agreements
    if ($LASTEXITCODE -ne 0) { throw 'VC++ runtime installation failed; install the Microsoft x64 redistributable and retry.' }
}
foreach ($component in @('control', 'vision')) {
    $environment = Join-Path $root ".venv-$component-windows"
    $interpreter = Join-Path $environment 'Scripts\python.exe'
    if (-not (Test-Path $interpreter)) {
        & $python -m venv $environment
        if ($LASTEXITCODE -ne 0) { throw "Could not create $component environment." }
    }
    & $interpreter (Join-Path $PSScriptRoot 'check_python.py')
    if ($LASTEXITCODE -ne 0) { throw "$environment must use Python 3.12 x64. Move the old environment aside before retrying." }
    & $interpreter -m pip install --only-binary=:all: -r (Join-Path $PSScriptRoot "requirements-$component.txt")
    if ($LASTEXITCODE -ne 0) { throw "$component dependencies could not be installed; see the pip error above." }
}
$controlPython = Join-Path $root '.venv-control-windows\Scripts\python.exe'
$visionPython = Join-Path $root '.venv-vision-windows\Scripts\python.exe'
& $controlPython -c 'import numpy,scipy,websockets,jsonschema'
if ($LASTEXITCODE) { throw 'Control dependency import failed.' }
& $visionPython -c 'import cv2,numpy,onnxruntime,pyrealsense2,mediapipe'
if ($LASTEXITCODE) { throw 'Vision dependency import failed. Check VC++ x64 and Media Feature Pack on Windows N/KN.' }
if (-not (Test-Path (Join-Path $root 'web\dist-native\index.html'))) {
    if (-not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) {
        throw 'Source ZIP needs Node.js 22.12+ to build the UI. Install Node.js LTS and rerun, or use the prebuilt Windows native bundle.'
    }
    Push-Location (Join-Path $root 'web')
    $oldDemo = $env:VITE_DEMO_MODE
    $oldWs = $env:VITE_ROBOT_WS_URL
    $oldVision = $env:VITE_VISION_PREVIEW_URL
    try {
        $env:VITE_DEMO_MODE = 'false'
        $env:VITE_ROBOT_WS_URL = 'ws://127.0.0.1:8765'
        $env:VITE_VISION_PREVIEW_URL = 'http://127.0.0.1:8767/frame.jpg'
        & npm.cmd ci
        if ($LASTEXITCODE) { throw 'UI dependency installation failed.' }
        & node.exe node_modules/vue-tsc/bin/vue-tsc.js -b
        if ($LASTEXITCODE) { throw 'UI type check failed.' }
        & node.exe node_modules/vite/bin/vite.js build --outDir dist-native
        if ($LASTEXITCODE) { throw 'UI build failed.' }
    } finally {
        $env:VITE_DEMO_MODE = $oldDemo
        $env:VITE_ROBOT_WS_URL = $oldWs
        $env:VITE_VISION_PREVIEW_URL = $oldVision
        Pop-Location
    }
}
if (-not $SkipModel) {
    & $visionPython (Join-Path $root 'zhenjiu\models\wholebody\download_models.py') --model rtmw-l
    if ($LASTEXITCODE) { throw 'Model download/hash verification failed. Retry the installer when connectivity is restored.' }
}
if (-not (Test-Path (Join-Path $PSScriptRoot 'device.local.json'))) {
    Copy-Item (Join-Path $PSScriptRoot 'device.example.json') (Join-Path $PSScriptRoot 'device.local.json')
}
Write-Host 'Installation complete. No robot/camera services were started.'
Write-Host 'Prepare SDK and this device calibration/configuration: docs/WINDOWS_NATIVE.md'
Write-Host 'Then double-click windows\Start-Native.cmd. Camera diagnostic: windows\Diagnose-Camera.cmd'
