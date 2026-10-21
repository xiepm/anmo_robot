#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
穴位关键点实时识别（YOLOv8-pose 训练模型推理）
================================================
加载训练好的 YOLOv8-pose 模型，用摄像头实时检测穴位位置和名称。

用法：
  # 用自己训练的模型（推荐）
  python acupoint_yolo_live.py --model runs/pose/acupoint_train/weights/best.pt --data-yaml dmd_bak_yolo/data.yaml

  # 用官方预训练 yolov8n-pose 测试管线（显示的是 COCO 17 个人体关键点，不是穴位）
  python acupoint_yolo_live.py --model yolov8n-pose.pt

  # 指定摄像头 / 视频文件
  python acupoint_yolo_live.py --model best.pt --data-yaml data.yaml --source 1
  python acupoint_yolo_live.py --model best.pt --data-yaml data.yaml --source video.mp4 --no-display

快捷键：q 退出，s 保存快照，空格 暂停，t 切换显示低置信点
"""
import argparse
import glob
import os
import sys
import time

import numpy as np

try:
    import cv2
except ImportError:
    sys.exit("缺少 opencv-python，请先运行: pip install opencv-python")

try:
    from ultralytics import YOLO
except ImportError:
    sys.exit("缺少 ultralytics，请先运行: pip install ultralytics")

from PIL import Image, ImageDraw, ImageFont


# COCO 17 关键点名称（用官方预训练模型时的默认名称）
COCO_KEYPOINT_NAMES = [
    "nose", "left_eye", "right_eye", "left_ear", "right_ear",
    "left_shoulder", "right_shoulder", "left_elbow", "right_elbow",
    "left_wrist", "right_wrist", "left_hip", "right_hip",
    "left_knee", "right_knee", "left_ankle", "right_ankle",
]


def find_cjk_font():
    for c in [
        "C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf",
        "C:/Windows/Fonts/simsun.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/arphic/uming.ttc",
    ]:
        if os.path.exists(c):
            return c
    for h in glob.glob("/usr/share/fonts/**/*CJK*", recursive=True):
        if h.endswith((".ttc", ".otf", ".ttf")):
            return h
    return None


def load_keypoint_names(data_yaml_path):
    """从 data.yaml 或 acupoint_names.txt 读取关键点名称。
    YOLOv8-pose 的 data.yaml 不直接存关键点名，所以优先读同目录下的 acupoint_names.txt。
    """
    if not data_yaml_path:
        return COCO_KEYPOINT_NAMES
    yaml_dir = os.path.dirname(os.path.abspath(data_yaml_path))
    names_file = os.path.join(yaml_dir, "acupoint_names.txt")
    if os.path.exists(names_file):
        names = []
        for line in open(names_file, encoding="utf-8"):
            parts = line.strip().split("\t")
            if len(parts) >= 2:
                names.append(parts[1])
            elif parts[0]:
                names.append(parts[0])
        if names:
            return names
    # 尝试从 data.yaml 解析 kpt_shape 数量，返回占位名
    try:
        import re
        txt = open(data_yaml_path, encoding="utf-8").read()
        m = re.search(r"kpt_shape:\s*\[(\d+)", txt)
        if m:
            n = int(m.group(1))
            return [f"acupoint_{i}" for i in range(n)]
    except Exception:
        pass
    return COCO_KEYPOINT_NAMES


def render_frame(img_rgb, persons, kpt_names, font_path, conf_thresh=0.3,
                 show_low=False, font_size=16):
    """把检测到的穴位点绘制到帧上。persons: list of dict {xy, conf}"""
    pil = Image.fromarray(img_rgb)
    draw = ImageDraw.Draw(pil)
    try:
        base_font = ImageFont.truetype(font_path, font_size) if font_path else ImageFont.load_default()
        font_h = font_size + 2 if font_path else 12
    except Exception:
        base_font = ImageFont.load_default()
        font_h = 12

    used_boxes = []

    def box_hit(bx, by, bw, bh):
        for ux, uy, uw, uh in used_boxes:
            if bx < ux + uw and bx + bw > ux and by < uy + uh and by + bh > uy:
                return True
        return False

    def place_label(x, y, label):
        w = draw.textlength(label, font=base_font)
        h = font_h
        cands = [
            (x + 8, y - h - 4), (x - w - 8, y - h - 4),
            (x + 8, y + 8), (x - w - 8, y + 8),
            (x + 22, y - h - 14), (x - w - 22, y - h - 14),
        ]
        bx = by = None
        for cx, cy in cands:
            cx = max(0, min(cx, pil.width - w - 2))
            cy = max(0, min(cy, pil.height - h - 2))
            if not box_hit(cx, cy, w, h):
                bx, by = cx, cy
                break
        if bx is None:
            bx, by = cands[0]
            bx = max(0, min(bx, pil.width - w - 2))
            by = max(0, min(by, pil.height - h - 2))
        draw.text((bx, by), label, fill=(255, 255, 255), font=base_font,
                  stroke_width=2, stroke_fill=(0, 0, 0))
        used_boxes.append((bx, by, w, h))
        lx = max(bx, min(x, bx + w))
        ly = max(by, min(y, by + h))
        if (lx, ly) != (x, y):
            draw.line([(x, y), (lx, ly)], fill=(255, 255, 255), width=1)

    n_total = 0
    for person in persons:
        xy = person["xy"]  # (N, 2)
        conf = person["conf"]  # (N,)
        # 按置信度从高到低绘制
        order = np.argsort(-conf)
        for idx in order:
            c = float(conf[idx])
            if c < conf_thresh:
                continue
            if not show_low and c < 0.5:
                continue
            x, y = float(xy[idx][0]), float(xy[idx][1])
            if x <= 0 and y <= 0:
                continue
            name = kpt_names[idx] if idx < len(kpt_names) else f"kpt{idx}"
            # 颜色：高置信绿，中蓝，低灰
            if c >= 0.7:
                color = (22, 163, 74)
            elif c >= 0.5:
                color = (29, 78, 216)
            else:
                color = (120, 113, 108)
            r = 5 if c >= 0.5 else 4
            xi, yi = int(round(x)), int(round(y))
            draw.ellipse([xi - r, yi - r, xi + r, yi + r], fill=color, outline="white", width=2)
            label = name if font_path else f"k{idx}"
            place_label(xi, yi, label)
            n_total += 1

    return np.array(pil), n_total


def main():
    ap = argparse.ArgumentParser(description="YOLOv8-pose 穴位实时识别")
    ap.add_argument("--model", required=True, help="训练好的 .pt 模型路径，或 yolov8n-pose.pt")
    ap.add_argument("--data-yaml", default="", help="数据集 data.yaml（用于读取穴位名称）")
    ap.add_argument("--source", default="0", help="摄像头编号或视频文件路径")
    ap.add_argument("--conf", type=float, default=0.3, help="关键点置信度阈值")
    ap.add_argument("--max-width", type=int, default=960, help="处理帧最大宽度")
    ap.add_argument("--win-scale", type=float, default=1.5, help="显示窗口放大倍数")
    ap.add_argument("--no-display", action="store_true", help="不弹窗")
    ap.add_argument("--output-video", default="", help="保存标注视频路径")
    ap.add_argument("--save-snapshot", action="store_true", help="自动保存第一帧快照")
    args = ap.parse_args()

    print(f"加载模型: {args.model}")
    model = YOLO(args.model)
    kpt_names = load_keypoint_names(args.data_yaml)
    print(f"关键点数量: {len(kpt_names)}（前5个: {kpt_names[:5]}）")
    font_path = find_cjk_font()
    print(f"中文字体: {font_path or '未找到（将显示编号）'}")

    src = int(args.source) if args.source.isdigit() else args.source
    cap = cv2.VideoCapture(src)
    if not cap.isOpened():
        sys.exit(f"无法打开视频源: {args.source}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    print(f"视频源: {args.source}  尺寸 {w}x{h}  fps≈{fps:.1f}")

    writer = None
    if args.output_video:
        scale = args.max_width / w if w > args.max_width else 1.0
        writer = cv2.VideoWriter(args.output_video, cv2.VideoWriter_fourcc(*"mp4v"),
                                 fps, (int(w * scale), int(h * scale)))

    show_low = False
    paused = False
    frame_idx = 0
    t_last = time.time()
    snap_saved = False
    is_camera = isinstance(src, int)

    while True:
        if not paused:
            ok, frame = cap.read()
            if not ok:
                print("视频结束")
                break
            fh, fw = frame.shape[:2]
            if fw > args.max_width:
                scale = args.max_width / fw
                frame = cv2.resize(frame, (args.max_width, int(fh * scale)), interpolation=cv2.INTER_AREA)
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

            # YOLO 推理
            results = model.predict(rgb, conf=0.25, verbose=False)[0]
            persons = []
            if results.keypoints is not None and len(results.keypoints) > 0:
                for kps in results.keypoints:
                    persons.append({
                        "xy": kps.xy.cpu().numpy()[0],  # (N, 2)
                        "conf": kps.conf.cpu().numpy()[0] if kps.conf is not None else np.ones(len(kps.xy[0])),
                    })

            annotated, n_pts = render_frame(rgb, persons, kpt_names, font_path,
                                            conf_thresh=args.conf, show_low=show_low)
            fps_now = 1.0 / max(1e-6, time.time() - t_last)
            t_last = time.time()
            bgr = cv2.cvtColor(annotated, cv2.COLOR_RGB2BGR)
            overlay = f"fps:{fps_now:4.1f}  points:{n_pts}  [s]save [t]low_conf [space]pause [q]quit"
            cv2.putText(bgr, overlay, (10, 24), cv2.FONT_HERSHEY_SIMPLEX,
                        0.55, (0, 255, 0), 2, cv2.LINE_AA)

            if args.save_snapshot and n_pts > 0 and not snap_saved:
                cv2.imwrite("acupoint_yolo_snapshot.jpg", bgr)
                print("已保存快照: acupoint_yolo_snapshot.jpg")
                snap_saved = True

            if writer is not None:
                writer.write(bgr)

            if not args.no_display:
                scale = args.win_scale if args.win_scale > 1.0 else 1.0
                show = bgr
                if scale > 1.0:
                    sw = min(int(bgr.shape[1] * scale), 1200)
                    sh = int(bgr.shape[0] * sw / bgr.shape[1])
                    show = cv2.resize(bgr, (sw, sh), interpolation=cv2.INTER_LINEAR)
                cv2.imshow("Acupoint YOLO - 穴位实时识别", show)
                key = cv2.waitKey(1) & 0xFF
                if key in (ord("q"), 27):
                    break
                elif key == ord("s"):
                    fn = f"acupoint_yolo_snapshot_{int(time.time())}.jpg"
                    cv2.imwrite(fn, bgr)
                    print(f"已保存快照: {fn}")
                elif key == ord("t"):
                    show_low = not show_low
                    print(f"低置信点显示: {'开' if show_low else '关'}")
                elif key == ord(" "):
                    paused = not paused
                    print("已暂停" if paused else "继续")
            else:
                frame_idx += 1
                if frame_idx % max(1, int(fps)) == 0:
                    print(f"帧 {frame_idx}  fps={fps_now:.1f}  穴位 {n_pts} 个")

    cap.release()
    if writer is not None:
        writer.release()
    cv2.destroyAllWindows()
    print("已退出")


if __name__ == "__main__":
    main()
#（注：内容由AI生成）
