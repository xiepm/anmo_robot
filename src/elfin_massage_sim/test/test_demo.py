import unittest
import numpy as np
from elfin_massage_sim.model import description
from elfin_massage_sim.geometry import approach
from elfin_massage_sim.kinematics import Kinematics
from elfin_massage_sim.collision import CollisionChecker
from elfin_massage_sim.trajectory import Planner,OBSERVATION_Q
from elfin_massage_sim.demo_scene import DEMO_POINTS

class DemoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model=Kinematics(description('wrist',True))
        cls.collisions=CollisionChecker(cls.model)
        cls.planner=Planner(cls.model,cls.collisions)
    def test_two_independent_cameras_and_human_collision(self):
        root=self.model.root
        self.assertEqual(root.find("joint[@name='camera_mount']/parent").get('link'),'elfin_end_link')
        self.assertEqual(root.find("joint[@name='global_camera_mount']/parent").get('link'),'world')
        namespaces={e.text for e in root.findall('gazebo/sensor/plugin/ros/namespace')}
        self.assertEqual(namespaces,{'/camera','/global_camera'})
        q=self.model.ik([.6,0,.375],OBSERVATION_Q)
        with self.assertRaisesRegex(ValueError,'human_torso'):
            self.collisions.check(q)
    def test_demo_rejects_head_and_couch(self):
        for point in ((.6,-.44,.4),(.6,.7,.4),(.6,0,.23)):
            with self.assertRaises(ValueError):approach(point,.02,'human')
    def test_three_point_path(self):
        stages=self.planner.demo(np.zeros(6),DEMO_POINTS)
        self.assertEqual(len(stages),17)
        for i,point in enumerate(DEMO_POINTS,1):
            press=next(s for s in stages if s.name==f'P{i}_PRESS')
            np.testing.assert_allclose(self.model.fk(press.positions[-1])[:3,3],np.asarray(point)+[0,0,.02],atol=.0002)
        np.testing.assert_allclose(stages[-1].positions[-1],OBSERVATION_Q)
        with self.assertRaises(ValueError):self.planner.demo(np.zeros(6),DEMO_POINTS[:1])
