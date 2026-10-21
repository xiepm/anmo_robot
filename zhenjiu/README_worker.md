# Base 坐标接入更新（2026-10-13）

现已接入控制端标定转换，默认模型为 RTMW-L（现有机位旋转 90°）。实机 LOCATE_RESULT 可附加 `data.base_preview`；曝光有效性和运动许可仍为 false。运行方式、数据有效期和控制交接见 [README_base_coordinates.md](README_base_coordinates.md)。下文早期 MediaPipe 默认说明已由本更新取代。

# vision_worker：独立相机与识别进程

2026-10-12。后台实现已交付，保留原 `run_live.sh` 手动演示入口。正式后台不创建窗口，不导入 ROS 或机械臂 SDK，不发送机器人命令。

## 启动与退出

先在另一个终端启动控制任务提供的服务（默认 REAL 只读业务入口）：

```bash
/home/xpm/rehabilitation/scripts/start_control.sh
```

确认其监听 8766 后，再启动视觉客户端：

```bash
/home/xpm/rehabilitation/zhenjiu/run_worker.sh
```

如果提示 `Connect call failed ('127.0.0.1', 8766)`，表示控制服务没有接受连接；不是模型初始化失败。视觉会自动重连。只做视觉独立测试时，使用下文 18766 测试桩，并给 worker 指定对应 `--uri`。

默认 `REAL`，独占 D435i（640×480、30 FPS），连接 `ws://127.0.0.1:8766/vision`。先关闭占用同一相机的 ROS RealSense 驱动或手动演示。第二个 REAL worker 会因文件锁退出；该锁不能锁住外部 ROS 驱动。

控制服务尚未启动时每秒尝试重连；Ctrl+C 或 SIGTERM 退出并释放资源。默认模型读取本目录 `models/pose_landmarker_full.task`，没有模型时上报 ERROR，不自动下载或切换到模拟。

```bash
# 依赖安装（沿用现有 Python 3.12 环境）
cd /home/xpm/rehabilitation/zhenjiu
.venv/bin/python -m pip install -r requirements.txt
# 限时运行及诊断（输出目录需预先存在）
./run_worker.sh --run-seconds 15 --diagnostics reports/vision_worker/diagnostics.json
# 明确标记的原始点数据回放，不打开相机，状态 mode=SIMULATION
./run_worker.sh --replay tests/fixtures/points_replay.json
```

`--locate-timeout` 默认 30 秒；`--uri` 可指定其他本机测试端口；`--contract` 默认读取 `/home/xpm/rehabilitation/config/rehab_protocol`。目录由控制任务维护，本次没有修改公共配置。

## 工作方式

- asyncio 负责 WebSocket 收发、1 秒心跳及状态变化上报（20ms 检查周期）。单独的有界发送队列阻止慢服务端无限堆积消息，发送超时关闭连接。
- 采集线程持续取彩色和对齐深度，只保留一组最新帧。图像、深度帧引用与彩色内参一起交给推理线程，避免取到另一个时刻的深度。
- 一个推理线程复用 MediaPipe 和现有比例规则；只在 LOCATE 后处理新接收的帧。不使用跨扫描平滑，不重用之前任务的结果。
- 接受定位立即 REPLY；重复 LOCATE 拒绝、不排队。相机/模型异常或帧年龄超过 500ms 时拒绝受理。默认 30 秒未获得有效点返回 FAILED。
- CANCEL 立即 REPLY 并使当前任务失效，单次发布 CANCELED。底层推理可能还在运行；退出前保持忙状态并拒绝新任务，迟到成功结果不会发布。
- 断连使本连接任务失效，发送队列丢弃；重连不重放结果。旧推理尚未退出时新连接不能提交新定位。
- SIGINT/SIGTERM 请求退出；RealSense 取帧等待上限 1 秒。退出时每个线程最多等待 6 秒并记录超时，不能保证打断永久阻塞的本地库调用；线程为 daemon，逻辑结果已先失效。

输入使用控制方 `vision_request.schema.json`，输出使用 `vision_message.schema.json` 校验。重复 JSON 字段、非有限数字、二进制和非法字段均关闭该连接；合法命令的业务拒绝使用 REPLY。没有新增消息 ID 或业务字段。

## 点、坐标与当前能力边界

按公共 `acupoints.json` 过滤名称和部位，`L/R/None` 转为 `LEFT/RIGHT/MIDLINE`，左右为人体自身左右。未知名称不作近似替换，同名同侧重复导致失败。

| 公共部位 | 当前规则覆盖 | 未覆盖 |
|---|---|---|
| BACK | 大椎（back，粗略规则） | 肩井：现有规则缺失，不用肩髃替代 |
| UPPER_LIMB | 曲池、合谷（合谷是粗略规则） | 专用穴位模型及定位精度验证 |
| LOWER_LIMB | 足三里、三阴交 | 专用穴位模型及定位精度验证 |

后台使用现有高/中/低规则以覆盖公共库。协议没有逐点置信档字段，不能把 `coordinate_valid=true` 解读为解剖精度已经合格。人体关键点不完整、局部皮肤入镜、遮挡和背面姿态误识别仍会造成漏检或错标。

点为原彩色光学坐标系 `camera_color_optical_frame` 中的 XYZ 毫米值。三维点必须为有限数且 Z>0；缺深度时坐标 null、coordinate_valid=false。至少一个有效点才可返回 SUCCEEDED。

**当前真实输出的 capture_host_monotonic_ms 一律 null、capture_time_valid 一律 false；normal_camera 一律 null、normal_valid 一律 false。** 设备时间戳/时间域只写本地诊断，尚未建立可信曝光时刻到 CLOCK_MONOTONIC 的映射。接收帧的 monotonic 时间仅用于活性/过期检查，不冒充曝光时间。收到 LOCATE 后选取新接收帧也不能单独证明已排除驱动内的旧曝光帧。

因此，即使获得 XYZ 并返回 SUCCEEDED，控制端也必须继续拒绝用于真实运动。本次未实现表面法向、手眼标定、机器人位姿配对、人体移动后的定位失效判断或精度验收。基座变换及执行判定属于控制进程。

本次检查宿主与项目 ROS 用户态环境的 time namespace 均为 `time:[4026531834]`；这只确认时钟命名空间一致，不代表设备曝光时间已完成映射。

## 测试

```bash
cd /home/xpm/rehabilitation
zhenjiu/.venv/bin/python -m unittest discover -s zhenjiu/tests -v
```

本地控制端桩仅监听本机，不连接机械臂。两个终端运行：

```bash
# 终端一：测试服务端，结果保存在给定文件
cd /home/xpm/rehabilitation/zhenjiu
.venv/bin/python -m rehab_vision.stub --port 18766 --region UPPER_LIMB --view front \
  --output reports/vision_worker/messages.json
# 终端二：真实相机；如只测协议，可追加 --replay tests/fixtures/points_replay.json
./run_worker.sh --uri ws://127.0.0.1:18766/vision --locate-timeout 5 --run-seconds 12
```

回放 JSON 是模拟的原始点包，不是人体精度测试，也不把历史拍摄时间当成当前时间。本次还直接调用现有控制端的纯模拟状态机，验证无曝光时间的结果被拒绝、模式不匹配/心跳超时和扫描超时可检测；未连接真实机器人执行业务。

测试与实机证据见 [验收记录](reports/vision_worker/验收记录.md)。

## 给控制任务的接入说明

正式入口：`/home/xpm/rehabilitation/zhenjiu/run_worker.sh`（不需 ROS runtime）。由控制任务加入总启动器；不要同时启动 vision_sim 或 ROS RealSense 驱动。worker 通过 8766/vision 使用既有协议。

需要共同处理的后续事项：公共库中的肩井尚无算法；可信曝光时间映射与停稳窗口验证、法向、手眼外参和定位精度尚未就绪。现有控制端会拒绝无有效采集时间的定位，这是预期门槛，不应为了界面显示成功而改成接收时间或假法向。

本次仅修改 `zhenjiu/`；没有改 UI、控制代码、公共协议、总启动脚本或厂家 SDK。
