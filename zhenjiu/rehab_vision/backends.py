"""RealSense and explicitly marked replay adapters. No robot access."""
import json
import math
from pathlib import Path
import time
from types import SimpleNamespace

from .engine import Frame


class RealSource:
    mode = 'REAL'

    def __init__(self):
        self.cap = None

    def open(self):
        from acupoint_locator_refactored import RealSenseCapture
        self.cap = RealSenseCapture(640, 480, 30)

    def read(self):
        import numpy as np
        cap = self.cap
        frames = cap.align.process(cap.pipeline.wait_for_frames(1000))
        color, depth = frames.get_color_frame(), frames.get_depth_frame()
        if not color or not depth:
            return None
        intrinsics = color.profile.as_video_stream_profile().intrinsics
        # Retain the matching depth frame and intrinsics with this color frame.
        # Never read cap.depth_frame later from the inference thread.
        depth_context = SimpleNamespace(rs=cap.rs, width=color.get_width(), height=color.get_height(),
                                       depth_frame=depth, color_intrinsics=intrinsics)
        return Frame((np.asanyarray(color.get_data()).copy(), depth_context), metadata=dict(
            camera_serial=cap.serial,
            color_device_timestamp_ms=color.get_timestamp(),
            color_timestamp_domain=str(color.get_frame_timestamp_domain()),
            depth_device_timestamp_ms=depth.get_timestamp(),
            depth_timestamp_domain=str(depth.get_frame_timestamp_domain()),
            color_frame_number=color.get_frame_number(), depth_frame_number=depth.get_frame_number(),
            width=color.get_width(), height=color.get_height(),
            intrinsics=dict(fx=intrinsics.fx, fy=intrinsics.fy, ppx=intrinsics.ppx,
                            ppy=intrinsics.ppy, model=str(intrinsics.model), coeffs=list(intrinsics.coeffs)),
            capture_time_valid=False))

    def close(self):
        if self.cap:
            try:
                self.cap.release()
            finally:
                self.cap = None


class PoseModel:
    def __init__(self, contract, model_path, backend='mediapipe', rotation=0):
        self.contract, self.model_path = contract, Path(model_path)
        self.landmarker = None
        self.backend, self.rotation = backend, rotation

    def open(self):
        import acupoint_locator_refactored as locator
        self.locator = locator
        self.rules = locator.make_rules()
        if self.backend != 'mediapipe':
            from pose_backends import WholeBodyONNX
            self.landmarker = WholeBodyONNX(self.backend, rotation=self.rotation)
            return
        if not self.model_path.is_file():
            raise FileNotFoundError(f'本地姿态模型不存在: {self.model_path}')
        self.locator = locator
        # IMAGE mode avoids substituting a host receipt time for a camera timestamp.
        options = locator.vision.PoseLandmarkerOptions(
            base_options=locator.mp_python.BaseOptions(model_asset_path=str(self.model_path)),
            running_mode=locator.vision.RunningMode.IMAGE, num_poses=1,
            min_pose_detection_confidence=0.5, min_pose_presence_confidence=0.5)
        self.landmarker = locator.vision.PoseLandmarker.create_from_options(options)
        self.rules = locator.make_rules()

    def infer(self, frame, data):
        loc = self.locator
        bgr, depth_context = frame.payload
        rgb = loc.cv2.cvtColor(bgr, loc.cv2.COLOR_BGR2RGB)
        if self.backend == 'mediapipe':
            detected = self.landmarker.detect(loc.mp.Image(image_format=loc.mp.ImageFormat.SRGB, data=rgb))
            landmarks = detected.pose_landmarks[0] if detected.pose_landmarks else None
        else:
            landmarks = self.landmarker.infer(bgr)
        if not landmarks:
            return []
        names = {e['name'] for e in self.contract.regions[data['region']]}
        points = loc.compute_points(loc.Body(landmarks), self.rules,
                                    {'高', '中', '低'}, names, rgb.shape,
                                    view=data['view'], face_mode='off')
        # Avoid image-edge points that would deproject outside the frame.
        points = [p for p in points if 0 <= p['x'] < rgb.shape[1] - 0.5
                  and 0 <= p['y'] < rgb.shape[0] - 0.5]
        return loc.RealSenseCapture.attach_depth_coordinates(depth_context, points, rgb.shape)

    def close(self):
        if self.landmarker:
            self.landmarker.close()
            self.landmarker = None


class ReplaySource:
    """Recorded *raw point* packets; never masquerades as a live camera.

    A packet repeats at 30 Hz for deterministic communication tests. Original
    exposure timestamps/normals are deliberately not promoted to live validity.
    """
    mode = 'SIMULATION'

    def __init__(self, path=None, records=None):
        self.records = records if records is not None else json.loads(Path(path).read_text(encoding='utf-8'))['frames']
        if not self.records:
            raise ValueError('回放 frames 不能为空')
        self.index = 0

    def open(self):
        pass

    def read(self):
        time.sleep(1 / 30)
        packet = self.records[self.index % len(self.records)]
        self.index += 1
        return Frame(packet, metadata={'source': 'SIMULATION raw-point replay'})

    def close(self):
        pass


class ReplayModel:
    def open(self):
        pass

    def infer(self, frame, data):
        if frame.payload.get('region') != data['region'] or frame.payload.get('view') != data['view']:
            return []
        return frame.payload['points']

    def close(self):
        pass
