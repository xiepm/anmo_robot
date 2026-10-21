# 新设备的标定配置

发布包不包含开发现场的标定矩阵、相机序列号、路线或工作范围。旧文件名和实测报告若在历史文档中出现，仅说明当时的调试过程，不代表适用于新设备。

1. 复制 [calibration.example.json](calibration.example.json) 为 `calibration.local.json`。该模板故意标为 `unconfigured`，不能用于预览坐标或机器人运动。
2. 核对相机如何安装。当前代码使用 `virtual_flange_with_joint6_zero` 腕部参考，由厂商 FK 计算前五轴实际值、第六轴数学值为零的参考位姿；这不表示要把真实第六轴转到零位。若相机安装方式不同，需要重新确认参考系和适配逻辑。
3. 依据 [手眼标定采集说明](README.md) 使用 `capture.py` 采集、`solve.py` 求解，并保存本次设备的采集证据。板尺寸、相机内参、序列号和标定矩阵必须来自本设备；不能复制历史数值。
4. 对结果完成核对后，将实测字段填入本地配置，通过控制进程的 `--hand-eye <文件路径>` 指定。具体启动命令以根目录安装说明为准。程序会检查参考系、单位、适用状态、相机身份和实时反馈，未配置完成时拒绝坐标投影。
5. 独立基座坐标精度验收的填写模板为 [absolute_accuracy_measurements.example.json](../../config/commissioning/absolute_accuracy_measurements.example.json)。求解重投影误差或测试通过不等于完成独立精度验收。

测试使用 `src/massage_controller/test/fixtures/` 中的合成旋转、平移和虚构相机序列号，仅验证计算和通信逻辑。不要将测试中的 `solved_and_holdout_evaluated` 标记用于未测量的真实设备。

相机/支架/基座位置变化后应重新核对标定。本地文件、现场照片与执行授权不会随 `git push` 上传。
