# Windows 使用说明

Windows 有两种使用方式：用 Docker 体验模拟界面，或用浏览器访问已经连接设备的 Linux 工作站。另已提供 [Windows 原生适配与验证包](WINDOWS_NATIVE.md)，用于直接接相机和机械臂；Windows 实机曝光及完整路线仍需现场验证。

| 目标 | 使用方式 | 需要什么 |
| --- | --- | --- |
| 在 Windows 体验页面、方案和模拟流程 | Docker Desktop 的 Linux 容器 | Docker Desktop；无需机械臂、相机、厂家 SDK 或 ROS |
| 操作已经部署的真实设备 | Windows 浏览器 + SSH 转发到 Linux | Windows OpenSSH Client、Linux 登录账号、已运行的三进程服务 |
| 相机和机械臂直接接 Windows，由 Windows 执行控制 | 原生适配与验证包 | 见 [原生说明](WINDOWS_NATIVE.md)；尚未完成 Windows 曝光与运动验收 |

## 1. Docker 模拟体验

安装并启动 [Docker Desktop for Windows](https://docs.docker.com/desktop/setup/install/windows-install/)，使用 **Linux containers**。硬件虚拟化和 WSL2 等前置条件以 Docker 官方当前要求为准。

可在 [GitHub 仓库](https://github.com/xiepm/anmo_robot) 点击 **Code → Download ZIP**，解压后在项目根目录打开 PowerShell。Docker 模拟构建只使用本仓库的网页、控制代码和模拟视觉，不需要 Git 命令、Git 子模块、ROS 模型、ONNX 权重或现场配置。也可以使用 Git 克隆同一仓库。

可以双击 [windows/Docker-Demo.cmd](../windows/Docker-Demo.cmd) 启动；脚本检查 Docker 状态，构建并启动模拟服务，随后打开浏览器。在 PowerShell 中运行 `windows\Docker-Demo.cmd stop` 可停止模拟服务，`windows\Docker-Demo.cmd logs` 可查看日志。

也可以直接使用 Docker Compose：

```powershell
docker compose up --build -d
```

首次运行需要下载基础镜像和构建依赖。完成后打开 [模拟网页](http://127.0.0.1:5173/)。检查运行和日志：

```powershell
docker compose ps
docker compose logs -f
```

结束模拟体验：

```powershell
docker compose down
```

此镜像运行模拟控制与模拟视觉。容器内 Nginx 使用 8080，Windows 对外只绑定 `127.0.0.1:5173`；网页使用同源 `/control` WebSocket。不会连接真实机械臂或读取 Windows USB 相机。真实源码默认的本机回环地址保持不变。

下载 ZIP 不包含原厂 ROS 子模块，但它们不是 Docker 模拟体验所需内容；需要旧 Gazebo/ROS 模型时再按 [安装说明](INSTALLATION.md) 获取子模块。模拟成功不代表完成实机部署或坐标验收。

### 已构建的 Docker 包

[Windows Docker demo 工作流](https://github.com/xiepm/anmo_robot/actions/workflows/docker-demo.yml) 成功后可在运行页 Artifacts 下载 `anmo-robot-windows-docker`（需登录 GitHub，保留 30 天）。解压后双击 `windows/Docker-Demo.cmd`，脚本加载随包镜像，无需在用户电脑上重新构建。Docker Desktop 本身仍需事先安装并启动；首次下载整个包需要联网。

## 2. Windows 连接现有 Linux 实机

这一方式把相机、机械臂网线、控制进程和视觉进程继续放在 Linux 工作站，Windows 只显示网页并发送原有 UI 命令。控制与视觉仍在同一操作系统、同一时钟和 SDK 所有权范围内。

### 2.1 前置条件

1. Linux 工作站已经完成本机安装、标定和配置，并由现场人员按 [操作说明](OPERATIONS.md) 启动真实三进程。
2. Linux 的 SSH 服务已由管理员配置并允许该账户做本地端口转发；Windows 与它网络可达。
3. 操作者知道实际 Linux 登录目标，格式为 `用户名@主机名` 或 `用户名@IP`。仓库不预填账号和设备地址。
4. Windows 已安装 OpenSSH Client。可运行 `ssh -V` 检查；没有时，在 Windows“可选功能”中添加 **OpenSSH Client**。不需要在 Windows 安装 OpenSSH Server。[Microsoft 安装说明](https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh_install_firstuse)

先关闭 Docker 模拟容器等占用 Windows 本地 5173、8765、8767 的程序。脚本检查到冲突会退出，不会终止现有进程。

### 2.2 运行连接脚本

在资源管理器中双击 [windows/Connect-Device.cmd](../windows/Connect-Device.cmd)，按提示填写实际 `用户名@主机`。

也可以在项目根目录的 PowerShell 中运行：

```powershell
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File ".\windows\连接现有设备.ps1"
```

该命令中的执行策略仅作用于这一次 PowerShell 进程，不修改系统全局策略。企业策略限制脚本执行时应按所在机构的管理要求使用；也可手动运行下一节列出的标准 `ssh` 命令。

需要从已打开的 PowerShell 显式传入目标时，调用脚本的 `-Target` 参数；例如：

```powershell
& ".\windows\连接现有设备.ps1" -Target "实际用户名@实际主机"
```

示例中的中文占位内容必须替换。脚本不会请求把密码写入文件；账号密码或密钥认证由 OpenSSH 自己处理。首次连接会显示 SSH 主机密钥指纹，先与 Linux 设备管理者核对，再决定是否接受。脚本没有关闭主机密钥校验。

登录成功后保持该窗口打开，在浏览器打开 [本机转发网页](http://127.0.0.1:5173/)。正常转发时 SSH 可以一直没有新输出；这并不是卡死。脚本显示网页地址只表示即将建立转发，并不保证 Linux 服务已经启动。

原控制服务同一时刻只接纳一个 UI WebSocket 客户端。使用 Windows 窗口接管前，应在任务空闲时关闭 Linux 上的旧控制页面，避免两个界面争用连接。不要在执行过程中通过刷新、关闭窗口或切换电脑来接管任务。

### 2.3 转发的端口

| Windows 本地监听 | Linux 目标 | 内容 |
| --- | --- | --- |
| `127.0.0.1:5173` | `127.0.0.1:5173` | 网页 |
| `127.0.0.1:8765` | `127.0.0.1:8765` | UI 与控制的 WebSocket |
| `127.0.0.1:8767` | `127.0.0.1:8767` | 实时图像及本次冻结照片 |

内部视觉/坐标服务 8766 不需要转发。所有监听限定 Windows 本机，不把控制端口开放给局域网其他电脑。Linux 原服务的监听地址也不用改成 `0.0.0.0`。

等价的手动命令如下，把最后一行目标替换为实际账号和地址：

```powershell
ssh -N -T -o ExitOnForwardFailure=yes -o StrictHostKeyChecking=ask `
  -o GatewayPorts=no -o ControlMaster=no -o ControlPath=none `
  -o ServerAliveInterval=15 -o ServerAliveCountMax=3 -o ConnectTimeout=10 `
  -L 127.0.0.1:5173:127.0.0.1:5173 `
  -L 127.0.0.1:8765:127.0.0.1:8765 `
  -L 127.0.0.1:8767:127.0.0.1:8767 `
  实际用户名@实际主机
```

脚本使用 `-N`，不会在 Linux 执行启动命令、使能机械臂、拍照或运行路线。Linux 服务必须事先由现场人员准备。后续在网页点击准备/开始才进入原有执行流程。

### 2.4 正常退出与排错

退出前先在 UI 结束任务，确认机械臂停稳，再在 SSH 窗口按 `Ctrl+C`。结束转发不会关闭 Linux 服务；活动任务中 SSH 中断会造成 UI 连接丢失，控制端按既有逻辑请求停止，不会重连后自动恢复路线。

| 现象 | 处理 |
| --- | --- |
| 找不到 `ssh.exe` | 安装 Windows OpenSSH Client 后重新打开终端 |
| 本地端口占用 | 正常退出已有模拟容器、网页服务或旧转发；脚本不会杀进程 |
| `Permission denied` | 核对 Linux 账号、密钥或密码及服务器访问策略 |
| 主机密钥发生变化 | 联系设备管理者核实原因，不通过关闭校验绕过提示 |
| SSH 已连接，网页拒绝连接 | 检查 Linux 的网页服务；端口转发成功不保证远端应用在监听 |
| 网页正常、设备未连接 | 检查 Linux 控制服务与真实反馈，以及是否已有其他 UI 客户端 |
| 网页正常、预览失效 | 检查 Linux 视觉服务和相机；Windows 转发不会修复 Linux USB 枚举 |
| 关闭窗口后页面失联 | 重新建立 SSH 转发；依据页面状态重新扫描/确认，不重发旧执行命令 |

Windows 无需为了这种访问方式安装机器人 SDK、RealSense 驱动、Python、Node.js、Docker 或 ROS。它需要浏览器和 OpenSSH Client；真实设备的软件与配置继续由 Linux 工作站管理。

## 3. Windows 原生与 WSL2 实机支持范围

控制锁、启动时钟身份与停止信号已加入 Windows 原生适配；Linux 路径继续保留原实现。SDK 的 Python 网络通信部分与平台测试不等于完整 Windows 实机链路已验收，详见 [原生说明](WINDOWS_NATIVE.md)。

Windows 上的 RealSense 可用性也不能只以“能显示 RGB 图像”判断：项目的冻结照片坐标还要求深度、曝光元数据、全局时间与机器人停稳反馈匹配，当前验证依据来自 Linux UVC。RealSense 官方 Windows 文档包含每设备元数据注册配置，需要独立核对。[RealSense Windows 安装说明](https://github.com/realsenseai/librealsense/blob/master/doc/installation_windows.md)

WSL2 的 USB 接入需要 USB/IP 工具，不能把 Windows 中插入的相机直接视为 WSL 内已经可用。USB/IP 附加在重启、设备复位或拔插后需要重新建立；现有相机恢复流程包含设备复位，因此还需验证 Windows 侧重新附加、元数据完整性与长时间采集。[Microsoft WSL USB 指南](https://learn.microsoft.com/en-us/windows/wsl/connect-usb)、[usbipd-win 说明](https://github.com/dorssel/usbipd-win/blob/master/README.md)

Docker Desktop 也不提供通用直接 USB 透传；其 USB/IP 方案不保证所有设备可用。因此本次 Docker 镜像定位为模拟体验，不能通过简单加 `--privileged` 或映射设备就宣布真实相机和机械臂可用。[Docker Desktop FAQ](https://docs.docker.com/desktop/troubleshoot-and-support/faqs/general/)、[Docker USB/IP 说明](https://docs.docker.com/desktop/features/usbip/)

如果后续需要把全部实机工作迁到一台 Windows 电脑，应单独完成锁与进程管理、统一启动/时钟身份、SDK 停止反馈、相机元数据及热插拔测试；继续使用已校验的 Linux 实机端可避免在本轮部署中改变这些设备链路。

## 4. 脚本编码

`连接现有设备.ps1` 使用 **UTF-8 BOM**，兼容 Windows PowerShell 5.1 对中文的读取。`Connect-Device.cmd` 仅含 ASCII，通过 Unicode 码点定位中文脚本名，不依赖系统中文代码页。编辑后应保留上述编码；不要将 CMD 保存为含乱码的中文路径调用。
