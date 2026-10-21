"""Solve a candidate hand-eye transform; never install it into robot control."""
import argparse
import json
from pathlib import Path

import cv2
import numpy as np


def rigid(value):
    T = np.asarray(value, dtype=float)
    if (T.shape != (4, 4) or not np.isfinite(T).all()
            or not np.allclose(T[3], [0, 0, 0, 1], atol=1e-8)
            or not np.allclose(T[:3, :3].T @ T[:3, :3], np.eye(3), atol=1e-6)
            or abs(np.linalg.det(T[:3, :3])-1) > 1e-6):
        raise ValueError('Invalid rigid transform')
    return T


def solve_pairs(base, target):
    if len(base) != len(target) or len(base) < 3:
        raise ValueError('Insufficient paired poses')
    base, target = [rigid(t) for t in base], [rigid(t) for t in target]
    rotations = [cv2.Rodrigues(base[0][:3,:3].T @ t[:3,:3])[0].ravel() for t in base[1:]]
    singular = np.linalg.svd(rotations, compute_uv=False)
    if singular[1] < np.deg2rad(5):
        raise ValueError('Rotation diversity insufficient: use at least two independent axes')
    if hasattr(cv2, 'calibrateHandEye'):
        R, t = cv2.calibrateHandEye(
            [a[:3,:3] for a in base], [a[:3,3] for a in base],
            [a[:3,:3] for a in target], [a[:3,3] for a in target],
            method=cv2.CALIB_HAND_EYE_PARK)
    else:
        # OpenCV 5.0.0.93 omits this Python binding. Solve AX=XB using
        # SO(3) logarithm alignment, then linear least squares translation.
        pairs = [(np.linalg.inv(base[i]) @ base[j], target[i] @ np.linalg.inv(target[j]))
                 for i in range(len(base)) for j in range(i+1, len(base))]
        cross = sum(np.outer(cv2.Rodrigues(a[:3,:3])[0].ravel(),
                             cv2.Rodrigues(b[:3,:3])[0].ravel()) for a,b in pairs)
        U, _, Vt = np.linalg.svd(cross)
        R = U @ np.diag([1, 1, np.linalg.det(U@Vt)]) @ Vt
        A = np.concatenate([a[:3,:3]-np.eye(3) for a,b in pairs])
        rhs = np.concatenate([R @ b[:3,3]-a[:3,3] for a,b in pairs])
        t, _, rank, _ = np.linalg.lstsq(A, rhs, rcond=None)
        if rank < 3:
            raise ValueError('Translation solve is rank deficient')
    result = np.eye(4)
    result[:3,:3], result[:3,3] = R, t.ravel()
    return rigid(result), singular.tolist()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('session', type=Path)
    p.add_argument('--reference', choices=['flange','mount'], default='flange')
    args = p.parse_args()
    samples = [json.loads(path.read_text()) for path in sorted(args.session.glob('sample_*.json'))]
    if not samples:
        p.error('No samples')
    for s in samples:
        if not (s.get('origin_confirmed_by_operator') or s.get('origin_tracking_verified')):
            p.error('Provisional samples have unconfirmed board origin; recapture in GUI')
        if (s['units'] != 'm' or s['mode'] != 'eye_in_hand'
                or s['camera_serial'] != samples[0]['camera_serial']
                or s['board'] != samples[0]['board'] or s['intrinsics'] != samples[0]['intrinsics']):
            p.error('Mixed camera, board, intrinsics, mode or units')
    train = [s for s in samples if s['split'] == 'train']
    valid = [s for s in samples if s['split'] == 'validation']
    if len(train) < 15 or len(valid) < 5:
        p.error(f'Need >=15 training and >=5 held-out poses; have {len(train)}, {len(valid)}')
    key='T_base_flange' if args.reference=='flange' else 'T_base_reference'
    if args.reference=='mount' and any(s.get('mount_reference')!='virtual_flange_with_joint6_zero' for s in samples):
        p.error('Unverified/mixed mounting reference')
    X, diversity = solve_pairs([s[key] for s in train], [s['T_camera_board'] for s in train])
    transforms = [rigid(s[key]) @ X @ rigid(s['T_camera_board']) for s in train]
    U, _, Vt = np.linalg.svd(sum(t[:3,:3] for t in transforms))
    center = np.eye(4)
    center[:3,:3] = U @ np.diag([1, 1, np.linalg.det(U@Vt)]) @ Vt
    center[:3,3] = np.mean([t[:3,3] for t in transforms], axis=0)
    def residual(s):
        observed = rigid(s[key]) @ X @ rigid(s['T_camera_board'])
        return dict(translation_mm=float(np.linalg.norm(observed[:3,3]-center[:3,3])*1000),
                    rotation_deg=float(np.rad2deg(np.linalg.norm(cv2.Rodrigues(center[:3,:3].T @ observed[:3,:3])[0]))),
                    image=s['image'])
    report = dict(status='candidate_requires_independent_depth_and_absolute_validation',
                  calibration_valid=False, units='m',
                  reference_frame='flange' if args.reference=='flange' else 'virtual_flange_with_joint6_zero',
                  T_base_board_mean=center.tolist(), rotation_diversity_singular_values_rad=diversity,
                  train_residuals=[residual(s) for s in train], validation_residuals=[residual(s) for s in valid],
                  board=samples[0]['board'], camera_serial=samples[0]['camera_serial'],
                  note='Do not use for robot motion until measured absolute and RGB-D errors are accepted.')
    report['T_flange_camera' if args.reference=='flange' else 'T_reference_camera']=X.tolist()
    output = args.session/'candidate_hand_eye.json'
    output.write_text(json.dumps(report, indent=2))
    print(output)
    print('Held-out max translation mm:', max(r['translation_mm'] for r in report['validation_residuals']))


if __name__ == '__main__':
    main()
