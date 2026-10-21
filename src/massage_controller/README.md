> 当前完整安装与运行入口请先阅读[发布版说明](../../docs/OPERATIONS.md)。本文保留模块开发和历史接口细节，涉及原工作站路径或阶段记录时，以根目录 README 和 docs 为准。

# control_node：实机反馈、业务状态机与关节可视化

> 2026-10-12 最新：无前端空载实机运动已跑通，含返回起点、固定姿态长直线自动插点、P1/P2/P3 停留及真实停止确认；73 项回归通过。[实机记录](reports/waypoint_runs/实机逐点调试记录_20261012.md)。默认 REAL 业务路径继续只读，真实运动须显式进入逐点调试模式。下方 2026-10-10 段落为此前只读阶段说明。

2026-10-10更新：**真实机械臂读取已合入控制主进程，默认后端为REAL**。型号经用户确认是 `elfin5_l`，连接 `192.168.0.10:10003`。控制进程内部SDK采集线程持续读取实际关节角、TCP和状态；不再要求另起只读读取进程。

真实运动、末端输出、实际停止通路尚未验收，当前是实机反馈调试阶段。`SCAN / SET_PLAN / START`明确拒绝未就绪业务动作，`STOP`明确返回不能执行实机停止，不伪报STOPPED。现场移动/停止仍用厂家示教器及硬件装置。控制程序本轮没有发送运动、使能、复位或停止命令。

## 启动

```bash
cd /home/xpm/rehabilitation
# 实机控制进程 + 被动模型 + RViz，可Ctrl+C一起退出
python3 scripts/start_real_debug.py
# 停止由启动器管理的本工作区调试进程
python3 scripts/stop_real_debug.py
# 不开RViz窗口，仍发布镜像TF
python3 scripts/start_real_debug.py --no-rviz
# 只开控制进程
scripts/start_control.sh
# 已有控制进程时，单独打开模型窗口
scripts/start_robot_mirror.sh
```

以上入口不启动视觉模拟器、Gazebo、运动控制器、joint_state_publisher或旧演示轨迹程序。端口已占用时会失败，不自动杀死其他进程。不要同时运行 `read_robot.sh --count 0` 和实机主进程；前者保留为单独诊断入口。

用户态Ubuntu22.04/ROS2 Humble运行环境在脚本内自动加载；宿主路径 `/home/xpm/rehabilitation` 映射为 `/workspace`。首次部署依赖与构建：

```bash
scripts/runtime.sh python3 -m pip install --target /workspace/.runtime/controller_deps -r /workspace/src/massage_controller/requirements.txt
scripts/runtime.sh colcon build --packages-select massage_controller --symlink-install
```

有线网口本次已临时添加192.168.0.20/24；重连/重启后网络设置说明见 [ROBOT_READONLY.md](ROBOT_READONLY.md)。没有修改厂家SDK、机器人网络配置或旧仿真项目。

## 进程内结构

```text
control_node（一个业务进程）
  SDK采集线程：连接 → 每100ms读取真实反馈 → 更新最新快照
                        ↓
  asyncio业务循环：约20ms一轮
    每约100ms取最新快照 → 反馈新鲜度/故障/状态机处理
    UI与视觉WebSocket消息在独立协程中处理
    每200ms及状态变化时推送业务STATUS
                        ↓
  ROS回调/发布任务：真实关节、完整遥测、业务状态
```

SDK阻塞不占用事件循环。采集异常立即清有效性，1秒退避后尝试重连；重复消费缓存不会刷新其原始采样时间。反馈超过500ms无效；首次连接允许8秒初始化窗口，但窗口内也不会显示设备反馈有效。运行后连接丢失/急停/故障进入ERROR；采集线程继续工作，恢复后仍不自动清故障或启动业务。

线程不改变业务状态机，不保存动作队列，不生成模拟反馈。控制器若自行处于仿真模式（IsSimulation=1），REAL后端拒绝把它当作实机反馈。

## 接口

| 接口 | 内容 |
|---|---|
| `ws://127.0.0.1:8765` | UI原有5命令；STATUS.mode现在为REAL，连接和反馈有效性来自实机 |
| `ws://127.0.0.1:8766/vision` | 视觉协议；REAL后端只认可REAL视觉模式，定位执行门槛尚未满足 |
| `/control_node/status` | std_msgs/String，业务STATUS，约5Hz |
| `/control_node/robot_telemetry` | std_msgs/String，真实关节/TCP/设备状态、原始接收时间、数据年龄、动作能力说明 |
| `/robot/joint_states` | sensor_msgs/JointState，仅有新鲜真实采样时发布；角度为弧度 |
| `/robot_mirror/robot_description` | 被动elfin5_l模型；移除了硬件/控制/仿真插件 |
| `/robot_mirror/tf`、`/robot_mirror/tf_static` | 模型显示变换，使用mirror_前缀，与机器人运动坐标系隔离 |
| `/robot_mirror/feedback_status` | RViz新鲜度文字，实时为绿色，过期/断线为红色 |

`/control_node/stop`原有普通请求订阅保留，但本阶段不具备真实停止能力；不能作为实机停止通路。UI发送STOP会收到明确失败回复。GET_STATUS和反馈发布正常工作。

```bash
scripts/controller_runtime.sh ros2 topic echo /robot/joint_states
scripts/controller_runtime.sh ros2 topic echo /control_node/robot_telemetry
scripts/controller_runtime.sh ros2 topic echo /control_node/status
scripts/controller_runtime.sh ros2 topic hz /robot/joint_states
```

主进程不每帧写旧诊断程序的latest.json；正式数据来源为上述ROS话题和WebSocket。需要留存可用rosbag或显式测试记录。

## 模型与真实角度对应

配置位于 `config/real_robot.json`：实际型号elfin5_l已确认；当前J1–J6同序映射到elfin_joint1–6，`q_visual[i] = radians(sign[i] * J[index[i]] + offset_deg[i])`，初值为同序、正号、零偏移。原始SDK角度同时保存在遥测中，不被映射修改。

模型只消费 `/robot/joint_states`，没有独立的模拟关节反馈源，更不会反向控制机械臂。订阅的同时间戳真实采样与发布角度已逐帧验证，转换误差为0。模型SDK关节序号映射明确，但真实每轴物理正方向/机械零位尚未逐轴工装验证，因此 `joint_mapping_verified=false`；不能凭静止同数值宣称全空间物理一致。不得为了看起来接近而随意添加补偿。

使用对应上游elfin5_l名义URDF；没有伪造工具、相机安装或TCP标定。模型TF仅用于显示，不等于经标定的机器人基座/法兰/TCP真值。TCP保留厂家XYZ毫米和RxRyRz度；未擅自转换PoseStamped或TF。

反馈过期时停止发布新的JointState，并发布无效遥测和红色文字；RViz可能保留最后姿态，不能把冻结画面当新鲜反馈。若整个控制进程退出，新鲜度文字在0.6秒后消失。

## 代码位置

| 文件 | 职责 |
|---|---|
| `massage_controller/node.py` | 默认REAL入口、采集线程生命周期、ROS发布 |
| `massage_controller/real_device.py` | SDK工作线程、反馈缓存、重连、新鲜度、明确关节映射 |
| `massage_controller/sdk_readonly.py` | 厂家SDK读取、只读白名单、超时与字段解析 |
| `massage_controller/core.py` | 唯一业务状态机；通过设备适配层区分真实/测试后端 |
| `massage_controller/server.py` | UI/视觉WebSocket及20ms调度 |
| `launch/real_mirror.launch.py` | 只显示模型，不启动任何运动控制器 |
| `config/real_robot.json` | IP、周期、型号、映射与验证状态 |
| `config/real_mirror.rviz` | 模型及实时/过期提示显示 |

## 离线测试与实机调试分开

旧模拟状态机保留用于自动回归，必须显式指定：

```bash
scripts/start_control.sh --backend simulation-test
# 完整离线接口桩
python3 scripts/start_three_processes.py --offline-simulation-test --vision-sim
```

这些入口只用于离线协议测试，不是当前实机调试启动方式，也不提供当前实机可视化的数据。默认启动不回退到模拟设备；真实设备断开时也不会生成模拟关节角。未来正式三业务进程由启动器使用REAL视觉入口和UI入口组合，可视化是可选调试工具。

公共协议仍在 `/home/xpm/rehabilitation/config/rehab_protocol`；没有新增UI命令或网络ID。待实机动作适配接入后沿用现有SCAN→SET_PLAN→START流程，当前不能用测试几何/能量数据绕过真实门槛。

## 验证

```bash
scripts/controller_runtime.sh python3 -m unittest discover -s src/massage_controller/test -v
# 显式实机只读集成测试，占用18765/18766端口；运行前关闭其他实机读进程与镜像
scripts/controller_runtime.sh python3 src/massage_controller/test/integrated_real_smoke.py
```

最新记录：[实机合并验收](reports/integrated_real/验收记录.md)。原模拟验收和独立只读记录保留为历史证据。

## 2026-10-12：新增无前端空载逐点调试模式

填写 `config/commissioning/waypoints.json`，按[填写与运行说明](../../总规划/机械臂空载逐点调试_填写与运行.md)验证、执行。入口仍为控制进程：

```bash
scripts/start_control.sh --waypoint-plan config/commissioning/waypoints.json
# 上一条仅离线校验；下一条连接后等待终端 START
scripts/start_control.sh --waypoint-plan config/commissioning/waypoints.json --execute
```

此模式有单独的 `WaypointSession` 状态机、SDK MoveL/GrpStop 适配和 ROS 发布，在同一个 control_node 进程中运行，不同时启动 UI/视觉业务服务器。默认 REAL 业务模式和原 UI SET_PLAN 协议仍维持只读限制；本次没有将空载移动冒充正式治疗流程。

代码：`waypoint_plan.py`（文件校验）、`waypoint_motion.py`（SDK 适配/逐点执行/停止反馈）、`waypoint_runtime.py`（终端/ROS）、`robot_lock.py`（SDK 客户端互斥）。只读 SDK 白名单维持原有读命令，只有显式调试适配器允许 WayPointEx/GrpStop。运动适配器不会自动使能、复位或写末端输出。所有更新后的项目 SDK 客户端使用同一设备锁；旧的已运行进程必须先退出，厂家示教器/外部软件不受此锁管理。

模板未填坐标，实际运动与停止尚未实机验收。详见填写文档。
