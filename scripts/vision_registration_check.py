#!/usr/bin/env python3
"""Exercise real Qt, RGB-D registration and ros2_control, in either camera mode."""
import argparse,json,time
from pathlib import Path
import numpy as np
import rclpy
from rclpy.time import Time
from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import Qt,QPoint
from PyQt5.QtTest import QTest
from elfin_massage_sim.hmi import HmiWindow
from elfin_massage_sim.hmi_backend import HmiNode
from elfin_massage_sim.frames import matrix,inverse,points

parser=argparse.ArgumentParser();parser.add_argument('--mode',choices=['wrist','dual'],default='wrist');args=parser.parse_args()
rclpy.init(args=['--ros-args','-p','use_sim_time:=true','-p','camera_namespace:='+('/camera' if args.mode=='wrist' else '/global_camera'),'-p','scene:=human','-p','sensing_mode:='+args.mode])
app=QApplication([]);node=HmiNode();window=HmiWindow(node);window.show()
out=Path('/workspace/reports/vision')/args.mode;out.mkdir(parents=True,exist_ok=True)
report={'passed':False,'mode':args.mode}
def wait(predicate,timeout=120):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        app.processEvents();time.sleep(.006)
        if predicate():return
    raise AssertionError('Timeout: '+node.status)
def spin(seconds):
    end=time.monotonic()+seconds
    while time.monotonic()<end:app.processEvents();time.sleep(.006)
def register(source):
    window.registration_source.setCurrentIndex(window.registration_source.findData(source))
    wait(lambda:node.display_frame(source) is not None)
    QTest.mouseClick(window.register_button,Qt.LeftButton);spin(.3)
    assert node.registration is not None,node.status
    r=node.registration
    assert r['reference_comparison']['translation_error_mm']<8,r
    assert r['reference_comparison']['rotation_error_deg']<3,r
    return r
try:
    wait(lambda:node.motion.ready() and node.display_frame('wrist') is not None)
    window.human.setCurrentIndex(1);spin(.3)
    assert not window.plan_button.isEnabled()
    try:node.prepare([[0,-.05,0],[0,.05,0]],.02,.02)
    except ValueError:report['unregistered_plan_rejected']=True
    else:raise AssertionError('Unregistered planning accepted')
    if args.mode=='wrist':
        assert window.global_view.isHidden()
        assert 'global_camera_link' not in node.motion.model.transforms(node.motion.q)
        assert not any(t.startswith('/global_camera/') for t,_ in node.get_topic_names_and_types())
        report['global_sensor_absent']=True
    else:
        report['global_registration']=register('global')
    QTest.mouseClick(window.view_button,Qt.LeftButton)
    wait(lambda:not node.motion.busy and node.motion.phase=='COMPLETE',100);spin(.6)
    report['wrist_registration']=register('wrist')
    window.grab().save(str(out/'registered.png'))
    before=node.motion.q.copy()
    QTest.mouseClick(window.plan_button,Qt.LeftButton)
    wait(lambda:node.motion.phase=='PREVIEW_READY' and window.execute_button.isEnabled())
    assert np.max(np.abs(node.motion.q-before))<.001
    report['preview_did_not_move']=True
    report['reregister']=register('wrist')
    assert node.motion.prepared is None
    report['reregister_invalidates_plan']=True
    # Two genuine mouse clicks on wrist image, using projection only to aim the test mouse.
    window.kind.setCurrentText('相机点选折线');window.clear_points();spin(.3)
    report['clicks']=[]
    for body in [[-.06,.07,0],[-.06,-.07,0]]:
        view=window.wrist_view;snapshot=view.snapshot
        stamp=Time.from_msg(snapshot[1].header.stamp);info=snapshot[2]
        world=points(node.body_pose(),body);twc=matrix(node.tf.lookup_transform('world',info.header.frame_id,stamp))
        c=points(inverse(twc),world);u=round(info.k[0]*c[0]/c[2]+info.k[2]);v=round(info.k[4]*c[1]/c[2]+info.k[5])
        rect=view.rect_image;position=QPoint(round(rect.x()+(u+.5)*rect.width()/info.width),round(rect.y()+(v+.5)*rect.height()/info.height))
        old=len(window.custom);QTest.mouseClick(view,Qt.LeftButton,pos=position);spin(.2)
        assert len(window.custom)==old+1,node.status
        result=window.last_mapping
        np.testing.assert_allclose(points(np.asarray(result['T_world_human']),result['human_m']),result['world_m'],atol=1e-9)
        assert np.linalg.norm(np.asarray(result['world_m'])-world)<.008
        report['clicks'].append(result)
    QTest.mouseClick(window.plan_button,Qt.LeftButton)
    wait(lambda:node.motion.phase=='PREVIEW_READY' and window.execute_button.isEnabled())
    selected=node.preview().copy();window.grab().save(str(out/'preview.png'))
    # Planned endpoints must be derived from vision coordinates.
    target=points(node.body_pose(),np.asarray(window.custom)+[0,0,.02])
    np.testing.assert_allclose(selected[[0,-1]],target,atol=.0002)
    QTest.mouseClick(window.execute_button,Qt.LeftButton)
    wait(lambda:node.execution_result is not None,180)
    result=node.execution_result
    assert result['phase']=='COMPLETE' and result['controller_error_code']==0,result
    trace=np.array([s['world_m'] for s in result['trace_samples']]);assert len(trace)>20
    a=selected[:-1];delta=np.diff(selected,axis=0);den=np.sum(delta*delta,axis=1);errors=[]
    for p in trace:
        alpha=np.clip(np.sum((p-a)*delta,axis=1)/np.maximum(den,1e-15),0,1)
        errors.append(np.linalg.norm(a+alpha[:,None]*delta-p,axis=1).min())
    assert max(errors)<.003
    report.update(execution=result,trace_max_error_mm=max(errors)*1000,preview_world_m=selected.tolist())
    spin(.4);window.grab().save(str(out/'completed.png'))
    window.tabs.setCurrentIndex(1);spin(1.2);window.grab().save(str(out/'frames.png'))
    report['passed']=True
finally:
    if not report['passed']:window.grab().save(str(out/'failure.png'))
    if node.motion.busy:node.motion.cancel();spin(2)
    (out/'integration.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    window.ros_timer.stop();window.timer.stop();node.motion.close();node.destroy_node();window.close()
    if rclpy.ok():rclpy.shutdown()
print(json.dumps({'passed':report['passed'],'mode':args.mode,'trace_max_error_mm':report.get('trace_max_error_mm')}))
