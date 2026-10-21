# anmo_robot · 视觉定位与机械臂无接触联调系统

面向华沿 Elfin 机械臂与 Intel RealSense RGB-D 相机的研发项目：网页选择方案，拍照识别穴位，人工拖动微调，确认最终 Base 坐标，由控制进程通过厂商 SDK 校验和执行无接触路线，正常完成后返回本次起始点位。

当前实机流程面向**假人／工装的无接触联调**。项目没有完成真人接触、恒力按摩或医疗疗效验证；界面的方案名称和参数不等于这些能力已经实现。

## 从哪里开始

| 你要做的事 | 文档 |
| --- | --- |
| 新电脑安装、依赖与环境配置 | [安装说明](docs/INSTALLATION.md) |
| 开机启动、扫描、调整、执行、返回起点、排错 | [操作说明](docs/OPERATIONS.md) |
| 理解三进程、数据流、模块和坐标版本 | [项目架构](docs/ARCHITECTURE.md) |
| 准备真实设备配置与标定 | [配置模板](config/commissioning/README.md)、[手眼标定](zhenjiu/hand_eye/NEW_INSTALLATION.md) |
| 获取厂商 SDK | [SDK 安装说明](SDK/README.md) |
| 查看通信报文 | [当前协议与 JSON Schema](config/rehab_protocol/README.md) |
| 运行独立 ROS／Gazebo 仿真 | [仿真说明](docs/SIMULATION.md)、[Demo](DEMO.md)、[桌面 HMI](HMI.md) |
| 了解第三方许可 | [第三方声明](THIRD_PARTY_NOTICES.md) |

## 下载与最小启动

建议使用 Ubuntu 22.04／24.04 x86_64。网页和独立控制进程不需要 ROS；真实视觉另需 Python 3.12、D435i 相机及识别模型。Node.js 使用 22.12 以上的 22.x 版本；完整版本和系统包见安装说明。

```bash
git clone --recurse-submodules https://github.com/xiepm/anmo_robot.git
cd anmo_robot

# 创建控制 Python 环境，安装网页依赖并构建网页；不会连接或启动机械臂。
./scripts/install_standalone.sh
```

先用三个终端验证**软件模拟模式**，无需机械臂、相机、厂商 SDK 或手眼标定：

```bash
# 终端 1：模拟控制端
./scripts/start_control.sh --backend simulation-test --standalone

# 终端 2：模拟视觉端
./scripts/start_vision_sim.sh

# 终端 3：网页
./scripts/start_web.sh
```

打开 [http://127.0.0.1:5173/](http://127.0.0.1:5173/)。软件模拟模式用于检查页面和通信，不能证明真实设备路线可达。结束后在这三个终端分别按 `Ctrl+C`，再切换实机模式。

**实机首次启动前**，按安装说明准备 SDK、视觉模型、本机机器人配置、自己的手眼标定、曝光配对配置及无接触工具参数。不要复制其他机器的标定或已确认路线。配置完成后：

```bash
# 普通用户、桌面登录后运行；不要 sudo
./启动系统.sh
./启动系统.sh --status
```

这个入口按顺序启动控制、视觉、网页服务，不会自动执行运动。重复运行会复用已有服务。它默认用于实机；只有完成本机安装和配置后才使用。

## 当前功能

- 四个预设方案；按方案显示和连接相应穴位。腰背方案使用命门、左右肾俞、腰阳关、左右大肠俞。
- 相机实时预览、重新插拔恢复、拍照冻结 RGB-D 数据；扫描本身不驱动机械臂。
- 在照片上拖动穴位；以同张照片深度和曝光配对反馈重新计算 Base 坐标，只有最后一次明确确认的版本可用于路线。
- 从当前真实位姿过渡到穴位上方，按确认顺序执行；SDK 逆解／正解复核、工具偏移和无接触高度分别处理。
- 正常完成后沿已生成的返回路线回到本次起始位姿，并核对停止反馈；故障或停止不会自动继续剩余路线。
- 三进程状态与重连、照片版本失效处理、短时预览延迟恢复、停止通路检查和执行记录。
- 独立的 ROS 2 Humble／Gazebo Classic 仿真及桌面 HMI，作为开发与验证工具。

具体可执行条件以当前控制端反馈为准。识别结果属于比例定位候选点，需要人工核对；新设备的绝对坐标精度、工具外形和实际通路须在现场验证。

## 项目结构

```text
anmo_robot/
├── web/                       当前 Vue 3 + TypeScript 网页及视觉桥接
├── src/massage_controller/    状态机、坐标确认、SDK 路线与执行控制
├── zhenjiu/                   D435i 采集、姿态/穴位定位、视觉进程、标定工具
├── config/rehab_protocol/     当前消息 Schema、穴位词典、模拟配置
├── config/commissioning/     新设备配置模板（本机配置不入 Git）
├── scripts/                   安装、启动、测试、设备与仿真工具
├── docs/                      安装、操作、架构与仿真说明
├── src/massage_hardware/      早期 ROS 实机只读接入与相机节点
├── src/elfin_massage_sim/      独立运动学／碰撞／Gazebo／HMI
├── src/elfin_robot_ros2/       原厂机器人模型（固定提交的 Git 子模块）
├── SDK/README.md              厂商 SDK 获取与安装说明
├── web0/                      原始 UI 参考版本，当前启动入口不用它
└── 总规划/                    设计文档与历史协议参考
```

当前工程以代码及 `config/rehab_protocol/` 为准。`总规划/历史参考/`、旧模块说明中的历史进度、绝对路径和旧协议不能替代当前安装操作文档。

## 发布内容与本机数据

本仓库包含源码、测试、资源、配置示例、依赖锁定文件和文档。以下内容由安装／运行生成或在现场准备，不随源码上传：

- `node_modules`、Python 虚拟环境、ROS 构建结果、`.runtime/jammy` 等隔离环境。
- 相机照片、运行日志、测试报告、临时文件、备份。
- 本机设备配置、手眼标定、测量记录、已确认的现场执行授权。
- 大型 ONNX／MediaPipe 模型：通过官方来源下载；ONNX 提供固定 SHA256 校验。
- 厂商 SDK 包：从厂商取得适用版本后按说明放置，保留厂商条款。

机器人模型子模块固定到 `1381e3f6d404eaf3311cae4311641e4d8f369b1a`。如果忘记递归克隆，运行 `git submodule update --init --recursive`。GitHub 的 Download ZIP 不包含子模块内容；完整仿真请使用 Git 克隆方式。

## 开发与验证

```bash
# 网页类型检查及生产构建
cd web
npm ci
npm run build
node --test tests/*.test.mjs
cd ..

# 不连接设备的安装入口测试
python3 -m unittest discover -s scripts/tests -v

# 控制端测试按模块运行，依赖范围见安装说明
PYTHONPATH=src/massage_controller:src/massage_controller/test \
  .venv-control/bin/python -m unittest test_point_confirmation test_commissioning_authorization
```

真实 SDK、ROS、FCL 或相机专项测试需要相应可选依赖；缺少这些组件不能据此宣称完成实机验收。不要在机械臂运行期间执行会接管 SDK 或停止设备的调试脚本。

## 许可

本项目新增代码沿用 [Apache-2.0](LICENSE)。Elfin 子模块、SOEM、厂商 SDK、识别模型分别适用各自的许可或使用条款，见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
