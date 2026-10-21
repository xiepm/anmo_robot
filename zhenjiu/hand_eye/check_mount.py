"""Diagnose camera attachment with a small, explicitly requested J6 motion.

Also exports vendor forward-kinematics reference poses without moving to them.
"""
import argparse
import copy
import json
from pathlib import Path
import signal
import contextlib
import sys

import cv2
import numpy as np
import pyrealsense2 as rs

from auto_capture import Reader, ROOT, make_plan
from capture import board_pose
from check_board import detect
from massage_controller.waypoint_plan import POSE_KEYS
from massage_controller.waypoint_motion import WaypointSession


class ReferenceReader(Reader):
    allowed_commands = Reader.allowed_commands | {'ACS2PCS'}

    def forward(self, joints):
        out=[]
        with contextlib.redirect_stdout(sys.stderr):
            rc=self.sdk.HRIF_GetForwardKin(0,0,joints,[0.]*6,[0.]*6,out)
        v=np.asarray(out,dtype=float)
        if rc or v.shape!=(6,) or not np.isfinite(v).all():raise ValueError('Vendor FK failed')
        return v.tolist()

    def matrix(self, pose):
        return self.flange(dict(tcp_pose=dict(xyz_mm=pose[:3],rxyz_deg=pose[3:]),
                                configured_tool_pose_mm_deg=[0.]*6,configured_user_pose_mm_deg=[0.]*6))


def angle(R):return float(np.rad2deg(np.linalg.norm(cv2.Rodrigues(R)[0])))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--probe',action='store_true',help='Execute J6 +3 degrees and return')
    p.add_argument('--augment',type=Path,help='Read-only FK of previously recorded samples')
    args=p.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    cfg=json.loads((ROOT/'src/massage_controller/config/real_robot.json').read_text())
    r=ReferenceReader(ROOT/cfg['sdk_relative_path'],cfg['host'],cfg['port'])
    pipeline=rs.pipeline();streaming=False;session=None
    log=(args.output/'events.jsonl').open('a',buffering=1)
    def stop(*_):
        if session:session.request_stop()
    signal.signal(signal.SIGINT,stop);signal.signal(signal.SIGTERM,stop)
    try:
        r.connect()
        if args.augment:
            results=[]
            for path in sorted(args.augment.glob('sample_*.json')):
                s=json.loads(path.read_text());q=s['robot_after']['joint_positions_deg']
                actual_fk=r.matrix(r.forward(q))
                observed=np.asarray(s['T_base_flange'])
                error_mm=float(np.linalg.norm(actual_fk[:3,3]-observed[:3,3])*1000)
                error_deg=angle(actual_fk[:3,:3].T@observed[:3,:3])
                if error_mm>.1 or error_deg>.02:raise ValueError('FK does not match captured Base/flange feedback')
                refq=q[:5]+[0.]
                s['T_base_reference']=r.matrix(r.forward(refq)).tolist()
                s['mount_reference']='virtual_flange_with_joint6_zero'
                s['fk_actual_vs_feedback']=dict(translation_mm=error_mm,rotation_deg=error_deg)
                s['source_sample']=str(path.resolve())
                s['image']=str((args.augment/s['image']).resolve())
                (args.output/path.name).write_text(json.dumps(s,indent=2))
                results.append(s['fk_actual_vs_feedback'])
            print('Read-only FK augmentation:',len(results),'samples',flush=True)
            return
        if not args.probe:raise ValueError('Choose --probe or --augment')
        current=r.sample();start=current['tcp_pose']['xyz_mm']+current['tcp_pose']['rxyz_deg']
        q=current['joint_positions_deg'];probe_q=q[:];probe_q[5]+=3.
        target=r.forward(probe_q)
        if np.linalg.norm(np.array(target[:3])-start[:3])>.1:
            raise ValueError('J6 FK changes flange position unexpectedly')
        r.inverse(target,q)
        plan=make_plan(start)
        plan['points']=[dict(name='J6_PLUS_3',pose=dict(zip(POSE_KEYS,target)),dwell_s=.5),
                        dict(name='J6_RETURN',pose=dict(zip(POSE_KEYS,start)),dwell_s=.5)]
        session=WaypointSession(plan,r,lambda x:log.write(json.dumps(x)+'\n'))
        session.check_start(session.read())
        (args.output/'plan.json').write_text(json.dumps(plan,indent=2))
        config=rs.config();config.enable_stream(rs.stream.color,640,480,rs.format.bgr8,30)
        pipeline.start(config);streaming=True
        for _ in range(30):pipeline.wait_for_frames(5000)
        records=[]
        def capture(name):
            for _ in range(10):color=pipeline.wait_for_frames(5000).get_color_frame()
            image=np.asanyarray(color.get_data()).copy();corners=detect(image)
            if corners is None:raise ValueError('Lost board')
            if records:
                prev=np.asarray(records[0]['corners_px'])
                if np.linalg.norm(corners-prev)>np.linalg.norm(corners[::-1]-prev):corners=corners[::-1].copy()
            T,e=board_pose(corners,color.profile.as_video_stream_profile().get_intrinsics())
            actual=session.read()
            if not session.settled(actual):raise ValueError('Not settled')
            value=dict(name=name,T_camera_board=T.tolist(),corners_px=corners.tolist(),
                       reprojection_rms_px=e,robot=actual)
            records.append(value)
            cv2.imwrite(str(args.output/(name+'.png')),image)
            (args.output/(name+'.json')).write_text(json.dumps(value,indent=2))
        capture('before')
        session.stop_required=True;session.stop_and_confirm()
        for i,point in enumerate(plan['points'],1):
            session.visit(i,point,session.read())
            capture('after' if i==1 else 'returned')
        a,b=records[:2];A=np.asarray(a['T_camera_board']);B=np.asarray(b['T_camera_board'])
        joints=np.array(b['robot']['joint_positions_deg'])-a['robot']['joint_positions_deg']
        result=dict(joint_changes_deg=joints.tolist(),camera_rotation_deg=angle(A[:3,:3]@B[:3,:3].T),
                    camera_board_translation_change_mm=float(np.linalg.norm(A[:3,3]-B[:3,3])*1000),
                    mean_corner_displacement_px=float(np.linalg.norm(np.array(a['corners_px'])-b['corners_px'],axis=1).mean()))
        (args.output/'result.json').write_text(json.dumps(result,indent=2))
        print(json.dumps(result),flush=True)
    finally:
        try:
            if session and session.stop_required:
                session.stop_and_confirm();print('STOP CONFIRMED',flush=True)
        finally:
            if streaming:pipeline.stop()
            (args.output/'queries.json').write_text(json.dumps(list(r.audit),indent=2))
            r.close();log.close()


if __name__=='__main__':main()
