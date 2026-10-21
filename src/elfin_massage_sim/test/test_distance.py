import time,unittest
from unittest.mock import Mock
from collections import deque
from types import SimpleNamespace
from rclpy.time import Time
import numpy as np
from scipy.spatial.transform import Rotation
from elfin_massage_sim.distance import camera_body_distance
from elfin_massage_sim.hmi_backend import HmiNode

class DistanceTests(unittest.TestCase):
    def test_depth_arrival_does_not_require_an_rgb_frame(self):
        node=object.__new__(HmiNode);node.range_queues={'wrist':deque(maxlen=8)};node.range_received={}
        header=SimpleNamespace(stamp=Time(seconds=1).to_msg(),frame_id='optical')
        depth=SimpleNamespace(width=100,height=80,header=header,encoding='32FC1')
        info=SimpleNamespace(width=100,height=80,header=header)
        node.receive_range('wrist',depth,info)
        self.assertEqual(list(node.range_queues['wrist']),[(depth,info)])
        old=node.range_received['wrist'];node.note_range_frame('wrist',depth)
        self.assertEqual(old,node.range_received['wrist'])
    def test_stopped_clock_expires_even_if_old_depth_packets_arrive(self):
        node=object.__new__(HmiNode);node.registration={'fake':True}
        node.range_clock=(100,time.monotonic()-1);node.range_received={'wrist':(90,time.monotonic())}
        node.range_frame=Mock()
        self.assertIn('时钟停止',node.camera_distance('wrist')['reason']);node.range_frame.assert_not_called()
    def scene(self,height=.4):
        # Downward camera, translated from the body origin: range is not axial depth.
        camera=np.eye(4);camera[:3,:3]=Rotation.from_euler('x',np.pi).as_matrix();camera[:3,3]=[.6,.04,.4+height]
        human=np.eye(4);human[:3,3]=[.6,0,.4]
        return np.full((80,100),height),[100,0,50,0,100,40,0,0,1],camera,human
    def test_motion_changes_range_and_depth_is_not_euclidean_range(self):
        far=camera_body_distance(*self.scene(.4));near=camera_body_distance(*self.scene(.2))
        self.assertAlmostEqual(far['origin_distance_m'],np.hypot(.4,.04))
        self.assertTrue(far['surface_valid']);self.assertGreater(far['surface_range_m'],.4)
        self.assertGreater(far['origin_distance_m']-near['origin_distance_m'],.19)
        self.assertGreater(far['surface_range_m']-near['surface_range_m'],.18)
    def test_units_occluder_and_invalid_depth(self):
        depth,k,camera,human=self.scene()
        a=camera_body_distance(depth,k,camera,human)
        b=camera_body_distance((depth*1000).astype(np.uint16),k,camera,human,'16UC1')
        self.assertAlmostEqual(a['surface_range_m'],b['surface_range_m'])
        # A foreground obstacle and distant bed cannot become back measurements.
        for z in [.2,.8,np.nan,0,np.inf]:
            result=camera_body_distance(np.full_like(depth,z),k,camera,human)
            self.assertFalse(result['surface_valid']);self.assertIsNone(result['surface_range_m'])
            self.assertAlmostEqual(result['origin_distance_m'],a['origin_distance_m'])
    def test_common_world_transform_does_not_change_ranges(self):
        depth,k,camera,human=self.scene();world=np.eye(4)
        world[:3,:3]=Rotation.from_euler('xyz',[.4,-.2,.8]).as_matrix();world[:3,3]=[2,-3,1]
        a=camera_body_distance(depth,k,camera,human);b=camera_body_distance(depth,k,world@camera,world@human)
        self.assertAlmostEqual(a['origin_distance_m'],b['origin_distance_m'])
        self.assertAlmostEqual(a['surface_range_m'],b['surface_range_m'])
    def test_registration_and_wall_clock_staleness_gate_output(self):
        node=object.__new__(HmiNode);node.registration=None;node.range_frame=Mock()
        self.assertFalse(node.camera_distance('wrist')['valid'])
        node.registration={'fake':True};node.range_received={'wrist':(100,time.monotonic()-1)}
        self.assertIn('暂停',node.camera_distance('wrist')['reason'])
        node.range_frame.assert_not_called()
        node.range_received={'wrist':(100,time.monotonic())};node.range_frame.return_value=None
        self.assertIn('TF',node.camera_distance('wrist')['reason'])
