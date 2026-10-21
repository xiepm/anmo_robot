# 三进程公共协议基线

## 切换方案与二维未知区域误判（2026-10-21）

归档照片 143709 的 13 处平面重叠均由一个无效深度像素及其范围扩展引起，缺测射线与实际路线并非处于相同高度。现在配置 `unknown_check_mode: FROZEN_3D_VISIBILITY`，在原精确二维方格相交之后，用同次曝光的原生深度视角核对这些实际路线区间。旧配置默认 `XY_PROJECTION`，旧照片没有原生几何时要求重新拍照，不用插值填洞或提高全局高度消除未知记录。

视觉在同一 pipeline、同一 frameset 中保留 align 之前的 depth；冻结 NPZ 追加 `native_depth_m`、`native_rays`、`T_color_depth`（米）、原生和对齐 depth 帧号/时间戳、`geometry_frame: DEPTH_OPTICAL_NATIVE`。原 `depth_m/rays` 定位接口保持兼容。Base 响应和视觉内嵌 Base 响应追加可选的已验证 `exposure_clock`，消费端严格匹配其中的 depth 曝光，允许 RGB 与 depth 计数器不同。重连时重取内外参、射线并清理旧句柄。

工具尖端去程和回程均变换到 `T_base_depth = T_base_color × T_color_depth` 的原生光学视角。对每条实际相交区间按整数投影坐标连续分段，并检查所有闭端点；每个邻域要求 4×4 深度完整有效，路线最大相机 Z 必须在最近观测深度前至少 10 mm。缺测、前景遮挡、视野外、非支持投影、错曝光仍保留未通过状态。该检查只细化已观测模型中的尖端中心线，不声称完成工具外形或整臂碰撞验收；原现场假人无接触授权和 SDK 复核不变。

新增可选 `STATUS.data.noncontact.route_review.depth_visibility` 显示三维核对结果。完整诊断证据绑定候选路线、场景、上下文、实际工具偏置、曝光、原生深度/rays/外参内容摘要与算法版本；单次授权额外绑定该证据摘要。切换方案会立即清空旧照片、微调、坐标确认和执行状态，切回原方案也重新扫描；点选同一方案保留现有调整。


现场使用同一真实相机和控制器完成 3 → 6 → 3 方案切换、拍照和只读路线预检：照片 3205/8950/13470 各有 3/5/19 处二维重叠，原生三维核对均为 `OBSERVED_CLEAR`，SDK 分别复核 132/124/132 个采样位置，总预检约 0.66–0.71 s。页面恢复“确认现场无障碍并准备路线”，未触发授权、准备、运动或停止指令，实测关节位置不变。证据见 [现场验证](../../web/reports/plan-switch-depth-20261021/live-validation.json)。相关离线回归和前端构建通过。

## 路线预检耗时与未知区域误报修正（2026-10-21）

SDK 所有权仍归原反馈工作线程。新增等待钩子利用两次反馈之间的空闲时间响应数学查询，不再每个 IK/FK 查询固定等待一个 100 ms 反馈周期；不缓存逆解、不省略回程采样。每项之间检查停止、待执行任务及停止通路检查，反馈 deadline 到达后不启动下一项。在途单项仍受原 SDK 读取超时约束，因此 100 ms 是采样目标周期，不是严格最坏时延保证。

归档照片 109955 同一条 136 点路线经真实 SDK 只读白名单对比：原调度 13.710 s，新调度 0.302 s，两者全部逆解/正解复核通过；新版近期最大反馈间隔 0.103 s。未发送运动、使能、停止或倍率指令，前后实测位姿保持一致。证据见 [计时结果](../../web/reports/route-review-optimization-20261021/benchmark.json)。进度通过现有 `route_review.reason/sample_count/waypoint_count` 显示；版本取消后不继续发布旧进度。归档记录新增诊断字段 `review_elapsed_s`，不改变许可。

`unknown_clearance` 改为连续线段到闭合 10×10 mm 方格的精确距离，边/角接触仍算相交，完整未知格记录要求不变。此照片原 8 个圆形近似提示中 2 个为误报，精确平面相交为 6 个；82 个未知格来自无效深度射线及扩展，不是 82 个实体障碍。检查仅描述二维投影，不声称三维实体碰撞。未知区域未被删除或推断成自由空间；UI/授权错误文案明确这一含义。证据见 [分析与路线图](../../web/reports/unknown-cells-20261021/fc52e9df-exact-xy-analysis.json)。本次 156 项后端回归、24 项前端回归及 TypeScript/Vite 构建通过。

## 正常完成返回本次起始点位（2026-10-21）

当前 `config/commissioning/noncontact.json` 已启用严格布尔配置 `return_to_start: true`。每次准备使用实际读取并经 SDK 预检绑定的 Base 法兰起始位姿，沿原去程折线逆序返回，保持本次固定姿态；回程不重复穴位停留。回程末点使用完整起始 XYZ/Rx/Ry/Rz，不再次叠加工具偏移。全部回程点一起参与 SDK 预检、路线指纹及本次授权；旧路线须重新扫描确认。缺少该配置的历史候选维持原几何与指纹。

控制端完成全部回程路点后发送普通停止、确认连续停止反馈，并在恢复临时速度倍率后再次核对实际起始位姿及停稳状态，才报告 `COMPLETED`。新增可选 `STATUS.data.noncontact.returning_to_start` 与 `returned_to_start`；后者只有本次归位完成且停止确认才为 true。STOP、连接失效或故障只停止当前任务，不触发自动返回；回程出错不报告正常完成。零距离路点不发送可能无法观察到 ID 的空运动指令，仍确认新鲜停稳反馈并保留要求的停留。

确认页在归位并停稳后提供“继续使用 · 重新扫描”。下一轮保留所选方案，清空照片、手动调整、执行许可和旧摘要；控制端仅在新的 SCAN 被受理后清除上一轮终态。已完成 132 项后端相关回归、24 项前端回归及 TypeScript/Vite 构建。此更新未发起实机运动，真实往返仍需在新的已确认路线中验证。

## 确认点路线简化（2026-10-21）

当前 `config/commissioning/noncontact.json` 使用 `route_mode: "DIRECT_ABOVE_POINTS"`：以控制器读取的实际 Base 法兰位姿为起点，固定当前姿态，补偿法兰 +Z 20 mm 工具偏移后，直接依次到达最终确认点对应的无接触上方目标。目标高度保持不低于本次可见最高表面加原有最小距离与余量；不再先在起点原地下降，也不在每个点重复执行抬起/接近/抬离。

首选直线方案未通过 SDK 复核时，尝试 `CURRENT_HEIGHT_THEN_ABOVE_POINTS`：先以实际当前工具尖端高度横移到第一个目标上方，再下降到该目标，随后依次经过剩余上方目标。不使用“法兰 Z + 工具长度”代替实际工具尖端高度。未知 `route_mode` 拒绝计算；未设置该字段的旧手工配置与归档路线继续使用原 `LIFT_MOVE_APPROACH` 几何，避免改变历史路线重建结果。

真实运动入口仍为唯一 SDK 工作线程内的 `HRIF_WayPointEx(type=1, isJoint=0)`，即 MoveL。传入的最终目标为补偿后的 Base 法兰坐标，SDK 参数使用零 TCP/零 UCS。10 mm 间隔的逆解/正解验证点不进入运动队列；仅超过现有 500 mm 单段上限时，沿原直线拆成少量共线段，保留最终坐标与停留时间。照片、坐标版本、起点、未知区域及执行授权检查继续生效。

已完成 76 项相关回归。使用照片 41266 的归档确认点与当前实际位姿做只读真实 SDK 复核：首选直线方案通过 85 个采样，路线节点 10→3、长度约 1064.6→833.5 mm；首段较长，实际会形成 4 个 SDK 目标。验证通过只读传输白名单执行，未发送运动、使能、停止或倍率指令，前后位姿一致。原照片仍有 1 个起点投影未知格，不能据此宣布通行验收通过；新照片仍需重新确认。记录见 [真实 SDK 复核](../../web/reports/route-simplification-20261021/real-sdk-review.json)。

## 阶段 3：候选无接触路线预检

2026-10-16 用户要求暂缓虚拟模型检查：`config/commissioning/noncontact.json` 设置 `virtual_model_check: "DEFERRED"`。该策略跳过模型预热、加载和依赖模型的碰撞计算，路线仍使用同一 SDK 连接执行逆解/正解复核；模型不可用也不再中断 SDK 数学检查。可选状态字段 `model_check_state` 为 NOT_CHECKED/DEFERRED/CONSISTENT/MISMATCH/UNAVAILABLE，`model_check_note` 提供说明。DEFERRED 时模型偏差为 null，碰撞通过标志仍为 false，既有执行条件不变。默认 ENABLED 保留模型检查。

用户亦要求暂缓实物外形核对；这记录为未验收事项，不推断其间隙合格。新照片 203875 的 106 个采样位置通过真实 SDK 检查，但仍有 12/510 个未知栅格。阶段 3 为 PARTIAL_SDK_ROUTE_VERIFIED；阶段 4 尚未发送运动。详见 `web/reports/step3/stage3-progress.json`。

真实非接触配置下，最终点位确认及场景分析完成后，控制端自动启动后台候选路线计算。`STATUS.data.noncontact.route_review` 给出 CHECKING/BLOCKED 状态、照片/确认版本、工具尖端和法兰目标、采样数、模型与 SDK 的位姿偏差、候选高度尝试及阻碍。新字段可选，不改变旧报文；目前 `execution_eligible` 和 `full_collision_checked` 恒为 false。此预检不发送 PREPARE_NONCONTACT / START_NONCONTACT，不给运动许可。

路线使用原已确认顺序、固定当前姿态及用户给定的法兰 +Z 20 mm 工具偏移。旧 `LIFT_MOVE_APPROACH` 配置优先试原初始高度，再试“可见最高表面 + 最小间距 + 额外余量 + 10 mm”；当前简化配置的候选方式见上节。每段按不超过 10 mm 采样，经唯一 SDK 采样线程执行 PCS2ACS/ACS2PCS 数学查询，不另开 SDK 连接；逐点复核 FK 位姿（0.5 mm / 0.2°）及相邻关节角变化（不大于 10°，不暗中绕回 360°）。这些数值是当前工程检查阈值，不是临床精度或连续轨迹安全证明。

以厂家 URDF 与控制器的同关节参考 FK 比较（2 mm / 0.2° 一致性门限）；不一致时禁止借用该模型宣布自碰撞或场景碰撞通过。模型一致时才执行厂家本体碰撞网格的离散 FCL 检查和冻结深度 10 mm 占用体素检查。沿途继续对比模型 FK 与 SDK FK。既有模型的机械相邻壳体排除表仍适用，因此报告限定为模型及采样范围，不是完整整臂许可。相机/支架/工具未建模、相机盲区、未知深度及控制器连续扫掠路径未验证仍阻止执行。

视觉新增只读本地 `GET http://127.0.0.1:8767/snapshot/geometry.npz?frame=...&received=...`。仅返回指定冻结照片的 depth_m、实际内参反投影 rays 以及同一 frame_number/received_monotonic_s；错误或过期照片标识返回 503。控制在后台线程限量读取并再次检查标识，数组仅在内存使用，不把新帧混入旧路线。路径固定为本机相机服务，不接受 UI 提供任意文件路径或网址。

重新调整、清除、照片失效或新确认会取消旧预检；旧结果不能进入新版本状态。报告落在 `web/reports/step3/routes/<revision>.json`，内含输入证据和采样关节解。候选计划 mode=NONCONTACT_ROUTE_REVIEW、path_confirmed=false，与运动计划校验器故意不兼容，不能直接作为执行文件使用。关闭进程先取消预检并处理控制停止，再等待计算线程退出。

维护方：控制进程任务。绝对路径 `/home/xpm/rehabilitation/config/rehab_protocol`。

以总规划的 V0.1 文档为业务依据；此目录将其落为 JSON Schema 和最小名称配置。UI 使用 ui_request/ui_response，视觉使用 vision_request/vision_message；两者读取同一份 acupoints.json。schema 只做结构校验，当前定位、能力、范围、计时整除、取消屏障等由控制端做语义校验。

初始穴位库覆盖当前报文正例，不代表视觉已支持全部穴位。不要让 UI 自行扩充成控制端未知标签，视觉也不能把近似名称静默映射为不同穴位。

simulation.json 的全部阈值仅用于模拟联调。views 是显式模拟拍摄配置，不能据此认定真实观察面已经确认。当前控制端默认 REAL，已接入真实反馈；实机连接与关节映射配置在 src/massage_controller/config/real_robot.json。真实运动仍待标定和运动能力验收，模拟几何/能量不可用于实机。

接口差异：无新增业务命令/ID/状态字段；力方案带 gap_mm 予以拒绝；重复 JSON 键、NaN、Infinity、二进制消息均拒绝。无 ID 时先完成旧定位终态与取消回复，再开始新任务。

## 2026-10-13 拍照识别联调扩展

显式 `--enable-real-scan` 允许实机停稳条件下受理 SCAN→LOCATE，不允许运动。新增可选 `STATUS.data.scan_snapshot`：部位、相机帧号、主机接收时刻、识别到的穴位、`execution_eligible=false` 及执行阻塞原因。此前“无新增状态字段”是原始基线的记录。

`scan_snapshot` 表示本次照片识别完成，可进入 UI 路线确认；不表示 `positioning_valid=true`。控制端核对视觉附带的 Base 包确由本控制进程生成、在本次请求后采集、接收时仍新鲜、点与转换包一致，且机械臂停稳。不会将主机接收时间改称曝光时间，也不会把仅显示有效的坐标升级为运动目标。现有 SET_PLAN/START 执行限制保持有效。

UI 仍使用原五条命令；没有新增业务 ID。照片由视觉进程按相机帧号和接收时刻提供，只保存在内存，重新拍照替换。控制与视觉断联使控制端照片结果失效。无接触执行的姿态、工具补偿、路径范围和停止适配仍需继续联调。

## 2026-10-14 方案穴位目录补齐（v0.3）

BACK 新增腰阳关（MIDLINE）、天宗与风池（LEFT/RIGHT）；已有肩井条目接入视觉规则。当前候选总计 25 个，既有前 18 个预览编号不变。协议字段及业务命令不变，仍由同一目录进行名称与侧别校验。`腰背` 是原 UI 的区域标签，不是已定义的穴位，未加入目录、未别名映射至肾俞等其他穴位。

视觉新增的是未作解剖校正的比例候选，不是网络重新训练或标准穴位精准定位。风池额外要求当前双耳可见及头颈几何一致。任何缺失/无有效深度的候选都不能虚构坐标；`capture_time_valid` 和运动资格限制保持原状。

UI 拍照后通过视觉只读 `/snapshot.jpg` 的 `points` 查询参数请求按方案名称、侧别、顺序重绘同一张原始照片。只影响图上标记及列表，不裁剪公共 LOCATE 结果，不改变控制端可执行点集。帧号与接收时间必须精确匹配，不匹配返回 503。

## 2026-10-14 医生微调后的最终坐标确认

此扩展取代前述“UI 仍使用原五条命令”的历史描述。新增 `CONFIRM_POINTS`（版本 revision、原照片帧号与接收时刻、按方案顺序排列的完整最终像素点）和 `CLEAR_POINTS`（空 data）。浏览器只提交点名、侧别及图像 x/y，不提供或自行推算机械臂 XYZ。

控制端将确认请求转为 `REFINE_POINTS`；视觉通过 `REFINE_RESULT` 回传同版本、同照片、同顺序的像素及相机三维点。拍照时将对齐深度图与原图一起冻结在内存。重算只用该照片深度；越界、无效深度、深度边缘或不属于本次识别的点会被拒绝，不使用新实时帧或旧穴位三维点替代。控制端用本次拍照的基座变换及同一标定转换为 Base 坐标。

`STATUS.data.point_confirmation` 包含 `PENDING/CONFIRMED/FAILED`、请求像素、最终相机/Base 表面点及错误。命令 REPLY 仅表示受理，UI 必须等待匹配版本的 CONFIRMED。`CLEAR_POINTS` 撤销确认及旧方案；重扫、STOP、断联、反馈失效、标定变化、关节变化超过 0.05°、照片超过 300 秒均使确认失效。已撤销版本的迟到结果不能恢复确认。

REAL 的 `SET_PLAN` 必须携带 `confirmation_revision`，路线名称、侧别、顺序必须等于该版本；控制端把 `confirmed_surface_points` 绑定到方案，START 再核对版本和点集。确认结果是表面坐标，`execution_eligible` 仍为 false；不是末端姿态/工具补偿后的运动目标。真实运动能力、采集同步和路径验收门槛继续生效，确认坐标不会开启运动。

## 假人无接触执行接口（显式配置，验收未完成时拒绝运动）

通过 `--noncontact-config config/commissioning/noncontact.json --enable-real-scan` 加载独立联调模式。`PREPARE_NONCONTACT` 提交最后确认的 `revision` 与按序名称/侧别，控制端校验现场配置、原照片时间配对和标定资格，工作线程生成补偿路线并校验普通停止通路。`STATUS.noncontact` 返回阶段、路线 SHA256、表面点/工具尖端/法兰目标及具体阻塞原因。`START_NONCONTACT` 必须携带显示过的同一 `plan_sha256`；发命令前再次核对版本、照片和配置。已有 STOP 用于中断，受理回复不能替代真实停止反馈。

与治疗 SET_PLAN/START 分开：本模式不采纳温度/力或 35 分钟治疗参数，不发送能量输出；首轮每点停留 2 秒，速度 5 mm/s、加速度参数 10，保持实际拍照姿态，不自动旋转。工具沿法兰 +Z 伸出 20 mm，工具尖端与法兰目标分别计算；在现场最高表面上方保留最小 20 mm 加额外 10 mm 规划余量，过渡及当前起点必须处于已确认的可通行范围。范围检查不是全机器人碰撞检测。没有明确现场几何、绝对坐标验收或时间依据，接口拒绝进入 SDK 运动阶段。

`PROJECT_POINTS.data.exposure_clock` 可选，携带彩色/深度的 global_time、设备 FRAME_TIMESTAMP、SENSOR_TIMESTAMP、ACTUAL_EXPOSURE 和主机时钟采样。控制端校验墙上时钟跳变、曝光新鲜度、RGB/深度间隔和曝光窗口两侧连续停稳反馈，合格时 `capture_time_valid=true`、`pairing_method=stationary_global_exposure`，同时回传 `capture_window_monotonic_s`。缺失或失败仍是预览，`capture_time_error` 提供原因；曝光窗口起点必须晚于本次扫描请求。只有标定的独立绝对精度和运动资格也通过时，才可产生 `motion_eligible=true`。本轮未改动标定验收标志。

曝光语义依据 [librealsense 元数据定义](https://github.com/realsenseai/librealsense/blob/master/include/librealsense2/h/rs_frame.h) 与 [帧时间戳说明](https://github.com/realsenseai/librealsense/blob/master/include/librealsense2/hpp/rs_frame.hpp)。配对窗口额外扩展 50 ms，用于停稳采集核对，不宣称经过外部测量认证的时钟精度。


## 自动深度场景参数（2026-10-15）

`noncontact.json` 的 `geometry_source: "FROZEN_DEPTH"` 启用自动场景分析。`workspace_base_mm`、`surface_ceiling_base_mm` 和 `safe_transfer_height_base_mm` 可以保持 null，不要求人工填写；本次生成值仅与最终确认版本绑定保存在内存，不把某张照片的几何参数写成永久配置。

控制端在 `REFINE_POINTS.data.scene_context` 携带本次照片标识、确认版本、标定摘要、冻结的 T_base_camera（平移单位米）、当前零 TCP/零 UCS 下的法兰 Base XYZ（mm）。视觉对整张冻结对齐深度图逐像素使用相机 SDK 畸变模型反投影，转换为 Base；不借用实时新帧，不填深度洞，也不删除最高点以降低安全高度。`REFINE_RESULT.data.scene` 返回来源摘要、可见表面包围范围、最高 Z、有效深度数、未知栅格数及完整未知栅格中心列表。保留原字段名 `unknown_cell_examples_base_xy_mm` 以兼容现有读取端，但新结果不再截取前 64 项。场景或结果报文超过传输预算时明确返回失败，不发送截断列表。

分析范围为当前位置至最终确认穴位依序连线周围 50 mm（分析带宽，不是工具或整臂碰撞半径）。采用 10 mm XY 栅格，每个 5 mm 象限须有深度观测；空象限、视野外和缺失深度射线可能经过的区域保留为未知。缺失射线检查覆盖本帧可见高度区间；不据此宣称高度区间之外或遮挡背后的空间无障碍。单帧分辨率不能排除所有细小障碍。任一未知栅格阻止准备运动。

候选工具尖端过渡高度取 `max(可见最高 Z + 最小间距 + 额外余量 + 10 mm, 当前法兰 Z + 工具长度)`。自动法兰运动边界包含当前法兰、最终点和候选过渡高度，加入工具偏移上界及余量。边界是本次命令范围约束，不是自由空间证明。NO_DATA 不生成几何参数；PARTIAL 可以显示候选值，但始终禁止执行。

场景摘要进入 `STATUS.data.noncontact.scene_analysis`，确认页显示数值和未知区域提示。调整、重扫、STOP、照片/标定/位姿失效时一并撤销。场景结果必须与请求的上下文 SHA256、照片和确认版本一致。密集计算在视觉后台线程执行，不持有相机健康发布锁；自动场景确认超时为 30 秒（UI 等待 32 秒），普通确认仍为 5 秒。

本步骤只完成表面分析和几何参数生成，`collision_checked=false`、`execution_eligible=false`。自动模式仍阻止进入 SDK 运动，等待后续整臂扫掠体、遮挡/盲区、可达性检查以及标定和采集同步验收。不能通过手改 path_confirmed 或标定标志绕过这些未完成事项。人工配置模式保留用于已有独立验收的工装路径。


## 阶段 3 未知深度诊断（2026-10-16）

scene 新增可选 coverage_diagnostics：分别统计 incomplete_quadrant_cells、invalid_ray_cells 及 overlapping_cause_cells。未知总数保持两类的并集，原掩码、栅格大小和范围不变；不补洞、不删除外围未知格。

route_review 新增可选 unknown_route_clearance：在通过 SDK 的候选路线中，对法兰节点加上按实际姿态旋转后的工具偏移，得到工具尖端 XY 连续折线。对每个未知栅格中心计算到各线段的最短距离，再扣除 10 mm 方格的外接圆半径，得到保守下界。零长度升降段也处理。只有列表包含全部未知格且无重复时才给出完整距离结论；历史截断结果或其他不完整列表仍返回 INCOMPLETE。距离是 XY 中心线诊断，不证明工具宽度、关节扫掠体或未观测空间；execution_eligible 保持 false。

最新实机照片 5916：105 个 SDK 采样通过，9/509 个未知格，缺测象限计数为 0、无效深度射线计数为 9。全部未知格位于候选中心线外围，保守下界 39.1979 mm，最近格中心 Base XY=[-445,-85] mm。报告位于 web/reports/step3/routes/76337b07-03a4-4f90-8dfe-231a8db68bd8.json。阶段 3 整体验收仍未完成，阶段 4 未发出运动。

离线位置图可用 zhenjiu/.venv/bin/python web/scripts/render_route_coverage.py <报告.json> <输出.png> 重建；不连接相机或机械臂。现有图在 web/reports/step3/coverage-map.png。


## 阶段 4：静止停止检查与候选执行绑定（2026-10-16）

新增 CHECK_NONCONTACT_STOP，data 为严格空对象。仅在配置了非接触联调、IDLE、反馈新鲜且停稳时受理；活动任务或候选路线正在计算时拒绝。工作线程复用唯一 SDK 连接，检查真实反馈、零 TCP/UCS，发送一次 GrpStop，并要求命令后连续 3 帧新的停稳反馈及位姿保持一致。不会调用运动、使能、复位或能量输出；响应不确定时不重发。STATUS.noncontact.stop_preflight 包含 NONE/CHECKING/PASSED/FAILED、check_id、是否发送命令、停止反馈是否确认、时间与前后位姿。PASSED 仅表示静止检查，不代表运动中停止性能或路线许可，execution_eligible 始终 false。

修正自动场景执行准备与预检路线不一致的问题：ReviewManager 保留当前确认/场景/配置版本的完整候选结果和工具旋转。effective_profile 使用实际通过 SDK 的候选高度；PREPARE_NONCONTACT 通过既有执行条件后，将完整候选冻结交给唯一 SDK 工作线程。工作线程按原起点、工具旋转和最终点重建候选并比对几何，校验配置、版本和实际起点；拒绝缺少复核结果的自动场景，不重新回退到初始高路线。path_confirmed 及原采集、标定和现场条件仍须满足，候选计算本身不会制造许可。

本轮实测静止 GrpStop 与 3 帧反馈通过；没有发出 WayPointEx。最新扫描 450446 的 104 个采样位置通过 SDK，候选尖端过渡 Z=278.0106 mm；26 个未知格，距离中心线平面保守下界 21.1695 mm。与用户先前查看的照片 5916 并非同一版本。阶段 4 仍未完成运动执行验收，见 web/reports/step4/stage4-progress.json。


### 单次假人无接触路线授权与倍率（2026-10-16）

`AUTHORIZE_NONCONTACT` 接收当前 `revision`、`points` 及两个必须为 true 的显式确认字段 `operator_observed_no_obstacles`、`reuse_existing_calibration`。只有同步有效、SDK 复核通过、当前确认版本有效、工具尖端路线未涉及未知格且记录完整时，状态才返回 `authorization_available=true`。该字段仅代表可以让操作员确认，不能直接执行。授权写入 `config/commissioning/noncontact.authorization.json`，绑定全部输入指纹并在采集后 300 秒前失效。

流程为现场确认 → `PREPARE_NONCONTACT` → 实际 STOP 检查 → READY → `START_NONCONTACT`。当前配置采用 10 mm/s、20 mm/s²、固定姿态。`temporary_speed_override=1.0` 仅对已授权的本次路线生效：START 后停稳设置并回读，结束/异常先 STOP 后恢复原值；所有变更写入运行日志。没有该选项的既有调试会话不读取或写入倍率。标定与碰撞报告保持原始事实，授权不等同于自动碰撞或独立坐标精度验收。
