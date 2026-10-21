# 背部近景与稳定显示（2026-10-12）

本次针对实时窗口的点位/标签抖动和横向背部模型近景问题。保留 MediaPipe，新增 DWPose-L、RTMW-L 两种官方 ONNX 权重和背部专用入口。没有修改 UI、控制或公共协议，也没有把显示滤波用于机器人控制。

## 使用

```bash
/home/xpm/rehabilitation/zhenjiu/run_back.sh
```

该入口选择 DWPose-L、躯干模式、自适应显示滤波、输入顺时针旋转90度，默认显示背面，可按 `v` 切换正面。90度适用于此次实拍的“头在原画面左侧”摆放；不旋转显示图或深度图，推理关键点映射回原图后再采样深度。

- `v` / `V`：切换正面/背面，清空点位与深度滤波、标签布局，并恢复实时画面。请根据实际拍摄方向手动选择；程序不会自动判断正反面。背面保留大椎、命门、双侧肾俞；正面显示膻中、中脘、神阙、气海、关元、双侧天枢及有效XYZ。双肩和双髋需入镜；当前入口不显示面部细节穴位。启动即显示正面可加 `--view front`。显式指定 `--back-only` 时仍固定背面。

- `o`：模型输入方向每次顺时针转90度，试到头朝上的输入方向。相机原图坐标不变。
- `r`：鼠标框选一个人的区域，Enter确认；Esc取消后恢复整幅画面。不要把区域缩到只剩一块皮肤。
- `s`：保存标注图与坐标；空格暂停/继续；`q`、Esc或窗口关闭按钮退出。

```bash
# 同一背部入口试 RTMW-L（先退出当前窗口）
/home/xpm/rehabilitation/zhenjiu/run_back.sh --pose-backend rtmw-l --pose-rotation 0
# 人体竖直、头朝上的画面
/home/xpm/rehabilitation/zhenjiu/run_back.sh --pose-rotation 0
# 对照未经时间滤波的结果
/home/xpm/rehabilitation/zhenjiu/run_back.sh --stabilizer off
# 仍可使用原 MediaPipe 实时入口（默认增加显示滤波）
/home/xpm/rehabilitation/zhenjiu/run_live.sh
```

## 改动

`pose_backends.py` 直接对整幅图或指定区域执行单人 top-down 姿态推理，绕过“先找到整个人体框”的前置条件。两种模型输出 COCO-WholeBody 133点，本次仅将可明确对应的人体躯干/四肢关键点映射到原规则使用的索引；没有用眼/腕等点伪造缺失的面部细节或手指点。背景或多人场景不属于这一整图单人入口的可靠使用条件。

旋转和裁剪通过同一仿射链回到原始像素坐标。深度仍来自原彩色对齐深度，不交换XYZ轴，不把转正后像素直接传给原相机内参。

`stabilization.py` 增加随速度变化的低通滤波：静止时更强抑抖，持续运动时提高响应速度；连续三帧建立点，孤立大跳点隐藏，连贯新位置确认后重新建立。丢失的点不继续显示旧坐标；深度无效立即清空XYZ。标签固定已选的相对方向，避免逐帧避让导致文字左右跳跃。

默认像素跳变门限60px、三维跳变门限100mm、失效间隔0.3s，是显示滤波参数，不是人体定位精度/安全阈值。XYZ显示也经过时间滤波，因此并不严格代表一次原始曝光的测量；快照说明中已标记，不能作为机械臂运动依据。

## 实测结果与局限

同源输入：本地 D435i 采集60帧，对每3帧取一帧得到20个横向背部模型图像。固定光照/静态模型的短片段，没有独立解剖真值。CPU为 Ryzen 7 5800H，ONNX使用4线程；宿主 NVIDIA 驱动报告 NVML 版本不匹配，本次没有使用/修复GPU。

| 模型/输入方向 | 单次推理中位耗时 | 20帧中输出背部点的帧数 |
|---|---:|---:|
| MediaPipe，0度 | 15.48ms | 13 |
| DWPose-L，0度 | 31.77ms | 0 |
| DWPose-L，90度 | 31.85ms | 20 |
| RTMW-L，0度 | 55.17ms | 20 |
| RTMW-L，90度 | 57.26ms | 20 |

DWPose-L/90度的“命门”显示点相邻有效帧平均位移：原始0.722px，原固定EMA 0.350px，自适应滤波0.304px。自适应输出18/20帧，前2帧用于建立跟踪。不同方法的有效帧数不同；该指标反映此短样本显示稳定性，不能证明识别准确率、一般距离范围或所有姿态下的优劣。原模型的大幅不稳定不能只靠滤波补救。

90帧真实流验证：约20.5–22.0fps，稳定阶段4个背部显示点，4个有效深度坐标；打开桌面窗口后的日志约18–19fps。已保存一份经过稳定建立后的含XYZ快照。30项测试通过，覆盖原后台协议回归及新增滤波、边界和旋转映射测试。

证据位于 `reports/pose_upgrade/`：`comparison.json`、`jitter_comparison.json`、`benchmark.log`、`live_check.log`、`tests_host.log`。`tests.log`是受限沙箱中被中断的尝试，以完成的宿主测试 `tests_host.log` 为准。样本 `camera_sample.npz` 留在本地，用于复现 `benchmark_pose.py`。

**仍然存在的限制：** 本次复用的背部规则只有大椎、命门、双侧肾俞，且都是粗略比例估算。骨架关键点可能存在高置信错估，尤其是真人遮挡、假人、严重裁切和姿态异常；点出现或不抖不代表位置正确。双肩/双髋不可用时规则不能可靠推算，只拍局部皮肤的问题没有被这两个模型彻底解决。更近距离还可能出现D435i深度缺失，2D有点不等于XYZ有效。肩井、可信曝光时间、法向、手眼标定和人体精度验收仍未完成。

三进程后台 `run_worker.sh` 本轮继续使用原 MediaPipe 模型；新模型及显示滤波先在人工可视化入口验证，不自动改变控制端接收的数据语义。

## 模型来源与复现

- [DWPose官方仓库](https://github.com/IDEA-Research/DWPose)
- [RTMW官方模型配置与结果](https://github.com/open-mmlab/mmpose/blob/main/configs/wholebody_2d_keypoint/rtmpose/cocktail14/rtmw_cocktail14.md)
- [RTMLib模型列表与SimCC参考实现](https://github.com/Tau-J/rtmlib)

本次使用两者的256×192 ONNX导出，不是384×288版本。推理预处理遵循官方导出的BGR、均值/方差、1.25倍区域padding和SimCC split_ratio=2约定。推理端只新增ONNXRuntime，不装PyTorch/MMCV或另一套OpenCV。

权重路径及下载地址/SHA-256：`models/wholebody/manifest.json`。重新部署：

```bash
cd /home/xpm/rehabilitation/zhenjiu
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python models/wholebody/download_models.py
.venv/bin/python benchmark_pose.py
```

改动前实时入口备份位于 `backups/pose_upgrade/acupoint_locator_refactored.py`。
