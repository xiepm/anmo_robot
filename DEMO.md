# 双相机人体按摩动作 Demo

在项目目录运行：

```bash
./scripts/stop_sim.sh
./scripts/start_demo.sh
```

打开 Gazebo、RViz 和 **Elfin | Massage demonstration** 窗口。左侧是独立的全局 RGB-D 相机，右侧是随机械臂运动的末端相机。场景为俯卧简化人体和按摩床，球头连接杆也已显示。模型为项目内生成的原创几何体，无需下载人体素材。

- **START DEMO / D**：从全局相机深度图测量背部三个标记点，规划整段动作，依次执行观察、预接近、法向接近、抬起、下压、退回，最后返回观察位。约 90 秒，实际耗时随仿真速度变化。
- **VIEW POSE / V**：返回观察位。
- **STOP / 空格**：取消当前规划或轨迹，并丢弃后续阶段。
- 左键点击左侧人体背部中央：执行单点接近。头部、四肢、床面和弧面边缘不会作为按摩目标。
- 如果仿真暂停，先点击 Gazebo 左下角播放按钮。键盘需聚焦演示窗口；按钮不依赖输入法。支持大小写快捷键。

本演示只支持模型背部中央的平面区域：world x=0.51…0.69 m、y=-0.13…0.13 m、z≈0.40 m。平面法向为 +Z，工具向下，最低 TCP 保留 20 mm 间隙。这是按摩动作和视觉定位展示，未实现人体接触、软组织形变或恒力控制。不是对任意人体曲面的自动识别或法向估计。

双相机独立话题：

| 相机 | 图像 | 深度 | 内参 | 光学坐标系 |
|---|---|---|---|---|
| 全局固定 | `/global_camera/image_raw` | `/global_camera/depth/image_raw` | `/global_camera/camera_info` | `global_camera_optical_frame` |
| 末端固定 | `/camera/image_raw` | `/camera/depth/image_raw` | `/camera/camera_info` | `camera_optical_frame` |

Demo 的三个点不是直接写入机械臂的固定世界坐标：预设标记投影到全局相机，从当前深度图反投影，并按图像时间戳转换到 world 后才规划。机器人全部运动仍使用独立 URDF/SciPy 逆解、五次轨迹与 FCL 检查，直接调用 FollowJointTrajectory，不依赖 MoveIt。碰撞模型包含整个人体、床、地面、相机和工具。

只启动演示窗口以降低渲染负载：

```bash
./scripts/start_demo.sh gazebo_gui:=false rviz:=false
```

自动执行一轮并录制真实界面（路径为隔离环境内路径）：

```bash
./scripts/start_demo.sh auto_demo:=true record_path:=/workspace/reports/demo/massage-demo.mp4
```

录像旁会生成同名 JSON 和 PNG，记录三维测量点、实际执行阶段、控制器结果、TCP 采样与结束画面。录像为固定帧率采样，负载较高时播放时长可能短于实际执行耗时；数值验收以 JSON 时间戳为准。

原有蓝垫场景保持可用，使用 `./scripts/start_sim.sh`。人体模型生成脚本为 `scripts/generate_mannequin.py`；运行后执行 `./scripts/build_runtime.sh` 更新安装内容。

验收结果：[报告](reports/demo/VALIDATION.md) · [已录制 Demo](reports/demo/massage-demo-h264.mp4)。
