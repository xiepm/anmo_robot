#requires -Version 5.1
<#
Connect the browser to an existing Linux workstation through OpenSSH.
No remote commands, device startup, or process termination are performed.
#>
[CmdletBinding()]
param(
    [string]$Target
)

$ErrorActionPreference = 'Stop'

try {
    if ([string]::IsNullOrWhiteSpace($Target)) {
        $Target = Read-Host '请输入 Linux 登录目标（用户名@主机名或 IP，没有默认值）'
    }
    $Target = $Target.Trim()
    if ($Target -notmatch '^[A-Za-z0-9_][A-Za-z0-9_.-]*@(?:[A-Za-z0-9][A-Za-z0-9.-]*|\[[0-9A-Fa-f:]+\])$') {
        throw '目标格式应为 用户名@主机名 或 用户名@IP；不要填写网址、密码、空格或 SSH 参数。'
    }

    $sshCommand = Get-Command ssh.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $sshCommand) {
        throw '未找到 Windows OpenSSH 客户端。请在 Windows“可选功能”中安装 OpenSSH Client，再运行本脚本。'
    }

    # Probe only our IPv4 loopback addresses. Release every temporary listener;
    # ExitOnForwardFailure also covers a port claimed after this preflight.
    $localPorts = @(5173, 8765, 8767)
    foreach ($localPort in $localPorts) {
        $listener = $null
        try {
            $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $localPort)
            $listener.Server.ExclusiveAddressUse = $true
            $listener.Start()
        }
        catch {
            throw "无法使用本地 127.0.0.1:$localPort，端口可能被占用或系统限制。请先正常退出占用该端口的程序；本脚本不会终止任何已有进程。"
        }
        finally {
            if ($null -ne $listener) { $listener.Stop() }
        }
    }

    $sshArguments = @(
        '-N', '-T',
        '-o', 'ExitOnForwardFailure=yes',
        '-o', 'StrictHostKeyChecking=ask',
        '-o', 'GatewayPorts=no',
        '-o', 'ControlMaster=no',
        '-o', 'ControlPath=none',
        '-o', 'ServerAliveInterval=15',
        '-o', 'ServerAliveCountMax=3',
        '-o', 'ConnectTimeout=10',
        '-L', '127.0.0.1:5173:127.0.0.1:5173',
        '-L', '127.0.0.1:8765:127.0.0.1:8765',
        '-L', '127.0.0.1:8767:127.0.0.1:8767',
        $Target
    )

    Write-Host ''
    Write-Host "正在连接 $Target，仅建立本机端口转发。"
    Write-Host '首次连接会显示主机密钥指纹，请与 Linux 设备管理者核对后再接受。'
    Write-Host 'SSH 登录成功且 Linux 三个服务已运行后，打开：http://127.0.0.1:5173/'
    Write-Host '保持此窗口打开。SSH 正常转发时可能没有后续输出。'
    Write-Host '本脚本不会启动相机或机械臂。退出前先在 UI 结束任务并确认停稳，再按 Ctrl+C。'
    Write-Host ''
    & $sshCommand.Source @sshArguments
    $sshExitCode = $LASTEXITCODE
    if ($sshExitCode -ne 0) {
        Write-Host "SSH 连接已结束，退出码 $sshExitCode。请查看上方 OpenSSH 错误信息。" -ForegroundColor Red
    }
    else {
        Write-Host 'SSH 转发已结束。Linux 服务本身没有被本脚本停止。'
    }
    exit $sshExitCode
}
catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
