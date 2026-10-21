#!/usr/bin/env python3
"""Synthetic ROS messages in a private domain; no robot action publisher exists."""
import time,json
from pathlib import Path
import numpy as np
import rclpy
from sensor_msgs.msg import Image,CameraInfo,JointState
from std_msgs.msg import String
from rclpy.qos import qos_profile_sensor_data
from PyQt5.QtWidgets import QApplication
from massage_hardware.console import HardwareMonitor,Console
rclpy.init(args=['--ros-args','-p','profile:=/workspace/src/massage_hardware/config/workstation.yaml'])
app=QApplication([]);n=HardwareMonitor();w=Console(n);w.show();pub=rclpy.create_node('synthetic_hardware_fixture')
c=n.profile['camera'];r=n.profile['robot'];out=Path('/workspace/reports/hardware')
ps={key:pub.create_publisher(typ,topic,qos_profile_sensor_data if typ is not String else 10) for key,typ,topic in [
 ('rgb',Image,c['rgb_topic']),('depth',Image,c['depth_topic']),('info',CameraInfo,c['info_topic']),
 ('joints',JointState,r['joint_states_topic']),('io',String,n.profile['io']['state_topic'])]}
def spin(seconds):
 end=time.monotonic()+seconds
 while time.monotonic()<end:app.processEvents();rclpy.spin_once(pub,timeout_sec=0);time.sleep(.005)
try:
 spin(.7);assert not n.fresh('rgb')
 w.grab().save(str(out/'disconnected.png'))
 rgb=n.bridge.cv2_to_imgmsg(np.full((240,320,3),(40,120,160),np.uint8),'rgb8')
 depth=n.bridge.cv2_to_imgmsg(np.full((240,320),500,np.uint16),'16UC1')
 info=CameraInfo(width=320,height=240);info.k=[250.,0.,160.,0.,250.,120.,0.,0.,1.]
 joints=JointState(name=r['joint_names'][::-1],position=[.6,.5,.4,.3,.2,.1])
 io=String(data=json.dumps(dict(start=False,stop=False,reset_request=False,safety_ok_feedback=False)))
 end=time.monotonic()+2
 while time.monotonic()<end:
  for m in (rgb,depth,info,joints):m.header.stamp=pub.get_clock().now().to_msg();m.header.frame_id=c['optical_frame']
  for key,m in [('rgb',rgb),('depth',depth),('info',info),('joints',joints),('io',io)]:ps[key].publish(m)
  spin(.1)
 assert n.fresh('rgb') and n.fresh('depth') and n.fresh('io')
 np.testing.assert_allclose(n.joints,[.1,.2,.3,.4,.5,.6]);assert n.depth.encoding=='16UC1'
 assert n.io['safety_ok_feedback'] is False
 w.grab().save(str(out/'synthetic-connected.png'));spin(1.0)
 assert not n.fresh('rgb') and not n.fresh('io')
 assert '过期' in w.state.toPlainText();w.grab().save(str(out/'stale.png'))
 (out/'console-test.json').write_text(json.dumps({'passed':True,'synthetic_data':True,'joint_order_verified':True,'stale_status_verified':True,'real_camera_tested':False,'real_robot_tested':False},indent=2))
finally:w.timer.stop();w.spin_timer.stop();n.destroy_node();pub.destroy_node();w.close();rclpy.shutdown()
