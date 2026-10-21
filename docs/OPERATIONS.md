# 启动与操作说明

本文按当前同机部署说明日常使用和排错。先按 [安装说明](INSTALLATION.md) 准备新电脑；系统组成与数据有效性见 [架构说明](ARCHITECTURE.md)。以下命令除特别说明外，均在仓库根目录执行。

## 1. 启动前确认配置

新电脑不能直接沿用另一台机器的运行目录、标定和路线许可。开始前需确认下列项目：

| 项目 | 当前代码位置/参数 | 要确认什么 |
| --- | --- | --- |
| 机器人连接 | `src/massage_controller/config/real_robot.json`；`REHAB_ROBOT_CONFIG` / `--robot-config` | 实际 IP、端口、机型和 SDK 相对路径；SDK 也可用 `REHAB_SDK` / `--sdk` 指定 |
| 手眼标定 | `REHAB_HAND_EYE` / `--hand-eye` | 与本机相机安装、机器人参考点相符的标定文件；默认文件名含历史标定日期，不是通用模板 |
| 曝光时序 | `REHAB_CAPTURE_TIMING`；默认 `config/commissioning/capture_timing.local.json` | 控制与视觉使用同一份本机配置；从 example 建立后仍需验证，`verified=false` 不可当作已通过 |
| 无接触配置 | `config/commissioning/noncontact.json`；`REHAB_NONCONTACT_CONFIG` / `--noncontact-config` | 假人/工装范围、物理工具、固定姿态、速度、间隙、回程策略 |
| 视觉环境 | `web/scripts/start_vision_preview.sh` | 已安装的 `zhenjiu/.venv` 或 `REHAB_VISION_PYTHON`、模型和 RealSense 访问权限 |
| 控制环境 | `scripts/controller_runtime.sh` | 显式 `REHAB_CONTROL_PYTHON`；未指定时优先已有 `.runtime/jammy`，否则使用 `.venv-control` |
| 网页环境 | `web/package-lock.json`、`web/dist` | 安装依赖并完成本次源码构建 |

当前真实链路限定法兰 +Z 20 mm 的物理工具、固定当前姿态、无接触试运行。换工具或改变相机安装需要重新核对相关配置和标定。现场参数未完成时，仍可先运行离线测试；不要把旧的 `.authorization.json` 当作安装配置。

发布版本中的 `*.example.json` 是结构示例，不能替代实测配置。机器人配置可从 `src/massage_controller/config/real_robot.example.json` 建立本机文件；手眼 `calibration.example.json` 不包含可执行的已验收标定，须使用实际标定结果并通过控制入口的 `--hand-eye` 指定。

`REHAB_*` 环境变量需要在启动前设置；`启动系统.sh` 会传入新建的 systemd 服务。显式命令行参数优先于对应环境变量。已在运行的服务仍使用原环境，重复启动不会改变它；应在任务结束并停稳后正常停止，再重新启动。原隔离环境只映射项目目录，配置应放在项目内；使用原生 Python 环境时可指定其他绝对路径。

## 2. 日常启动与打开网页

登录 Linux 桌面后，用普通用户运行：

```bash
bash 启动系统.sh
```

打开 [本机 UI](http://127.0.0.1:5173/)。启动脚本调用 `scripts/start_system.sh`，依次创建三个用户级临时 systemd 服务：

| 服务 | 用途 | 当前自动重启策略 |
| --- | --- | --- |
| `rehabilitation-control.service` | 真实控制、坐标服务、执行状态 | 进程退出不自动重启；内部处理 SDK 重连 |
| `rehabilitation-vision-preview.service` | 相机、识别、实时图像 | 异常或正常退出均按服务策略重新启动，手动 stop 除外 |
| `rehabilitation-web.service` | 已构建的 UI | 失败后重启 |

重复运行脚本会复用已有服务，不会重启正在工作的控制器，也不会重新执行旧路线。可以关闭启动终端；服务由当前用户 systemd 管理。脚本不配置开机自动运行，重启电脑并登录后需再次启动。启动连接不会自动使能、复位或运动机械臂。

只查看状态：

```bash
bash 启动系统.sh --status
systemctl --user status rehabilitation-control.service rehabilitation-vision-preview.service rehabilitation-web.service --no-pager
```

模型首次加载可能比网页慢。网页可打开、顶部设备已连接、实时图像正常、当前照片/路线有效是不同状态，逐项查看页面提示。

## 3. 正常无接触操作流程

### 3.1 选择方案与拍照

1. 在首页进入方案选择，选择本次方案并确认就位。
2. 在人体定位页查看实时画面，使该方案所需部位与关键点可靠入镜。当前对象为假人/工装，机械臂应在既定观察位停稳。
3. 点击“开始扫描”。这是拍照并识别穴位，不执行机械臂扫掠运动。
4. 扫描成功进入确认页，显示本次冻结照片及方案点位。选中另一方案会清除旧照片和调整，需要重新拍照。

如果要重新取景或已经挪动假人，点击“重新扫描”回到实时画面，再拍新照片。不要在旧照片上继续确认已经移动的对象。

### 3.2 微调与确认最终坐标

1. 在照片上拖动点位微调；连线按方案顺序更新。
2. 完成编辑后点击点位确认按钮，等待控制端确认全部最终坐标。需要再次调整时进入“重新调整穴位”。
3. 等待本次深度场景与 SDK 路线预检完成，查看具体阻碍。最终下发的是该次确认之后、通过冻结深度重新计算的 Base 坐标对应的无接触目标。

点位编辑不是直接给机器人发送运动。机械臂拍照后移动、相机断开、标定变化或照片超过五分钟，都需要重拍。短时纯实时画面延迟可能保留照片和编辑，但撤销本次确认；恢复后重新确认即可。纯确认请求超时且照片仍有效时也可重试，无需故意放宽时间或状态检查。

### 3.3 检查停止、准备与启动

1. 按页面提示完成“检查停止通路”。该动作发送普通停止并核对反馈，不启动路线。
2. 预检通过后，根据页面状态点击“确认现场无障碍并准备路线”或“准备无接触路线”。前者明确记录当前假人/工装路线的现场确认与沿用现有标定，之后执行准备。
3. 等待“开始无接触运动”可用，核对当前方案、点位及路线提示，再显式点击并确认启动。
4. 执行中保持 UI 连接和实时视觉正常。当前配置按固定姿态经过各点上方，并沿原去程路线返回本次起点。
5. 以页面“已回到本次起始点位并停稳”为正常结束依据。随后可点“继续使用 · 重新扫描”进入下一轮。

“授权可用”只表示允许操作员确认当前路线，并不表示已启动。准备也不会自动 START。界面里的服务时长、温度和按摩力选项不代表当前无接触试运行会进行相应治疗；本次实际行为以无接触状态区显示为准。

### 3.4 停止与中断

需要中断时使用页面停止，等待停止确认。中途停止、UI 丢失、视觉健康失效或 SDK 异常不会自动返回起点，也不会重连后继续上次运动。故障处理后根据页面要求重新拍照、确认和准备。

页面普通停止不是硬件急停的替代品。如果实际运动异常且无法获得停止确认，按现场已建立的硬件停机流程处理；不要通过杀进程来验证机械臂已经停下。

## 4. 停止服务与更新代码

先在 UI 结束任务并确认机械臂停稳，再停止控制，最后停止视觉和网页：

```bash
systemctl --user stop rehabilitation-control.service
systemctl --user stop rehabilitation-vision-preview.service rehabilitation-web.service
```

服务未运行时重复 stop 无须启动它。不要用 `kill -9` 或删除 SDK 锁文件代替正常退出；SDK 所有权冲突应先找到并退出原进程。

UI 源码变化后，日常入口继续使用旧 `dist`，不会自动热更新。重新构建：

```bash
cd web
npm ci
npm run build
```

已有正确依赖时可跳过 `npm ci`。若从旧机器复制的依赖执行权限异常，应重新安装依赖。构建后在设备空闲时刷新页面；活动任务中刷新或关闭 UI 会触发连接失效停止。

修改 Python、SDK 配置或模型后，需先停止相关服务再更新，按启动入口重建服务并重新扫描。仅编辑文件不会更新已加载的 Python 进程。不要运行两套控制或两套相机采集程序来“刷新配置”。

## 5. 手动三终端运行

以下用于开发排错，**与 systemd 日常入口二选一**。确认三个相关服务已停止，且本机环境和配置已准备好。每个终端先进入仓库根目录。

终端一：

```bash
scripts/start_control.sh --backend real --standalone --enable-real-scan \
  --noncontact-config config/commissioning/noncontact.json
```

终端二：

```bash
web/scripts/start_vision_preview.sh
```

终端三：

```bash
scripts/start_web.sh
```

这与当前日常入口参数一致。新机器应通过前述环境变量或 `--robot-config`、`--hand-eye` 指定已经配置好的本机文件。`--standalone` 表示不发布 ROS 话题，并非仿真模式；新部署原生控制环境会默认使用它。

前台方式需要保持终端打开；正常停止任务并停稳后，用各自终端的 `Ctrl+C` 退出。`start_real_debug.py` 只负责控制和可选被动模型显示，不是完整网页/视觉启动入口；`start_hmi.sh`、`start_sim.sh` 属于仿真流程。`zhenjiu/run_live.sh`、`run_base.sh` 和独立 worker 会占用相机，不能与网页视觉进程同时使用同一设备。

UI 开发热更新可以在第三个终端改用：

```bash
cd web
npm run dev -- --host 127.0.0.1 --port 5173 --strictPort
```

这只切换网页提供方式，不替代控制和视觉进程。保持单个活动 UI 标签页，控制服务默认只接纳一个 UI WebSocket 连接。

## 6. 排错

先运行 `bash 启动系统.sh --status`，再查看对应日志和页面详细错误。不要只根据禁用按钮判断原因。

| 现象 | 含义与处理 |
| --- | --- |
| 浏览器 `ERR_CONNECTION_REFUSED` | 网页端口未监听，检查 `rehabilitation-web` 和 `web.log`；默认端口是 5173，旧的 5174 地址不一定有服务 |
| “下位机未连接” | 检查控制服务和机器人网络/反馈；同一电脑不代表控制进程已经运行或连接成功 |
| 有图像，但 Base 坐标为空 | 检查 8766 坐标服务、标定、曝光配对和关键点/深度有效性；不必先重启相机 |
| 预览显示“服务未连接” | 检查视觉服务及 8767；模型导入/加载失败看 `vision.log` |
| 相机 `RECONNECTING` 或取帧超时 | 先确认 USB 设备仍被系统识别、供电和接线正常，服务会自动重试；恢复画面后重新扫描 |
| 点位缺失 | 检查方案对应部位、肩髋参考点、遮挡和有效深度；有 RGB 像素不等于该点有可靠深度 |
| “当前状态不允许此操作” | 查看控制状态和是否仍有准备、确认、停止或执行任务；等待终态或正常停止，不伪造 IDLE |
| “没有可确认的本次照片坐标” | 照片已失效或控制端没有本次采集记录，回实时画面重拍；短延迟恢复但照片仍保留时重新确认 |
| “实时画面暂时延迟，本次确认已撤销” | 已保留有效冻结照片和调整；等实时健康恢复，再人工确认，旧许可不会自动恢复 |
| SDK 逆解失败/某穴位当前姿态不可达 | 查看具体点、段和姿态；需要现场调整观察位/工装后重新拍照，不能跳过 IK 或偷偷移动最终点 |
| 深度未测清区域重叠/三维核对未通过 | 表示证据不足，不必然是实体障碍；调整视角、消除遮挡或无效深度后重拍 |
| 准备或启动按钮禁用 | 查看最终点确认、实时健康、停止通路、SDK 预检、深度核对和现场确认各项提示；设备已连接只是其中一项 |
| 运动比指令速度慢很多 | 检查本次日志中的全局倍率读取/应用/恢复结果；当前显式临时倍率流程负责恢复原值，不要并行手动修改 |
| 路线结束但没有“回到起点并停稳” | 不能按正常归位处理，查看 `returned_to_start`、STOP 和倍率恢复错误 |
| 端口占用或 SDK 已被占用 | 检查是否已运行日常服务、旧前台控制或厂商程序；复用或正常退出原所有者 |

只读检查示例：

```bash
ss -ltnp | rg ':(5173|8765|8766|8767)\b'
curl --max-time 3 http://127.0.0.1:8767/health
lsusb
tail -n 100 web/reports/services/control.log
tail -n 100 web/reports/services/vision.log
tail -n 100 web/reports/services/web.log
```

日常入口把标准输出写入上述文件。手动 `systemd-run` 建立的其他服务可能写入 journal，可用 `journalctl --user -u 服务名 --since today` 查阅。日志/运行目录可能包含机器地址、坐标和采集信息，分享前应脱敏。

相机未出现在 `lsusb` 时，重启网页不能修复 USB 枚举。仓库里的 `recover_camera_usb_port.py` 针对原现场特定 Hub 拓扑编写，不能直接作为新机器的通用恢复命令；不要照抄旧设备号执行端口复位。

## 7. 离线回归与构建

正常单元测试使用假 SDK/假相机或本机临时端口，不需要启动真实三进程。控制环境已安装时，在仓库根目录运行：

```bash
scripts/controller_runtime.sh python3 -m unittest discover \
  -s src/massage_controller/test -p 'test_*.py' -v
zhenjiu/.venv/bin/python -m unittest discover -s zhenjiu/tests -p 'test_*.py' -v
zhenjiu/.venv/bin/python -m unittest discover -s web/tests -p 'test_*.py' -v
```

UI 测试与构建在 `web/` 目录运行：

```bash
node --test tests/*.test.mjs
npm run build
```

`npm run build` 同时运行 TypeScript/Vue 类型检查和 Vite 构建。没有名为 `npm test` 的当前默认脚本。

`integrated_real_smoke.py`、`robot_ros_readonly_smoke.py`、`read_robot.sh`、带 `--execute` 的 waypoint 命令会涉及实际设备或额外 SDK 所有者，不属于上述离线回归，不能在已有控制服务旁自动运行。测试通过也不自动授权真实运动。

## 8. 配置、记录与迁移

| 数据 | 位置/来源 | 迁移原则 |
| --- | --- | --- |
| 公共协议 | `config/rehab_protocol/` | UI、控制、视觉同步更新；保持 schema 与测试一致 |
| 本机机器人配置 | `src/massage_controller/config/real_robot.json` 或显式文件 | 根据新设备重新核对网络、型号、SDK 与参考系 |
| 标定与工具 | `--hand-eye`、无接触配置 | 与实际安装绑定，不能从旧电脑直接推断有效 |
| 最新预览坐标 | `web/.runtime/base_coordinates_latest.json` | 临时输出，不导入为执行路线 |
| 路线预检报告 | `web/reports/step3/routes/` | 本次照片/版本的诊断证据，不是通用放行文件 |
| 执行记录 | `src/massage_controller/reports/noncontact_runs/` | 记录命令、反馈、停止和倍率恢复；按现场数据规则保存 |
| 单次现场确认 | 无接触配置旁的 `.authorization.json` | 限当次照片与路线，不能跨机器/重启/重拍复用 |
| 环境与构建 | `.runtime/`、`.venv-control/`、`zhenjiu/.venv/`、`web/node_modules/`、`web/dist/`、ROS build/install/log | 在目标机器重新安装/构建，不当作源码发布 |

运行时会生成坐标、路线和可能的诊断图像/深度数据。不要仅依据界面“不保存影像”等展示文案推断整个调试系统没有落盘记录；对外发布仓库或日志前，应独立检查生成目录、快照和报告。日常恢复应从新鲜反馈、实时画面和新照片开始，不从历史日志恢复执行任务。
