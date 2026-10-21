"""Single-person top-down DWPose-L / RTMW-L ONNX adapters.

Exports: OpenMMLab model zoo, paths/checksums in models/wholebody/manifest.json.
Pre/postprocessing follows the model's SimCC contract (BGR, affine crop,
mean/std, split ratio 2); no person detector gate for close torso views.
"""
from pathlib import Path
from types import SimpleNamespace
import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent / 'models' / 'wholebody'
# COCO body17 -> MediaPipe indices consumed by the existing geometry rules.
# Unsupported face detail/finger landmarks remain invisible; never duplicate an
# eye or wrist into a made-up anatomical landmark.
BODY_MAP = {0: 0, 2: 1, 5: 2, 7: 3, 8: 4, 11: 5, 12: 6, 13: 7, 14: 8,
            15: 9, 16: 10, 23: 11, 24: 12, 25: 13, 26: 14, 27: 15, 28: 16}


def rotation_matrix(shape, rotation):
    h, w = shape[:2]
    return np.array({0: [[1, 0, 0], [0, 1, 0]],
                     90: [[0, -1, h - 1], [1, 0, 0]],
                     180: [[-1, 0, w - 1], [0, -1, h - 1]],
                     270: [[0, 1, 0], [-1, 0, w - 1]]}[rotation], dtype=np.float32)


def transform_points(points, matrix):
    return np.asarray(points) @ matrix[:, :2].T + matrix[:, 2]


def affine_input(bgr, input_size, roi=None):
    h, w = bgr.shape[:2]
    box = np.array(roi if roi is not None else [0, 0, w, h], dtype=np.float32)
    center = (box[:2] + box[2:]) / 2
    scale = (box[2:] - box[:2]) * 1.25
    iw, ih = input_size
    scale[0] = max(scale[0], scale[1] * iw / ih)
    scale[1] = scale[0] * ih / iw
    factor = iw / scale[0]
    matrix = np.array([[factor, 0, iw / 2 - center[0] * factor],
                       [0, factor, ih / 2 - center[1] * factor]], dtype=np.float32)
    crop = cv2.warpAffine(bgr, matrix, (iw, ih), flags=cv2.INTER_LINEAR)
    crop = (crop.astype(np.float32) - np.array([123.675, 116.28, 103.53], np.float32))
    crop /= np.array([58.395, 57.12, 57.375], np.float32)
    return np.ascontiguousarray(crop.transpose(2, 0, 1)[None]), center, scale


def landmarks_from_coco(keypoints, scores, shape, threshold=0.5):
    h, w = shape[:2]
    out = [SimpleNamespace(x=0., y=0., visibility=0., presence=0.) for _ in range(33)]
    for mp_idx, coco_idx in BODY_MAP.items():
        x, y = keypoints[coco_idx]
        score = float(scores[coco_idx])
        # Keep the original model score rather than manufacturing visibility.
        inside = (np.isfinite([x, y, score]).all() and 0.01*w <= x < 0.99*w
                  and 0.01*h <= y < 0.99*h and score >= threshold)
        out[mp_idx] = SimpleNamespace(x=float(x / w), y=float(y / h),
                                     visibility=score if inside else 0., presence=score if inside else 0.,
                                     raw_visibility=score)
    return out


class WholeBodyONNX:
    def __init__(self, name='rtmw-l', threshold=0.5, threads=4, rotation=0):
        import onnxruntime as ort
        path = ROOT / f'{name}.onnx'
        if not path.is_file():
            raise FileNotFoundError(f'缺少 {path}；运行 models/wholebody/download_models.py 下载官方模型')
        options = ort.SessionOptions()
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(path), options, providers=['CPUExecutionProvider'])
        inp = self.session.get_inputs()[0]
        self.input_name = inp.name
        self.input_size = (int(inp.shape[3]), int(inp.shape[2]))
        self.threshold, self.name = threshold, name
        self.rotation = rotation
        self.roi = None
        self.last_keypoints = self.last_scores = None

    def infer(self, bgr):
        original_shape = bgr.shape
        matrix = rotation_matrix(original_shape, self.rotation)
        roi = self.roi
        if self.rotation:
            bgr = cv2.rotate(bgr, {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180,
                                  270: cv2.ROTATE_90_COUNTERCLOCKWISE}[self.rotation])
        if roi is not None:
            x1, y1, x2, y2 = roi
            corners = transform_points([[x1, y1], [x1, y2], [x2, y1], [x2, y2]], matrix)
            roi = [*corners.min(axis=0), *corners.max(axis=0)]
        tensor, center, scale = affine_input(bgr, self.input_size, roi)
        x, y = self.session.run(None, {self.input_name: tensor})
        if x.shape[1] != 133 or y.shape[1] != 133:
            raise RuntimeError('不是预期的 COCO-WholeBody 133 点模型')
        coords = np.stack([x[0].argmax(-1), y[0].argmax(-1)], axis=-1).astype(np.float32) / 2
        scores = (x[0].max(-1) + y[0].max(-1)) * 0.5
        coords = coords / np.array(self.input_size) * scale + center - scale / 2
        coords = transform_points(coords, cv2.invertAffineTransform(matrix))
        self.last_keypoints, self.last_scores = coords, scores
        return landmarks_from_coco(coords, scores, original_shape, self.threshold)

    def close(self):
        self.session = None


def select_startup_rotation(model, frames):
    """Choose once from current imagery; never switch frame conventions at runtime.

Every inference still maps pixels back into the original camera image. Require
observed shoulders/hips and consistent left/right geometry before scoring.
"""
    original=model.rotation
    report=[]
    for angle in (0,90,180,270):
        model.rotation=angle
        qualities=[]
        for frame in frames:
            lm=model.infer(frame)
            xy=np.array([[lm[i].x,lm[i].y] for i in (11,12,23,24)])
            score=min(lm[i].visibility for i in (11,12,23,24))
            shoulder=xy[0]-xy[1];hip=xy[2]-xy[3]
            trunk=(xy[2]+xy[3]-xy[0]-xy[1])/2
            valid=(np.isfinite(xy).all() and score>=max(.5,model.threshold)
                   and min(np.linalg.norm(shoulder),np.linalg.norm(hip),np.linalg.norm(trunk))>=.08
                   and np.dot(shoulder,hip)>0)
            qualities.append(float(score) if valid else 0.)
        report.append(dict(rotation=angle,valid_frames=sum(q>0 for q in qualities),
                           median_score=float(np.median(qualities))))
    best=max(report,key=lambda r:(r['valid_frames'],r['median_score'],r['rotation']==original))
    model.rotation=best['rotation'] if best['valid_frames'] else original
    return report
