#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
人体穴位识别 · 重构版（图片 + 摄像头实时）
=================================================
基于"姿态估计 + 骨度分寸比例法"：MediaPipe Pose 检测 33 个姿态关键点，
按《针灸学》骨度分寸（肘—腕 12 寸、膝—外踝 16 寸、脐—耻骨 5 寸等）换算穴位坐标，
输出穴位位置 (x, y) 与名称（含 WHO 编码），并绘制标注图。

用法：
  # 1) 图片模式
  python acupoint_locator_all.py --input 图片.jpg --view front|back
      [--output 标注图.jpg] [--json 结果.json]

  # 2) Intel RealSense D435i 实时模式（同步彩色 + 对齐深度）
  python acupoint_locator_refactored.py live [--source realsense] [--view front|back]
      [--tiers 高,中] [--max-width 960]
      [--output-video out.mp4] [--no-display]

安装（Python 3.10+，无需 GPU/torch）：
  pip install mediapipe numpy pillow opencv-python
  # 使用 Intel RealSense D435i 时另需：
  pip install pyrealsense2

说明：
  - 置信档：高=直接取姿态关键点；中=比例法推算；低=粗略估计（演示用）。
  - 左/右以被摄者自身方位为准。
  - front/back 必须按拍摄方向明确指定，避免把正面规则误画到背面。
  - 本脚本为教学/演示用途，不能替代临床定位。
"""
import argparse
import glob
import json
import os
import sys
import time
from pathlib import Path

import numpy as np

try:
    import mediapipe as mp
    from mediapipe.tasks import python as mp_python
    from mediapipe.tasks.python import vision
except ImportError:
    sys.exit("缺少 mediapipe，请先运行: pip install mediapipe numpy pillow")

from PIL import Image, ImageDraw, ImageFont
from stabilization import StablePoints, TorsoDisplayTracker, BaseDisplayFilter
from back_template import BACK_NAMES, BACK_IDS, rules as back_rules

try:
    import cv2
    HAVE_CV2 = True
except ImportError:
    HAVE_CV2 = False

MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/pose_landmarker/"
             "pose_landmarker_full/float16/latest/pose_landmarker_full.task")
DEFAULT_MODEL = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "models", "pose_landmarker_full.task")

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

    def __init__(self, lm, allow_display_tracking=False):
        self.p = {i: (lm[i].x * 1.0, lm[i].y * 1.0) for i in range(33)}
        self.v = {i: lm[i].visibility if (
            np.isfinite([lm[i].x, lm[i].y, lm[i].visibility]).all()
            and 0 <= lm[i].x <= 1 and 0 <= lm[i].y <= 1) else 0.0
            for i in range(33)}
        self.display_tracked = {i:bool(allow_display_tracking and getattr(lm[i],'display_tracking_valid',False)
            and getattr(lm[i],'display_tracking_score',0)>=.4
            and np.isfinite([lm[i].x,lm[i].y]).all() and 0<=lm[i].x<=1 and 0<=lm[i].y<=1)
            for i in range(33)}
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
        return all(self.v.get(i, 0) >= thresh or (thresh<=.5 and self.display_tracked.get(i,False)) for i in idx)


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
        lambda b, s: shift(b.navel, b.side_dir(s, 0.60), b.inch_abd * 2.0)
        if b.visible(SH_L, SH_R, HIP_L, HIP_R) else None)

    # ---------- 背部（独立 back 模式，沿动态躯干轴定位） ----------
    rules.extend(back_rules())

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
    back_names = BACK_NAMES
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


def render_frame(img_rgb, points, upscale=1.5, font_size=18, show_labels=True,
                 show_coordinates=False, coordinate_font_size=10,
                 label_layout=None, status_text="", back_panel=False, base_requested=False,
                 panel_keys=None):
    """把穴位点绘制到 RGB 图像上，返回标注后的 RGB numpy 数组。

    upscale>1 时先放大底图再标注，标签更清晰（实时建议 1.0）。
    """
    font_path = find_cjk_font()
    # A photo selection must filter the image markers AND the table. Never draw
    # the complete live overlay and merely hide unselected table rows.
    if panel_keys is not None:
        selected = set(panel_keys)
        points = [p for p in points if (p['name'], p['side']) in selected]
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
        coordinate_font = (ImageFont.truetype(font_path, coordinate_font_size)
                           if font_path else ImageFont.load_default())
        coordinate_font_h = coordinate_font_size + 1 if font_path else 11
    except Exception:
        base_font = ImageFont.load_default()
        coordinate_font = base_font
        font_h = 12
        coordinate_font_h = 11

    dense = show_labels and (back_panel or (any('preview_id' in p for p in points) and len(points)>8))
    panel_x = pil.width
    if dense:
        table_font = ImageFont.truetype(font_path,16) if font_path else base_font
        row_count=(len(panel_keys) if panel_keys is not None else len(BACK_IDS)) if back_panel else len(points)
        canvas = Image.new('RGB',(pil.width+450,max(pil.height,76+row_count*26+34)),(24,28,34))
        canvas.paste(pil,(0,0))
        pil = canvas
        draw = ImageDraw.Draw(pil)
        draw.line((panel_x,0,panel_x,pil.height),fill=(80,85,90),width=1)
        draw.text((panel_x+12,8),'背部候选 · 比例估算（未校正）',font=base_font,fill=(255,216,120))
        draw.text((panel_x+12,29),'坐标单位 mm；Base=基座，Cam=相机',font=base_font,fill=(190,200,210))
        for offset,title in ((12,'编号 / 穴位'),(132,'坐标系'),(228,'X'),(314,'Y'),(400,'Z')):
            draw.text((panel_x+offset,54),title,font=base_font,
                      anchor='mt' if offset>=228 else 'lt',
                      fill=(115,220,255) if title=='Z' else (235,235,235))
        draw.line((panel_x+10,73,pil.width-10,73),fill=(80,85,90))

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

    def place_label(x, y, label, coordinate_text="", key=None):
        w = draw.textlength(label, font=base_font)
        if coordinate_text:
            w = max(w, draw.textlength(coordinate_text, font=coordinate_font))
        h = font_h + (coordinate_font_h if coordinate_text else 0)
        cands = []
        for gap in (10, 22, 36, 52):
            cands.extend([
                (x + gap, y - h - gap / 2), (x - w - gap, y - h - gap / 2),
                (x + gap, y + gap / 2), (x - w - gap, y + gap / 2),
                (x - w / 2, y - h - gap), (x - w / 2, y + gap),
            ])

        best = None
        for rank, (bx, by) in enumerate(cands):
            if label_layout is not None and key in label_layout and rank != label_layout[key]:
                continue
            bx = max(0, min(bx, pil.width - w - 2))
            by = max(0, min(by, pil.height - h - 2))
            box = (bx, by, w, h)
            label_overlap = sum(overlap_area(box, u) for u in used_boxes)
            point_overlap = sum(overlap_area(box, u) for u in point_boxes)
            score = label_overlap * 20.0 + point_overlap * 8.0 + rank
            if best is None or score < best[0]:
                best = (score, bx, by, rank)

        _, bx, by, rank = best
        if label_layout is not None:
            label_layout[key] = rank
        # 连接到标签框内离穴位最近的点，明确“点”和“文字”不是同一坐标。
        tx = min(max(x, bx), bx + w)
        ty = min(max(y, by), by + h)
        draw.line((x, y, tx, ty), fill=(235, 235, 235), width=1)
        draw.text((bx, by), label, fill=(255, 255, 255), font=base_font,
                  stroke_width=2, stroke_fill=(0, 0, 0))
        if coordinate_text:
            draw.text((bx, by + font_h - 1), coordinate_text,
                      fill=(255, 235, 120), font=coordinate_font,
                      stroke_width=1, stroke_fill=(0, 0, 0))
        used_boxes.append((bx, by, w, h))

    ordered = sorted(points,key=lambda q: q.get('preview_id',100)) if dense else sorted(points,key=lambda q:tier_order[q['tier']])
    if dense and back_panel:
        lookup={(p['name'],p['side']):p for p in points}
        ids = BACK_IDS.items() if panel_keys is None else ((key, i+1) for i,key in enumerate(panel_keys))
        ordered=[dict(lookup.get(key,dict(name=key[0],side=key[1],
            code='',tier='低',x=0,y=0,display_missing=True)), preview_id=num) for key,num in ids]
    for row, p in enumerate(ordered):
        x, y = int(round(p["x"])), int(round(p["y"]))
        color = tier_color[p["tier"]]
        r = 9 if dense else (5 if p["tier"] != "低" else 4)
        if not p.get('display_missing'):
            draw.ellipse([x - r, y - r, x + r, y + r], fill=color, outline="white", width=2)
        if show_labels:
            label = p["name"] if font_path else p["code"]
            xyz = p.get("camera_point_mm") if show_coordinates else None
            coordinate_text = ""
            if p.get("coordinate_valid") and isinstance(xyz, (list, tuple)) and len(xyz) == 3:
                # 单位固定为 mm，避免每个标签重复写单位而遮挡画面。
                coordinate_text = f"Cam X{xyz[0]:.0f} Y{xyz[1]:.0f} Z{xyz[2]:.0f}"
            if show_coordinates and p.get('base_coordinate_valid'):
                bx, by, bz = p['base_point_mm']
                coordinate_text = f"Base X{bx:.0f} Y{by:.0f} Z{bz:.0f}"
            elif show_coordinates and (base_requested or p.get('coordinate_display_frame')=='Base'):
                coordinate_text = 'Base —'
            if dense:
                number = str(p.get('preview_id',row+1))
                if not p.get('display_missing'):
                    draw.ellipse((x-r,y-r,x+r,y+r),fill=(24,28,34),outline=(255,216,120),width=1)
                    draw.text((x,y),number,font=coordinate_font,anchor='mm',fill=(255,230,160))
                side = {'L':'左','R':'右',None:''}.get(p.get('side'),'')
                ty = 78+row*26
                if row%2==0:
                    draw.rectangle((panel_x+1,ty-2,pil.width-1,ty+23),fill=(31,36,44))
                draw.text((panel_x+12,ty),f'{number:>2} {side}{label}',font=base_font,fill=(245,245,245))
                frame_name, values = '—', None
                if show_coordinates and p.get('base_coordinate_valid'):
                    frame_name, values = 'Base', p['base_point_mm']
                elif base_requested or p.get('coordinate_display_frame')=='Base':
                    frame_name = 'Base'
                elif show_coordinates and p.get('coordinate_valid') and xyz is not None:
                    frame_name, values = 'Cam', xyz
                draw.text((panel_x+132,ty),frame_name,font=base_font,fill=(190,200,210))
                for axis, offset in enumerate((228,314,400)):
                    value = f'{values[axis]:.1f}' if values is not None else '—'
                    draw.text((panel_x+offset,ty),value,font=table_font,anchor='mt',
                              fill=(115,220,255) if axis==2 else (255,220,130))
            else:
                place_label(x, y, label, coordinate_text, (p["name"], p["side"]))

    if status_text:
        draw.rectangle((0, pil.height - font_h - 12, pil.width, pil.height), fill=(25, 25, 25))
        draw.text((8, pil.height - font_h - 6), status_text, font=base_font,
                  fill=(255, 220, 110))

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
            if r['region'] == 'back':
                out[-1].update(preview_id=BACK_IDS[(r['name'],s if r['paired'] else None)],
                    localization_method='pose_proportion_estimate', anatomical_location_validated=False)
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


class RealSenseCapture:
    """D435i 的同步彩色/深度采集器；深度帧对齐到彩色画面。"""

    def __init__(self, width=640, height=480, fps=30, manual_rgb_exposure=None):
        try:
            import pyrealsense2 as rs
        except ImportError as exc:
            raise RuntimeError(
                "缺少 RealSense Python SDK，请在当前环境运行: pip install pyrealsense2"
            ) from exc

        self.rs = rs
        self.width = int(width)
        self.height = int(height)
        self.fps = int(fps)
        self.pipeline = rs.pipeline()
        config = rs.config()
        config.enable_stream(rs.stream.depth, self.width, self.height, rs.format.z16, self.fps)
        config.enable_stream(rs.stream.color, self.width, self.height, rs.format.bgr8, self.fps)
        try:
            profile = self.pipeline.start(config)
            self.serial = profile.get_device().get_info(rs.camera_info.serial_number)
            for sensor in profile.get_device().query_sensors():
                if sensor.supports(rs.option.global_time_enabled):
                    sensor.set_option(rs.option.global_time_enabled, 1)
        except RuntimeError as exc:
            raise RuntimeError(f"无法启动 Intel RealSense 彩色/深度流: {exc}") from exc
        self.capture_clock = None
        if manual_rgb_exposure is not None:
            from rehab_vision.capture_clock import CaptureClock
            self.capture_clock = CaptureClock(rs, profile, manual_rgb_exposure, self.fps)
        self.align = rs.align(rs.stream.color)
        self.depth_frame = None
        self.color_intrinsics = None
        self.opened = True

    def isOpened(self):
        return self.opened

    def get(self, prop):
        if prop == cv2.CAP_PROP_FRAME_WIDTH:
            return float(self.width)
        if prop == cv2.CAP_PROP_FRAME_HEIGHT:
            return float(self.height)
        if prop == cv2.CAP_PROP_FPS:
            return float(self.fps)
        return 0.0

    def read(self):
        # Keep the physical depth exposure before align creates a synthetic
        # color-view depth frame. Never retain a native frame across failures.
        self.raw_depth_frame = None
        self.depth_frame = None
        try:
            raw_frames = self.pipeline.wait_for_frames(5000)
            raw_depth = raw_frames.get_depth_frame()
            frames = self.align.process(raw_frames)
            color_frame = frames.get_color_frame()
            aligned_depth = frames.get_depth_frame()
            if not color_frame or not raw_depth or not aligned_depth:
                return False, None
            raw_time, aligned_time = raw_depth.get_timestamp(), aligned_depth.get_timestamp()
            if (raw_depth.get_frame_number() != aligned_depth.get_frame_number()
                    or not np.isfinite(raw_time) or not np.isfinite(aligned_time)
                    or raw_time != aligned_time
                    or raw_depth.get_frame_timestamp_domain() != aligned_depth.get_frame_timestamp_domain()):
                raise RuntimeError('原生深度与对齐深度不是同一次曝光')
            self.raw_depth_frame, self.depth_frame = raw_depth, aligned_depth
            self.color_intrinsics = color_frame.profile.as_video_stream_profile().intrinsics
            self.received_at = time.monotonic()
            self.frame_metadata = dict(camera_serial=self.serial,
                color_frame_number=color_frame.get_frame_number(),
                color_device_timestamp_ms=color_frame.get_timestamp(),
                color_timestamp_domain=str(color_frame.get_frame_timestamp_domain()))
            # Preserve raw clock evidence. Only the control process may validate
            # a capture/robot pairing; host receipt is never labelled exposure.
            try:
                if self.capture_clock is None:
                    raise ValueError('未启用经过单位核对的手动曝光采集配置')
                self.frame_metadata['exposure_clock']=self.capture_clock.read(color_frame,self.depth_frame)
            except (RuntimeError,ValueError) as exc:
                self.frame_metadata['exposure_clock_error']=str(exc)
                if getattr(self,'last_clock_error',None)!=str(exc):
                    print(f'曝光时钟校验未就绪：{exc}',file=sys.stderr)
                self.last_clock_error=str(exc)
            return True, np.asanyarray(color_frame.get_data())
        except RuntimeError as exc:
            self.raw_depth_frame = self.depth_frame = None
            print(f"RealSense 取帧失败: {exc}", file=sys.stderr)
            return False, None

    def attach_depth_coordinates(self, points, rgb_shape):
        """为穴位像素坐标补充 RealSense 相机坐标，单位毫米。"""
        if self.depth_frame is None or self.color_intrinsics is None:
            return points
        rgb_h, rgb_w = rgb_shape[:2]
        scale_x = self.width / max(1, rgb_w)
        scale_y = self.height / max(1, rgb_h)
        out = []
        for p in points:
            u = int(round(p["x"] * scale_x))
            v = int(round(p["y"] * scale_y))
            samples = []
            for yy in range(max(0, v - 2), min(self.height, v + 3)):
                for xx in range(max(0, u - 2), min(self.width, u + 3)):
                    distance_m = self.depth_frame.get_distance(xx, yy)
                    if distance_m > 0:
                        samples.append(distance_m)
            q = dict(p, coordinate_valid=False, depth_mm=None, camera_point_mm=None)
            if samples and 0 <= u < self.width and 0 <= v < self.height:
                distance_m = float(np.median(samples))
                xyz_m = self.rs.rs2_deproject_pixel_to_point(
                    self.color_intrinsics, [float(u), float(v)], distance_m)
                q.update(coordinate_valid=True,
                         depth_mm=round(distance_m * 1000.0, 1),
                         camera_point_mm=[round(float(axis) * 1000.0, 1) for axis in xyz_m])
            out.append(q)
        return out

    def release(self):
        if self.opened:
            self.opened = False
            if self.capture_clock is not None:
                errors = self.capture_clock.restore()
                if errors: print(f"恢复相机曝光配置失败: {errors}", file=sys.stderr)
            try:
                self.pipeline.stop()
            except RuntimeError as exc:
                print(f"相机释放提示: {exc}", file=sys.stderr)


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
    ap.add_argument("--view", choices=("front", "back", "both"), default="front",
                    help="拍摄方向：正面 front、背面 back；不建议使用 both")
    ap.add_argument("--face-mode", choices=("core", "all", "off"), default="core",
                    help="面部穴位：核心 core、全部 all、关闭 off（默认 core，减少拥挤）")
    ap.add_argument("--back-offset-x", type=float, default=0.0,
                    help="背部整体左右校准，单位为肩宽；向图像右侧为正（如 0.02）")
    ap.add_argument("--back-offset-y", type=float, default=0.0,
                    help="背部整体上下校准，单位为躯干长度；向图像下方为正")
    ap.add_argument("--no-labels", action="store_true", help="只画穴位点，不画名称")
    ap.add_argument("--model", default=DEFAULT_MODEL,
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
    out_points = compute_points(
        body, make_rules(), tiers_ok, want, rgb.shape,
        view=args.view, face_mode=args.face_mode,
        back_offset_x=args.back_offset_x, back_offset_y=args.back_offset_y,
    )
    annotated = render_frame(rgb, out_points, upscale=args.upscale,
                             show_labels=not args.no_labels)
    Image.fromarray(annotated).save(args.output)

    result = {
        "image": args.input,
        "coordinate_space": "像素（原图分辨率）",
        "view": args.view,
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
def save_snapshot(rgb, points, output_dir="acupoint_snapshots", show_labels=True,
                  show_coordinates=False):
    """保存带唯一文件名的标注图 + JSON；RealSense 快照可包含 XYZ 相机坐标。"""
    annotated = render_frame(rgb, points, upscale=1.2, font_size=16,
                             show_labels=show_labels,
                             show_coordinates=show_coordinates,
                             coordinate_font_size=10)
    # 相对于启动程序时的当前路径保存，毫秒时间戳确保连续截图不会覆盖。
    os.makedirs(output_dir, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    stem = f"acupoints_{stamp}_{time.time_ns() // 1_000_000 % 1000:03d}"
    jpg_path = os.path.join(output_dir, stem + ".jpg")
    json_path = os.path.join(output_dir, stem + ".json")
    suffix = 1
    while os.path.exists(jpg_path) or os.path.exists(json_path):
        stem = f"acupoints_{stamp}_{time.time_ns() // 1_000_000 % 1000:03d}_{suffix}"
        jpg_path = os.path.join(output_dir, stem + ".jpg")
        json_path = os.path.join(output_dir, stem + ".json")
        suffix += 1
    Image.fromarray(annotated).save(jpg_path)
    data = {
        "image": os.path.abspath(jpg_path),
        "note": "实时版快照：x/y为原图像素；XYZ如有为相机毫米坐标，可能经过显示滤波，非机械臂执行依据",
        # compute_points 已经输出像素坐标，不能再次乘图像宽高。
        "acupoints": [dict(p, x=round(p["x"], 1), y=round(p["y"], 1)) for p in points],
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"已保存快照: {os.path.abspath(jpg_path)}  {os.path.abspath(json_path)}")


def main_live():
    if not HAVE_CV2:
        sys.exit("实时模式需要 opencv-python，请先运行: pip install opencv-python")
    ap = argparse.ArgumentParser(description="人体穴位定位（摄像头/视频实时模式）")
    ap.add_argument('--base-coordinates', action='store_true', help='从控制进程取得标定后的 Base 坐标预览')
    ap.add_argument('--base-uri', default='ws://127.0.0.1:8766/coordinates')
    ap.add_argument('--base-output', default=str(Path(__file__).resolve().parent / 'reports/base_coordinates_latest.json'))
    ap.add_argument('--pose-auto-rotation', action=argparse.BooleanOptionalAction, default=False,
                    help='启动时比较四个模型输入方向并固定使用最可靠方向；不改变相机坐标系')
    ap.add_argument("--source", default="realsense",
                    help="视频源：realsense（默认，Intel D435i）、摄像头编号或视频文件")
    ap.add_argument("--rs-width", type=int, default=640, choices=(424, 480, 640, 848, 1280),
                    help="RealSense 彩色/深度同步流宽度（默认 640）")
    ap.add_argument("--rs-height", type=int, default=480, choices=(240, 360, 480, 720),
                    help="RealSense 彩色/深度同步流高度（默认 480）")
    ap.add_argument("--rs-fps", type=int, default=30, choices=(6, 15, 30, 60),
                    help="RealSense 帧率（默认 30）")
    ap.add_argument("--model", default=DEFAULT_MODEL,
                    help="MediaPipe 姿态模型路径（缺失自动下载）")
    ap.add_argument("--pose-backend", choices=("mediapipe", "dwpose-l", "rtmw-l"),
                    default="mediapipe", help="人体关键点模型；ONNX 模式为单人整幅图/手选区域推理")
    ap.add_argument("--pose-threshold", type=float, default=0.5,
                    help="ONNX 关键点分数下限；不是穴位准确率")
    ap.add_argument("--pose-rotation", type=int, choices=(0, 90, 180, 270), default=0,
                    help="ONNX 输入顺时针旋转角度，坐标仍映射回原图；头朝左可用90")
    ap.add_argument("--back-only", action="store_true", help="仅显示背部穴位并要求肩髋参考完整")
    ap.add_argument("--torso-only", action="store_true", help="仅显示躯干穴位，可按 v 切换正面/背面")
    ap.add_argument("--stabilizer", choices=("adaptive", "ema", "off"), default="adaptive",
                    help="实时显示滤波；adaptive 抑制抖动并拒绝孤立跳点")
    ap.add_argument("--tiers", default="高,中", help="显示的置信档，逗号分隔")
    ap.add_argument("--points", default="", help="只显示指定穴位（逗号分隔中文名）")
    ap.add_argument("--view", choices=("front", "back"), default="front",
                    help="初始拍摄方向；运行中按 v 可切换")
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
    ap.add_argument("--max-frames", type=int, default=0,
                    help="处理指定帧数后退出；0 为持续运行，用于摄像头自检")
    args = ap.parse_args()
    if not 0 < args.pose_threshold <= 1:
        ap.error("--pose-threshold 必须在 (0,1] 内")
    if args.back_only:
        args.view, args.face_mode = "back", "off"

    tiers_ok = {t.strip() for t in args.tiers.split(",") if t.strip()}
    want = {n.strip() for n in args.points.split(",") if n.strip()}
    if args.back_only:
        back_names = {r["name"] for r in make_rules() if r["region"] == "back"}
        want = want & back_names if want else back_names
        if not want:
            ap.error("--back-only 下指定的穴位没有可用背部规则")

    if args.pose_backend == "mediapipe":
        landmarker = open_landmarker(ensure_model(args.model))
    else:
        from pose_backends import WholeBodyONNX
        landmarker = WholeBodyONNX(args.pose_backend, threshold=args.pose_threshold,
                                  rotation=args.pose_rotation)
    print(f"关键点模型: {args.pose_backend}；显示滤波: {args.stabilizer}")

    is_realsense = args.source.lower() in {"realsense", "intel", "d435", "d435i"}
    if is_realsense:
        try:
            cap = RealSenseCapture(args.rs_width, args.rs_height, args.rs_fps)
        except RuntimeError as exc:
            sys.exit(str(exc))
        src = "Intel RealSense D435i"
    else:
        src = int(args.source) if args.source.isdigit() else args.source
        cap = cv2.VideoCapture(src)
    if not cap.isOpened():
        sys.exit(f"无法打开视频源: {args.source}")
    fps = cap.get(cv2.CAP_PROP_FPS)
    if not fps or fps <= 0:
        fps = 30.0
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    stream_label = "Intel RealSense D435i（彩色+对齐深度）" if is_realsense else str(args.source)
    print(f"视频源: {stream_label}  原始尺寸 {w}x{h}  fps≈{fps:.1f}")

    rules = make_rules()

    writer = None
    if args.output_video:
        scale = args.max_width / w if w > args.max_width else 1.0
        video_size = (int(w*scale),int(h*scale))
        writer = cv2.VideoWriter(args.output_video, cv2.VideoWriter_fourcc(*"mp4v"),
                                 fps, (int(w * scale), int(h * scale)))
        print(f"标注视频将写入: {args.output_video}")

    if args.torso_only:
        rules = [rule for rule in rules if rule["region"] in {"front_trunk", "back"}]
    view_mode = args.view
    # 背部规则标为低置信，但在明确选择 back 后应默认显示。
    show_low = view_mode == "back"
    paused = False
    frame_idx = 0
    t_last = time.time()
    snap_saved = False
    is_camera = isinstance(src, int) or is_realsense
    def new_smoother():
        return StablePoints() if args.stabilizer == "adaptive" else PointSmoother(args.smooth_alpha)
    smoother = new_smoother()
    torso_tracker = TorsoDisplayTracker(acquire=max(.5,args.pose_threshold))
    base_display_filter = BaseDisplayFilter()
    label_layout = {}
    base_client = None
    base_packet = None
    capture_generation = getattr(getattr(cap, 'recovery', None), 'generation', None)
    if args.base_coordinates:
        if not is_realsense:
            cap.release()
            landmarker.close()
            sys.exit('Base 坐标需要已标定的 RealSense 彩色/深度流')
        from rehab_vision.base_client import BaseClient, write_latest, invalidate
        base_client = BaseClient(args.base_uri)

    try:
        if args.pose_auto_rotation and args.pose_backend!='mediapipe':
            from pose_backends import select_startup_rotation
            initial_frames=[]
            for i in range(23):
                ok,initial=cap.read()
                if not ok:
                    raise RuntimeError('无法读取方向自检帧')
                if i>=20:initial_frames.append(initial.copy())
            rotation_report=select_startup_rotation(landmarker,initial_frames)
            print('启动输入方向自检: '+json.dumps(rotation_report,ensure_ascii=False),flush=True)
            print(f'已固定模型输入方向: {landmarker.rotation} 度；原始相机坐标不变',flush=True)
        while True:
            if not paused:
                ok, frame = cap.read()
                if not ok:
                    print("视频结束")
                    break
                generation = getattr(getattr(cap, 'recovery', None), 'generation', None)
                if generation != capture_generation:
                    # USB recovery starts a new camera session. Do not blend
                    # previous point/torso/Base display history into its image.
                    smoother = new_smoother()
                    torso_tracker.reset()
                    base_display_filter = BaseDisplayFilter()
                    label_layout.clear()
                    base_packet = None
                    capture_generation = generation
                fh, fw = frame.shape[:2]
                if fw > args.max_width:
                    scale = args.max_width / fw
                    frame = cv2.resize(frame, (args.max_width, int(fh * scale)),
                                       interpolation=cv2.INTER_AREA)
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                ts = int(time.monotonic() * 1000) if is_camera else int(frame_idx * 1000 / fps)
                if args.pose_backend == "mediapipe":
                    results = landmarker.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB,
                                                                   data=rgb), ts)
                    landmarks = results.pose_landmarks[0] if results.pose_landmarks else None
                else:
                    landmarks = landmarker.infer(frame)
                frame_idx += 1

                points = []
                status_text = "未检测到可靠人体关键点，请调整相机和入镜范围"
                display_issue = status_text
                display_landmarks = (torso_tracker.update(landmarks,rgb.shape)
                                     if args.stabilizer == 'adaptive' else landmarks)
                if landmarks:
                    display_issue = ''
                    body = Body(display_landmarks,allow_display_tracking=True)
                    effective_tiers = tiers_ok | ({"低"} if show_low else set())
                    points = compute_points(
                        body, rules, effective_tiers, want, rgb.shape,
                        view=view_mode, face_mode=args.face_mode,
                        back_offset_x=args.back_offset_x,
                        back_offset_y=args.back_offset_y,
                    )
                    if not show_low:
                        points = [p for p in points if p["tier"] != "低"]
                    if not points:
                        display_issue = '当前关键点或筛选条件未生成穴位'
                    status_text = "点位为比例估算；XYZ 为相机坐标(mm)；滤波仅用于显示"
                    if (view_mode == "back" or args.torso_only) and not body.visible(SH_L, SH_R, HIP_L, HIP_R):
                        missing = [name for name, idx in (('左肩',SH_L),('右肩',SH_R),('左髋',HIP_L),('右髋',HIP_R))
                                   if not body.visible(idx)]
                        display_issue = '关键点不足：' + '/'.join(missing) + '（需可靠识别且入镜）'
                # Export the instantaneous model pixels/depth, BEFORE display filters.
                if base_client:
                    # Tracking never lowers the validity bar of exported data.
                    strict_points = compute_points(Body(landmarks),rules,effective_tiers,want,rgb.shape,
                        view=view_mode,face_mode=args.face_mode,
                        back_offset_x=args.back_offset_x,back_offset_y=args.back_offset_y) if landmarks else []
                    if not show_low:
                        strict_points=[p for p in strict_points if p['tier']!='低']
                    measured_points = cap.attach_depth_coordinates(strict_points, rgb.shape)
                    base_packet = base_client.project(measured_points, cap.frame_metadata, cap.received_at)
                    write_latest(args.base_output, base_packet)
                if args.stabilizer != "off":
                    points = smoother.update(points)  # Also update/reset on lost detections.
                    if not points and not display_issue:
                        display_issue = '穴位稳定中，等待连续可靠帧'
                if is_realsense:
                    points = cap.attach_depth_coordinates(points, rgb.shape)
                    if args.stabilizer == "adaptive":
                        points = smoother.depth(points)
                    if points and not any(p.get("coordinate_valid") for p in points):
                        display_issue = "深度无效或坐标尚未稳定：请调整距离，检查镜头遮挡和反光"
                if base_client:
                    lookup = {(p['name'], p['side']): p for p in base_packet['data']['points']}
                    for p in points:
                        side = {'L':'LEFT','R':'RIGHT',None:'MIDLINE'}.get(p.get('side'),p.get('side'))
                        raw = lookup.get((p['name'],side), {})
                        p.update(base_point_mm=raw.get('base_point_mm'),
                                 base_coordinate_valid=raw.get('base_coordinate_valid',False),
                                 coordinate_display_frame='Base')
                    status_text = base_packet['data']['error'] or 'Base 坐标(mm)预览；原始测量输出，尚未开放自动跑点'
                if args.stabilizer == 'adaptive':
                    points = base_display_filter.update(points)
                    if torso_tracker.state == 'TRACKING_WEAK':
                        for p in points:
                            p.update(coordinate_valid=False,camera_point_mm=None,depth_mm=None,
                                     base_coordinate_valid=False,base_point_mm=None)
                        display_issue = '关键点置信度暂低：仅跟踪图中位置，坐标暂停'
                if view_mode == 'back' and not display_issue and (not base_client or not base_packet['data']['error']):
                    status_text = f'背部候选 {len(points)} 个 · 比例估算；坐标平滑仅供显示' if args.stabilizer=='adaptive' else f'背部候选 {len(points)} 个 · 比例估算；坐标仅供预览'
                # A healthy coordinate RPC must not mask a failed pose/depth stage.
                status_text = display_issue or status_text
                if (args.save_snapshot and points and not snap_saved
                        and (not is_realsense or any(p.get("coordinate_valid") for p in points))):
                    save_snapshot(rgb, points, show_labels=not args.no_labels,
                                  show_coordinates=is_realsense)
                    snap_saved = True

                annotated = render_frame(
                    rgb, points, upscale=1.0,
                    # 三维坐标作为名称的第二行，用更小字体保持实时画面可读。
                    font_size=14 if is_realsense else 16,
                    show_labels=not args.no_labels,
                    show_coordinates=is_realsense,
                    coordinate_font_size=10,
                    label_layout=label_layout,
                    back_panel=view_mode=='back' and (args.torso_only or args.back_only),
                    base_requested=base_client is not None,
                    status_text=("正面" if view_mode == "front" else "背面") + " [V切换] | " + status_text,
                )
                fps_now = 1.0 / max(1e-6, time.time() - t_last)
                t_last = time.time()
                bgr = cv2.cvtColor(annotated, cv2.COLOR_RGB2BGR)
                depth_count = sum(bool(p.get("coordinate_valid")) for p in points)
                source_text = "D435i" if is_realsense else "RGB"
                overlay = (f"{args.pose_backend} fps:{fps_now:4.1f} pts:{len(points)} xyz:{depth_count} "
                           f"{view_mode} [v]view [r]ROI [o]rotate [s]save [q]quit")
                cv2.putText(bgr, overlay, (10, 24), cv2.FONT_HERSHEY_SIMPLEX,
                            0.55, (0, 255, 0), 2, cv2.LINE_AA)

                if writer is not None:
                    # Preserve the configured video dimensions when the dense
                    # coordinate panel expands the display canvas.
                    tw, th = video_size
                    fit = min(tw/bgr.shape[1],th/bgr.shape[0])
                    video_frame = np.zeros((th,tw,3),dtype=np.uint8)
                    fitted = cv2.resize(bgr,(max(1,int(bgr.shape[1]*fit)),max(1,int(bgr.shape[0]*fit))))
                    video_frame[:fitted.shape[0],:fitted.shape[1]] = fitted
                    writer.write(video_frame)

                if frame_idx == 1 or frame_idx % 30 == 0:
                    print(f"帧 {frame_idx}  fps={fps_now:.1f}  穴位 {len(points)} 个  "
                          f"有效深度 {depth_count} 个", flush=True)
                    if display_issue:
                        print(display_issue, flush=True)
                        if args.pose_backend != 'mediapipe':
                            print('肩/髋置信度（左肩、右肩、左髋、右髋）: ' +
                                  ', '.join(f'{landmarker.last_scores[i]:.3f}' for i in (5,6,11,12)), flush=True)
                    if base_packet:
                        count = sum(p['base_coordinate_valid'] for p in base_packet['data']['points'])
                        print(f"Base 有效坐标 {count} 个；{base_packet['data']['error'] or '转换服务正常（预览）'}", flush=True)
                if args.max_frames > 0 and frame_idx >= args.max_frames:
                    break

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
                # 处理标题栏关闭事件，避免下一帧 imshow 将窗口重新创建。
                try:
                    window_visible = cv2.getWindowProperty(
                        "Acupoint Locator - 穴位实时定位", cv2.WND_PROP_VISIBLE)
                except cv2.error:
                    break  # 窗口已销毁。
                if window_visible < 1:
                    break
                if key in (ord("q"), 27):
                    break
                elif key in (ord("s"), ord("S")):
                    save_snapshot(rgb, points, show_labels=not args.no_labels,
                                  show_coordinates=is_realsense)
                elif key == ord("t"):
                    show_low = not show_low
                    print(f"低置信穴位显示: {'开' if show_low else '关'}")
                elif key in (ord("v"), ord("V")):
                    if base_packet:
                        write_latest(args.base_output, invalidate(base_packet, '拍摄面切换，等待新帧'))
                    if args.back_only:
                        print("当前为背部专用模式，固定 back；全身模式请去掉 --back-only")
                        continue
                    view_mode = "back" if view_mode == "front" else "front"
                    show_low = view_mode == "back"
                    smoother = new_smoother()
                    torso_tracker.reset()
                    base_display_filter = BaseDisplayFilter()
                    label_layout.clear()
                    paused = False
                    print(f"拍摄方向已切换为: {view_mode}（{'正面' if view_mode == 'front' else '背面'}）")
                elif key == ord("r") and args.pose_backend != "mediapipe":
                    box = cv2.selectROI("Choose person region", frame, showCrosshair=True)
                    cv2.destroyWindow("Choose person region")
                    x, y, rw, rh = box
                    landmarker.roi = [x, y, x + rw, y + rh] if rw > 0 and rh > 0 else None
                    smoother = new_smoother()
                    torso_tracker.reset()
                    base_display_filter = BaseDisplayFilter()
                    label_layout.clear()
                    print(f"单人推理区域: {landmarker.roi or '整幅画面'}")
                elif key == ord("o") and args.pose_backend != "mediapipe":
                    landmarker.rotation = (landmarker.rotation + 90) % 360
                    smoother = new_smoother()
                    torso_tracker.reset()
                    base_display_filter = BaseDisplayFilter()
                    label_layout.clear()
                    print(f"模型输入旋转: {landmarker.rotation} 度（相机坐标仍为原始光学坐标）")
                elif key == ord(" "):
                    paused = not paused
                    if paused and base_packet:
                        write_latest(args.base_output, invalidate(base_packet, '显示已暂停，坐标失效'))
                        cv2.putText(bgr, 'PAUSED - coordinates INVALID', (10,55),
                                    cv2.FONT_HERSHEY_SIMPLEX, .6, (0,0,255), 2)
                    print("已暂停" if paused else "继续")

    except KeyboardInterrupt:
        print("收到退出信号")
    finally:
        if base_client:
            base_client.close()
            if base_packet:
                write_latest(args.base_output, invalidate(base_packet, '视觉窗口已退出，坐标失效'))
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
