"""Read-only board/camera preflight. Does not connect to or move the robot.

The nominal PDF dimensions are not a measured calibration. This command saves
raw color data and corner detection only; it never emits a hand-eye transform.
"""
import argparse
import json
from pathlib import Path
from datetime import datetime, timezone

import cv2
import numpy as np


def detect(image):
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    found, corners = cv2.findChessboardCornersSB(
        gray, (9, 6), cv2.CALIB_CB_EXHAUSTIVE | cv2.CALIB_CB_ACCURACY)
    return corners.reshape(-1, 2) if found else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', type=Path, help='Offline image instead of camera')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    report = dict(created_at=datetime.now(timezone.utc).isoformat(),
                  board_type='checkerboard', inner_corners=[9, 6],
                  nominal_square_mm=25, printed_scale_verified=False,
                  mounting_reference_verified=False, calibration_valid=False)
    if args.image:
        frame = cv2.imread(str(args.image))
        if frame is None:
            raise ValueError(f'Cannot read {args.image}')
        report['source'] = str(args.image)
    else:
        import pyrealsense2 as rs
        pipeline, config = rs.pipeline(), rs.config()
        config.enable_stream(rs.stream.color, 640, 480, rs.format.bgr8, 30)
        profile = pipeline.start(config)
        try:
            for _ in range(30):
                frames = pipeline.wait_for_frames(5000)
            color = frames.get_color_frame()
            frame = np.asanyarray(color.get_data()).copy()
            intr = color.profile.as_video_stream_profile().get_intrinsics()
            report.update(source='RealSense raw color',
                          camera_serial=profile.get_device().get_info(rs.camera_info.serial_number),
                          frame_number=color.get_frame_number(),
                          camera_timestamp_ms=color.get_timestamp(),
                          timestamp_domain=str(color.get_frame_timestamp_domain()),
                          intrinsics=dict(width=intr.width, height=intr.height,
                                          fx=intr.fx, fy=intr.fy, ppx=intr.ppx, ppy=intr.ppy,
                                          distortion_model=str(intr.model), coeffs=list(intr.coeffs)))
        finally:
            pipeline.stop()
    corners = detect(frame)
    report['board_detected'] = corners is not None
    report['corners_px'] = corners.tolist() if corners is not None else []
    report['corner_count'] = len(corners) if corners is not None else 0
    report['note'] = ('Checkerboard origin may reverse across views. Resolve physical '
                      'origin consistently before hand-eye solving. No robot pose captured.')
    cv2.imwrite(str(args.output / 'raw_color.png'), frame)
    annotated = frame.copy()
    if corners is not None:
        cv2.drawChessboardCorners(annotated, (9, 6), corners.reshape(-1, 1, 2), True)
        cv2.circle(annotated, tuple(np.rint(corners[0]).astype(int)), 9, (0, 0, 255), 2)
    cv2.imwrite(str(args.output / 'board_detection.png'), annotated)
    (args.output / 'preflight.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in report.items() if k != 'corners_px'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
