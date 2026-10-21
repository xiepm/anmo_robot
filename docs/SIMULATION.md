# 独立 ROS 2／Gazebo 仿真

这是与网页实机流程分开的开发工具。新电脑请先阅读[安装说明](INSTALLATION.md)；下面的 `.runtime/jammy` 命令仅适用于已准备该隔离环境的工作站。原生 Ubuntu 22.04 使用 `scripts/setup_humble.sh` 构建后直接运行 ROS 命令，不需要复制原工作站的 `.runtime`。

## 本机启动

宿主机 Ubuntu 26.04，`.runtime/jammy` 内安装 Ubuntu 22.04 / ROS 2 Humble。共享宿主内核，以普通用户运行。请使用包装脚本进入环境，不需要 sudo 或手动 source。

```bash
cd /path/to/anmo_robot
./scripts/start_sim.sh
# 停止，切换相机模式
./scripts/stop_sim.sh
./scripts/start_sim.sh camera_mode:=wrist
# 节省 CPU：只打开相机窗口
./scripts/start_sim.sh gazebo_gui:=false rviz:=false
```

同时只能运行一套仿真。桌面锁屏时，请解锁查看 Gazebo、RViz 和 `Elfin massage RGB-D` 窗口。RViz 使用普通 RobotModel 和点标记，不加载运动规划插件。当前默认软件 OpenGL 渲染。

- 左键：点击蓝色垫子可见的顶面，规划整个流程并执行。
- **V**：到预设观察位姿。末端相机第一次点选前先按 V。
- 支持大小写快捷键；也可以点击窗口底部的 **View pose / Home / Cancel** 按钮，不依赖输入法。键盘操作需要相机窗口获得焦点。
- **H**：通过退回/观察流程回零位。
- **空格**：取消计算或取消当前关节轨迹；取消后不执行剩余阶段。
- **Q**：取消后退出点选窗口；整个仿真使用 `./scripts/stop_sim.sh` 停止。

机械臂可能遮住相机中的垫面。点击机械臂表面会被拒绝；可先按 V 调整观察位置，或点击未被遮挡的区域。

如果提示 `No fresh joint feedback`，检查 Gazebo 是否暂停，点击 Gazebo 左下角播放按钮后再操作。仿真暂停时没有关节反馈，程序会拒绝开始运动。图中白色球体是半径 25 mm 的简化按摩头；小方块是相机。球头通过固定关节连接末端，暂未绘制连接杆。

## ROS 2 常用工具

本机工具安装在 `.runtime/jammy` 的 ROS 2 Humble 环境中，通过以下命令调用，无需在宿主机手动 source。每个图形工具可以在单独的终端中启动。

```bash
cd /path/to/anmo_robot
./scripts/runtime.sh ros2 --help          # ROS2 CLI
./scripts/runtime.sh ros2 launch --help   # Launch
./scripts/runtime.sh rviz2                # RViz
./scripts/runtime.sh rqt                  # RQt，从 Plugins 菜单选择工具
./scripts/runtime.sh ros2 run rqt_graph rqt_graph  # 节点/话题关系图
./scripts/runtime.sh ros2 run rqt_plot rqt_plot    # 数据曲线
./scripts/runtime.sh ros2 bag --help      # rosbag2 录制与回放
./scripts/runtime.sh gazebo --version    # 项目使用的 Gazebo Classic 11
```

启动完整项目仿真（包含 Gazebo 和 RViz）使用 `./scripts/start_sim.sh`。

## 运动流程

```text
RGB-D 点选 → 曝光时间戳 TF → world 表面点
  → OBSERVE：预设观察位姿 (0.60, 0.00, 0.65) m
  → PREAPPROACH：目标表面上方 0.15 m
  → NORMAL_APPROACH：沿世界 -Z 直线，停在表面上方 0.04 m
  → FollowJointTrajectory → elfin_arm_controller → Gazebo
```

再次点选或从低位返回时，先沿 +Z 退到 z=0.60 m，再返回预设观察位姿。每次下发运动前，会生成并检查整条路径。每个阶段在控制器返回成功后才执行下一阶段；失败或取消会终止后续阶段。

蓝色垫中心 `(0.6, 0, 0.2)`，尺寸 `(0.5, 0.5, 0.4)` m，顶面 z=0.4。这里只处理该水平顶面，法向固定为 `(0,0,1)`；没有自动估计人体曲面法向。点选范围、深度、图像时效均检查，不可达和碰撞路径会拒绝，不会自动绕障。

TCP 在 `elfin_end_link` 的 +Z 方向 0.10 m，按摩头为半径 0.025 m 的球体。工具 +Z 朝下，平面内固定转角为 90°，以使用远离关节限位的逆解分支。观察关节角由原厂 URDF 计算并固定在 `trajectory.py`，如更换型号或几何参数必须重新求解验证。

## 独立软件模块

| 文件 | 作用 |
|---|---|
| `distance.py` | 相机到人体原点的距离、可见背部深度距离及有效点筛选 |
| `registration.py` | RGB-D 三色标记刚体配准、独立参考误差比较、视觉人体规划模型 |
| `hmi_backend.py` / `hmi.py` | 单 / 双相机界面、视觉配准、曝光时间戳点选、轨迹预览与执行 |
| `kinematics.py` | 解析 URDF，正运动学、带关节限位的最小二乘逆解 |
| `collision.py` | 读取原厂 STL 碰撞网格，通过独立 python-fcl 检查机械臂、相机、球头、垫子和地面 |
| `trajectory.py` | 固定观察分支、笛卡尔直线路径、连续逆解、五次插值、速度和加速度限制 |
| `direct_motion.py` | 后台线程计算；按阶段直接调用 FollowJointTrajectory，处理结果、取消和关节反馈 |
| `click_camera.py` | 同步 RGB-D，鼠标点选、时间戳 TF 转换和界面 |
| `model.py` / `sim.launch.py` | 扩展厂商 URDF，启动 Gazebo、TF、控制器及普通 RViz |

默认上限：关节速度 0.35 rad/s、关节加速度 0.5 rad/s²；笛卡尔接近速度上限 0.05 m/s，最后法向接近 0.02 m/s。轨迹用时间缩放满足限制；控制器接收位置、速度、加速度及时间点。逆解残差要求位置不超过 0.2 mm、姿态不超过 0.002 rad，连续路径限制关节跳变。

碰撞检查包含插值中间点，初始轨迹每 25 ms 采样，慢速时间缩放后不改变几何路径；它是离散验证，不是连续扫掠体积证明。保留原厂机械连接件之间的显式碰撞豁免，没有关闭机械臂与垫子/地面的检查。此版本完成仿真定位和无接触接近，**尚未实现人体接触、恒力控制或软组织模型**。

## 话题和接口

| 名称 | 用途 |
|---|---|
| `/camera/image_raw`、`/camera/depth/image_raw`、`/camera/camera_info` | 对齐 RGB-D 与内参 |
| `/massage/surface_point` | 测得的 world 表面点，PointStamped |
| `/massage/target_point` | 带间隙的最终 TCP 目标，PointStamped |
| `/massage/motion_state` | PLANNING / OBSERVE / PREAPPROACH / NORMAL_APPROACH / RETREAT / HOME / COMPLETE / CANCELLED / REJECTED 等状态 |
| `/elfin_arm_controller/follow_joint_trajectory` | control_msgs/action/FollowJointTrajectory |
| `/joint_states`、`/tf`、`/tf_static` | 实际关节状态与坐标变换 |

相机参数 `clearance` 默认 0.04 m，此三阶段流程允许 `0.02 <= clearance < 0.15`；`max_image_age` 默认 0.5 s。支持 32FC1 米和 16UC1 毫米深度。真实相机还需内参标定、RGB/深度对齐与相机外参标定。

## 编译、测试与验收

```bash
./scripts/build_runtime.sh
./scripts/check_runtime.sh
./scripts/runtime.sh python3 -m unittest discover -s src/elfin_massage_sim/test -v
./scripts/runtime.sh ros2 control list_controllers
./scripts/runtime.sh ros2 action list -t
```

只构建 `elfin_description`、`elfin5_l_ros2_gazebo`、`elfin_massage_sim` 三个包。独立依赖是 NumPy、SciPy 和 python-fcl 0.7.0.8。移植到原生 Ubuntu 22.04：先安装 ROS 2 Humble desktop，再运行 `scripts/setup_humble.sh`。隔离环境安装清单在 `scripts/install_runtime_packages.sh`。

自动联调时关闭常规点选节点，避免两套客户端同时控制：

```bash
./scripts/start_sim.sh gazebo_gui:=false rviz:=false click_gui:=false
# 另一终端：测试真实图像点选、阶段执行、TCP 到位与法向路径
./scripts/runtime.sh python3 scripts/integration_check.py --mode global
./scripts/runtime.sh python3 scripts/control_check.py
# 停止上一套，启动 camera_mode:=wrist 后
./scripts/runtime.sh python3 scripts/integration_check.py --mode wrist
```

当前独立实现的验收数据放在 `reports/independent/`，总结见 本机 reports/independent/VALIDATION.md（生成文件，不随源码发布）。早期 `reports/integration-global.json` 等文件属于旧版本，不能作为当前版本的验收结果。

机器人模型来源：[huayan-robotics/elfin_robot_ros2](https://github.com/huayan-robotics/elfin_robot_ros2/tree/humble_ethercat)，版本锁定见 `../upstream.repos`。独立算法依赖：[SciPy least_squares](https://docs.scipy.org/doc/scipy/reference/generated/scipy.optimize.least_squares.html)、[python-fcl](https://github.com/BerkeleyAutomation/python-fcl)。原厂源码保留其许可证，新增代码 Apache-2.0。
