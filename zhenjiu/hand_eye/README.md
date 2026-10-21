# 手眼标定采集与核对

新设备配置入口见 [NEW_INSTALLATION.md](NEW_INSTALLATION.md)。本仓库不发布开发现场的实测矩阵、序列号、照片和已授权工作范围。模板不含可运行标定，测试数据都是合成数据。

## 确定参考系

生产坐标转换当前使用 `virtual_flange_with_joint6_zero`：对实际前五轴与数学上的第六轴零值调用厂家 FK，形成相机安装的腕部参考。该定义不表示实际第六轴应转至零位。只有实际安装符合此参考系时才能使用它。

`capture.py` 中的通用采集器记录的是 Base 下实际法兰位姿；`solve.py --reference flange` 默认处理这种数据。腕部参考的数据需要包含经过核对的 `T_base_reference` 与 `mount_reference`，再使用 `solve.py --reference mount`。不能把法兰求解结果改名后当作腕部标定，也不能直接套用其他设备的矩阵。

安装参考核对工具见 `check_mount.py`。相机固定在不同部位时，须相应调整和验证采集/参考定义。

## 棋盘与采集

当前 `capture.py` 的 `object_points()` 使用 9×6 内角点和固定棋盘尺寸；这些是工具的建模参数。使用前核对自己的棋盘尺寸与代码、`board.json` 一致，或按实测同步修改。棋盘必须平整固定，整批采集期间不能移动。

在项目根目录执行（先退出占用相机的视觉预览）：

```bash
cd zhenjiu
QT_QPA_PLATFORM=xcb .venv/bin/python hand_eye/capture.py \
  --output reports/hand_eye/new_session
```

采集器独占相机和项目 SDK 连接，只读取机器人反馈并调用数学接口；不使能、不自动移动机械臂。使用示教器调整观察姿态，每次停稳后再保存。

- `S`：保存求解样本，目标至少 20 组；完整内角点入镜，位置和倾角有变化，包含至少两个不同轴的转动。
- `V`：保存至少 5 组独立验证姿态，不参与求解。
- `F`：反转角点次序。保存前必须确认红圈始终对应同一个物理棋盘内角点，不能只凭图像左上角判定原点。
- `Q`、Esc 或关闭窗口：退出并释放连接。

采集器检查运动/故障、关节漂移、完整角点、重复姿态、采集间隔和重投影误差。拒绝原因必须处理后重采。这些是采样质量检查，不是机器人安全功能，也不是基座绝对精度验收。

## 求解与结果使用

```bash
# 通用法兰样本：
.venv/bin/python hand_eye/solve.py reports/hand_eye/new_session --reference flange
# 仅当样本已包含核对过的腕部参考时：
.venv/bin/python hand_eye/solve.py reports/hand_eye/new_session --reference mount
```

使用 `T_A_B` 表示将 B 系点转换到 A 系，矩阵平移单位为米。腕部参考的运行关系为：

```text
p_base_m = T_base_reference × T_reference_camera × [p_camera_mm / 1000, 1]
```

求解器检查样本来源、棋盘原点、旋转变化和数量，并输出候选结果及留出姿态残差。候选仍标为 `calibration_valid=false`，不会直接写入控制配置。还需要核对 RGB-D 深度与坐标链路，并根据现场用途完成独立精度验收。重投影误差、留出姿态残差和 SDK 计算一致性都不能单独证明 Base 绝对误差。

正式配置格式见 [calibration.example.json](calibration.example.json)，独立测量模板见 [absolute_accuracy_measurements.example.json](../../config/commissioning/absolute_accuracy_measurements.example.json)。控制进程用 `--hand-eye` 指定本地文件。

## 自动采集工具的边界

`auto_capture.py` 是针对已核对现场编写的辅助工具。它默认只做读取和预检，增加 `--execute` 才可能运动；所需本机工作范围、起点和授权记录不随仓库发布。新用户不能把原有默认值或历史文档当作本机通行证明。

自动采集拒绝已变化起点、异常状态、不一致 TCP/UCS、逆解分支大变或棋盘丢失；故障停止时不会自动续跑。先完成参考系、样本质量和本机几何核对，再决定是否使用该工具。
