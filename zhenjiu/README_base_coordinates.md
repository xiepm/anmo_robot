# 穴位相机坐标与 Base 坐标接入（2026-10-13）

已接入实测文件 `hand_eye/calibration_20261013.json`。转换发生在控制进程内部，视觉进程没有 SDK 连接，也不发送运动指令。

## 运行

先启动控制进程（当前为只读实机后端）：

```bash
/home/xpm/rehabilitation/scripts/start_control.sh --standalone
```

再启动带 Base 预览的 RTMW-L 窗口：

```bash
/home/xpm/rehabilitation/zhenjiu/run_base.sh
```

V 切换正背面；Q、Esc 或窗口关闭按钮退出。有效标签显示 `Base X/Y/Z`，单位毫米；转换无效时只显示带 `Cam` 前缀的相机坐标，底栏说明原因。实时结果原子写入 `zhenjiu/reports/base_coordinates_latest.json`；暂停、切面、退出会作废旧结果，异常退出则靠结果有效期判断。不要同时启动 `run_worker.sh`、ROS RealSense 驱动和此窗口，它们会争用相机。

三进程无窗口模式使用 `run_worker.sh`，默认改用 RTMW-L 和 90° 输入旋转（现有横向机位），可通过 `--pose-backend mediapipe|dwpose-l|rtmw-l`、`--pose-rotation 0|90|180|270` 调整。它在收到 LOCATE 并得到有效点时，通过同一控制坐标接口填入 `LOCATE_RESULT.data.base_preview`。原来的相机坐标字段保持兼容；SIMULATION 不附加实机 Base 结果。仅需原协议时使用 `--no-base-coordinates`。控制端现有 REAL 业务扫描/治疗门禁仍保持原行为，本次没有开放自动跑点。

## 坐标与接口

相机实际固定在不随 J6 输出旋转的腕部。变换为：

```text
T_base_reference = 厂商 FK([实测J1,J2,J3,J4,J5,0], TCP=0, UCS=0)
P_base_mm = R(T_base_reference × T_reference_camera) × P_camera_mm
            + 1000 × t(T_base_reference × T_reference_camera)
```

J6=0 仅是数学参考，不会命令第六轴转到零位。不能用当前实际法兰位姿替换参考系。矩阵平移单位米，最终点单位毫米；相机点始终来自原始彩色光学坐标，模型输入旋转不会改变坐标轴。

控制端在原 8766 端口增加 `ws://127.0.0.1:8766/coordinates`，接受 `PROJECT_POINTS`、返回 `BASE_COORDINATES`。严格格式见公共协议目录的 `base_coordinates_request.schema.json` 和 `base_coordinates_response.schema.json`。这是控制进程中的附加接口，不是第四个业务进程。控制代码亦可复用 `massage_controller.base_coordinates.BaseProjector`；SDK 查询仍由原 FeedbackWorker 串行持有。

每个点包含：

| 字段 | 含义 |
|---|---|
| `name` / `side` | 穴位名和 LEFT/RIGHT/MIDLINE |
| `camera_point_mm` / `coordinate_valid` | 当前模型像素、配对深度反投影得到的原始相机点 |
| `base_point_mm` / `base_coordinate_valid` | 经过实测外参和停稳反馈转换的 Base 预览点 |

结果还包含 `frame=robot_base`、`units=mm`、`camera_serial`、`calibration_sha256`、`frame_number`、`host_boot_id`、`frame_received_monotonic_s`、`valid_until_monotonic_s`、`feedback_window`、`error`。

**结果可用于显示与控制进程后续联调，当前不能直接作为自动运动目标。** `base_coordinate_valid=true` 只表示满足停稳预览转换条件，不表示穴位解剖准确、曝光同步已验证或路径可执行。`motion_eligible=false` 和 `capture_time_valid=false` 在本接口中固定；不能由坐标非空推断运动许可。显示平滑不参与导出的测量坐标。

目前将图像接收时间与前后机器人反馈配对，要求同一主机启动标识、采集前至少 0.4 s 停稳记录、关节变化不超过 0.05°、反馈空隙不超过 0.25 s、最新反馈不超过 0.5 s、图像接收后 1 s 内使用。机械臂运动/故障、反馈断联、错误相机序列号等均返回空 Base 坐标。**主机接收时间不是曝光时间**，这组检查不能证明相机缓冲区没有旧曝光。因此后续控制接运动前仍须完成曝光同步/清缓冲验证、独立 Base 精度复核，以及工具偏置、接近方向和路径处理。不得将人体表面点直接当作法兰目标。

## 本次验证

- 原有视觉 40 项、控制 73 项回归测试及新增 7 项坐标/通信测试通过。
- 新测试覆盖实测保留样本换算、米/毫米、错误时钟/相机/参考系、反馈缺口、瞬时运动、过期、深度异常、严格报文、SDK 数学调用和无运动通道。
- 实机只读反馈、D435i 彩色/对齐深度、控制 SDK 正解、实测标定和 WebSocket 往返已验证。`reports/base_integration/live_coordinate_check.json` 保存标定板角点的历史验证结果：相机 `[62.6,120.1,430.0]` → Base 约 `[-488.382,-144.622,122.029]` mm。它不是穴位，也不是当前目标。
- 现场当前视野是棋盘板，DWPose 未检测到可靠人体；不能将该实机验证描述成已测到穴位。人体/模型入镜后，同一条链路会输出实际检测到的穴位点。

## 人体模型实测补充

2026-10-13 换上背部人体模型后，同一机位连续 20 帧：DWPose-L 有 3 帧生成背部穴位，RTMW-L 为 20/20 帧。前者主要因右髋置信度在 0.50 门槛附近波动，后者双髋最低约 0.65/0.61。已将 `run_back.sh`（含 `run_base.sh`）与 worker 的默认后端切换为 RTMW-L，保留 90° 旋转和原置信度门槛。大椎、命门及双侧肾俞均有有效深度和 Base 预览坐标，记录在 `reports/torso_diagnosis/live_models.json`。底栏优先显示关键点/深度不足，不再被 Base 服务正常提示覆盖。此项是模型表面的定位预览验证，不改变运动门禁。

## 背部候选覆盖扩展

原先 `make_rules()` 仅定义 4 个背部位置，这是点数少的直接原因，不是 RTMW 只能识别四个穴位。现由 `back_template.py` 提供 18 个带稳定编号的候选：大椎、身柱、至阳、命门，以及左右肺俞、心俞、膈俞、肝俞、脾俞、肾俞、大肠俞。公共 BACK 目录同步扩展，worker 不再把新增点过滤掉。既有肩井目录项保留兼容，但没有对应可靠规则，不输出假点。

画面超过 8 个背部点时采用编号+右侧名称/坐标列表；左右以模型自身解剖方向为准。关键点不足、左右配对翻转或躯干几何退化时不输出候选，像素/深度稳定逻辑及 Base 预览门禁保持不变。

这次扩展的是 RTMW 姿态之后的**比例定位模型**，没有重新训练网络，也没有测得椎骨位置。原始输出标注 `localization_method=pose_proportion_estimate`、`anatomical_location_validated=false`，界面明确显示未校正。用户确认没有厂家圆点对照表，因此不将模型上的圆点、螺丝或孔识别为已知穴位。

名义椎体层级参考 [背部定位研究](https://doi.org/10.1145/3777577.3777630) 和 [背俞穴实测研究](https://pmc.ncbi.nlm.nih.gov/articles/PMC11353071/)。肩髋间图像比例属于本项目工程先验：保留已有大椎 0.05、命门/肾俞 0.60 的相对位置，按名义层级插值，其余双侧点沿局部肩髋侧向放置。该插值和 0.16 躯干宽度横向偏移不是已测量的椎间距或标准骨度分寸。点数增加不等于解剖准确度提升；真正提高定位精度，需要专家标注或厂家点位真值并做独立误差评估。

## 显示跳变修复（2026-10-13）

背部模式的相机区域和 18 行坐标表固定为相同布局，即使关键点暂时丢失也不改变画布尺寸；缺失行显示 `—`，不画不存在的穴位点。Base 显示模式不会因坐标等待/失效而自动切换成 Cam 数值。

`run_back.sh`、`run_base.sh` 和网页预览启用 `--pose-auto-rotation`：启动时用三帧比较四种模型输入方向，检查肩髋置信度和非退化左右几何，再固定选择；不逐帧切换。各方向的输出始终映射回原始相机像素，标定参数不变。需要强制方向时用 `--no-pose-auto-rotation --pose-rotation 90` 等参数。

显示专用躯干跟踪需要连续位置观测和最近的 ≥0.5 置信证据，低于 0.4、越界、大幅跳动或时间间隔过大即失效；只持续低置信观测不能创建新跟踪。置信度短时回落时可显示当前图像中的连续位置，但坐标留空并提示暂停。该跟踪不改写模型置信度，也不参与导出原始测量的有效性判断。

Base 列添加独立显示滤波，原始 `base_coordinates_latest.json` 仍来自未平滑、严格通过原规则的当前帧；失效后立即清空显示坐标。网页先解码下一张 JPEG 再替换上一张，避免加载间隙闪空白。实机验证记录见 `reports/jitter_fix/summary.json`。

## 2026-10-14 对齐网页预设方案

候选范围由 18 扩展为 25：新增腰阳关、双侧肩井/天宗/风池。对应的名称与标准位置参考 [WHO Standard Acupuncture Point Locations](https://iris.who.int/bitstream/handle/10665/353407/9789290613831-eng.pdf) 中 GV3、GB21、SI11、GB20；代码里的图像比例是工程先验，不是 WHO 骨度分寸换算。原始结果继续明确 `pose_proportion_estimate` 和 `anatomical_location_validated=false`，不会声称已检测到椎骨、肩胛骨或枕骨。

肩井使用既有 C7 候选与同侧肩关键点的中点；腰阳关使用与大肠俞相同的 L4 名义层级；天宗使用躯干轴与侧向比例候选；风池增加双耳观测及几何检查，耳点被遮挡时不从躯干凭空生成颈部位置。这些位置需通过标注数据与实物解剖定位进一步验证。现有假人表面圆点、螺丝不作为穴位标签。

网页中的“左/右腰背”尚未定义具体穴位，保持缺失并提示，不将区域标签冒充穴位。确认照片的方案筛选不影响就位页的全量实时预览。
