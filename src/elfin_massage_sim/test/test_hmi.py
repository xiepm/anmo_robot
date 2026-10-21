import unittest
import xml.etree.ElementTree as ET
from unittest.mock import Mock
import numpy as np
from scipy.spatial.transform import Rotation
from elfin_massage_sim.frames import points,inverse,pixel_mapping,same_pose,template_path,validate_body_path
from elfin_massage_sim.model import description
from elfin_massage_sim.kinematics import Kinematics,DOWN
from elfin_massage_sim.collision import CollisionChecker
from elfin_massage_sim.trajectory import Planner
from elfin_massage_sim.direct_motion import DirectMotion
from types import SimpleNamespace
from rclpy.time import Time
from rclpy.clock import ClockType
from elfin_massage_sim.hmi_backend import HmiNode

class FrameTests(unittest.TestCase):
    def test_display_waits_for_exposure_time_tf(self):
        node=object.__new__(HmiNode)
        def snapshot(t):
            header=SimpleNamespace(stamp=Time(seconds=t).to_msg())
            return (object(),SimpleNamespace(header=header),SimpleNamespace(header=SimpleNamespace(frame_id='optical')))
        older,newer=snapshot(.95),snapshot(1.)
        node.frame_queues={'wrist':[older,newer]}
        node.get_clock=lambda:SimpleNamespace(now=lambda:Time(seconds=1.03,clock_type=ClockType.ROS_TIME))
        node.tf=SimpleNamespace(can_transform=lambda target,source,stamp:source=='human_frame' or stamp.nanoseconds<1_000_000_000)
        self.assertIs(node.display_frame('wrist'),older)
        node.get_clock=lambda:SimpleNamespace(now=lambda:Time(seconds=2.,clock_type=ClockType.ROS_TIME))
        self.assertIsNone(node.display_frame('wrist'))
    def test_start_state_change_invalidates_reviewed_trajectory(self):
        motion=object.__new__(DirectMotion)
        motion.busy=False;motion.ready=lambda:True;motion.q=np.zeros(6)
        motion.prepared=[SimpleNamespace(positions=np.array([[.1]*6]))]
        motion.state=Mock()
        self.assertFalse(motion.execute_prepared())
        self.assertIsNone(motion.prepared)
        motion.state.assert_called_once()
    def test_rotated_body_and_camera_round_trip(self):
        h=np.eye(4);h[:3,:3]=Rotation.from_euler('xyz',[.1,-.05,.7]).as_matrix();h[:3,3]=[.6,-.1,.4]
        c=np.eye(4);c[:3,:3]=Rotation.from_euler('xyz',[.2,.3,-.4]).as_matrix();c[:3,3]=[.2,.5,1.4]
        result=pixel_mapping(300,220,.7,[550,0,320,0,550,240,0,0,1],640,480,c,h)
        np.testing.assert_allclose(points(h,result['human_m']),result['world_m'],atol=1e-12)
        np.testing.assert_allclose(points(inverse(c),result['world_m']),result['camera_m'],atol=1e-12)
        wrong=np.array(result['human_m'])+[.6,-.1,.4]
        self.assertGreater(np.linalg.norm(wrong-result['world_m']),.1)
    def test_templates_and_pose_change(self):
        for shape in ['纵向直线','横向往返','椭圆轨迹']:
            for region in ['背部中央','背部左侧','背部右侧']:validate_body_path(template_path(shape,region))
        moved=np.eye(4);moved[0,3]=.002
        self.assertFalse(same_pose(np.eye(4),moved))
        with self.assertRaises(ValueError):validate_body_path([[0,0,0],[.2,0,0]])
    def test_trace_uses_registered_body_frame(self):
        root=ET.fromstring(description('wrist',True));joint=root.find("joint[@name='human_registration']/origin")
        joint.set('xyz','.61 .025 .4');joint.set('rpy','0 0 .18')
        model=Kinematics(ET.tostring(root,encoding='unicode'));checker=CollisionChecker(model)
        path=template_path('纵向直线')
        stages=Planner(model,checker).trace(np.zeros(6),path)
        trace=next(s for s in stages if s.name=='TRACE');t=model.transforms(np.zeros(6))['human_frame']
        np.testing.assert_allclose(model.fk(trace.positions[-1])[:3,3],points(t,path[-1]+[0,0,.02]),atol=.0002)
        np.testing.assert_allclose(model.fk(trace.positions[-1])[:3,:3],t[:3,:3]@DOWN,atol=.002)
