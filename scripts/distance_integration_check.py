#!/usr/bin/env python3
"""Real moving RGB-D ranges and paused-clock invalidation; run without another HMI."""
import json,time,argparse
from pathlib import Path
import numpy as np
import rclpy
from std_srvs.srv import Empty
from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import Qt
from PyQt5.QtTest import QTest
from elfin_massage_sim.hmi import HmiWindow
from elfin_massage_sim.hmi_backend import HmiNode
parser=argparse.ArgumentParser();parser.add_argument('--mode',choices=['dual','wrist'],default='dual');parser.add_argument('--panel-only',action='store_true');args=parser.parse_args()
rclpy.init(args=['--ros-args','-p','use_sim_time:=true','-p','scene:=human','-p','sensing_mode:='+args.mode,'-p','camera_namespace:='+('/camera' if args.mode=='wrist' else '/global_camera')])
app=QApplication([]);node=HmiNode();window=HmiWindow(node);window.show()
out=Path('/workspace/reports/distance')/args.mode;out.mkdir(parents=True,exist_ok=True)
report={'passed':False,'mode':args.mode,'samples':[],'invalid_samples':[]};seen={};paused=False;trace_shot=False
pause=node.create_client(Empty,'/pause_physics');resume=node.create_client(Empty,'/unpause_physics')
def pump():
    global trace_shot
    app.processEvents();time.sleep(.006)
    for source,result in node.range_status.items():
        if not result['valid']:report['invalid_samples'].append(dict(result,phase=node.motion.phase))
        if result['valid'] and seen.get(source)!=result['stamp_ns']:
            seen[source]=result['stamp_ns'];report['samples'].append(dict(result,phase=node.motion.phase))
    if node.motion.phase=='TRACE' and not trace_shot:
        window.grab().save(str(out/'moving.png'));trace_shot=True

def wait(predicate,timeout=120):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        pump()
        if predicate():return
    raise AssertionError('Timeout: '+node.status)
def spin(seconds):
    end=time.monotonic()+seconds
    while time.monotonic()<end:pump()
def service(client):
    wait(client.service_is_ready,10);f=client.call_async(Empty.Request());wait(f.done,10);f.result()
try:
    wait(lambda:node.motion.ready() and node.display_frame('wrist') is not None)
    wait(lambda:bool(node.range_status));assert not any(r['valid'] for r in node.range_status.values())
    window.human.setCurrentIndex(1);QTest.mouseClick(window.view_button,Qt.LeftButton)
    wait(lambda:not node.motion.busy and node.motion.phase=='COMPLETE');spin(.6)
    wait(lambda:node.display_frame('wrist') is not None and window.register_button.isEnabled(),20)
    QTest.mouseClick(window.register_button,Qt.LeftButton)
    wait(lambda:node.range_status.get('wrist',{}).get('surface_valid'),5)
    report['observation']=node.range_status.copy();spin(.4);window.grab().save(str(out/'observation.png'))
    report['motion_checks']=not args.panel_only
    if args.mode=='wrist':
        assert list(node.range_status)==['wrist'] and window.global_view.isHidden()
        assert 'global_camera_link' not in node.motion.model.transforms(node.motion.q)
    if not args.panel_only:
        QTest.mouseClick(window.plan_button,Qt.LeftButton)
        wait(lambda:node.motion.phase=='PREVIEW_READY' and window.execute_button.isEnabled())
        QTest.mouseClick(window.execute_button,Qt.LeftButton)
        wait(lambda:node.execution_result is not None,180)
        assert node.execution_result['phase']=='COMPLETE'
        report['execution_phase']=node.execution_result['phase']
        for sample in report['samples']:
            expected=np.linalg.norm(np.asarray(sample['camera_world_m'])-sample['human_world_m'])
            assert abs(sample['origin_distance_m']-expected)<1e-10
            assert -.05<=sample['age_s']<=.5
        trace=[s for s in report['samples'] if s['source']=='wrist' and s['phase']=='TRACE' and s['surface_valid']]
        assert len(trace)>15
        before=report['observation']['wrist']['surface_range_m'];during=float(np.median([s['surface_range_m'] for s in trace]))
        assert before-during>.12,(before,during)
        report['wrist_surface_observation_mm']=before*1000;report['wrist_surface_trace_median_mm']=during*1000
        if args.mode=='dual':
            global_samples=[s for s in report['samples'] if s['source']=='global']
            assert len(global_samples)>30
            span=np.ptp([s['origin_distance_m'] for s in global_samples]);assert span<.001
            report['fixed_camera_origin_span_mm']=float(span*1000)
        else:assert list(node.range_status)==['wrist'] and window.global_view.isHidden()
    spin(.4);window.grab().save(str(out/'completed.png'))
    # ROS time stops with Gazebo: wall-clock receipt age must still expire.
    service(pause);paused=True
    wait(lambda:time.monotonic()-node.range_clock[1]>.7 and not any(r['valid'] for r in node.range_status.values()),3)
    assert not any(r['valid'] for r in node.range_status.values()),node.range_status
    report['paused_status']=node.range_status.copy();window.grab().save(str(out/'paused.png'))
    service(resume);paused=False
    wait(lambda:node.range_status['wrist']['valid'],5)
    report['resumed_valid']=True;report['passed']=True
finally:
    if paused:service(resume)
    if node.motion.busy:node.motion.cancel();spin(2)
    if not report['passed']:window.grab().save(str(out/'failure.png'))
    (out/'integration.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    window.ros_timer.stop();window.timer.stop();node.motion.close();node.destroy_node();window.close();rclpy.shutdown()
print(json.dumps({k:v for k,v in report.items() if k not in ('samples','invalid_samples','observation')},ensure_ascii=False))
