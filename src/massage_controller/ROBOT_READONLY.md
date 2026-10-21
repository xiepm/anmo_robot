# 机械臂关节角度与末端位姿：独立诊断入口

**后续更新：读取已合入control_node。日常实机调试请使用 [README.md](README.md) 中的 `start_control.sh` 或 `start_real_debug.py`；本文工具仅保留为独立诊断，不需要与主进程同时运行。**

2026-10-10 已实机读取成功。机器人 `192.168.0.10:10003`，电脑有线口 `enp2s0` 增加 `192.168.0.20/24`。控制器返回 `IsSimulation=0`，读取真实设备反馈。未发送使能、复位、停止、运动或输出命令。

## 使用

在宿主终端执行：

```bash
cd /home/xpm/rehabilitation
# 读一次，显示六个关节角、TCP位置、厂家姿态角
scripts/read_robot.sh
# 持续读取，默认10Hz；Ctrl+C退出
scripts/read_robot.sh --count 0
# 每秒显示一次
scripts/read_robot.sh --count 0 --interval 1
# 同时发布ROS话题（同一个读取进程）
scripts/read_robot.sh --count 0 --ros
# 获取机器可读JSON
scripts/read_robot.sh --format json
```

`--count N` 读N次后退出；`--quiet` 不逐条打印；`--jsonl /workspace/.../samples.jsonl` 追加记录；`--audit /workspace/.../commands.json` 在退出时保存查询命令审计。输出参数使用运行环境内 `/workspace` 路径。

最新快照宿主路径：
`/home/xpm/rehabilitation/src/massage_controller/reports/robot_readonly/latest.json`。

这里的 `valid=true` 表示本次读取成功，不代表文件永不过期。消费者必须同时检查当前宿主 `time.monotonic()` 与 `host_monotonic_after_s` 的差值，超过 `feedback_max_age_ms=500` 即视为过期。单次读取结束后文件会保留历史快照；消费者不能据此判断程序仍在线。文件通过临时文件原子替换，读取失败或正常收到终止信号时写入无效记录，避免留下旧数值冒充本次结果。

## 返回字段

| 字段 | 含义 |
|---|---|
| `joint_positions_deg` | J1–J6 实际角度，度，来自同次 ReadActPos 的0–5项 |
| `tcp_pose.xyz_mm` | 末端实际 TCP 的 X/Y/Z，毫米，6–8项 |
| `tcp_pose.rxyz_deg` | 厂家 Rx/Ry/Rz，度，9–11项；未转换为ROS欧拉角或四元数 |
| `configured_tool_pose_mm_deg` | 当前控制器工具坐标配置，12–17项 |
| `configured_user_pose_mm_deg` | 当前控制器用户坐标配置，18–23项 |
| `robot_state` | 运动、使能、故障、急停、电箱连接、到位等原始状态字段 |
| `controller_simulation` | 控制器自己的仿真标志；本次确认false |
| `host_monotonic_before_s/after_s` | 主机顺序读取状态、位置前后的单调时间，不是厂家曝光或编码器硬件时间戳 |

字段映射已对照本地厂家 Python 手册3.6.1/3.6.8及 CPS.py；实际位置严格要求24个有限数。TCP值保留控制器报告的坐标语义，没有把TCP冒称已标定的法兰，也没有进行手眼/基座转换。本次工具与用户坐标配置均读为零，仍需现场核验工具/TCP的物理定义后才能用于规划。

SDK源码未修改。读取适配器在自身导入实例上加只读命令白名单和每次收发的总超时（默认1秒）；分片报文合并至分号结束。超时或断线立即关闭对应socket，避免把迟到响应当下一次读数。厂家初始连接自身仍可能等待数秒。此工具不是实时安全控制器。

## ROS

`--ros` 启动 `/robot_readonly_reader`：

- `/robot_readonly/joint_states`：`sensor_msgs/JointState`，关节名称 `huayan_joint_1` 至 `huayan_joint_6`，角度转为弧度；时间戳为主机接收时刻。
- `/robot_readonly/telemetry`：`std_msgs/String`，上述完整JSON，保留厂家TCP角度和配置。

不发布TF或PoseStamped，以免在尚未核验厂家角度约定时生成错误旋转。读取工具没有ROS控制订阅。该程序是当前人工实机调试入口，不接入模拟业务STATUS，不把真实读数混入SIMULATION控制后端；后续控制设备适配可以复用 `ReadOnlyReader`。

## 网络配置

本轮只临时修改正在运行的有线连接：保留原有 `169.254.58.0/16`，增加 `192.168.0.20/24`；Wi-Fi默认路由仍为原来的 `10.81.0.1`。持久连接 `netplan-enp2s0` 仍是原先link-local配置，没有覆盖它。重启或重新连接网口后可能需要重新设置：

```bash
nmcli device modify enp2s0 ipv4.method manual \
  ipv4.addresses '169.254.58.0/16,192.168.0.20/24' ipv4.never-default yes
```

该命令针对本次机器已核验的有线配置；不要直接用于其他电脑。若要恢复该网口原持久配置，可执行 `nmcli device reapply enp2s0`（会终止当前机器人通信）。机器人网口配置未修改。

## 实测证据

`reports/robot_readonly/` 保存首次SDK原始读数、100次连续读数、命令审计、采样汇总和ROS测试记录。

- 100次读取全部有效，100ms目标间隔；平均状态+位置读取约1.55ms，最大约2.75ms，最大采样间隔约100.17ms。
- 命令审计仅有 IsHRAppCmdExist、IsSimulation、ReadFastCmdPort、ReadCmdList、ReadRobotState、ReadActPos。
- 首次读取J2约17.150°；之后读数更新为接近零位，证据文件保留各次采样时间，不把早期值称作当前值。
- ROS实机只读测试结果见 `ros_readonly_result.json`；一般回归测试 `tests.log` 包含字段切片、坏值拒绝、写命令拦截、分片收包、超时及原控制进程测试。

真实读数已接通；运动控制、真实停止、手眼标定和末端能量控制仍未验收。
