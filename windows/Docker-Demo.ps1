# Windows PowerShell 5.1+ / Docker Desktop (Linux containers).
[CmdletBinding()]
param(
    [ValidateSet('start','stop','status','logs','rebuild')][string]$Action = 'start',
    [ValidateRange(1024,65535)][int]$Port = 5173
)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw 'Install and start Docker Desktop (WSL 2 / Linux containers): https://docs.docker.com/desktop/setup/install/windows-install/'
}
# Do not redirect native stderr under ErrorActionPreference=Stop: Windows
# PowerShell 5.1 may terminate before the explicit exit-code checks below.
& docker info --format '{{.OSType}}' | Out-Variable osType | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Docker Desktop is not running. Start it, then try again.' }
if ("$osType".Trim() -ne 'linux') { throw 'Switch Docker Desktop to Linux containers.' }
& docker compose version | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Docker Compose v2 is required. Update Docker Desktop.' }
$env:ANMO_PORT = "$Port"
$compose = @('compose','--project-directory',$root,'-f',(Join-Path $root 'compose.yaml'))
switch ($Action) {
    'stop' { & docker @compose down; if ($LASTEXITCODE) { throw 'Could not stop the demo.' }; exit }
    'status' { & docker @compose ps; if ($LASTEXITCODE) { throw 'Could not read demo status.' }; exit }
    'logs' { & docker @compose logs --tail 150 -f; exit }
    'rebuild' { if (-not (Test-Path (Join-Path $root 'docker/Dockerfile'))) { throw 'Rebuild requires a source checkout, not the offline image bundle.' }; & docker @compose build --pull; if ($LASTEXITCODE) { throw 'Image build failed. Check network access and the build log.' } }
}
# A downloaded release may contain an offline image. Source ZIP users build
# once; neither path installs Python/Node on Windows or touches robot hardware.
$archive = Join-Path $root 'anmo-robot-demo.tar.gz'
if ((Test-Path $archive) -and $Action -ne 'rebuild') {
    # The same tag may already refer to an older downloaded bundle. Loading
    # again reuses cached layers and makes this bundle's image authoritative.
    & docker load --input $archive
    if ($LASTEXITCODE) { throw 'The bundled image could not be loaded.' }
}
$imageId = @(& docker image ls --quiet anmo-robot-demo:local)
if ($LASTEXITCODE) { throw 'Could not check the local Docker image.' }
if ($imageId.Count -gt 0) {
    & docker @compose up -d --no-build --pull never --wait --wait-timeout 120
} else {
    if (-not (Test-Path (Join-Path $root 'docker/Dockerfile'))) {
        throw 'No bundled image or source Dockerfile was found. Download and extract the complete package.'
    }
    & docker @compose up -d --build --wait --wait-timeout 120
}
if ($LASTEXITCODE) { throw 'Demo startup failed. Run Docker-Demo.cmd logs for details. If port 5173 is busy, use -Port 5174.' }
$url = "http://127.0.0.1:$Port/"
Write-Host "Simulation is ready: $url"
Write-Host 'No camera, robot SDK, heating or robot motion is connected by this package.'
Write-Host 'Stop: windows\Docker-Demo.cmd stop'
Start-Process $url
