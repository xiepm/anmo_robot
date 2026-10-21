#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
穴位关键点模型训练（YOLOv8-pose）
==================================
基于 Ultralytics YOLOv8-pose，在 DMD-BAK（或其他 YOLO pose 格式数据集）上微调。

前置：
  pip install ultralytics
  （已自动安装 torch；如果你已有 torch 环境，ultralytics 会复用）

用法：
  # 从头微调（推荐，用预训练 yolov8n-pose 初始化）
  python train_acupoint_yolo.py --data dmd_bak_yolo/data.yaml

  # 指定模型大小和轮数
  python train_acupoint_yolo.py --data dmd_bak_yolo/data.yaml --model yolov8s-pose.pt --epochs 150 --imgsz 640

  # 断点续训
  python train_acupoint_yolo.py --resume runs/pose/train/weights/last.pt

训练完成后，模型在 runs/pose/train*/weights/best.pt
"""
import argparse
import os
import sys


def main():
    ap = argparse.ArgumentParser(description="YOLOv8-pose 穴位关键点训练")
    ap.add_argument("--data", default="dmd_bak_yolo/data.yaml", help="数据集 data.yaml 路径")
    ap.add_argument("--model", default="yolov8n-pose.pt",
                    help="预训练模型：yolov8n-pose.pt(快) / yolov8s-pose.pt / yolov8m-pose.pt(准)")
    ap.add_argument("--epochs", type=int, default=100, help="训练轮数")
    ap.add_argument("--imgsz", type=int, default=640, help="输入图像尺寸")
    ap.add_argument("--batch", type=int, default=16, help="batch size（显存小就调小，如 8）")
    ap.add_argument("--device", default="", help="训练设备：0=GPU, cpu=CPU，留空自动选择")
    ap.add_argument("--project", default="runs/pose", help="输出项目目录")
    ap.add_argument("--name", default="acupoint_train", help="本次训练名称")
    ap.add_argument("--resume", default="", help="断点续训：传入 last.pt 路径")
    ap.add_argument("--workers", type=int, default=4, help="数据加载线程数")
    args = ap.parse_args()

    try:
        from ultralytics import YOLO
    except ImportError:
        sys.exit("缺少 ultralytics，请先运行: pip install ultralytics")

    if args.resume:
        # 断点续训
        if not os.path.exists(args.resume):
            sys.exit(f"找不到续训模型: {args.resume}")
        model = YOLO(args.resume)
        model.train(resume=True)
        return

    if not os.path.exists(args.data):
        sys.exit(f"找不到数据集配置: {args.data}\n请先运行 dmd_bak_to_yolo.py 转换数据。")

    print(f"加载预训练模型: {args.model}")
    model = YOLO(args.model)

    # 检查 GPU
    import torch
    device = args.device if args.device else ("0" if torch.cuda.is_available() else "cpu")
    if device == "cpu":
        print("警告: 未检测到 GPU，将用 CPU 训练（会很慢，建议用有 NVIDIA 显卡的机器）")
    else:
        print(f"使用 GPU: {torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'unknown'}")

    print(f"\n开始训练：{args.epochs} 轮, imgsz={args.imgsz}, batch={args.batch}")
    print(f"数据集: {args.data}")
    print("-" * 60)

    results = model.train(
        data=args.data,
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        device=device,
        project=args.project,
        name=args.name,
        workers=args.workers,
        pretrained=True,
        optimizer="auto",
        verbose=True,
    )

    print("\n" + "=" * 60)
    print("训练完成！")
    print(f"最佳模型: {results.save_dir / 'weights' / 'best.pt'}")
    print(f"最后模型: {results.save_dir / 'weights' / 'last.pt'}")
    print("\n摄像头实时推理：")
    print(f"  python acupoint_yolo_live.py --model {results.save_dir / 'weights' / 'best.pt'} --data-yaml {args.data}")


if __name__ == "__main__":
    main()
#（注：内容由AI生成）
