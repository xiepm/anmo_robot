# Exercise the Windows launcher with a native fake docker.exe. No Docker daemon,
# containers, browser, camera or robot are used. Run with Windows PowerShell 5.1.
$ErrorActionPreference = 'Stop'
$repository = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$sandbox = Join-Path ([IO.Path]::GetTempPath()) ('anmo-docker-tests-' + [Guid]::NewGuid().ToString('N'))
$bin = Join-Path $sandbox 'bin'
$originalPath = $env:PATH
$originalLocation = Get-Location
$testEnvironment = @('ANMO_TEST_LOG','ANMO_TEST_IMAGE','ANMO_TEST_BROWSER','ANMO_TEST_FAIL','ANMO_TEST_OSTYPE','ANMO_PORT')
$savedEnvironment = @{}
foreach ($name in $testEnvironment) { $savedEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }

function Assert-True([bool]$condition, [string]$message) {
    if (-not $condition) { throw $message }
}

try {
    New-Item -ItemType Directory -Path $bin -Force | Out-Null
    Add-Type -OutputAssembly (Join-Path $bin 'docker.exe') -OutputType ConsoleApplication -TypeDefinition @'
using System;
using System.IO;
public class FakeDocker {
    public static int Main(string[] args) {
        string command = String.Join("|", args);
        File.AppendAllText(Environment.GetEnvironmentVariable("ANMO_TEST_LOG"), command + "\n");
        string image = Environment.GetEnvironmentVariable("ANMO_TEST_IMAGE");
        string fail = Environment.GetEnvironmentVariable("ANMO_TEST_FAIL");
        if ((fail == "info" && args[0] == "info") ||
            (fail == "load" && args[0] == "load") ||
            (fail == "list" && args[0] == "image") ||
            (fail == "up" && Array.IndexOf(args, "up") >= 0)) {
            Console.Error.WriteLine("fake native stderr: " + fail);
            return 1;
        }
        if (args[0] == "info") {
            Console.WriteLine(Environment.GetEnvironmentVariable("ANMO_TEST_OSTYPE") ?? "linux");
        } else if (args[0] == "image" && args[1] == "ls") {
            if (File.Exists(image)) Console.WriteLine("sha256:" + File.ReadAllText(image));
        } else if (args[0] == "image" && args[1] == "inspect") {
            if (!File.Exists(image)) { Console.Error.WriteLine("No such image"); return 1; }
        } else if (args[0] == "load") {
            File.WriteAllText(image, "bundled-new");
        } else if (args[0] == "compose" && (Array.IndexOf(args, "build") >= 0 || Array.IndexOf(args, "--build") >= 0)) {
            File.WriteAllText(image, "built-new");
        }
        return 0;
    }
}
'@
    $env:PATH = "$bin;$originalPath"
    $runner = Join-Path $sandbox 'run-launcher.ps1'
    @'
param([string]$Launcher, [string]$Action)
$ErrorActionPreference = 'Stop'
function global:Start-Process {
    param([string]$FilePath)
    [IO.File]::WriteAllText($env:ANMO_TEST_BROWSER, $FilePath)
}
try {
    & $Launcher -Action $Action -Port 5174
    exit 0
} catch {
    Write-Host $_.Exception.Message
    exit 1
}
'@ | Set-Content -Path $runner -Encoding ASCII

    $cases = @(
        @{Name='fresh-source'; Source=$true; Build=$true; Success=$true},
        @{Name='fresh-offline'; Archive=$true; Load=$true; Image='bundled-new'; Success=$true},
        @{Name='updated-offline'; Archive=$true; Existing=$true; Load=$true; Image='bundled-new'; Success=$true},
        @{Name='cached-source'; Source=$true; Existing=$true; Image='old'; Success=$true},
        @{Name='rebuild-source'; Source=$true; Existing=$true; Action='rebuild'; Build=$true; Image='built-new'; Success=$true},
        @{Name='rebuild-source-with-archive'; Source=$true; Archive=$true; Action='rebuild'; Build=$true; Image='built-new'; Success=$true},
        @{Name='missing-bundle'; Success=$false},
        @{Name='failed-load'; Archive=$true; Load=$true; Fail='load'; Success=$false},
        @{Name='failed-image-list'; Source=$true; Fail='list'; Success=$false},
        @{Name='daemon-not-running'; Source=$true; Fail='info'; Success=$false},
        @{Name='windows-containers'; Source=$true; OsType='windows'; Success=$false},
        @{Name='failed-start'; Source=$true; Build=$true; Fail='up'; Success=$false}
    )
    foreach ($case in $cases) {
        # Include a space in the checkout path to exercise native argument quoting.
        $root = Join-Path $sandbox ('case ' + $case.Name)
        $windowDirectory = Join-Path $root 'windows'
        New-Item -ItemType Directory -Path $windowDirectory -Force | Out-Null
        $launcher = Join-Path $windowDirectory 'Docker-Demo.ps1'
        Copy-Item (Join-Path $repository 'windows/Docker-Demo.ps1') $launcher
        Set-Content (Join-Path $root 'compose.yaml') 'services: {}'
        if ($case.Source) {
            New-Item -ItemType Directory -Path (Join-Path $root 'docker') | Out-Null
            Set-Content (Join-Path $root 'docker/Dockerfile') 'FROM scratch'
        }
        if ($case.Archive) { Set-Content (Join-Path $root 'anmo-robot-demo.tar.gz') 'fake archive' }
        $env:ANMO_TEST_LOG = Join-Path $root 'commands.txt'
        $env:ANMO_TEST_IMAGE = Join-Path $root 'image.txt'
        $env:ANMO_TEST_BROWSER = Join-Path $root 'browser.txt'
        $env:ANMO_TEST_FAIL = $case.Fail
        $env:ANMO_TEST_OSTYPE = $case.OsType
        if ($case.Existing) { [IO.File]::WriteAllText($env:ANMO_TEST_IMAGE, 'old') }
        $action = if ($case.Action) { $case.Action } else { 'start' }
        # Only this outer harness allows native failures; the child executes the
        # production launcher's Stop policy under Windows PowerShell 5.1.
        $ErrorActionPreference = 'Continue'
        $output = & (Join-Path $PSHOME 'powershell.exe') -NoProfile -ExecutionPolicy Bypass -File $runner -Launcher $launcher -Action $action 2>&1
        $result = $LASTEXITCODE
        $ErrorActionPreference = 'Stop'
        Assert-True (($result -eq 0) -eq [bool]$case.Success) "$($case.Name): unexpected exit $result; $output"
        $commands = [IO.File]::ReadAllLines($env:ANMO_TEST_LOG)
        $loads = @($commands | Where-Object { $_ -like 'load|*' })
        $builds = @($commands | Where-Object { $_ -match '\|(--build|build)(\||$)' })
        Assert-True (($loads.Count -gt 0) -eq [bool]$case.Load) "$($case.Name): wrong image load decision"
        Assert-True (($builds.Count -gt 0) -eq [bool]$case.Build) "$($case.Name): wrong image build decision"
        Assert-True ((Test-Path $env:ANMO_TEST_BROWSER) -eq [bool]$case.Success) "$($case.Name): browser launched before readiness or was not launched"
        if ($case.Image) { Assert-True ([IO.File]::ReadAllText($env:ANMO_TEST_IMAGE) -eq $case.Image) "$($case.Name): wrong image version" }
        if ($case.Success) {
            Assert-True ([IO.File]::ReadAllText($env:ANMO_TEST_BROWSER) -eq 'http://127.0.0.1:5174/') "$($case.Name): wrong browser URL"
        }
        Write-Host "PASS $($case.Name)"
    }
} finally {
    Set-Location $originalLocation
    $env:PATH = $originalPath
    foreach ($name in $testEnvironment) { [Environment]::SetEnvironmentVariable($name, $savedEnvironment[$name], 'Process') }
    Remove-Item -LiteralPath $sandbox -Recurse -Force -ErrorAction SilentlyContinue
}
