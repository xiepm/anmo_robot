# 路线 B：穴位关键点微调（YOLOv8-pose）

用真实穴位标注数据集微调 YOLOv8-pose 模型，精度高于路线 A 的比例法，支持摄像头实时识别。

## 数据集下载

| 数据集 | 部位 | 规模 | 下载链接 | 格式 |
|--------|------|------|----------|------|
| **DMD-BAK**（推荐） | 背部 | 2691 张，约 50 类穴位 | https://www.kaggle.com/datasets/chunzheye/dmd-bak | labelme JSON |
| AcuSim | 头颈 | 174 穴，合成多视角 | https://github.com/ZoeApokalypse/acuSim | JSON |
| Ear210 | 耳部 | 210 张 | https://www.kaggle.com/datasets/chg0901/ear210-dataset-coco | COCO |
| NBSDC 人体穴位识别与定位数据集 | 腹/上肢/下肢 | 24 穴，RGB+深度 | http://www.nbsdc.cn（注册后下载） | 自定义 |

> DMD-BAK 的 GitHub 仓库含官方转换脚本：https://github.com/Ye-ChunZhe/DMDBAK
> 本方案自带转换器 `dmd_bak_to_yolo.py`，无需运行官方脚本。

## 安装

```bash
pip install ultralytics opencv-python pillow numpy
# ultralytics 会自动安装 torch（如有 NVIDIA GPU，建议先装 CUDA 版 torch）
```

## 三步工作流

### 第 1 步：转换数据

下载 DMD-BAK 并解压，假设解压到 `dmd_bak_raw/`（内含 `images/` 和 `labelme_jsons/`）：

```bash
python dmd_bak_to_yolo.py --src dmd_bak_raw --out dmd_bak_yolo
```

输出 `dmd_bak_yolo/`，含：
- `data.yaml` — YOLO 训练配置
- `acupoint_names.txt` — 穴位名→索引对照表
- `images/train/`, `images/val/` — 图片
- `labels/train/`, `labels/val/` — YOLO pose 标注

### 第 2 步：训练

```bash
# 快速训练（nano 模型，CPU 也能跑但慢）
python train_acupoint_yolo.py --data dmd_bak_yolo/data.yaml --epochs 100

# 精度优先（small 模型，建议 GPU）
python train_acupoint_yolo.py --data dmd_bak_yolo/data.yaml --model yolov8s-pose.pt --epochs 150 --batch 16
```

训练完成后模型在 `runs/pose/acupoint_train/weights/best.pt`。

> **GPU 要求**：DMD-BAK 2691 张图，yolov8n-pose 100 轮，单张 RTX 3060 约 30-60 分钟；CPU 可能需要 10+ 小时。建议用有 NVIDIA 显卡的机器或 Colab。

### 第 3 步：摄像头实时识别

```bash
python acupoint_yolo_live.py --model runs/pose/acupoint_train/weights/best.pt --data-yaml dmd_bak_yolo/data.yaml
```

快捷键：`q` 退出，`s` 保存快照，`空格` 暂停，`t` 切换低置信点显示。

## 文件说明

| 文件 | 用途 |
|------|------|
| `dmd_bak_to_yolo.py` | DMD-BAK labelme → YOLO pose 格式转换 |
| `train_acupoint_yolo.py` | YOLOv8-pose 训练（支持断点续训） |
| `acupoint_yolo_live.py` | 摄像头/视频实时推理（中文标签+引导线） |

## 与路线 A 的区别

- **路线 A**（acupoint_locator_all.py）：姿态估计 + 骨度分寸比例法，0 标注成本，全身约 46 穴，精度中等，无需训练。
- **路线 B**（本方案）：在真实穴位标注上微调模型，精度高，但只覆盖数据集包含的部位（DMD-BAK 只有背部），需要 GPU 训练。

建议：先跑路线 A 快速验证，再用路线 B 对重点部位（如背部）做高精度微调。
#（注：内容由AI生成）
