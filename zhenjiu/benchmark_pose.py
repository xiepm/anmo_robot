"""Same recorded RGB frames, timing and availability; NOT an accuracy benchmark."""
import json
from pathlib import Path
import time
import numpy as np
import cv2
from pose_backends import WholeBodyONNX
import acupoint_locator_refactored as loc
from stabilization import StablePoints

ROOT = Path(__file__).resolve().parent / 'reports/pose_upgrade'
sample = np.load(ROOT / 'camera_sample.npz')
frames = sample['frames'][::3]
rules = loc.make_rules()
summary = []
for name in ['mediapipe', 'dwpose-l', 'rtmw-l']:
    model = loc.open_landmarker(loc.DEFAULT_MODEL) if name == 'mediapipe' else WholeBodyONNX(name)
    for angle in ([0] if name == 'mediapipe' else [0, 90]):
        if name != 'mediapipe': model.rotation = angle
        times, scores, points_log = [], [], []
        for i, frame in enumerate(frames):
            start = time.monotonic()
            if name == 'mediapipe':
                rgb=cv2.cvtColor(frame,cv2.COLOR_BGR2RGB)
                r=model.detect_for_video(loc.mp.Image(image_format=loc.mp.ImageFormat.SRGB,data=rgb),i*100+1)
                lm=r.pose_landmarks[0] if r.pose_landmarks else None
            else: lm=model.infer(frame)
            times.append((time.monotonic()-start)*1000)
            pts=[]
            if lm:
                pts=loc.compute_points(loc.Body(lm),rules,{'高','中','低'},{'大椎','命门','肾俞'},frame.shape,view='back',face_mode='off')
            scores.append([float(lm[j].visibility) for j in [11,12,23,24]] if lm else [0]*4)
            points_log.append(pts)
        entry=dict(model=name,rotation=angle,frames=len(frames),median_ms=float(np.median(times[1:])),
                   frames_with_back_points=sum(bool(p) for p in points_log),
                   mean_shoulder_hip_scores=np.mean(scores,axis=0).tolist())
        summary.append(entry)
        print(entry,flush=True)
        (ROOT/f'{name}_{angle}_points.json').write_text(json.dumps(points_log,ensure_ascii=False,indent=2))
        annotated=loc.render_frame(cv2.cvtColor(frames[-1],cv2.COLOR_BGR2RGB),points_log[-1],upscale=1,font_size=16)
        cv2.imwrite(str(ROOT/f'{name}_{angle}.jpg'),cv2.cvtColor(annotated,cv2.COLOR_RGB2BGR))
    model.close()
(ROOT/'comparison.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
