# 人体定位页实时穴位预览

当前实现将 RealSense 采集、姿态推理、实时网页预览及单次冻结拍照放在同一个视觉进程中。完整安装和操作见 [安装说明](../docs/INSTALLATION.md)、[操作说明](../docs/OPERATIONS.md)。不要同时运行多个程序占用相机。

## 启动与查看

在项目根目录运行 `./启动系统.sh`，它依次管理控制、视觉和网页服务。网页地址为 <http://127.0.0.1:5173/>。服务已经运行时重复执行启动脚本会复用已有进程。

只做视觉调试时，可在没有后台视觉服务占用相机的情况下运行：

```bash
./web/scripts/start_vision_preview.sh
# 有限帧自检，结束后释放相机：
./web/scripts/start_vision_preview.sh --max-frames 60
```

此入口使用 `zhenjiu/.venv` 的 RealSense、姿态模型和穴位渲染；当前预览默认使用 RTMW-L。Base 坐标由控制进程的 `ws://127.0.0.1:8766/coordinates` 提供，视觉端不创建机器人 SDK 连接、不发送运动命令。

## 画面、坐标与控制是不同状态

- `/position` 左侧显示实时画面、穴位候选点和坐标，可放大查看。
- `127.0.0.1:8767/frame.jpg` 返回内存中的最新 JPEG；超过 1.5 秒的画面返回 503。
- `127.0.0.1:8767/health` 返回画面新鲜度、帧号、相机状态及具体错误。
- 页面约每 200 ms 拉取画面，请求、响应读取和解码均有超时；失败后持续重试，切回页面立即更新。
- 右上角“设备已连接 · 实机”表示控制连接，不能代替相机连接、有效坐标或路线可执行状态。
- 有画面但 Base XYZ 为 `—` 时，检查控制服务、8766 端口、手眼标定、反馈有效性和深度。不能用旧坐标代替失效数据。

最新坐标缓存位于 `web/.runtime/base_coordinates_latest.json`，它是预览反馈，不是执行路线。图像在当前流程内存中使用；历史调试输出和现场照片不随源码发布。

## 拍照与手动确认

“开始扫描”请求新采集的一帧并冻结彩色图、深度和身份信息；识别后进入 `/confirm`。医生拖动点位，控制端对最终像素位置重新计算三维坐标。只有当前照片对应的最终确认坐标才参与后续路线检查。

短暂实时帧延迟时，空闲状态下可暂时保留同一张有效冻结照片和编辑草稿，但坐标确认/路线授权会撤销，恢复后须重新确认。相机真实断开、长时间失效、照片超时、机械臂移动或标定变化会使旧照片失效，必须重新拍摄。恢复连接不会自动重发扫描、确认或运动任务。

实时显示稳定化只影响显示，不能提高坐标有效性或替代路线检查。候选穴位采用姿态关键点与比例估算，必须人工核对落点。

## 相机重新插拔

视觉服务默认每秒检测相机。相机重新枚举后会重建采集句柄、彩色/深度管线、曝光时钟和跟踪状态，无需刷新网页。初始化和第一帧推理需要时间。

已识别设备但连续 3 次无法启动或获取有效帧时，程序先关闭旧管线，再尝试该相机的 SDK 设备复位。一次连续故障最多成功请求一次复位，成功采集后才进入下一轮，复位间隔至少 30 秒。该机制不复位整个 Hub 或电脑 USB 控制器。

若 `lsusb` 和 SDK 均看不到相机，软件无法创建可操作的相机句柄。检查供电、线缆、接口和 USB 枚举后，再查看 `/health`。`scripts/recover_camera_usb_port.py` 是历史特定拓扑的单端口恢复工具，默认只读，并检查 Hub 型号、设备号及伴随分支；不能作为新电脑的通用热插拔命令。使用前必须核对本机拓扑，不能照搬旧设备号或放宽检查。

## 曝光身份配置

控制与视觉共同读取 `config/commissioning/capture_timing.local.json`，或环境变量 `REHAB_CAPTURE_TIMING` 指定的绝对路径。模板为 [capture_timing.example.json](../config/commissioning/capture_timing.example.json)。模板默认未核对，不能直接用于运动采集。

当前实现只支持已实现的 D435i 手动 RGB 曝光模型、固件 `5.15.1.55`、SDK `2.58.4`、30 fps。必须核对本机序列号、曝光单位和时钟行为后登记；不能只把 `verified` 改为 true 作为测量证据。缺少配置时仍可看画面，但采集时间不会被标为有效。此身份核对不替代手眼标定、坐标精度或现场路线确认。

## 服务与离线回归

```bash
./启动系统.sh --status
systemctl --user status rehabilitation-vision-preview.service
systemctl --user status rehabilitation-control.service
# 在项目根目录，使用已安装项目依赖的 Python：
PYTHONPATH=web/tests:web/scripts:zhenjiu:src/massage_controller \
  zhenjiu/.venv/bin/python -m unittest \
  test_vision_preview test_camera_recovery test_live_locate_bridge test_capture_clock
cd web
npm run build
```

服务日志默认位于 `web/reports/services/`。控制与视觉分别运行，摄像头重连不会重启控制服务。`--port` 可更改预览端口，同时需修改前端 `VITE_VISION_PREVIEW_URL` 并重新构建。默认服务仅监听本机。
