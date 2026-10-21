"""Manual stopped-pose collection. Only robot feedback and math queries are allowed."""
import argparse
import contextlib
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

import cv2
import numpy as np
import pyrealsense2 as rs

from check_board import detect

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src/massage_controller'))
from massage_controller.sdk_readonly import ReadOnlyReader


class CalibrationReader(ReadOnlyReader):
    # These two vendor functions only calculate poses; no motion/config writes.
    allowed_commands = ReadOnlyReader.allowed_commands | {'UcsTcp2Base', 'RPY2Quaternion'}

    def flange(self, sample):
        tcp = sample['tcp_pose']['xyz_mm'] + sample['tcp_pose']['rxyz_deg']
        pose, quat = [], []
        with contextlib.redirect_stdout(sys.stderr):
            rc = self.sdk.HRIF_UcsTcp2Base(
                0, tcp, sample['configured_tool_pose_mm_deg'],
                sample['configured_user_pose_mm_deg'], pose)
            if rc or len(pose) != 6:
                raise ValueError(f'Vendor base conversion failed: {rc}, {pose}')
            pose = np.array(pose, dtype=float)
            rc = self.sdk.HRIF_RPY2Quaternion(0, *pose[3:].tolist(), quat)
        if rc or len(quat) != 4 or not np.isfinite(pose).all():
            raise ValueError('Vendor rotation conversion failed')
        q = np.array(quat, dtype=float)
        if not np.isfinite(q).all() or abs(np.linalg.norm(q)-1) > .01:
            raise ValueError('Invalid vendor quaternion')
        w, x, y, z = q / np.linalg.norm(q)
        T = np.eye(4)
        T[:3, :3] = [[1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)],
                     [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)],
                     [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)]]
        T[:3, 3] = pose[:3] / 1000
        return T


def check_stopped(before, after):
    for s in (before, after):
        state = s['robot_state']
        if s['controller_simulation'] or state['moving'] or state['error'] or state['emergency_stop']:
            raise ValueError('Robot is moving, faulted, stopped by emergency, or simulated')
    delta = np.abs(np.array(before['joint_positions_deg']) - after['joint_positions_deg'])
    if max(delta) > .03:
        raise ValueError('Joint drift > 0.03 degrees; wait until stopped')
    for key in ('configured_tool_pose_mm_deg', 'configured_user_pose_mm_deg'):
        if not np.allclose(before[key], after[key], atol=1e-8, rtol=0):
            raise ValueError('Tool/user frame changed during capture')


def object_points():
    obj = np.zeros((54, 3), np.float64)
    obj[:, :2] = np.mgrid[0:9, 0:6].T.reshape(-1, 2) * [0.240/10, 0.170/7]
    return obj


def board_pose(corners, intr):
    # RealSense inverse Brown is only compatible here when distortion is zero.
    if any(abs(v) > 1e-12 for v in intr.coeffs):
        raise ValueError('Nonzero RealSense distortion needs explicit projection handling')
    K = np.array([[intr.fx, 0, intr.ppx], [0, intr.fy, intr.ppy], [0, 0, 1.]])
    obj = object_points()
    ok, rvec, tvec = cv2.solvePnP(obj, corners.astype(float), K, None)
    if not ok or tvec[2, 0] <= 0:
        raise ValueError('Board pose failed')
    projection, _ = cv2.projectPoints(obj, rvec, tvec, K, None)
    error = float(np.sqrt(np.mean(np.sum((projection.reshape(-1, 2)-corners)**2, axis=1))))
    if error > 1.0:
        raise ValueError(f'Board reprojection RMS too high: {error:.3f}px')
    T = np.eye(4)
    T[:3, :3] = cv2.Rodrigues(rvec)[0]
    T[:3, 3] = tvec.ravel()
    return T, error


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--once', action='store_true', help='Save one provisional pose, no GUI')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    config = json.loads((ROOT / 'src/massage_controller/config/real_robot.json').read_text())
    reader = CalibrationReader(ROOT / config['sdk_relative_path'], config['host'], config['port'])
    pipe = rs.pipeline()
    started = False
    title = 'Hand-eye capture: S=train V=validation F=flip Q=quit'
    reverse = False
    message = 'Keep board fixed. Confirm red origin at SAME physical corner before S/V.'
    try:
        reader.connect()
        cfg = rs.config()
        cfg.enable_stream(rs.stream.color, 640, 480, rs.format.bgr8, 30)
        profile = pipe.start(cfg)
        started = True
        for _ in range(30):
            pipe.wait_for_frames(5000)
        while True:
            color = pipe.wait_for_frames(5000).get_color_frame()
            frame = np.asanyarray(color.get_data()).copy()
            corners = detect(frame)
            if corners is not None and reverse:
                corners = corners[::-1].copy()
            key = ord('s') if args.once else -1
            if not args.once:
                display = frame.copy()
                if corners is not None:
                    cv2.drawChessboardCorners(display, (9, 6), corners.reshape(-1, 1, 2), True)
                    cv2.circle(display, tuple(np.rint(corners[0]).astype(int)), 10, (0, 0, 255), 3)
                cv2.putText(display, '54 corners' if corners is not None else 'BOARD NOT FOUND',
                            (8, 22), cv2.FONT_HERSHEY_SIMPLEX, .55, (0, 255, 0), 1)
                saved = [json.loads(p.read_text()) for p in args.output.glob('sample_*.json')]
                train_count = sum(s['split'] == 'train' for s in saved)
                val_count = sum(s['split'] == 'validation' for s in saved)
                cv2.putText(display, f'Train {train_count}/20  Validation {val_count}/5',
                            (8, 44), cv2.FONT_HERSHEY_SIMPLEX, .5, (0, 255, 0), 1)
                cv2.putText(display, message[:95], (8, 465), cv2.FONT_HERSHEY_SIMPLEX, .36, (0, 255, 255), 1)
                cv2.imshow(title, display)
                key = cv2.waitKey(1) & 255
                if key in (27, ord('q')) or cv2.getWindowProperty(title, cv2.WND_PROP_VISIBLE) < 1:
                    break
                if key in (ord('f'), ord('F')):
                    reverse = not reverse
                    continue
            if key not in (ord('s'), ord('S'), ord('v'), ord('V')):
                continue
            try:
                before = reader.sample()
                # Drain buffered video after pre-read, with a stopped-pose interval.
                for _ in range(8):
                    color = pipe.wait_for_frames(5000).get_color_frame()
                frame = np.asanyarray(color.get_data()).copy()
                after = reader.sample()
                check_stopped(before, after)
                duration = after['host_monotonic_after_s'] - before['host_monotonic_before_s']
                if not .1 < duration < 1:
                    raise ValueError('Capture pairing interval outside 0.1-1 seconds')
                corners = detect(frame)
                if corners is None:
                    raise ValueError('Full board not detected; no sample saved')
                if reverse:
                    corners = corners[::-1].copy()
                intr = color.profile.as_video_stream_profile().get_intrinsics()
                target, error = board_pose(corners, intr)
                base = reader.flange(after)
                # Do not fill a dataset with duplicate camera/robot poses.
                for path in args.output.glob('sample_*.json'):
                    old = np.array(json.loads(path.read_text())['T_base_flange'])
                    angle = np.linalg.norm(cv2.Rodrigues(old[:3,:3].T @ base[:3,:3])[0])
                    if np.linalg.norm(old[:3,3]-base[:3,3]) < .005 and angle < np.deg2rad(3):
                        raise ValueError('Duplicate pose (<5mm and <3deg); choose a different view')
                stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%f')
                name = f'sample_{stamp}'
                record = dict(schema_version=1, units='m', mode='eye_in_hand',
                              T_base_flange=base.tolist(), T_camera_board=target.tolist(),
                              origin_confirmed_by_operator=not args.once,
                              split='validation' if key in (ord('v'), ord('V')) else 'train',
                              corners_px=corners.tolist(), reprojection_rms_px=error,
                              board=json.loads((Path(__file__).parent/'board.json').read_text()),
                              robot_before=before, robot_after=after,
                              frame_number=color.get_frame_number(), timestamp_ms=color.get_timestamp(),
                              timestamp_domain=str(color.get_frame_timestamp_domain()),
                              camera_serial=profile.get_device().get_info(rs.camera_info.serial_number),
                              intrinsics=dict(width=intr.width, height=intr.height, fx=intr.fx, fy=intr.fy,
                                              ppx=intr.ppx, ppy=intr.ppy, distortion_model=str(intr.model),
                                              coeffs=list(intr.coeffs)), image=name+'.png')
                if not cv2.imwrite(str(args.output/(name+'.png')), frame):
                    raise IOError('Image write failed')
                (args.output/(name+'.json')).write_text(json.dumps(record, indent=2))
                message = f'Saved {record["split"]}: RMS {error:.3f}px'
                print(message, name, flush=True)
            except (ValueError, ConnectionError, RuntimeError) as exc:
                message = str(exc)
                print('Capture rejected:', message, flush=True)
                if args.once:
                    raise
            if args.once:
                break
    finally:
        if started:
            pipe.stop()
        (args.output/'robot_queries.json').write_text(json.dumps(list(reader.audit), indent=2))
        reader.close()
        cv2.destroyAllWindows()


if __name__ == '__main__':
    main()
