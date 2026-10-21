# 第三方组件与外部资源

本仓库根目录的 [LICENSE](LICENSE) 为本项目代码提供 Apache-2.0 许可。第三方组件继续遵循各自许可证，根许可证不替代第三方条款。`web0/` 和 `总规划/历史参考/` 是历史参考内容，不是另一个已验收的运行系统。

## Elfin ROS 2 与 SOEM

- 来源：<https://github.com/huayan-robotics/elfin_robot_ros2>
- 固定版本：`1381e3f6d404eaf3311cae4311641e4d8f369b1a`，同时记录于 [upstream.repos](upstream.repos)。
- 项目目录：`src/elfin_robot_ros2/`。保留机器人描述、网格、上游版权声明和许可证；网格是仿真/显示所需资源。
- Elfin 上游根许可证为 [BSD-3-Clause](src/elfin_robot_ros2/LICENSE)，版权所有者为 huayan-robotics。
- 内含的 [SOEM](src/elfin_robot_ros2/soem_ros2/SOEM/LICENSE) 使用 **GPLv2 with linking exception**，具有独立条款；不能将其标记为 Apache-2.0 或 BSD。其许可证还说明了 EtherCAT 商标及相关许可要求，使用 EtherCAT 主站时请阅读原文。

如果以 Git 子模块获取此目录，克隆后执行 `git submodule update --init --recursive`。GitHub 的主仓库 ZIP 不包含子模块源码；可按 `upstream.repos` 中的固定提交另行获取。

## 华沿机器人 SDK

实机适配使用厂家 `HuayanRobotPythonSDK_V1.1.1.1/CPS.py`。本机所用文件头声明 `All rights reserved.`，随包未提供公开再分发许可证，因此厂商 Python/C++/C#/Java SDK、二进制、压缩包和 PDF 手册不包含在此仓库中，也不受本仓库 Apache-2.0 许可覆盖。

请从华沿机器人官方或设备供应商获取适配自己控制器版本的 SDK 和许可。安装位置及离线测试要求见 [SDK/README.md](SDK/README.md)。项目中的适配器、参数校验和测试是本仓库代码；它们不授予厂商 SDK 的再分发权限。

## 视觉模型

模型权重在安装时从其官方来源下载，不直接纳入 Git。模型、训练数据和推理运行库可能有不同的许可及使用条件；使用下载资源前应阅读对应上游说明。

| 资源 | 来源与版本记录 | 本地用途 |
| --- | --- | --- |
| RTMW-L / DWPose-L ONNX | [OpenMMLab MMPose](https://github.com/open-mmlab/mmpose)，精确下载地址、尺寸和 SHA256 在 [manifest.json](zhenjiu/models/wholebody/manifest.json) | 全身关键点推理 |
| MediaPipe Pose Landmarker Full | [Google 官方模型](https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_full/float16/latest/pose_landmarker_full.task)，[MediaPipe 项目](https://github.com/google-ai-edge/mediapipe) | 兼容姿态推理路径 |

当前开发机所用 MediaPipe 文件的 SHA256 为 `4eaa5eb7a98365221087693fcc286334cf0858e2eb6e15b506aa4a7ecdcec4ad`。`latest` URL 可能变化；应校验下载内容并记录实际使用版本，而不是把任意新文件当作已验证模型。

## 包管理器依赖

前端、视觉和控制器依赖分别记录在 [web/package-lock.json](web/package-lock.json)、[zhenjiu/requirements.txt](zhenjiu/requirements.txt)、[src/massage_controller/requirements.txt](src/massage_controller/requirements.txt)。Vue、Vite、Pinia、OpenCV、MediaPipe、ONNX Runtime、RealSense 等包的许可证随其发布包提供，安装和分发时须保留相应声明。`node_modules/`、Python 虚拟环境及 ROS/系统镜像不是项目源代码，不上传到 Git。

## 本机数据

现场图像、扫描缓存、设备序列号、标定实测文件、路线授权和运行记录不随源码发布。仓库中的 `*.example.json` 为待填写模板，测试 fixture 为人工构造的数据，二者均不能作为新设备的现场标定或运动授权。
