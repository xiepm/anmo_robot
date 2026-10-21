#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DMD-BAK 数据集 → YOLOv8-pose 格式转换器
==========================================
把 DMD-BAK（背部穴位关键点数据集，labelme 格式）转换成 YOLO 姿态估计训练格式。

DMD-BAK 下载：https://www.kaggle.com/datasets/chunzheye/dmd-bak
下载后解压，目录结构应为：
  dmd_bak_raw/
    images/          (所有 jpg)
    labelme_jsons/   (所有 json，与图片同名)

用法：
  python dmd_bak_to_yolo.py --src dmd_bak_raw --out dmd_bak_yolo
  python dmd_bak_to_yolo.py --src dmd_bak_raw --out dmd_bak_yolo --val-ratio 0.2

输出：
  dmd_bak_yolo/
    data.yaml              # YOLO 训练配置（含穴位名称列表）
    images/train/  images/val/
    labels/train/  labels/val/
    acupoint_names.txt     # 穴位名→索引对照表
"""
import argparse
import json
import os
import random
import shutil
from pathlib import Path


def collect_acupoint_names(json_dir):
    """扫描所有 labelme JSON，收集出现过的穴位名（按首次出现顺序，稳定排序）"""
    names = []
    name_set = set()
    for jf in sorted(Path(json_dir).glob("*.json")):
        try:
            data = json.loads(jf.read_text(encoding="utf-8"))
        except Exception:
            continue
        for shp in data.get("shapes", []):
            if shp.get("shape_type") != "point":
                continue
            lbl = str(shp.get("label", "")).strip()
            if lbl and lbl not in name_set:
                name_set.add(lbl)
                names.append(lbl)
    return names


def labelme_to_yolo_label(json_path, name_to_idx, img_w, img_h):
    """把单张 labelme JSON 转成 YOLO pose 标注行。
    返回 (bbox_str, keypoints_str) 或 None（无有效标注时）。
    YOLO pose 格式：class cx cy w h kx1 ky1 v1 kx2 ky2 v2 ...
    坐标均归一化到 [0,1]；v=0 未标注，v=2 已标注可见。
    """
    try:
        data = json.loads(Path(json_path).read_text(encoding="utf-8"))
    except Exception:
        return None

    n_kpt = len(name_to_idx)
    kpts = [[0.0, 0.0, 0] for _ in range(n_kpt)]
    xs, ys = [], []
    for shp in data.get("shapes", []):
        if shp.get("shape_type") != "point":
            continue
        lbl = str(shp.get("label", "")).strip()
        if lbl not in name_to_idx:
            continue
        pts = shp.get("points", [])
        if not pts:
            continue
        x, y = float(pts[0][0]), float(pts[0][1])
        idx = name_to_idx[lbl]
        kpts[idx] = [x / img_w, y / img_h, 2]
        xs.append(x)
        ys.append(y)

    if not xs:
        return None

    # 由所有关键点推导人体 bbox（加 12% 边距）
    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)
    pad_x = (x_max - x_min) * 0.12 + 5
    pad_y = (y_max - y_min) * 0.12 + 5
    x_min = max(0, x_min - pad_x)
    y_min = max(0, y_min - pad_y)
    x_max = min(img_w, x_max + pad_x)
    y_max = min(img_h, y_max + pad_y)

    cx = (x_min + x_max) / 2.0 / img_w
    cy = (y_min + y_max) / 2.0 / img_h
    bw = (x_max - x_min) / img_w
    bh = (y_max - y_min) / img_h

    bbox_str = f"0 {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}"
    kpt_str = " ".join(f"{kx:.6f} {ky:.6f} {v}" for kx, ky, v in kpts)
    return f"{bbox_str} {kpt_str}"


def main():
    ap = argparse.ArgumentParser(description="DMD-BAK → YOLOv8-pose 格式转换")
    ap.add_argument("--src", required=True, help="DMD-BAK 解压目录（含 images/ 和 labelme_jsons/）")
    ap.add_argument("--out", default="dmd_bak_yolo", help="输出目录")
    ap.add_argument("--val-ratio", type=float, default=0.2, help="验证集比例")
    ap.add_argument("--seed", type=int, default=42, help="随机种子")
    args = ap.parse_args()

    src = Path(args.src)
    img_dir = src / "images"
    json_dir = src / "labelme_jsons"
    if not img_dir.is_dir() or not json_dir.is_dir():
        # 兼容 Kaggle 解压后的子目录结构
        for sub in src.iterdir():
            if sub.is_dir() and (sub / "images").is_dir():
                img_dir = sub / "images"
                json_dir = sub / "labelme_jsons"
                break
    if not img_dir.is_dir():
        raise SystemExit(f"找不到图片目录: {img_dir}（请确认 --src 指向解压后的 DMD-BAK 根目录）")
    if not json_dir.is_dir():
        raise SystemExit(f"找不到标注目录: {json_dir}")

    print("扫描穴位名称……")
    names = collect_acupoint_names(json_dir)
    if not names:
        raise SystemExit("未在 labelme JSON 中找到任何 point 类型标注，请检查数据格式。")
    name_to_idx = {n: i for i, n in enumerate(names)}
    print(f"共发现 {len(names)} 个穴位类别：{names[:10]}{'...' if len(names) > 10 else ''}")

    # 配对图片与标注
    pairs = []
    for jf in sorted(json_dir.glob("*.json")):
        stem = jf.stem
        # 尝试常见图片扩展名
        img_path = None
        for ext in (".jpg", ".jpeg", ".png", ".bmp"):
            cand = img_dir / (stem + ext)
            if cand.exists():
                img_path = cand
                break
        if img_path is None:
            # 读 JSON 里的 imagePath
            try:
                data = json.loads(jf.read_text(encoding="utf-8"))
                ip = data.get("imagePath", "")
                if ip:
                    cand = img_dir / Path(ip).name
                    if cand.exists():
                        img_path = cand
            except Exception:
                pass
        if img_path is None:
            print(f"  警告: 找不到 {stem} 对应的图片，跳过")
            continue
        pairs.append((img_path, jf))

    if not pairs:
        raise SystemExit("没有找到任何 图片+标注 配对，请检查目录结构。")
    print(f"共 {len(pairs)} 张有效图片")

    # 划分训练/验证
    random.seed(args.seed)
    random.shuffle(pairs)
    n_val = max(1, int(len(pairs) * args.val_ratio))
    val_pairs = pairs[:n_val]
    train_pairs = pairs[n_val:]
    print(f"训练集 {len(train_pairs)} 张，验证集 {len(val_pairs)} 张")

    # 创建输出目录
    out = Path(args.out)
    for split in ("train", "val"):
        (out / "images" / split).mkdir(parents=True, exist_ok=True)
        (out / "labels" / split).mkdir(parents=True, exist_ok=True)

    from PIL import Image
    converted = 0
    for split, plist in (("train", train_pairs), ("val", val_pairs)):
        for img_path, json_path in plist:
            try:
                with Image.open(img_path) as im:
                    img_w, img_h = im.size
            except Exception:
                print(f"  警告: 无法读取图片 {img_path.name}，跳过")
                continue
            label_line = labelme_to_yolo_label(json_path, name_to_idx, img_w, img_h)
            if label_line is None:
                continue
            # 复制图片 + 写标签
            dst_img = out / "images" / split / img_path.name
            dst_lbl = out / "labels" / split / (img_path.stem + ".txt")
            shutil.copy2(img_path, dst_img)
            dst_lbl.write_text(label_line + "\n", encoding="utf-8")
            converted += 1

    print(f"转换完成：{converted} 张图片已写入 {out}/")

    # 写穴位名对照表
    (out / "acupoint_names.txt").write_text(
        "\n".join(f"{i}\t{n}" for i, n in enumerate(names)) + "\n", encoding="utf-8")

    # 写 data.yaml
    yaml_content = f"""# DMD-BAK 背部穴位 YOLOv8-pose 数据集配置
path: {out.resolve()}
train: images/train
val: images/val

nc: 1
names: ["person"]

# 关键点配置
kpt_shape: [{len(names)}, 3]  # [穴位数量, 坐标维度(x,y,visibility)]
"""
    (out / "data.yaml").write_text(yaml_content, encoding="utf-8")
    print(f"data.yaml 已生成: {out / 'data.yaml'}")
    print(f"穴位名对照表: {out / 'acupoint_names.txt'}")
    print("\n下一步训练：python train_acupoint_yolo.py --data {}/data.yaml".format(out))


if __name__ == "__main__":
    main()
#（注：内容由AI生成）
