# Windows 直接连接相机和机械臂

此入口面向 **Windows 11 x64 + Python 3.12 x64**：相机 USB 和机械臂网线直接接 Windows，控制、视觉和网页都在这台电脑运行，不依赖 Docker、WSL、Linux 设备机或 ROS。

**当前是原生适配与现场验证包，不是已验收的实机安装版。** 已提供依赖安装、设备配置入口、三进程启动、设备互斥、正常停止和相机诊断。Windows 下的曝光时间模型尚未完成实测；当前可以进行相机预览及控制连接验证，不能把 Linux 的曝光 `verified=true` 配置复制过来就执行照片路线。曝光模型通过 Windows 实测并适配后，才可验收完整无接触路线。

## 1. 下载与安装

推荐从 [Windows native package 工作流](https://github.com/xiepm/anmo_robot/actions/workflows/windows-native.yml) 最近一次成功运行的 **Artifacts** 下载 `anmo-robot-windows-native`，解压到有写权限的目录，例如 `C:\anmo_robot`。GitHub Actions 下载需要登录 GitHub；构建产物保留 30 天。这个包带预构建网页，不需要安装 Node.js。

也可以在仓库 **Code → Download ZIP** 获取源码。源码 ZIP 不含预构建网页，需要先安装 Node.js 22.12+，安装脚本会构建网页；不用 Git 子模块即可运行本入口。

双击：

```text
windows\Install-Native.cmd
```

脚本会：

1. 查找 Python 3.12 x64；没有时通过 Windows Package Manager 安装。系统没有 `winget` 时会给出手动安装地址。
2. 检查 Microsoft VC++ x64 运行库；没有时通过 `winget` 安装。首次系统组件安装可能显示 Windows 自己的授权窗口。
3. 创建独立的 `.venv-control-windows`、`.venv-vision-windows`，只安装二进制 wheel，不在本机编译相机库。
4. 校验主要库能够导入，准备网页，按项目记录的 SHA-256 下载并校验 RTMW-L 模型。
5. 首次创建 `windows/device.local.json` 路径配置，不覆盖已有配置。

首次安装需要联网；模型和依赖下载失败后可再次运行脚本。安装本身不启动相机、不连接机械臂、不执行运动。不要把这两个 Windows 虚拟环境复制到 Linux，或把 Linux 的 `.venv` 复制到 Windows。

Windows N/KN 若缺少媒体组件，需要通过 Windows 设置安装 Media Feature Pack。CPU 推理不需要 CUDA 或 PyTorch。当前预构建静态网页不包含 Vite 的神经语音服务，流程语音不可用，页面与设备通信不依赖该功能。

## 2. 首次设备配置

这一部分属于安装现场的信息，无法在通用下载包中替用户填好。`windows/device.local.json` 只保存文件路径，可用相对项目根目录的路径，或者实际 Windows 绝对路径；JSON 中绝对路径建议用 `/`，例如 `C:/anmo_robot/...`。

| 配置 | 准备方式 |
| --- | --- |
| 厂家 Python SDK | 按 [SDK 说明](../SDK/README.md) 放置实际适用的 `CPS.py`。该 SDK 不随项目重新分发。 |
| `robot_config` | 从 `src/massage_controller/config/real_robot.example.json` 另存为 `real_robot.json`，填本机控制器 IP、端口、SDK 路径及已核对的机械臂参数。Windows 网卡与控制器网络必须可达。 |
| `hand_eye` | 本安装实测的手眼标定，默认 `zhenjiu/hand_eye/calibration.local.json`。同一机械结构迁移也需核对相机、工具安装未变化；不使用其他设备的标定。 |
| `capture_timing` | 从 `config/commissioning/capture_timing.example.json` 准备本机记录；当前 Windows 曝光模型尚未实测，保留未验证状态。相机诊断用于后续验证，不能改一个布尔值来代替验证。 |
| `noncontact_config` | 从 `config/commissioning/noncontact.example.json` 另存为 `noncontact.local.json`，核对实际工具、无接触间隙、速度与相关配置。 |

可以先执行不连接设备的文件检查：

```powershell
windows\Start-Native.cmd -Check
```

检查成功仅表示依赖文件和路径齐全，不代表相机时序、标定精度、机器人通信或通行空间已通过。

## 3. 日常启动与结束

1. 接好相机 USB 与机械臂网线，关闭占用相机的 RealSense Viewer。
2. 双击 `windows\Start-Native.cmd`。
3. 程序依次启动控制、视觉和网页，打开 `http://127.0.0.1:5173/`。保留启动窗口；实际连接状态以网页反馈为准。
4. 结束时先在网页停止并确认设备停稳，再回启动窗口按 `Ctrl+C`。程序请求控制端正常停止与关闭，再关闭其他进程。

启动不会自动使能、执行或恢复上次路线。后台进程异常时整组退出，不自动重放运动。重复启动若端口被占用会拒绝，不会结束其他进程。关闭窗口的 X 按钮或任务管理器强杀不是正常停止流程；正常退出使用 `Ctrl+C`。控制端未按期退出时会报错并继续等待，不将强制结束程序当作设备停止成功。

日志位于 `windows/reports/services/control.log`、`vision.log`、`web.log`。所有端口只监听本机：网页 5173、控制 8765、内部视觉 8766、预览 8767。

## 4. Windows 相机诊断与剩余验收

先正常退出启动窗口和 RealSense Viewer，再双击：

```text
windows\Diagnose-Camera.cmd
```

该工具只打开相机，记录枚举信息、固件、SDK、30 fps 原始 RGB/深度时间戳与元数据支持情况，不接触机械臂 SDK、不重置设备、不更改曝光设置、不推断未知曝光单位。JSON 报告在 `windows/reports/`；无相机、被占用或没有帧会给出失败原因。

Windows 现场还需完成：

- 驱动和元数据可用性：按 [RealSense Windows 官方安装说明](https://github.com/realsenseai/librealsense/blob/master/doc/installation_windows.md) 配置；能显示彩色图像不代表曝光元数据齐全。
- 相机首次启动、连续采集、拔插、系统重启后的恢复；提供上述诊断报告。
- Windows 曝光单位、时间戳域与机器人停稳反馈的配对实测。当前代码明确阻止未验证时序作为执行坐标证据。
- 对应型号 SDK 的只读反馈、停止反馈，然后在假人/工装上验收最终确认坐标、无接触路线及回起点。

本仓库的 Windows CI 只验证依赖导入、操作系统 API、脚本和打包；云端没有相机或机械臂，不代表上述现场项目已经完成。

## 5. 常见问题

| 现象 | 处理 |
| --- | --- |
| 双击后提示没有 Python / winget | 安装 [Python 3.12 x64](https://www.python.org/downloads/windows/)，保留 Python Launcher，再运行安装入口 |
| `DLL load failed` | 检查 Microsoft VC++ x64 运行库；N/KN 系统检查 Media Feature Pack |
| 缺少 SDK 或配置 | 按第 2 节准备；程序不会复制其他电脑的现场数据 |
| 本地端口已占用 | 正常关闭旧启动窗口、Docker 模拟服务或 SSH 转发，然后重试 |
| 相机已接上但没有画面 | 查看 `vision.log`，正常退出所有相机占用后运行相机诊断 |
| 提示 Windows 曝光模型未验证 | 当前阶段的明确限制；收集诊断并完成 Windows 时序适配，不能靠重装或修改 verified 消除 |
| SDK 未连接 | 核对控制器供电、IP/端口、网卡设置、厂家 SDK 版本，检查 `control.log` |

只有想体验模拟流程时才使用 `Docker-Demo.cmd`；直接连接 Windows 设备请使用本文的 Native 入口。
