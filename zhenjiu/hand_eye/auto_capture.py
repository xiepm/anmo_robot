"""Bounded free-space calibration using the already commissioned motion monitor.

Default is read-only planning/IK checks. --execute requires that saved plan and
an unchanged start pose. Never enables, resets, or retries a motion command.
"""
import argparse
import contextlib
from datetime import datetime, timezone
import json
from pathlib import Path
import signal
import time

import cv2
import numpy as np
import pyrealsense2 as rs

from capture import ROOT, CalibrationReader, check_stopped, board_pose, object_points
from check_board import detect
from massage_controller.waypoint_motion import MotionReader, WaypointSession
from massage_controller.waypoint_plan import POSE_KEYS, near, validate


class Reader(MotionReader):
    allowed_commands = MotionReader.allowed_commands | CalibrationReader.allowed_commands | {'PCS2ACS'}
    flange = CalibrationReader.flange

    def inverse(self, target, reference):
        result = []
        with contextlib.redirect_stdout(__import__('sys').stderr):
            rc = self.sdk.HRIF_GetInverseKin(0, 0, target, reference, [0.]*6, [0.]*6, result)
        q = np.asarray(result, dtype=float)
        if rc or q.shape != (6,) or not np.isfinite(q).all():
            raise ValueError(f'Inverse kinematics failed: {rc}, {result}')
        if np.max(np.abs(q-reference)) > 8:
            raise ValueError('IK branch/large joint change rejected (>8deg per sampled substep)')
        return q.tolist()


def make_plan(start):
    rotations = [(4,0,0),(8,0,0),(4,4,0),(0,8,0),(-4,4,0),(-8,0,0),
                 (-4,-4,0),(0,-8,0),(4,-4,0),(0,0,5),(5,0,5),(0,5,5),
                 (-5,0,5),(0,-5,5),(0,0,-5),(5,0,-5),(0,5,-5),
                 (-5,0,-5),(0,-5,-5),(3,3,3),
                 (6,2,2),(-2,6,-2),(-6,-2,2),(2,-6,-2),(2,2,-3)]
    points = []
    for i, r in enumerate(rotations):
        xyz = [start[0]+(i%3-1)*5, start[1]+((i//3)%3-1)*5, start[2]+(i%2)*5]
        # Current board almost fills the image height; use +/-4 degree views.
        angles = [(v+d*.5+180)%360-180 for v,d in zip(start[3:],r)]
        points.append(dict(name=f'{"train" if i<20 else "validation"}_{i+1:02}',
                           pose=dict(zip(POSE_KEYS, xyz+angles)), dwell_s=.5))
    return validate(dict(version=1, mode='FREE_SPACE_WAYPOINTS', robot_model='elfin5_l',
                         frame='Base', path_confirmed=True, target_reference='FLANGE',
                         tool_pose_mm_deg=[0.]*6, start_pose=dict(zip(POSE_KEYS,start)),
                         speed=3., acceleration=6., max_segment_mm=20., max_rotation_deg=15.,
                         workspace_mm=dict(min=[v-12 for v in start[:3]], max=[v+12 for v in start[:3]]),
                         move_timeout_s=30., points=points))


def orient(corners, previous):
    if previous is None:
        return corners, 0.
    costs = [float(np.mean(np.linalg.norm(c-previous, axis=1))) for c in (corners,corners[::-1])]
    if min(costs)>40 or abs(costs[0]-costs[1])<25:
        raise ValueError(f'Board correspondence ambiguous/too large: {costs}')
    return (corners if costs[0]<costs[1] else corners[::-1].copy()), min(costs)


def constrain_workspace(plan, constraint):
    applies = constraint.get('applies_to_current_calibration')
    if applies is None:
        raise ValueError('User workspace scope/reference is unresolved; no automatic movement')
    if applies is False:
        return plan  # Only set after explicit clarification; retains local small envelope.
    if constraint.get('frame') != 'Base' or constraint.get('reference_point') != 'FLANGE':
        raise ValueError('Need confirmed Base/flange workspace; do not guess TCP conversion')
    box = constraint['workspace_mm']
    plan['workspace_mm'] = dict(
        min=[max(a,b) for a,b in zip(plan['workspace_mm']['min'],box['min'])],
        max=[min(a,b) for a,b in zip(plan['workspace_mm']['max'],box['max'])])
    return validate(plan)


def observation_steps(actual, target):
    """Short moves keep full-grid correspondence observable between captures."""
    a, b = np.array(actual,dtype=float), np.array(target,dtype=float)
    delta = b-a
    delta[3:] = (delta[3:]+180)%360-180
    count=max(1,int(np.ceil(np.linalg.norm(delta[:3])/3.)),int(np.ceil(np.linalg.norm(delta[3:])/2.)))
    steps=[]
    for j in range(1,count+1):
        value=a+delta*j/count
        value[3:] = (value[3:]+180)%360-180
        steps.append(value.tolist())
    return steps


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--execute', action='store_true')
    p.add_argument('--limit', type=int, default=25, help='Pilot may execute only first N poses')
    args = p.parse_args()
    if not 1 <= args.limit <= 25: p.error('limit must be 1..25')
    args.output.mkdir(parents=True, exist_ok=True)
    cfg = json.loads((ROOT/'src/massage_controller/config/real_robot.json').read_text())
    reader = Reader(ROOT/cfg['sdk_relative_path'], cfg['host'], cfg['port'])
    session = None
    pipeline = rs.pipeline()
    streaming = False
    stop_requested = False
    def stop(*_):
        nonlocal stop_requested
        stop_requested = True
        if session: session.request_stop()
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    log = (args.output/('execution.jsonl' if args.execute else 'preflight.jsonl')).open('a', buffering=1)
    def record(obj): log.write(json.dumps(obj, ensure_ascii=False)+'\n')
    try:
        reader.connect()
        initial = reader.sample()
        start = initial['tcp_pose']['xyz_mm']+initial['tcp_pose']['rxyz_deg']
        plan_path = args.output/'plan.json'
        if args.execute:
            plan = validate(json.loads(plan_path.read_text()))
        else:
            plan = make_plan(start)
        constraint = json.loads((Path(__file__).parent/'user_workspace_20261013.json').read_text())
        plan = constrain_workspace(plan, constraint)
        session = WaypointSession(plan, reader, record)
        sample = session.read()
        session.check_start(sample)
        if stop_requested: raise RuntimeError('Cancelled before start')
        if not args.execute:
            reference = sample['joint_positions_deg']
            previous = np.array(start)
            checks = []
            for point in plan['points']:
                target = np.array([point['pose'][k] for k in POSE_KEYS])
                delta = target-previous
                delta[3:] = (delta[3:]+180)%360-180
                for fraction in (.25,.5,.75,1.):
                    pose = previous+fraction*delta
                    pose[3:] = (pose[3:]+180)%360-180
                    reference = reader.inverse(pose.tolist(), reference)
                    if max(abs(a-b) for a,b in zip(reference,sample['joint_positions_deg']))>25:
                        raise ValueError('IK pose exceeds 25-degree joint envelope around start')
                    checks.append(dict(pose=pose.tolist(), joints=reference))
                previous = target
            plan_path.write_text(json.dumps(plan,indent=2))
            (args.output/'preflight.json').write_text(json.dumps(dict(initial=sample,ik_checks=checks),indent=2))
            print('PREFLIGHT PASS: 25 targets, 100 sampled IK solutions. No motion sent.',flush=True)
            return
        if list(args.output.glob('sample_*.json')):
            raise ValueError('Execution directory already contains samples; do not resume automatically')
        conf=rs.config()
        conf.enable_stream(rs.stream.color,640,480,rs.format.bgr8,30)
        conf.enable_stream(rs.stream.depth,640,480,rs.format.z16,30)
        profile=pipeline.start(conf);streaming=True
        align=rs.align(rs.stream.color)
        for _ in range(30): pipeline.wait_for_frames(5000)
        previous_corners=None
        def capture(name, split):
            nonlocal previous_corners
            session.cancelled()
            before=session.read()
            if not session.settled(before): raise ValueError('Capture requires settled robot')
            for _ in range(8): frames=pipeline.wait_for_frames(5000)
            after=session.read()
            check_stopped(before,after)
            dt=after['host_monotonic_after_s']-before['host_monotonic_before_s']
            if not .1<dt<1: raise ValueError('Capture pairing interval invalid')
            frames=align.process(frames)
            color,depth=frames.get_color_frame(),frames.get_depth_frame()
            image=np.asanyarray(color.get_data()).copy()
            corners=detect(image)
            if corners is None: raise ValueError('Full checkerboard lost; stop sequence')
            corners,shift=orient(corners,previous_corners)
            intr=color.profile.as_video_stream_profile().get_intrinsics()
            target,error=board_pose(corners,intr)
            base=reader.flange(after)
            measured=[]
            for x,y in corners:
                ix,iy=int(round(x)),int(round(y))
                values=[depth.get_distance(u,v) for v in range(max(0,iy-2),min(480,iy+3))
                        for u in range(max(0,ix-2),min(640,ix+3))]
                values=[z for z in values if z>0]
                measured.append(rs.rs2_deproject_pixel_to_point(intr,[float(x),float(y)],float(np.median(values))) if len(values)>=13 else None)
            expected=(target[:3,:3]@object_points().T).T+target[:3,3]
            errors=[float(np.linalg.norm(np.array(v)-q)*1000) for v,q in zip(measured,expected) if v is not None]
            data=dict(schema_version=1, units='m', mode='eye_in_hand', split=split,
                      T_base_flange=base.tolist(),T_camera_board=target.tolist(),
                      origin_confirmed_by_operator=False,origin_tracking_verified=True,
                      origin_method='full-grid temporal correspondence; first frame defines origin',
                      corner_shift_px=shift,corners_px=corners.tolist(),reprojection_rms_px=error,
                      board=json.loads((Path(__file__).parent/'board.json').read_text()),
                      robot_before=before,robot_after=after,camera_serial=profile.get_device().get_info(rs.camera_info.serial_number),
                      timestamp_ms=color.get_timestamp(),timestamp_domain=str(color.get_frame_timestamp_domain()),
                      frame_number=color.get_frame_number(),
                      intrinsics=dict(width=intr.width,height=intr.height,fx=intr.fx,fy=intr.fy,ppx=intr.ppx,ppy=intr.ppy,
                                      distortion_model=str(intr.model),coeffs=list(intr.coeffs)),
                      camera_depth_points_m=measured,depth_vs_pnp_errors_mm=errors,image=name+'.png')
            if not cv2.imwrite(str(args.output/(name+'.png')),image):raise IOError('Image save failed')
            (args.output/(name+'.json')).write_text(json.dumps(data,indent=2))
            previous_corners=corners.copy()
            print(f'{name}: RMS={error:.3f}px, depth median={np.median(errors) if errors else float("nan"):.2f}mm',flush=True)
            session.cancelled()
        # Establish board visibility/origin before issuing even ordinary STOP.
        capture('initial_reference','reference')
        session.stop_required=True
        session.stop_and_confirm()
        session.check_start(session.read())
        move_index=0
        for i,point in enumerate(plan['points'][:args.limit],1):
            if stop_requested:session.request_stop()
            # Recheck IK at actual current joints; never blindly reuse reference joints.
            fresh=session.read()
            target=[point['pose'][k] for k in POSE_KEYS]
            steps=observation_steps(session.actual(fresh),target)
            for j,subtarget in enumerate(steps,1):
                fresh=session.read()
                reader.inverse(subtarget,fresh['joint_positions_deg'])
                move_index+=1
                subpoint=dict(name=f'{point["name"]}_step{j}',pose=dict(zip(POSE_KEYS,subtarget)),dwell_s=.35)
                session.visit(move_index,subpoint,session.read())
                final=j==len(steps)
                capture('sample_'+point['name'] if final else f'transition_{i:02}_{j:02}',
                        ('train' if i<=20 else 'validation') if final else 'transition')
        print('CAPTURE COMPLETE; holding final pose. No automatic return.',flush=True)
    finally:
        try:
            if session and session.stop_required:
                try:
                    session.stop_and_confirm()
                    print('STOP CONFIRMED',flush=True)
                except Exception:
                    print('STOP UNCONFIRMED: USE PENDANT / HARDWARE STOP',flush=True)
                    raise
        finally:
            try:
                if streaming:pipeline.stop()
                (args.output/'queries.json').write_text(json.dumps(list(reader.audit),indent=2))
            finally:
                reader.close()
                log.close()


if __name__=='__main__':main()
