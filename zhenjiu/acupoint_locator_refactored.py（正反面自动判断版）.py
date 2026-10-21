#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
人体穴位识别 · 重构版（图片 + 摄像头实时）
=================================================
基于"姿态估计 + 骨度分寸比例法"：MediaPipe Pose 检测 33 个姿态关键点，
按《针灸学》骨度分寸（肘—腕 12 寸、膝—外踝 16 寸、脐—耻骨 5 寸等）换算穴位坐标，
输出穴位位置 (x, y) 与名称（含 WHO 编码），并绘制标注图。

用法：
  # 1) 图片模式（--view 默认 auto，自动判断正反面）
  python acupoint_locator_refactored.py --input 图片.jpg [--view auto|front|back]
      [--output 标注图.jpg] [--json 结果.json]

  # 2) 摄像头/视频实时模式（--view 默认 auto，自动判断正反面）
  python acupoint_locator_refactored.py live [--source 0] [--view auto|front|back]
      [--tiers 高,中] [--max-width 960]
      [--output-video out.mp4] [--no-display]

安装（Python 3.10+，无需 GPU/torch）：
  pip install mediapipe numpy pillow opencv-python

说明：
  - 置信档：高=直接取姿态关键点；中=比例法推算；低=粗略估计（演示用）。
  - 左/右以被摄者自身方位为准。
  - 正反面自动判断：基于鼻子/眼睛/嘴角的可见度，正面可见度高、背面低。
    实时模式下按 v 键可在 自动→正面→背面 之间循环切换。
  - 本脚本为教学/演示用途，不能替代临床定位。
"""
import argparse
import glob
import json
import os
import sys
import time

import numpy as np

try:
    import mediapipe as mp
    from mediapipe.tasks import python as mp_python
    from mediapipe.tasks.python import vision
except ImportError:
    sys.exit("缺少 mediapipe，请先运行: pip install mediapipe numpy pillow")

from PIL import Image, ImageDraw, ImageFont

try:
    import cv2
    HAVE_CV2 = True
except ImportError:
    HAVE_CV2 = False

MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/pose_landmarker/"
             "pose_landmarker_full/float16/latest/pose_landmarker_full.task")

# =====================================================================
# MediaPipe Pose 33 关键点索引
# =====================================================================
NOSE = 0
E_L_IN, E_L, E_L_OUT = 1, 2, 3
E_R_IN, E_R, E_R_OUT = 4, 5, 6
EAR_L, EAR_R = 7, 8
MOUTH_L, MOUTH_R = 9, 10
SH_L, SH_R = 11, 12
ELB_L, ELB_R = 13, 14
WRI_L, WRI_R = 15, 16
PIN_L, PIN_R = 17, 18
IDX_L, IDX_R = 19, 20
THU_L, THU_R = 21, 22
HIP_L, HIP_R = 23, 24
KNE_L, KNE_R = 25, 26
ANK_L, ANK_R = 27, 28
HEE_L, HEE_R = 29, 30
FOO_L, FOO_R = 31, 32

# =====================================================================
# 几何工具
# =====================================================================
def mid(a, b):
    return ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)


def dist(a, b):
    return float(np.hypot(a[0] - b[0], a[1] - b[1]))


def unit(a, b):
    """从 a 指向 b 的单位向量（图像坐标，y 向下）"""
    d = dist(a, b)
    if d < 1e-6:
        return (0.0, 0.0)
    return ((b[0] - a[0]) / d, (b[1] - a[1]) / d)


def along(a, b, fraction):
    """a 到 b 方向上的插值点：a + fraction * (b - a)"""
    return (a[0] + (b[0] - a[0]) * fraction, a[1] + (b[1] - a[1]) * fraction)


def shift(a, direction, distance):
    return (a[0] + direction[0] * distance, a[1] + direction[1] * distance)


def normalize(v):
    d = float(np.hypot(v[0], v[1]))
    if d < 1e-6:
        return (0.0, 0.0)
    return (v[0] / d, v[1] / d)


def lateral_dir(anchor, midline_x):
    """远离躯干中线方向的单位水平向量：中线右侧取 +1，左侧取 -1"""
    return (1.0 if anchor[0] >= midline_x else -1.0, 0.0)


# =====================================================================
# 骨度分寸比例法
# =====================================================================
class Body:
    """由 33 个姿态关键点推导出的比例尺度"""

    def __init__(self, lm):
        self.p = {i: (lm[i].x * 1.0, lm[i].y * 1.0) for i in range(33)}
        self.v = {i: lm[i].visibility for i in range(33)}
        self.nose = self.p[NOSE]
        self.eye_in_mid = mid(self.p[E_L_IN], self.p[E_R_IN])
        self.eye_out_l, self.eye_out_r = self.p[E_L_OUT], self.p[E_R_OUT]
        self.ear_mid = mid(self.p[EAR_L], self.p[EAR_R])
        self.mouth_mid = mid(self.p[MOUTH_L], self.p[MOUTH_R])
        self.sh_mid = mid(self.p[SH_L], self.p[SH_R])
        self.hip_mid = mid(self.p[HIP_L], self.p[HIP_R])
        self.trunk_dir = unit(self.sh_mid, self.hip_mid)          # 向下
        self.trunk_len = dist(self.sh_mid, self.hip_mid)
        self.shoulder_width = dist(self.p[SH_L], self.p[SH_R])
        self.hip_width = dist(self.p[HIP_L], self.p[HIP_R])
        self.midline_x = self.sh_mid[0]
        # 骨度基准（近似）：剑胸结合≈肩髋 34% 处，脐≈60%，耻骨联合≈髋水平
        self.xiphoid = along(self.sh_mid, self.hip_mid, 0.34)
        self.navel = along(self.sh_mid, self.hip_mid, 0.60)
        self.inch_chest = dist(self.navel, self.xiphoid) / 8.0    # 剑突—脐 8 寸
        self.inch_abd = dist(self.navel, self.hip_mid) / 5.0      # 脐—耻骨 5 寸

    def torso_center(self, fraction):
        """肩中点到髋中点的动态躯干中线；人体倾斜时不再强制竖直。"""
        return along(self.sh_mid, self.hip_mid, fraction)

    def side_dir(self, side, fraction=0.5):
        """指向被摄者解剖学左/右侧的局部方向，兼顾肩线和髋线。"""
        sh = self.p[SH_L if side == "L" else SH_R]
        hip = self.p[HIP_L if side == "L" else HIP_R]
        ds = unit(self.sh_mid, sh)
        dh = unit(self.hip_mid, hip)
        return normalize(((1.0 - fraction) * ds[0] + fraction * dh[0],
                          (1.0 - fraction) * ds[1] + fraction * dh[1]))

    def torso_width(self, fraction=0.5):
        return (1.0 - fraction) * self.shoulder_width + fraction * self.hip_width

    def visible(self, *idx, thresh=0.5):
        return all(self.v.get(i, 0) >= thresh for i in idx)


# =====================================================================
# 正反面自动判断
# =====================================================================
def detect_view(body, thresh=0.40):
    """根据面部关键点可见度判断正面/背面。
    正面：鼻子、眼睛、嘴角可见度高；背面：这些点可见度显著降低。
    返回 ("front"|"back", confidence_score)。
    """
    face_idx = [NOSE, E_L_IN, E_R_IN, MOUTH_L, MOUTH_R]
    score = sum(body.v.get(i, 0) for i in face_idx) / len(face_idx)
    return ("front" if score >= thresh else "back", score)


class ViewSmoother:
    """实时模式下对正反面判断做多数投票，避免帧间闪烁。"""

    def __init__(self, window=5):
        self.window = max(1, int(window))
        self.history = []

    def update(self, detected):
        self.history.append(detected)
        if len(self.history) > self.window:
            self.history.pop(0)
        front = self.history.count("front")
        back = self.history.count("back")
        return "front" if front >= back else "back"


# =====================================================================
# 规则表：fn(b: Body, side: str) -> (x, y) | None，side 为 "L"/"R"
# =====================================================================
def make_rules():
    rules = []

    def add(name, code, tier, paired, fn):
        rules.append({"name": name, "code": code, "tier": tier,
                      "paired": paired, "fn": fn})

    def arm(s, base):
        return base + (0 if s == "L" else 1)

    def leg(s, base):
        return base + (0 if s == "L" else 1)

    # ---------- 头面部 ----------
    add("印堂", "EX-HN3", "高", False,
        lambda b, s: mid(b.p[E_L_IN], b.p[E_R_IN]) if b.visible(E_L_IN, E_R_IN) else None)
    add("攒竹", "BL-2", "高", True,
        lambda b, s: b.p[E_L_IN if s == "L" else E_R_IN] if b.visible(E_L_IN, E_R_IN) else None)
    add("丝竹空", "TE-23", "高", True,
        lambda b, s: b.p[E_L_OUT if s == "L" else E_R_OUT] if b.visible(E_L_OUT, E_R_OUT) else None)
    add("太阳", "EX-HN5", "中", True,
        lambda b, s: (lambda o, e: shift(o, unit(o, e), dist(o, e) * 0.25))(
            b.p[E_L_OUT if s == "L" else E_R_OUT], b.p[EAR_L if s == "L" else EAR_R])
        if b.visible(E_L_OUT if s == "L" else E_R_OUT, EAR_L if s == "L" else EAR_R) else None)
    add("迎香", "LI-20", "中", True,
        lambda b, s: along(b.nose, b.p[E_L_IN if s == "L" else E_R_IN], 0.45)
        if b.visible(NOSE, E_L_IN, E_R_IN) else None)
    add("人中", "GV-26", "中", False,
        # 沿鼻尖—嘴角中点的面部中线定位；标签位置不再代表穴位位置。
        lambda b, s: along(b.nose, b.mouth_mid, 0.30)
        if b.visible(NOSE, MOUTH_L, MOUTH_R) else None)
    add("地仓", "ST-4", "中", True,
        lambda b, s: along(b.p[MOUTH_L if s == "L" else MOUTH_R],
                           b.p[E_L_OUT if s == "L" else E_R_OUT], 0.25)
        if b.visible(MOUTH_L, MOUTH_R, E_L_OUT, E_R_OUT) else None)
    add("承浆", "CV-24", "低", False,
        lambda b, s: shift(b.mouth_mid, unit(b.mouth_mid, b.nose), -dist(b.mouth_mid, b.nose) * 0.5)
        if b.visible(NOSE, MOUTH_L, MOUTH_R) else None)
    add("百会", "GV-20", "低", False,
        lambda b, s: (b.nose[0],
                      b.eye_in_mid[1] - abs(b.nose[1] - b.eye_in_mid[1]) * 1.8)
        if b.visible(NOSE, E_L, E_R) else None)

    # ---------- 躯干前正中线 ----------
    add("膻中", "CV-17", "中", False,
        lambda b, s: along(b.sh_mid, b.hip_mid, 0.29) if b.visible(SH_L, SH_R, HIP_L, HIP_R) else None)
    add("中脘", "CV-12", "中", False,
        lambda b, s: shift(b.navel, unit(b.navel, b.xiphoid), b.inch_chest * 4.0)
        if b.visible(SH_L, SH_R, HIP_L, HIP_R) else None)
    add("神阙", "CV-8", "中", False,
        lambda b, s: b.navel if b.visible(SH_L, SH_R, HIP_L, HIP_R) else None)
    add("气海", "CV-6", "中", False,
        lambda b, s: shift(b.navel, b.trunk_dir, b.inch_abd * 1.5)
        if b.visible(SH_L, SH_R, HIP_L, HIP_R) else None)
    add("关元", "CV-4", "中", False,
        lambda b, s: shift(b.navel, b.trunk_dir, b.inch_abd * 3.0)
        if b.visible(SH_L, SH_R, HIP_L, HIP_R) else None)
    add("天枢", "ST-25", "中", True,
        lambda b, s: shift(b.navel, (lateral_dir(b.navel, b.midline_x)[0] if s == "L"
                                     else -lateral_dir(b.navel, b.midline_x)[0], 0.0),
                           b.inch_abd * 2.0)
        if b.visible(SH_L, SH_R, HIP_L, HIP_R) else None)

    # ---------- 背部（独立 back 模式，沿动态躯干轴定位） ----------
    add("大椎", "GV-14", "低", False,
        lambda b, s: b.torso_center(0.05)
        if b.visible(SH_L, SH_R, HIP_L, HIP_R) else None)
    add("命门", "GV-4", "低", False,
        lambda b, s: b.torso_center(0.60)
        if b.visible(SH_L, SH_R, HIP_L, HIP_R) else None)
    add("肾俞", "BL-23", "低", True,
        lambda b, s: shift(b.torso_center(0.60), b.side_dir(s, 0.60),
                           b.torso_width(0.60) * 0.16)
        if b.visible(SH_L, SH_R, HIP_L, HIP_R) else None)

    # ---------- 上肢 ----------
    add("肩髃", "LI-15", "中", True,
        lambda b, s: (lambda sh, elb, midx: shift(
            shift(sh, lateral_dir(sh, midx), dist(sh, elb) * 0.08),
            (0.0, 1.0), dist(sh, elb) * 0.04))(
            b.p[arm(s, SH_L)], b.p[arm(s, ELB_L)], b.midline_x)
        if b.visible(arm(s, SH_L), arm(s, ELB_L)) else None)
    add("曲池", "LI-11", "中", True,
        lambda b, s: (lambda e, sh, midx: shift(e, lateral_dir(e, midx), dist(sh, e) * 0.12))(
            b.p[arm(s, ELB_L)], b.p[arm(s, SH_L)], b.midline_x)
        if b.visible(arm(s, ELB_L), arm(s, SH_L)) else None)
    add("尺泽", "LU-5", "中", True,
        lambda b, s: (lambda e, sh, midx: shift(e, (-lateral_dir(e, midx)[0], 0.0), dist(sh, e) * 0.10))(
            b.p[arm(s, ELB_L)], b.p[arm(s, SH_L)], b.midline_x)
        if b.visible(arm(s, ELB_L), arm(s, SH_L)) else None)

    def forearm_pt(b, s, cun, lat_sign):
        w, e = arm(s, WRI_L), arm(s, ELB_L)
        if not b.visible(w, e):
            return None
        inch = dist(b.p[w], b.p[e]) / 12.0  # 肘横纹—腕横纹 12 寸
        base = shift(b.p[w], unit(b.p[w], b.p[e]), inch * cun)
        if abs(lat_sign) < 1e-6:
            return base
        return shift(base, (lateral_dir(base, b.midline_x)[0] * lat_sign, 0.0), inch * 0.8)

    add("列缺", "LU-7", "中", True, lambda b, s: forearm_pt(b, s, 1.5, 1.0))
    add("内关", "PC-6", "中", True, lambda b, s: forearm_pt(b, s, 2.0, -1.0))
    add("外关", "TE-5", "中", True, lambda b, s: forearm_pt(b, s, 2.0, 1.0))
    add("合谷", "LI-4", "低", True,
        lambda b, s: (lambda web, w: shift(web, unit(web, w), dist(web, w) * 0.30))(
            mid(b.p[arm(s, IDX_L)], b.p[arm(s, THU_L)]), b.p[arm(s, WRI_L)])
        if b.visible(arm(s, IDX_L), arm(s, THU_L), arm(s, WRI_L)) else None)

    # ---------- 下肢 ----------
    def leg_pt(b, s, cun, lat_sign, toward):
        k, a = leg(s, KNE_L), leg(s, ANK_L)
        if not b.visible(k, a):
            return None
        inch = dist(b.p[k], b.p[a]) / 16.0  # 膝中—外踝尖 16 寸
        if toward == "down":
            base = shift(b.p[k], unit(b.p[k], b.p[a]), inch * cun)
        else:  # up：向髋方向
            base = shift(b.p[k], unit(b.p[k], b.hip_mid), inch * cun)
        if abs(lat_sign) < 1e-6:
            return base
        return shift(base, (lateral_dir(base, b.hip_mid[0])[0] * lat_sign, 0.0), inch * 0.8)

    add("血海", "SP-10", "中", True, lambda b, s: leg_pt(b, s, 2.0, -1.0, "up"))
    add("足三里", "ST-36", "中", True, lambda b, s: leg_pt(b, s, 3.0, 1.0, "down"))
    add("三阴交", "SP-6", "中", True,
        lambda b, s: (lambda k, a: shift(b.p[a], unit(b.p[a], b.p[k]), dist(b.p[k], b.p[a]) / 16.0 * 3.0))(
            leg(s, KNE_L), leg(s, ANK_L))
        if b.visible(leg(s, KNE_L), leg(s, ANK_L)) else None)
    add("阳陵泉", "GB-34", "低", True, lambda b, s: leg_pt(b, s, 0.8, 1.0, "down"))
    add("阴陵泉", "SP-9", "低", True, lambda b, s: leg_pt(b, s, 0.8, -1.0, "down"))
    add("太冲", "LR-3", "低", True,
        lambda b, s: along(b.p[leg(s, FOO_L)], b.p[leg(s, HEE_L)], 0.30)
        if b.visible(leg(s, HEE_L), leg(s, FOO_L)) else None)

    # 规则元数据统一在这里归类，避免正面和背面穴位同时叠加。
    face_names = {"印堂", "攒竹", "丝竹空", "太阳", "迎香", "人中", "地仓", "承浆", "百会"}
    front_trunk_names = {"膻中", "中脘", "神阙", "气海", "关元", "天枢"}
    back_names = {"大椎", "命门", "肾俞"}
    for rule in rules:
        if rule["name"] in face_names:
            rule.update(region="face", views={"front"})
        elif rule["name"] in front_trunk_names:
            rule.update(region="front_trunk", views={"front"})
        elif rule["name"] in back_names:
            rule.update(region="back", views={"back"})
        else:
            rule.update(region="limb", views={"front", "back"})
    return rules


# =====================================================================
# 检测 + 绘制
# =====================================================================
def find_cjk_font():
    candidates = [
        # Windows 中文字体（按优先级）
        "C:/Windows/Fonts/msyh.ttc",      # 微软雅黑
        "C:/Windows/Fonts/msyhbd.ttc",    # 微软雅黑粗体
        "C:/Windows/Fonts/simhei.ttf",    # 黑体
        "C:/Windows/Fonts/simsun.ttc",    # 宋体
        # Linux 中文字体
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Medium.ttc",
        "/usr/share/fonts/truetype/arphic/uming.ttc",
        "/usr/share/fonts/truetype/arphic/ukai.ttc",
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    hits = glob.glob("/usr/share/fonts/**/*CJK*", recursive=True) + \
           glob.glob("/usr/share/fonts/**/*cjk*", recursive=True)
    for h in hits:
        if h.endswith((".ttc", ".otf", ".ttf")):
            return h
    return None


def ensure_model(model_path):
    """模型不存在时自动下载（约 9MB）"""
    if os.path.exists(model_path) and os.path.getsize(model_path) > 100000:
        return model_path
    print(f"正在下载姿态模型（首次运行，约 9MB）: {MODEL_URL}")
    import shutil
    import ssl
    import urllib.request
    os.makedirs(os.path.dirname(model_path) or ".", exist_ok=True)
    try:
        # 优先用系统证书库校验下载；部分 Windows 环境证书库损坏会抛 ASN1 异常
        urllib.request.urlretrieve(MODEL_URL, model_path)
        print("模型下载完成。")
        return model_path
    except Exception as e:
        print(f"常规方式下载失败：{e}")
        print("改用不校验证书的方式重试……")
        ctx = ssl._create_unverified_context()
        opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx))
        try:
            with opener.open(MODEL_URL, timeout=60) as resp, open(model_path, "wb") as f:
                shutil.copyfileobj(resp, f)
            print("模型下载完成。")
            return model_path
        except Exception as e2:
            if os.path.exists(model_path):
                os.remove(model_path)
            sys.exit("模型自动下载失败。\n"
                     "解决办法：把 pose_landmarker_full.task 手动下载后放到脚本同目录的 "
                     "models 文件夹下（即 models/pose_landmarker_full.task），再重新运行。")


def detect_pose(rgb, model_path):
    """图片模式：MediaPipe Tasks API 单帧检测，返回 pose_landmarks"""
    base_options = mp_python.BaseOptions(model_asset_path=model_path)
    options = vision.PoseLandmarkerOptions(
        base_options=base_options,
        running_mode=vision.RunningMode.IMAGE,
        num_poses=1,
        min_pose_detection_confidence=0.5,
        min_pose_presence_confidence=0.5,
    )
    landmarker = vision.PoseLandmarker.create_from_options(options)
    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
    result = landmarker.detect(mp_image)
    landmarker.close()
    return result


def open_landmarker(model_path):
    """实时模式：VIDEO 模式检测器（需严格递增时间戳）"""
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


def render_frame(img_rgb, points, upscale=1.5, font_size=18, show_labels=True):
    """把穴位点绘制到 RGB 图像上，返回标注后的 RGB numpy 数组。

    upscale>1 时先放大底图再标注，标签更清晰（实时建议 1.0）。
    """
    font_path = find_cjk_font()
    pil = Image.fromarray(img_rgb)
    if upscale != 1.0:
        pil = pil.resize((int(pil.width * upscale), int(pil.height * upscale)), Image.LANCZOS)
        points = [dict(p, x=p["x"] * upscale, y=p["y"] * upscale) for p in points]
    draw = ImageDraw.Draw(pil)
    tier_color = {"高": (22, 163, 74), "中": (29, 78, 216), "低": (120, 113, 108)}
    tier_order = {"高": 0, "中": 1, "低": 2}
    try:
        base_font = ImageFont.truetype(font_path, font_size) if font_path else ImageFont.load_default()
        font_h = font_size + 2 if font_path else 12
    except Exception:
        base_font = ImageFont.load_default()
        font_h = 12

    # 标签避让：从多圈候选位置中选择碰撞最少的位置，并画引导线。
    # 蓝/绿/灰圆点始终是穴位坐标；文字位置只负责显示。
    used_boxes = []
    point_boxes = []
    for p in points:
        px, py = p["x"], p["y"]
        point_boxes.append((px - 7, py - 7, 14, 14))

    def overlap_area(a, b):
        ax, ay, aw, ah = a
        bx, by, bw, bh = b
        return max(0.0, min(ax + aw, bx + bw) - max(ax, bx)) * \
               max(0.0, min(ay + ah, by + bh) - max(ay, by))

    def place_label(x, y, label):
        w = draw.textlength(label, font=base_font)
        h = font_h
        cands = []
        for gap in (10, 22, 36, 52):
            cands.extend([
                (x + gap, y - h - gap / 2), (x - w - gap, y - h - gap / 2),
                (x + gap, y + gap / 2), (x - w - gap, y + gap / 2),
                (x - w / 2, y - h - gap), (x - w / 2, y + gap),
            ])

        best = None
        for rank, (bx, by) in enumerate(cands):
            bx = max(0, min(bx, pil.width - w - 2))
            by = max(0, min(by, pil.height - h - 2))
            box = (bx, by, w, h)
            label_overlap = sum(overlap_area(box, u) for u in used_boxes)
            point_overlap = sum(overlap_area(box, u) for u in point_boxes)
            score = label_overlap * 20.0 + point_overlap * 8.0 + rank
            if best is None or score < best[0]:
                best = (score, bx, by)

        _, bx, by = best
        # 连接到标签框内离穴位最近的点，明确“点”和“文字”不是同一坐标。
        tx = min(max(x, bx), bx + w)
        ty = min(max(y, by), by + h)
        draw.line((x, y, tx, ty), fill=(235, 235, 235), width=1)
        draw.text((bx, by), label, fill=(255, 255, 255), font=base_font,
                  stroke_width=2, stroke_fill=(0, 0, 0))
        used_boxes.append((bx, by, w, h))

    for p in sorted(points, key=lambda q: tier_order[q["tier"]]):
        x, y = int(round(p["x"])), int(round(p["y"]))
        color = tier_color[p["tier"]]
        r = 5 if p["tier"] != "低" else 4
        draw.ellipse([x - r, y - r, x + r, y + r], fill=color, outline="white", width=2)
        if show_labels:
            label = p["name"] if font_path else p["code"]
            place_label(x, y, label)

    return np.array(pil)


# =====================================================================
# 公共：规则求值
# =====================================================================
def compute_points(body, rules, tiers_ok, want, img_shape, view="front",
                   face_mode="core", back_offset_x=0.0, back_offset_y=0.0):
    """对规则表逐条求值，返回像素坐标穴位点列表"""
    out = []
    core_face = {"印堂", "太阳", "迎香", "人中", "承浆"}
    for r in rules:
        if r["tier"] not in tiers_ok:
            continue
        if view != "both" and view not in r["views"]:
            continue
        if r["region"] == "face":
            if face_mode == "off":
                continue
            if face_mode == "core" and r["name"] not in core_face:
                continue
        if want and r["name"] not in want:
            continue
        sides = ("L", "R") if r["paired"] else ("L",)
        for s in sides:
            pt = r["fn"](body, s)
            if pt is None:
                continue
            x, y = pt
            if r["region"] == "back":
                # x 以肩宽为单位、y 以躯干长度为单位，方便不同分辨率复用校准值。
                x += back_offset_x * body.shoulder_width
                y += back_offset_y * body.trunk_len
            if not (0 <= x <= 1 and 0 <= y <= 1):
                continue
            out.append({"name": r["name"], "code": r["code"],
                        "side": s if r["paired"] else None,
                        "x": round(x * img_shape[1], 1),
                        "y": round(y * img_shape[0], 1),
                        "tier": r["tier"]})
    return out


class PointSmoother:
    """按穴位名和左右侧做指数移动平均，抑制实时画面左右跳动。"""

    def __init__(self, alpha=0.35):
        self.alpha = min(1.0, max(0.01, float(alpha)))
        self.state = {}

    def update(self, points):
        current = set()
        smoothed = []
        for p in points:
            key = (p["name"], p["side"])
            current.add(key)
            old = self.state.get(key, (p["x"], p["y"]))
            x = self.alpha * p["x"] + (1.0 - self.alpha) * old[0]
            y = self.alpha * p["y"] + (1.0 - self.alpha) * old[1]
            self.state[key] = (x, y)
            smoothed.append(dict(p, x=round(x, 1), y=round(y, 1)))
        self.state = {k: v for k, v in self.state.items() if k in current}
        return smoothed


# =====================================================================
# 图片模式
# =====================================================================
def main_image():
    ap = argparse.ArgumentParser(description="人体穴位定位（图片模式）")
    ap.add_argument("--input", required=True, help="输入人体图像路径")
    ap.add_argument("--output", default="result_annotated.jpg", help="标注图输出路径")
    ap.add_argument("--json", default="result_acupoints.json", help="结果 JSON 输出路径")
    ap.add_argument("--tiers", default="高,中,低", help="要输出的置信档，逗号分隔")
    ap.add_argument("--points", default="", help="只输出指定穴位（逗号分隔中文名）")
    ap.add_argument("--view", choices=("auto", "front", "back", "both"), default="auto",
                    help="拍摄方向：auto 自动判断、front 正面、back 背面；不建议使用 both")
    ap.add_argument("--face-mode", choices=("core", "all", "off"), default="core",
                    help="面部穴位：核心 core、全部 all、关闭 off（默认 core，减少拥挤）")
    ap.add_argument("--back-offset-x", type=float, default=0.0,
                    help="背部整体左右校准，单位为肩宽；向图像右侧为正（如 0.02）")
    ap.add_argument("--back-offset-y", type=float, default=0.0,
                    help="背部整体上下校准，单位为躯干长度；向图像下方为正")
    ap.add_argument("--no-labels", action="store_true", help="只画穴位点，不画名称")
    ap.add_argument("--model", default=os.path.join("models", "pose_landmarker_full.task"),
                    help="MediaPipe 姿态模型路径（缺失时自动下载）")
    ap.add_argument("--upscale", type=float, default=1.5, help="标注前放大底图倍数")
    args = ap.parse_args()

    tiers_ok = {t.strip() for t in args.tiers.split(",") if t.strip()}
    want = {n.strip() for n in args.points.split(",") if n.strip()}

    img = Image.open(args.input).convert("RGB")
    rgb = np.array(img)
    model_path = ensure_model(args.model)
    results = detect_pose(rgb, model_path)

    if not results.pose_landmarks:
        sys.exit("未检测到人体，请换一张清晰的全身照。")

    body = Body(results.pose_landmarks[0])
    view = args.view
    if view == "auto":
        view, view_score = detect_view(body)
        print(f"自动判断拍摄方向: {view}（面部可见度 {view_score:.2f}）")
    out_points = compute_points(
        body, make_rules(), tiers_ok, want, rgb.shape,
        view=view, face_mode=args.face_mode,
        back_offset_x=args.back_offset_x, back_offset_y=args.back_offset_y,
    )
    annotated = render_frame(rgb, out_points, upscale=args.upscale,
                             show_labels=not args.no_labels)
    Image.fromarray(annotated).save(args.output)

    result = {
        "image": args.input,
        "coordinate_space": "像素（原图分辨率）",
        "view": view,
        "back_calibration": {"x_shoulder_width": args.back_offset_x,
                             "y_trunk_length": args.back_offset_y},
        "note": "路线A：姿态关键点 + 骨度分寸比例法；仅教学演示，不能替代临床定位",
        "acupoints": out_points,
    }
    with open(args.json, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"检测到 1 个人体，共输出 {len(out_points)} 个穴位点")
    print(f"标注图: {args.output}   结果JSON: {args.json}")
    print("-" * 72)
    print(f"{'穴位':<6}{'编码':<10}{'侧':<5}{'x':<8}{'y':<8}{'置信'}")
    for p in out_points:
        print(f"{p['name']:<6}{p['code']:<10}{(p['side'] or '-'):<5}"
              f"{p['x']:<8.1f}{p['y']:<8.1f}{p['tier']}")


# =====================================================================
# 实时模式（摄像头 / 视频）
# =====================================================================
def save_snapshot(rgb, points, prefix="acupoints_live_snapshot", show_labels=True):
    """保存当前帧标注图 + JSON（坐标换算为原图像素）"""
    annotated = render_frame(rgb, points, upscale=1.2, font_size=16,
                             show_labels=show_labels)
    jpg_path = prefix + ".jpg"
    json_path = prefix + ".json"
    Image.fromarray(annotated).save(jpg_path)
    data = {
        "image": jpg_path,
        "note": "实时版快照：坐标单位为原图像素",
        # compute_points 已经输出像素坐标，不能再次乘图像宽高。
        "acupoints": [dict(p, x=round(p["x"], 1), y=round(p["y"], 1)) for p in points],
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"已保存快照: {jpg_path}  {json_path}")


def main_live():
    if not HAVE_CV2:
        sys.exit("实时模式需要 opencv-python，请先运行: pip install opencv-python")
    ap = argparse.ArgumentParser(description="人体穴位定位（摄像头/视频实时模式）")
    ap.add_argument("--source", default="0", help="摄像头编号（默认 0）或视频文件路径")
    ap.add_argument("--model", default=os.path.join("models", "pose_landmarker_full.task"),
                    help="MediaPipe 姿态模型路径（缺失自动下载）")
    ap.add_argument("--tiers", default="高,中", help="显示的置信档，逗号分隔")
    ap.add_argument("--points", default="", help="只显示指定穴位（逗号分隔中文名）")
    ap.add_argument("--view", choices=("auto", "front", "back"), default="auto",
                    help="拍摄方向：auto 自动判断（默认）、front 正面、back 背面；运行中按 v 切换")
    ap.add_argument("--face-mode", choices=("core", "all", "off"), default="core",
                    help="面部穴位：核心 core、全部 all、关闭 off")
    ap.add_argument("--back-offset-x", type=float, default=0.0,
                    help="背部整体左右校准，单位为肩宽；向图像右侧为正")
    ap.add_argument("--back-offset-y", type=float, default=0.0,
                    help="背部整体上下校准，单位为躯干长度；向图像下方为正")
    ap.add_argument("--smooth-alpha", type=float, default=0.35,
                    help="实时穴位平滑系数 0~1；越小越稳、延迟越大")
    ap.add_argument("--no-labels", action="store_true", help="只画穴位点，不画名称")
    ap.add_argument("--max-width", type=int, default=960, help="处理帧最大宽度，越小越快")
    ap.add_argument("--win-scale", type=float, default=1.5, help="显示窗口放大倍数（不影响处理速度，默认 1.5）")
    ap.add_argument("--no-display", action="store_true", help="不弹窗（服务器/无显示器）")
    ap.add_argument("--output-video", default="", help="另存标注后视频路径")
    ap.add_argument("--save-snapshot", action="store_true", help="检测到人体后自动存第一帧快照")
    args = ap.parse_args()

    tiers_ok = {t.strip() for t in args.tiers.split(",") if t.strip()}
    want = {n.strip() for n in args.points.split(",") if n.strip()}

    src = int(args.source) if args.source.isdigit() else args.source
    cap = cv2.VideoCapture(src)
    if not cap.isOpened():
        sys.exit(f"无法打开视频源: {args.source}")
    fps = cap.get(cv2.CAP_PROP_FPS)
    if not fps or fps <= 0:
        fps = 30.0
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    print(f"视频源: {args.source}  原始尺寸 {w}x{h}  fps≈{fps:.1f}")

    model_path = ensure_model(args.model)
    landmarker = open_landmarker(model_path)
    rules = make_rules()

    writer = None
    if args.output_video:
        scale = args.max_width / w if w > args.max_width else 1.0
        writer = cv2.VideoWriter(args.output_video, cv2.VideoWriter_fourcc(*"mp4v"),
                                 fps, (int(w * scale), int(h * scale)))
        print(f"标注视频将写入: {args.output_video}")

    auto_view = args.view == "auto"
    view_mode = "front"  # 自动模式下会被实时覆盖
    # 背部规则标为低置信，但在明确选择 back 后应默认显示。
    show_low = view_mode == "back"
    paused = False
    frame_idx = 0
    t_last = time.time()
    snap_saved = False
    is_camera = isinstance(src, int)
    smoother = PointSmoother(args.smooth_alpha)
    view_smoother = ViewSmoother(window=5)

    while True:
        if not paused:
            ok, frame = cap.read()
            if not ok:
                print("视频结束")
                break
            fh, fw = frame.shape[:2]
            if fw > args.max_width:
                scale = args.max_width / fw
                frame = cv2.resize(frame, (args.max_width, int(fh * scale)),
                                   interpolation=cv2.INTER_AREA)
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            ts = int(time.monotonic() * 1000) if is_camera else int(frame_idx * 1000 / fps)
            results = landmarker.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB,
                                                           data=rgb), ts)
            frame_idx += 1

            points = []
            if results.pose_landmarks:
                body = Body(results.pose_landmarks[0])
                # 自动判断正反面（基于面部关键点可见度，带帧间平滑）
                if auto_view:
                    detected, _ = detect_view(body)
                    new_view = view_smoother.update(detected)
                    if new_view != view_mode:
                        view_mode = new_view
                        show_low = view_mode == "back"
                        smoother = PointSmoother(args.smooth_alpha)
                effective_tiers = tiers_ok | ({"低"} if view_mode == "back" else set())
                points = compute_points(
                    body, rules, effective_tiers, want, rgb.shape,
                    view=view_mode, face_mode=args.face_mode,
                    back_offset_x=args.back_offset_x,
                    back_offset_y=args.back_offset_y,
                )
                if not show_low:
                    points = [p for p in points if p["tier"] != "低"]
                points = smoother.update(points)
                if args.save_snapshot and points and not snap_saved:
                    save_snapshot(rgb, points, show_labels=not args.no_labels)
                    snap_saved = True

            annotated = render_frame(rgb, points, upscale=1.0, font_size=16,
                                     show_labels=not args.no_labels)
            fps_now = 1.0 / max(1e-6, time.time() - t_last)
            t_last = time.time()
            bgr = cv2.cvtColor(annotated, cv2.COLOR_RGB2BGR)
            overlay = (f"fps:{fps_now:4.1f} points:{len(points)} "
                       f"view:{'AUTO' if auto_view else 'MAN'}->{view_mode} "
                       "[v]view [s]save [t]tier [space]pause [q]quit")
            cv2.putText(bgr, overlay, (10, 24), cv2.FONT_HERSHEY_SIMPLEX,
                        0.55, (0, 255, 0), 2, cv2.LINE_AA)

            if writer is not None:
                writer.write(bgr)

            if not args.no_display:
                # 放大显示窗口（仅影响显示，不改变处理分辨率与输出视频）
                scale = args.win_scale if args.win_scale > 1.0 else 1.0
                show = bgr
                if scale > 1.0:
                    sw = int(bgr.shape[1] * scale)
                    sw = min(sw, 1200)
                    sh = int(bgr.shape[0] * sw / bgr.shape[1])
                    show = cv2.resize(bgr, (sw, sh), interpolation=cv2.INTER_LINEAR)
                cv2.imshow("Acupoint Locator - 穴位实时定位", show)
                key = cv2.waitKey(1) & 0xFF
                if key in (ord("q"), 27):
                    break
                elif key == ord("s"):
                    save_snapshot(rgb, points, show_labels=not args.no_labels)
                elif key == ord("t"):
                    show_low = not show_low
                    print(f"低置信穴位显示: {'开' if show_low else '关'}")
                elif key == ord("v"):
                    # v 键循环：auto → front → back → auto
                    if auto_view:
                        auto_view = False
                        view_mode = "front"
                    elif view_mode == "front":
                        view_mode = "back"
                    else:
                        auto_view = True
                        view_smoother = ViewSmoother(window=5)
                    show_low = view_mode == "back"
                    smoother = PointSmoother(args.smooth_alpha)
                    print(f"拍摄方向: {'自动' if auto_view else '手动'} -> {view_mode}")
                elif key == ord(" "):
                    paused = not paused
                    print("已暂停" if paused else "继续")
            else:
                if frame_idx % max(1, int(fps)) == 0:
                    print(f"帧 {frame_idx}  fps={fps_now:.1f}  穴位 {len(points)} 个")

    cap.release()
    if writer is not None:
        writer.release()
    landmarker.close()
    if not args.no_display:
        cv2.destroyAllWindows()
    print("已退出")


# =====================================================================
# 入口：无参数            → 默认打开摄像头实时模式
#       python xxx.py live ...    → 实时模式（--source 可指定摄像头/视频）
#       python xxx.py --input 图  → 图片模式
# =====================================================================
if __name__ == "__main__":
    argv = sys.argv[1:]
    if argv and argv[0] == "live":
        sys.argv = [sys.argv[0]] + argv[1:]
        main_live()
    elif any(a == "--input" or a.startswith("--input=") for a in argv):
        main_image()
    else:
        # 无参数（或只有 --source/--tiers 等实时参数）时，默认打开摄像头
        print("未指定 --input，默认进入摄像头实时模式；图片模式请加 --input 图片.jpg")
        main_live()
#（注：内容由AI生成）
