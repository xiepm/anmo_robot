#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
人体穴位定位 · 实时版（摄像头 / 视频文件）
=============================================
用 OpenCV 打开摄像头（或视频文件），对每一帧做：
  MediaPipe PoseLandmarker（VIDEO 模式）→ 33 姿态关键点
  → 骨度分寸比例法映射穴位 → 叠加穴位点与名称显示。

用法：
  # 笔记本摄像头（默认）
  python acupoint_locator_live.py

  # 指定摄像头编号（外接 USB 摄像头可能为 1/2）
  python acupoint_locator_live.py --source 1

  # 处理视频文件（无摄像头时调试用），并另存标注后的视频
  python acupoint_locator_live.py --source test_video.mp4 --output-video out_live.mp4

快捷键（有窗口时）：
  q / Esc  退出
  s        保存当前帧标注图 + 结果 JSON（acupoints_live_snapshot.*）
  t        开关"低"置信档穴位显示
  空格     暂停/继续
"""
import argparse
import json
import os
import sys
import time

import numpy as np

try:
    import cv2
    import mediapipe as mp
    from mediapipe.tasks import python as mp_python
    from mediapipe.tasks.python import vision
except ImportError:
    sys.exit("缺少依赖，请先运行: pip install -r requirements.txt")

from acupoint_locator import (Body, ensure_model, make_rules, render_frame)


def open_landmarker(model_path):
    base_options = mp_python.BaseOptions(model_asset_path=model_path)
    options = vision.PoseLandmarkerOptions(
        base_options=base_options,
        running_mode=vision.RunningMode.VIDEO,
        num_poses=1,
        min_pose_detection_confidence=0.5,
        min_pose_presence_confidence=0.5,
        min_tracking_confidence=0.5,
    )
    return vision.PoseLandmarker.create_from_options(options)


def compute_points(body, rules, tiers_ok, want):
    """与静态版相同：对规则表逐条求值，返回穴位点列表（原图归一化坐标）"""
    out = []
    for r in rules:
        if r["tier"] not in tiers_ok:
            continue
        if want and r["name"] not in want:
            continue
        sides = ("L", "R") if r["paired"] else ("L",)
        for s in sides:
            pt = r["fn"](body, s)
            if pt is None:
                continue
            x, y = pt
            if not (0 <= x <= 1 and 0 <= y <= 1):
                continue
            out.append({"name": r["name"], "code": r["code"],
                        "side": s if r["paired"] else None,
                        "x": float(x), "y": float(y), "tier": r["tier"]})
    return out


def save_snapshot(rgb, points, prefix="acupoints_live_snapshot"):
    """保存当前帧标注图 + JSON（坐标换算为原图像素）"""
    annotated = render_frame(rgb, points, upscale=1.2, font_size=16)
    jpg_path = prefix + ".jpg"
    json_path = prefix + ".json"
    from PIL import Image
    Image.fromarray(annotated).save(jpg_path)
    h, w = rgb.shape[:2]
    data = {
        "image": jpg_path,
        "note": "实时版快照：坐标单位为原图像素",
        "acupoints": [dict(p, x=round(p["x"] * w, 1), y=round(p["y"] * h, 1)) for p in points],
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"已保存快照: {jpg_path}  {json_path}")


def main():
    ap = argparse.ArgumentParser(description="人体穴位定位 · 摄像头/视频实时版")
    ap.add_argument("--source", default="0",
                    help="摄像头编号（默认 0）或视频文件路径")
    ap.add_argument("--model", default=os.path.join("models", "pose_landmarker_full.task"),
                    help="MediaPipe 姿态模型路径（缺失自动下载）")
    ap.add_argument("--tiers", default="高,中", help="显示的置信档，逗号分隔（默认 高,中）")
    ap.add_argument("--points", default="", help="只显示指定穴位（逗号分隔中文名）")
    ap.add_argument("--max-width", type=int, default=960,
                    help="处理/显示帧的最大宽度，越小越快（默认 960）")
    ap.add_argument("--no-display", action="store_true",
                    help="不弹窗显示（无显示器环境，如服务器/沙箱调试）")
    ap.add_argument("--output-video", default="",
                    help="另存标注后视频的路径（如 out_live.mp4）")
    ap.add_argument("--save-snapshot", action="store_true",
                    help="检测到人体后自动保存第一帧快照（无显示器模式调试用）")
    args = ap.parse_args()

    tiers_ok = {t.strip() for t in args.tiers.split(",") if t.strip()}
    want = {n.strip() for n in args.points.split(",") if n.strip()}

    # ---- 打开视频源 ----
    src = int(args.source) if args.source.isdigit() else args.source
    cap = cv2.VideoCapture(src)
    if not cap.isOpened():
        sys.exit(f"无法打开视频源: {args.source}（检查摄像头编号或文件路径）")
    fps = cap.get(cv2.CAP_PROP_FPS)
    if not fps or fps <= 0:
        fps = 30.0
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    print(f"视频源: {args.source}  原始尺寸 {w}x{h}  fps≈{fps:.1f}")

    # ---- 模型与规则 ----
    model_path = ensure_model(args.model)
    landmarker = open_landmarker(model_path)
    rules = make_rules()

    # ---- 输出视频 ----
    writer = None
    if args.output_video:
        scale = args.max_width / w if w > args.max_width else 1.0
        ow, oh = int(w * scale), int(h * scale)
        writer = cv2.VideoWriter(args.output_video, cv2.VideoWriter_fourcc(*"mp4v"),
                                 fps, (ow, oh))
        print(f"标注视频将写入: {args.output_video}")

    # ---- 主循环 ----
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
            # 缩放到 max_width，提高实时性
            fh, fw = frame.shape[:2]
            if fw > args.max_width:
                scale = args.max_width / fw
                frame = cv2.resize(frame, (args.max_width, int(fh * scale)),
                                   interpolation=cv2.INTER_AREA)
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

            # 时间戳：摄像头用系统时钟（毫秒），视频用帧号换算，均需严格递增
            ts = int(time.monotonic() * 1000) if is_camera else int(frame_idx * 1000 / fps)
            results = landmarker.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB,
                                                           data=rgb), ts)
            frame_idx += 1

            points = []
            if results.pose_landmarks:
                body = Body(results.pose_landmarks[0])
                points = compute_points(body, rules, tiers_ok, want)
                if not show_low:
                    points = [p for p in points if p["tier"] != "低"]
                if args.save_snapshot and points and not snap_saved:
                    save_snapshot(rgb, points)
                    snap_saved = True

            annotated = render_frame(rgb, points, upscale=1.0, font_size=16)

            # 叠加 FPS / 点数提示（ASCII，避免中文乱码）
            fps_now = 1.0 / max(1e-6, time.time() - t_last)
            t_last = time.time()
            bgr = cv2.cvtColor(annotated, cv2.COLOR_RGB2BGR)
            overlay = f"fps:{fps_now:4.1f}  points:{len(points)}  [s]save [t]tier [space]pause [q]quit"
            cv2.putText(bgr, overlay, (10, 24), cv2.FONT_HERSHEY_SIMPLEX,
                        0.55, (0, 255, 0), 2, cv2.LINE_AA)

            if writer is not None:
                writer.write(bgr)

            if not args.no_display:
                cv2.imshow("Acupoint Locator - 穴位实时定位", bgr)
                key = cv2.waitKey(1) & 0xFF
                if key in (ord("q"), 27):
                    break
                elif key == ord("s"):
                    save_snapshot(rgb, points)
                elif key == ord("t"):
                    show_low = not show_low
                    print(f"低置信穴位显示: {'开' if show_low else '关'}")
                elif key == ord(" "):
                    paused = not paused
                    print("已暂停" if paused else "继续")
            else:
                # 无窗口模式：每秒打印一次状态
                if frame_idx % max(1, int(fps)) == 0:
                    print(f"帧 {frame_idx}  fps={fps_now:.1f}  穴位 {len(points)} 个")

    cap.release()
    if writer is not None:
        writer.release()
    landmarker.close()
    if not args.no_display:
        cv2.destroyAllWindows()
    print("已退出")


if __name__ == "__main__":
    main()
#（注：内容由AI生成）
