# 按摩机器人上位机：视觉配准与单 / 双相机

```bash
cd /home/xpm/rehabilitation
./scripts/stop_sim.sh
# 仅末端 RGB-D，相机随关节运动
./scripts/start_hmi.sh sensing_mode:=wrist
# 切换双相机：先停止当前仿真，再启动
./scripts/stop_sim.sh
./scripts/start_hmi.sh sensing_mode:=dual
```

默认模式为 dual。启动参数同时决定 Gazebo 传感器、ROS 订阅和界面布局；wrist 模式不生成全局相机及其 TF，不只是隐藏画面。双相机模式可选择任一路相机用于配准，两路均可点选。全局相机固定于世界坐标 (0.45, 0.20, 2.35) m，朝下俯视；末端相机固定于 elfin_end_link。

## 操作流程

1. 选择“人体 01 · 俯卧模型”，点击 **返回观察位**，等待运动完成。
2. 选择配准相机，确认背部橙、绿、蓝三个标记均可见，点击 **采集并配准人体**。界面显示估计原点、拟合残差、相对预设参考的平移和旋转误差。
3. 选择区域与纵向直线、横向往返或椭圆模板；也可在相机画面中依次点选背部，或编辑表格中的人体坐标。
4. 设置速度 5–40 mm/s、间隙 20–80 mm，点击 **规划并预览**。此时不移动；青色轨迹是将要执行的插值路径。
5. 点击 **执行已选轨迹**：观察位 → 表面上方接近点 → 沿估计法向接近 → 连续循迹 → 沿法向退回 → 返回观察位。
6. 人体移动后重新观察和配准。重新配准会清除本次点选及已规划结果。**停止运动 / 空格**取消计算或当前轨迹，后续阶段不再执行。

人体必须保持静止：配准完成后位姿锁定，循迹期间不会把最新相机画面误当作新的人体位姿，也没有持续人体跟踪。未配准、标记缺失、多候选、深度异常或拟合不合格时拒绝建立人体坐标，不会回退到预设值。

表格 H 为视觉估计的人体坐标，W 为世界坐标，显示毫米，底层使用米。三维场景可拖动和缩放；橙色轨迹来自关节反馈的 TCP 正运动学。左栏可滚动。

## 移动过程中的实时测距

TF 使用独立监听线程，避免界面绘制造成变换积压。测距独立同步深度和内参，不依赖 RGB 帧是否到达；RGB 点选仍使用完整 RGB-D 同步。右侧“实时测距”面板按 5 Hz 刷新，单相机模式显示末端一路，双相机模式显示两路：

- **人体原点（坐标计算）**：曝光时刻相机光心到视觉配准人体原点的欧氏距离，`d = norm(p_world_camera - p_world_human)`。人体原点在背部中央，不是整个身体的最近点。
- **可见背部（深度中位数）**：深度反投影得到三维点，按曝光时刻 TF 转入人体坐标，仅保留中央背部范围内的点，计算这些点到相机光心的三维距离中位数。它是代表性的可见背部距离，不是所有像素的最小深度，也不是全身最小间距。

显示单位为 mm，并显示所用帧的年龄。深度每 4 像素采样，背部范围采用 |H.x|≤0.09 m、|H.y|≤0.13 m、|H.z|≤0.008 m，至少 20 个有效采样点。若背部被遮挡、超出视野或有效深度不足，只把深度项置为无效；坐标计算项仍可显示。未配准、图像 / TF 过期或深度停止更新时，两项均不显示旧数值。深度与仿真时钟停止更新均使用 0.7 s 的真实经过时间判断，因此 Gazebo 暂停、ROS 时钟停止时也能失效。

`/massage/camera_distances`（std_msgs/String JSON）发布各路相机的 `origin_distance_m`、`surface_range_m`、独立有效标志、采集时间戳、相机 / 人体世界位置、配准版本和无效原因。底层距离单位为米。

测距沿用人体静止假设，人体移动后需要重新配准；这两项也不等于按摩头的接触间隙。当前测距用于显示和记录，不作为自动制动或接触力控信号。验证结果见 [动态测距联调报告](reports/distance/VALIDATION.md)。

## 人体坐标如何由相机得到

当前实现是**已知标记布局的 RGB-D 刚体配准**，不是无标记人体识别或完整曲面重建。三个标记在人体自身坐标中的位置经过预先定义，相当于固定在模型上的标定板：

| 标记 | 人体坐标 XYZ（米） |
|---|---|
| 橙 | (-0.05, -0.10, 0.001) |
| 绿 | (0.05, -0.02, 0.001) |
| 蓝 | (-0.04, 0.11, 0.001) |

图像分割获得三色标记的中心，用对齐深度与相机内参反投影到相机三维坐标，再用 SVD 刚体拟合得到 `T_camera_human`。估计器输入仅包括图像、深度、内参、曝光时刻相机外参和上述局部标记标定数据，**不接收人体在世界中的预设位姿**。

```text
T_world_camera(t) = T_world_end(t) × T_end_camera
T_world_human_estimated = T_world_camera(t_capture) × T_camera_human
```

估计完成后，单独读取仿真参考 `human_frame`，仅生成误差显示与报告。视觉结果发布为独立的 `world → human_estimated` TF；`/human_registration/status` 包含采集时间、来源、拟合误差、变换矩阵、配准版本和参考误差。TF 持续广播的是同一次静态估计，不能解释为持续视觉更新。

规划使用视觉位姿重建本次人体碰撞模型；不会修改 Gazebo 的真实人体位置。参考模型和规划模型因此保持分离。初始观察运动使用场景已有的静态障碍模型，视觉配准后人体路径、法向和人体碰撞几何均使用估计结果。

## 像素、人体、世界的映射

`T_target_source` 将 source 中的点变换到 target。相机光学坐标为 +Z 向前、+X 向右、+Y 向下；相机安装 link 与光学 frame 不可混用。

```text
p_camera = depth × [(u-cx)/fx, (v-cy)/fy, 1]
p_world  = T_world_camera(t_image) × p_camera
p_human  = inverse(T_world_human_estimated) × p_world
p_TCP_world = T_world_human_estimated × (p_path_human + [0,0,clearance])
R_tool_world = R_world_human_estimated × R_tool_down_in_human
```

同步 RGB、深度和内参，只显示存在曝光时刻 TF 的新鲜图像。点击使用实际显示帧的深度时间戳查询 TF，不能用点击时刻的最新关节位姿替代。32FC1 深度单位为米，16UC1 为毫米。审计记录保留像素、相机 / 世界 / 人体坐标、时间戳与转换矩阵。

人体原点是背部中央平面，+Z 是外法向，允许 |H.x|≤0.09 m、|H.y|≤0.13 m、|H.z|≤0.008 m。当前仅支持这一平面按摩区。

保存方案使用 version 2、frame_id `human_estimated`，包含人体路径、速度、间隙和采集审计数据。请先在当前场景配准，再载入人体局部路径并重新规划；文件内的旧配准只供审阅，不会替代当前视觉测量。旧版以预设 `human_frame` 保存的文件会被拒绝。

## 运动模块与验证

不使用 MoveIt。独立 URDF 正运动学、带限位 SciPy 逆解、五次插值、FCL 碰撞检查和 FollowJointTrajectory 完成运动。预览与执行使用同一份轨迹；参数、起始关节状态或配准变化后旧方案失效。关节最大速度 0.35 rad/s，加速度 0.5 rad/s²。

碰撞检查覆盖离散插值样本，不是连续碰撞证明。当前为保留间隙的仿真循迹，没有接触力控、软组织仿真或持续人体跟踪；真实相机还需要内参、RGB/深度对齐、手眼外参及人体标记布局标定。

```bash
./scripts/build_runtime.sh
./scripts/runtime.sh python3 -m unittest discover -s src/elfin_massage_sim/test -v
# 独立自动联调：禁止再启动第二个上位机控制客户端
./scripts/start_hmi.sh sensing_mode:=wrist gazebo_gui:=false click_gui:=false human_x:=0.615 human_y:=-0.015 human_yaw:=0.08
# 另一终端，有 X 显示时运行
./scripts/runtime.sh python3 scripts/vision_registration_check.py --mode wrist
```

dual 模式分别替换两条命令中的模式值。新验收结果见 [视觉联调报告](reports/vision/VALIDATION.md)，旧版预设坐标联调记录保留在 reports/hmi，仅作历史记录。运行仍采用宿主 Ubuntu 26.04 上的 Ubuntu 22.04 / ROS 2 Humble 隔离环境。
