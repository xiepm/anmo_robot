#!/usr/bin/env python3
"""Actual RGB-D -> independent IK -> trajectory controller -> Gazebo check."""
import argparse
import json
import math
import time
from pathlib import Path
import cv2
import numpy as np
from scipy.spatial.transform import Rotation
import rclpy
from rclpy.time import Time
from geometry_msgs.msg import PointStamped
from tf2_geometry_msgs import do_transform_point
from elfin_massage_sim.click_camera import ClickCamera
from elfin_massage_sim.kinematics import DOWN


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=['global','wrist'], default='global')
    parser.add_argument('--output', default='/workspace/reports/independent')
    args = parser.parse_args()
    rclpy.init(args=['--ros-args','-p','use_sim_time:=true'])
    node = ClickCamera(gui=False)
    motion = node.motion
    report = {'mode':args.mode, 'passed':False, 'measurements':[]}
    output = Path(args.output);output.mkdir(parents=True, exist_ok=True)
    normal_samples = []
    def spin():
        rclpy.spin_once(node, timeout_sec=0.03)
        if motion.phase == 'NORMAL_APPROACH' and motion.q is not None:
            normal_samples.append(motion.model.fk(motion.q)[:3,3].tolist())
    def wait(predicate, timeout, label):
        deadline = time.monotonic()+timeout
        while time.monotonic()<deadline:
            spin()
            if predicate():return
        raise RuntimeError(f'Timeout {label}: {node.status}')
    def observe():
        assert motion.start('observe'), node.status
        wait(lambda: not motion.busy, 120, 'observation')
        assert motion.phase == 'COMPLETE', node.status
    def image_ready():
        start = node.get_clock().now().nanoseconds
        wait(lambda: node.latest is not None and
             start+300_000_000 < Time.from_msg(node.latest[1].header.stamp).nanoseconds <= node.get_clock().now().nanoseconds,
             30,'fresh synchronized frame')
        node.displayed = node.latest
        stamp=Time.from_msg(node.displayed[1].header.stamp)
        wait(lambda:node.tf.can_transform('world','camera_optical_frame',stamp),5,'image TF')
    def click(expected):
        normal_samples.clear()
        image_ready()
        rgb,depth,info=node.displayed
        world=PointStamped();world.header.frame_id='world';world.header.stamp=depth.header.stamp
        world.point.x,world.point.y,world.point.z=expected
        camera=do_transform_point(world,node.tf.lookup_transform('camera_optical_frame','world',Time.from_msg(depth.header.stamp)))
        u=round(info.k[0]*camera.point.x/camera.point.z+info.k[2]);v=round(info.k[4]*camera.point.y/camera.point.z+info.k[5])
        frame=node.bridge.imgmsg_to_cv2(rgb,'bgr8').copy()
        cv2.drawMarker(frame,(u,v),(0,0,255),cv2.MARKER_CROSS,16,2)
        cv2.imwrite(str(output/f'{args.mode}-click-{len(report["measurements"])}.png'),frame)
        node.click(cv2.EVENT_LBUTTONDOWN,u,v,0,None)
        assert motion.busy,node.status
        measured=[node.last_surface.point.x,node.last_surface.point.y,node.last_surface.point.z]
        target=list(node.last_target)
        wait(lambda:not motion.busy,180,'three stage execution')
        assert motion.phase == 'COMPLETE', node.status
        end=time.monotonic()+0.5
        while time.monotonic()<end:spin()
        tf=node.tf.lookup_transform('world','massage_tcp',Time())
        p=tf.transform.translation;q=tf.transform.rotation
        actual=[p.x,p.y,p.z]
        angle=float(Rotation.from_matrix(DOWN@Rotation.from_quat([q.x,q.y,q.z,q.w]).as_matrix().T).magnitude())
        lateral=max(math.hypot(p[0]-target[0],p[1]-target[1]) for p in normal_samples)
        record={'pixel':[u,v],'expected_surface_m':expected,'measured_surface_m':measured,
                'projection_error_m':math.dist(expected,measured),'target_tcp_m':target,'actual_tcp_m':actual,
                'tcp_error_m':math.dist(target,actual),'orientation_error_rad':angle,
                'normal_max_lateral_error_m':lateral,'normal_sample_count':len(normal_samples),
                'controller_error_code':motion.last_result_code,'stages':motion.history}
        report['measurements'].append(record)
        assert [s['stage'] for s in motion.history][-3:]==['OBSERVE','PREAPPROACH','NORMAL_APPROACH']
        assert record['projection_error_m']<.01 and record['tcp_error_m']<.01 and angle<.05 and lateral<.01,record
    try:
        wait(lambda:motion.ready() and node.latest is not None,90,'robot/camera readiness')
        nodes=node.get_node_names()
        assert not any('move_group' in n or 'moveit' in n for n in nodes),nodes
        report['ros_nodes']=nodes
        if np.max(np.abs(motion.q)) > 0.05:
            assert motion.start('home'), node.status
            wait(lambda:not motion.busy,150,'initial home')
            assert motion.phase=='COMPLETE',node.status
        if args.mode=='wrist':observe()
        click([.6,.1,.4])
        # Repeat from a completed low pose: validates retreat and sequential reuse.
        # Wrist camera needs its observation FOV again before a second selection.
        if args.mode=='wrist':observe()
        click([.68,-.04,.4])
        report['passed']=True
    except Exception as exc:
        report['error']=str(exc);motion.cancel()
        deadline=time.monotonic()+5
        while motion.busy and time.monotonic()<deadline:spin()
    finally:
        (output/f'integration-{args.mode}.json').write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps(report,indent=2))
        motion.close();node.destroy_node();rclpy.shutdown()
    return 0 if report['passed'] else 1

if __name__=='__main__':raise SystemExit(main())
