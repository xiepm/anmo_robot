# 新电脑安装说明

本说明面向从 `xiepm/anmo_robot` 干净克隆的 Linux 电脑。日常三进程系统是 **Vue 网页 + 独立 Python 控制进程 + RealSense 视觉进程**；运行网页和独立控制不要求安装 ROS。系统组成见 [ARCHITECTURE.md](ARCHITECTURE.md)，日常操作见 [OPERATIONS.md](OPERATIONS.md)。

发布代码不包含本机虚拟环境、`node_modules`、构建结果、现场照片和执行许可。厂家 SDK、模型权重及新设备标定需要另行准备。推荐先完成纯模拟启动，再连接真实设备。当前实机执行功能用于假人或工装的无接触路线联调。

## 1. 系统与依赖

| 组件 | 需要的环境 | 是否必需 |
| --- | --- | --- |
| 操作系统 | Linux；新部署建议 Ubuntu 22.04/24.04，普通用户账户 | 必需；当前脚本使用 Linux 文件锁、`/proc` 和可选 systemd |
| 后台启动工具 | `systemctl` / `systemd-run`、`ss`（iproute2）、`flock`（util-linux）及有效用户会话 | `启动系统.sh` 需要；三个前台终端不需要 systemd |
| Node.js / npm | 锁文件要求 Node `^20.19.0` 或 `>=22.12.0`；建议使用 Node 22，本机验证为 22.22.1 | 网页必需 |
| 控制 Python | Python >=3.10；独立环境 `.venv-control` | 控制及 SIM 必需 |
| 控制依赖 | `websockets==12.0`、`jsonschema==4.23.0`、NumPy、SciPy；见 `src/massage_controller/requirements-standalone.txt` | 安装脚本安装 |
| 视觉 Python | Python 3.12；独立环境 `zhenjiu/.venv`，本机验证为 3.12.14 | 真实摄像头必需，SIM 不需要 |
| 视觉依赖 | `zhenjiu/requirements.txt` 固定版本，包括 pyrealsense2、ONNX Runtime、OpenCV、MediaPipe、NumPy | 真实摄像头必需 |
| RealSense | 当前实现与本机 D435i 验证；需可用 USB 数据连接和本机设备权限 | 实机视觉必需 |
| 华沿 Python SDK | 与机械臂控制器相匹配的 `CPS.py`；当前参考版本 V1.1.1.1 | 实机控制必需，SIM 不需要 |
| 模型 | 默认 RTMW-L ONNX；来源和 SHA-256 见 `zhenjiu/models/wholebody/manifest.json` | 实机识别必需 |
| ROS 2 Humble / Gazebo Classic | 原生 Ubuntu 22.04，或已有 `.runtime/jammy` 隔离环境 | 仅旧仿真、ROS 发布和模型窗口需要 |
| python-fcl / URDF 网格 | `python-fcl==0.7.0.8`、厂家模型子模块及相关 ROS 工具 | 完整虚拟碰撞检查和对应测试需要，当前暂缓虚拟模型的实机流程不加载它 |

当前开发机是 Ubuntu 26.04，历史 ROS 环境在私有 `.runtime/jammy` 中；该目录不会随仓库下载。新电脑可直接使用独立 Python 路线，不能假定已有该环境。不要把控制与视觉的依赖装进同一个虚拟环境：两者固定的 `websockets` 版本不同。

安装系统基础工具，例如 Ubuntu：

```bash
sudo apt-get update
sudo apt-get install -y git ca-certificates curl python3-venv python3-pip
```

另外准备符合上表的 Node.js/npm。Ubuntu 发行版默认的 `nodejs` 版本不一定满足要求，先检查 `node --version` 和 `npm --version`。Ubuntu 24.04 的默认 Python 为 3.12；Ubuntu 22.04 的默认 Python 3.10 可运行控制，但视觉需另行安装 Python 3.12，并通过 `--vision-python` 指定完整路径。

无窗口视觉模式不需要安装桌面、PyQt 或 ROS。OpenCV/ONNX 的系统动态库仍需可用；若导入报缺库，准备 `libgl1`、`libgomp1`、GLib，以及音频依赖所需的 `libportaudio2`。GLib 的 Ubuntu 包名在 22.04 为 `libglib2.0-0`，24.04 为 `libglib2.0-0t64`。不要同时向同一虚拟环境安装多个 `opencv-python`/`opencv-contrib-python`/headless 变体；本仓库依赖清单已选定 OpenCV 包。

## 2. 干净克隆与基础安装

```bash
git clone --recurse-submodules https://github.com/xiepm/anmo_robot.git
cd anmo_robot
git submodule update --init --recursive
./scripts/install_standalone.sh
```

安装器创建 `.venv-control`，安装控制依赖，然后在 `web/` 执行 `npm ci` 和 `npm run build`。它不会启动控制、相机或机械臂，不下载 SDK、模型，也不复制标定或执行许可。查看可用选项：

```bash
./scripts/install_standalone.sh --help
# 只安装控制 Python 依赖：
./scripts/install_standalone.sh --skip-web
# 指定控制解释器，并安装视觉依赖：
./scripts/install_standalone.sh --python /path/to/python3 \
  --vision --vision-python /path/to/python3.12
```

`/path/to/...` 是占位符，应替换为实际可执行文件。安装使用项目内虚拟环境，不要用 `sudo` 执行安装器或启动脚本。已有虚拟环境时安装器会更新其中的依赖；日常启动脚本不会自动运行 pip。

也可手动安装控制和网页：

```bash
python3 -m venv .venv-control
.venv-control/bin/python -m pip install -r src/massage_controller/requirements-standalone.txt
cd web
npm ci
npm run build
cd ..
```

如果只查看界面，运行 `./scripts/start_web.sh`，打开 <http://127.0.0.1:5173/>。此时显示设备未连接是正常现象。该入口服务已有 `web/dist`；修改 UI 源码后需要重新 `npm run build`。开发时可在 `web/` 运行 `npm run dev -- --host 127.0.0.1 --port 5173 --strictPort`，不要与正式网页服务同时占用 5173。

## 3. 先运行纯 SIM

先确保没有另一套实机或模拟进程占用 5173、8765、8766。分别在三个终端进入仓库根目录，运行：

```bash
# 终端一：模拟控制，不连接任何机械臂
./scripts/start_control.sh --backend simulation-test --standalone
```

```bash
# 终端二：模拟视觉协议，不打开相机
./scripts/start_vision_sim.sh
```

```bash
# 终端三：网页
./scripts/start_web.sh
```

打开 <http://127.0.0.1:5173/>。这是协议与页面流程模拟，不生成真实摄像头图像，不执行真实 SDK 预检，也不能验证现场坐标精度。它不需要 SDK、ONNX 模型、手眼文件或 ROS。结束时分别在三个终端按 Ctrl+C。

`启动系统.sh` 默认启动真实三进程，不是 SIM 入口。请显式使用上面的 `--backend simulation-test`。

## 4. 准备真实视觉

### 4.1 环境与模型

安装器的 `--vision` 选项使用 Python 3.12 创建 `zhenjiu/.venv`；也可手动执行：

```bash
python3.12 -m venv zhenjiu/.venv
zhenjiu/.venv/bin/python -m pip install -r zhenjiu/requirements.txt
zhenjiu/.venv/bin/python -c 'import cv2, numpy, onnxruntime, pyrealsense2, mediapipe; print("视觉依赖导入成功")'
```

默认预览使用 RTMW-L、背面模式和旋转适配，通过 ONNX Runtime 的 CPU 后端推理，不需要 CUDA 或 PyTorch。RTMW-L、DWPose-L 的下载工具、官方来源及固定哈希在 `zhenjiu/models/wholebody/`。下载器按仓库中的 `manifest.json` 检查 SHA-256 和字节数，通过后才替换模型，不改写预期哈希：

```bash
zhenjiu/.venv/bin/python zhenjiu/models/wholebody/download_models.py --model rtmw-l
# 后续仅校验本地文件，不联网、不修改模型：
zhenjiu/.venv/bin/python zhenjiu/models/wholebody/download_models.py --model rtmw-l --check
```

省略 `--model` 会处理两个 ONNX 模型；实时预览实际使用 `rtmw-l.onnx`。离线安装可从 `manifest.json` 记录的官方来源下载对应导出包，取出 ONNX 后按相同文件名放置并用 `--check` 校验。MediaPipe 的 `models/pose_landmarker_full.task` 只供相应旧入口/后端使用；它不能替代当前默认的 RTMW-L 权重。

权重与 SDK 保持原作者或厂商的使用、分发条件，项目根目录 Apache-2.0 不自动覆盖这些资源。原厂 ROS 模型来源与固定提交见 `upstream.repos` 和子模块，遵循 `src/elfin_robot_ros2/LICENSE`。

### 4.2 相机与曝光时序

接入相机后，可先用 `lsusb` 确认系统枚举到了 RealSense。若出现权限错误，按当前 librealsense/设备平台安装适用的 udev 规则，并重新插拔设备；不要以 root 常驻运行全部服务或把所有 USB 设备改为可写。

当前扫描不仅需要图像，还检查相机身份、固件/SDK 组合、曝光时间戳和深度帧对应关系。新相机必须建立本机曝光时序配置并完成验证；不能因为实时图像正常就复制另一台相机的允许记录。缺少该配置时可排查预览，但真实扫描/坐标确认应保持不可用。

```bash
cp -n config/commissioning/capture_timing.example.json \
  config/commissioning/capture_timing.local.json
```

根据本设备实际信息与时序验证结果填写该文件。示例的 `verified=false` 是有意设置；型号、固件、SDK、帧率及曝光范围仍需满足代码支持的组合。默认控制与视觉读取同一个本地文件，也可在启动前统一设置 `REHAB_CAPTURE_TIMING` 为项目内文件的绝对路径。该文件不是手眼标定，也不授予运动许可。

正常三进程只运行 `web/scripts/start_vision_preview.sh` 这一份采集者。不要同时打开 `run_back.sh`、`run_live.sh`、`run_worker.sh`、RealSense Viewer 或旧 ROS 相机启动器；这些入口可能争用同一设备。

## 5. 准备实机配置

### 5.1 SDK 和网络

从机械臂厂家获得与实际控制器匹配的 Python SDK。当前默认路径为：

```text
SDK/HUAYAN/python API接口/HuayanRobotPythonSDK_V1.1.1.1/CPS.py
```

可以保留此目录结构，也可以使用 `REHAB_SDK` 或控制命令的 `--sdk` 指定其他路径。当前 Python 入口使用 `CPS.py` 的通信接口，不要求安装目录里的 C++ SDK 或 MATLAB 示例。需要确认所获得 SDK 的使用与分发授权。

机器人网络由本机管理员按实际控制器设置。示例配置 IP 为 `192.168.0.10`，不是自动探测结果；主机有线网口需设为与控制器同网段且不冲突的地址。根据现场情况配置，不要复制开发机网卡名、Wi-Fi 设置或默认路由。

```bash
cp -n src/massage_controller/config/real_robot.example.json \
  src/massage_controller/config/real_robot.json
```

编辑实际机器人 IP、端口、SDK 路径、型号及关节映射。示例故意没有标记型号/映射已经验收；应根据本机核对结果设置，不能用修改标志代替核对。

### 5.2 本机标定和执行配置

手眼准备见 [新设备标定说明](../zhenjiu/hand_eye/NEW_INSTALLATION.md)。当前代码使用腕部数学参考 `virtual_flange_with_joint6_zero`，并非任意法兰安装的通用公式。相机序列号、内参、安装参考、变换矩阵和测量证据均应来自这台设备。仓库中的 `calibration.example.json` 是未配置结构，不能直接启动有效 Base 转换。

将本机实测结果保存为例如 `zhenjiu/hand_eye/calibration.local.json`，然后设置：

```bash
export REHAB_HAND_EYE="$PWD/zhenjiu/hand_eye/calibration.local.json"
export REHAB_ROBOT_CONFIG="$PWD/src/massage_controller/config/real_robot.json"
```

仅当准备无接触执行时，另建立本地路线配置：

```bash
cp -n config/commissioning/noncontact.example.json config/commissioning/noncontact.local.json
export REHAB_NONCONTACT_CONFIG="$PWD/config/commissioning/noncontact.local.json"
```

按 [配置说明](../config/commissioning/README.md) 核对工具、姿态、间隙、速度和回程设置。当前支持的工具模型为法兰 +Z 20 mm；示例值不代表已经测量你的末端。不要复制旧 `*.authorization.json`、冻结照片、路线报告或 SDK 锁文件。授权由操作员对本次照片和路线明确确认后生成，不属于安装参数。

### 5.3 分别启动，先验证连接与拍照

本机实测配置准备好后，在设置了上述环境变量的终端启动：

```bash
# 只接入反馈和拍照；不加入 --noncontact-config 时不提供路线运动入口
./scripts/start_control.sh --backend real --standalone --enable-real-scan
```

若已经导出 `REHAB_NONCONTACT_CONFIG`，它也会启用相应配置。只做首次反馈/拍照核对时，可在单次命令中取消该变量：

```bash
env -u REHAB_NONCONTACT_CONFIG ./scripts/start_control.sh \
  --backend real --standalone --enable-real-scan
```

在另外两个终端启动视觉和网页：

```bash
./web/scripts/start_vision_preview.sh
```

```bash
./scripts/start_web.sh
```

此过程只在明确启动控制进程后连接 SDK，不自动使能、复位或移动机械臂。单独能打开网页并不等于机械臂已连接；分别检查顶部设备状态、人体定位页实时画面、拍照与 Base 坐标结果。

### 5.4 日常后台启动

完成本机配置后，结束上述前台进程，登录桌面并在同一终端设置本机环境变量，再运行：

```bash
bash 启动系统.sh
bash 启动系统.sh --status
```

该脚本默认使用实机和无接触配置，依次启动控制、视觉、网页三个用户级 systemd 服务。若未指定 `REHAB_NONCONTACT_CONFIG`，默认读取 `config/commissioning/noncontact.json`。重复运行复用已有服务，不改变正在运行进程的配置、不重发旧任务；电脑重启登录后再运行一次。它不安装开机自动执行任务。

日志在 `web/reports/services/`，三个服务的操作与正常退出步骤见 [OPERATIONS.md](OPERATIONS.md)。没有 systemd 用户会话的 SSH/容器环境可采用三个前台终端，不必为此用 sudo 启动机器人进程。

## 6. 配置覆盖与路径

| 环境变量 | 默认值/用途 |
| --- | --- |
| `REHAB_CONTROL_PYTHON` | 显式指定控制解释器；不指定时优先原有 `.runtime/jammy`，否则 `.venv-control/bin/python` |
| `REHAB_VISION_PYTHON` | 显式指定视觉解释器；默认 `zhenjiu/.venv/bin/python` |
| `REHAB_ROBOT_CONFIG` | 等同 `--robot-config`；默认 `src/massage_controller/config/real_robot.json` |
| `REHAB_HAND_EYE` | 等同 `--hand-eye`；默认仍保留原机的历史文件名，新机应显式指定本机结果 |
| `REHAB_SDK` | 等同 `--sdk`；默认从机器人配置的 `sdk_relative_path` 定位 |
| `REHAB_CAPTURE_TIMING` | 控制与视觉共同使用的曝光时序配置；默认 `config/commissioning/capture_timing.local.json` |
| `REHAB_NONCONTACT_CONFIG` | 等同 `--noncontact-config`；手动启动默认不加，`启动系统.sh` 默认使用 `config/commissioning/noncontact.json` |

显式命令行参数优先。SIM 忽略环境中的无接触执行配置。`启动系统.sh` 会把这些变量传给新建的用户服务；已运行的服务保持原环境，更新配置要在结束任务、停稳并正常退出后重新启动。

主启动脚本通过自身位置定位仓库，不依赖 `/home/xpm`。历史说明、分析报告可能保留开发机路径，不能原样复制。原有隔离运行环境把项目映射到 `/workspace`；启动器会转换项目内显式绝对配置路径，仓库外文件需移入项目或切换到原生 `REHAB_CONTROL_PYTHON`。不要复制其他机器的 `.runtime`、`.venv` 或 ROS `build/install` 目录。

## 7. 可选：旧 ROS/Gazebo 仿真

这条路线与上面的 SIM 协议模拟不同，也不是当前网页三进程的必装项。

原生 Ubuntu 22.04 先安装 ROS 2 Humble desktop，再执行：

```bash
./scripts/setup_humble.sh
source /opt/ros/humble/setup.bash
source install/setup.bash
ros2 launch elfin_massage_sim sim.launch.py
```

`setup_humble.sh` 会安装系统 ROS/仿真依赖并进行 colcon 构建；需要 sudo，仅支持 Ubuntu 22.04。依赖列表以该脚本为准。子模块未完整获取时先运行 `git submodule update --init --recursive`。

已有开发机隔离环境的用户可继续用 `start_sim.sh`、`start_hmi.sh`、`build_runtime.sh`；这些脚本依赖预先安装的 `.runtime/jammy`。仓库没有从零制作该 Ubuntu rootfs/PRoot/ROS 环境的引导器，`install_runtime_packages.sh` 只是内部软件包清单，不能单独生成环境。新电脑优先使用上面的原生 Humble 路线。

`start_hmi.sh` 是旧 ROS 图形上位机，不是打开 Vue 网页；`start_hardware.sh` 是另一套 ROS 实机接入，可能启动第二个相机采集者。不要与当前真实三进程并行启动这些硬件入口。

## 8. 不接硬件的验证与常见问题

```bash
# 启动脚本的临时目录/fake 命令回归；不会打开设备
python3 -m unittest discover -s scripts/tests -p 'test_standalone_launch.py' -v

# 控制数学/协议的离线示例；新机器由 .venv-control 运行
./scripts/controller_runtime.sh python3 -m unittest discover \
  -s src/massage_controller/test -p 'test_route_visibility.py' -v

# 网页编译
cd web
npm run build
```

完整 ROS/FCL 模型测试需要第 7 节的可选依赖，不能把缺少这些依赖当成 SDK 或实机故障。测试中的合成坐标、假相机序列号和测试许可仅供离线验证。

| 现象 | 检查方式 |
| --- | --- |
| `127.0.0.1:5173` 拒绝连接 | 网页进程未监听；检查 `node --version`、`web/node_modules`、构建结果和 `web/reports/services/web.log` |
| `Runtime is not installed yet` | 调用了旧 ROS 包装器；网页/SIM/独立实机控制改用本说明的原生安装，Gazebo 需单独准备 ROS |
| 缺少 `.venv-control/bin/python` | 执行安装器；启动器不会偷偷安装或退回系统 Python |
| 缺少 `real_robot.json`、SDK 或手眼 | 按示例准备本机文件或设置对应 `REHAB_*`；SIM 不需要它们 |
| `ModuleNotFoundError: numpy/scipy` | 控制环境未装 `requirements-standalone.txt`，或指定了视觉/系统 Python |
| 实时画面正常但照片坐标不可用 | 检查本机手眼、相机身份/曝光时序、机器人停稳反馈；画面不能替代这些证据 |
| `未检测到 RealSense` | 先看 `lsusb` 是否枚举到相机，再检查数据线、USB 口和权限；网页重试不能修复操作系统未枚举的设备 |
| 重复启动提示端口或 SDK 被占用 | 查明并正常退出原进程，不另开第二份控制或相机服务 |
| UI 源码已修改但页面没变化 | `start_web.sh` 保留已有 dist；重新构建并按操作说明正常重启网页服务 |

首次安装成功只表示环境和接口可用。新机械臂、相机安装与末端的标定、时序、停止通路和现场路线核对，仍按本机事实进行。
