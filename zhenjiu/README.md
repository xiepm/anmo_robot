> 当前完整安装与运行入口请先阅读[发布版说明](../docs/INSTALLATION.md)。本文保留模块开发和历史接口细节，涉及原工作站路径或阶段记录时，以根目录 README 和 docs 为准。

# 穴位实时定位（D435i）

当前入口为 `acupoint_locator_refactored.py`，使用 MediaPipe 人体姿态和比例规则推算穴位，并读取对齐后的 RealSense 深度。不是已经训练好的穴位专用 YOLO 模型。

## 本机启动

```bash
/home/xpm/rehabilitation/zhenjiu/run_live.sh
```

默认使用 D435i、640×480、30 FPS、正面模式。将摄像头固定，让头、肩、髋和需要定位的四肢完整入镜；过近、遮挡或运动模糊可能造成漏检、错标。窗口上的 `points` 是输出点数，`depth` 是有有效深度的点数，不表示定位准确度。

- `v`：正面/背面切换（按实际拍摄方向选择）。
- `s`：保存标注 JPG 和 JSON 到本目录 `acupoint_snapshots/`。
- 空格：暂停/继续。
- `q` 或 Esc：退出。

```bash
# 背面
/home/xpm/rehabilitation/zhenjiu/run_live.sh --view back
# 检测到穴位时自动保存一次快照
/home/xpm/rehabilitation/zhenjiu/run_live.sh --save-snapshot
# 无窗口、60 帧自检，结束后释放摄像头
/home/xpm/rehabilitation/zhenjiu/run_live.sh --no-display --max-frames 60
# 普通摄像头（无深度坐标）
/home/xpm/rehabilitation/zhenjiu/run_live.sh --source 0
```

不要同时运行多个占用 D435i 的程序。JSON 的 `x/y` 为处理画面像素坐标；`camera_point_mm` 为相机坐标系 XYZ 毫米值，不能直接作为机械臂坐标。无有效深度时 `coordinate_valid` 为 false。

## 环境

本机已创建 `.venv`（Python 3.12），`requirements.txt` 保存实测依赖版本。重新部署时使用 Python 3.12：

```bash
cd /home/xpm/rehabilitation/zhenjiu
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

模型位于 `models/pose_landmarker_full.task`，默认路径相对于脚本，不受启动目录影响。启动器使用 XWayland 显示窗口。

2026-09-30 本机验证：D435i 彩色和对齐深度 60 帧自检通过，约 30 FPS；实时窗口、规则推理、深度坐标和快照输出可运行。近距离局部入镜时观察到错标，尚未验证穴位定位精度。本程序用于演示，不替代专业定位。

## 三进程后台接入（2026-10-12）

新增无窗口入口 `./run_worker.sh`，默认 REAL 模式连接控制端 8766/vision；与本页的手动演示不能同时占用相机。协议、取消、回放、测试和能力限制见 [后台进程说明](README_worker.md)。

## 背部近景稳定版（2026-10-12）

运行 `./run_back.sh`：使用 DWPose-L、按当前横向摆放转正模型输入、稳定像素和XYZ显示并固定标签方向。也可切换 RTMW-L。命令、同源对比结果和仍需肩髋参考的限制见 [背部近景说明](README_back.md)。后台 worker 的模型选择保持原状。
