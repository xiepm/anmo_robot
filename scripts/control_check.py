#!/usr/bin/env python3
"""Live rejection and cancellation checks for the standalone controller path."""
import copy,json,time
from pathlib import Path
import numpy as np
import cv2
import rclpy
from rclpy.time import Time
from action_msgs.msg import GoalStatus
from elfin_massage_sim.click_camera import ClickCamera

rclpy.init(args=['--ros-args','-p','use_sim_time:=true'])
node=ClickCamera(gui=False);motion=node.motion
report={'passed':False}
def wait(test,timeout=30):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        rclpy.spin_once(node,timeout_sec=.03)
        if test():return
    raise RuntimeError(f'Timeout: {node.status}')
try:
    wait(lambda:motion.ready() and node.latest is not None and
         0<=node.get_clock().now().nanoseconds-Time.from_msg(node.latest[1].header.stamp).nanoseconds<=200_000_000)
    node.displayed=node.latest
    node.click(cv2.EVENT_LBUTTONDOWN,-1,0,0,None)
    assert not motion.busy and node.status=='Pixel outside image',node.status
    report['out_of_image_rejected']=True
    old=copy.deepcopy(node.latest);old[1].header.stamp.sec=0;old[1].header.stamp.nanosec=0
    node.displayed=old;node.click(cv2.EVENT_LBUTTONDOWN,320,240,0,None)
    assert not motion.busy and 'Stale frame' in node.status,node.status
    report['stale_frame_rejected']=True
    assert motion.start('approach',[5.,0.,.4])
    wait(lambda:not motion.busy)
    assert motion.phase.startswith('REJECTED') and not motion.history,node.status
    report['out_of_workspace_never_sent']=True
    assert motion.start('observe');motion.cancel()
    wait(lambda:not motion.busy)
    assert motion.phase=='CANCELLED' and not motion.history,node.status
    report['planning_cancel_never_sent']=True
    q0=motion.q.copy()
    assert motion.start('home')
    wait(lambda:motion.goal_handle is not None and np.max(abs(motion.q-q0))>.005,60)
    node.displayed=node.latest;node.click(cv2.EVENT_LBUTTONDOWN,320,240,0,None)
    assert 'Motion busy' in node.status,node.status
    report['busy_click_rejected']=True
    history_count=len(motion.history)
    motion.cancel();wait(lambda:not motion.busy,10)
    assert motion.phase=='CANCELLED' and motion.last_action_status==GoalStatus.STATUS_CANCELED,node.status
    # Allow the controller to settle, then verify sustained position for a second.
    end=time.monotonic()+.5
    while time.monotonic()<end:rclpy.spin_once(node,timeout_sec=.03)
    hold=motion.q.copy();drift=0.;end=time.monotonic()+1.
    while time.monotonic()<end:
        rclpy.spin_once(node,timeout_sec=.03);drift=max(drift,float(np.max(abs(motion.q-hold))))
    assert drift<.001,drift
    assert len(motion.history)==history_count
    report.update(passed=True,cancellation_status=motion.last_action_status,
                  stopped_max_joint_drift_rad=drift,no_later_stage_after_cancel=True)
except Exception as exc:
    report['error']=str(exc);motion.cancel()
    deadline=time.monotonic()+5
    while motion.busy and time.monotonic()<deadline:rclpy.spin_once(node,timeout_sec=.03)
finally:
    output=Path('/workspace/reports/independent');output.mkdir(exist_ok=True,parents=True)
    (output/'control-check.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))
    motion.close();node.destroy_node();rclpy.shutdown()
raise SystemExit(0 if report['passed'] else 1)
