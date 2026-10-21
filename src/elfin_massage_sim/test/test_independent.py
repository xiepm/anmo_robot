import unittest
from threading import Event
import numpy as np
from elfin_massage_sim.model import description
from elfin_massage_sim.kinematics import Kinematics, DOWN
from elfin_massage_sim.collision import CollisionChecker
from elfin_massage_sim.trajectory import Planner, OBSERVATION_Q


class IndependentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = Kinematics(description('wrist'))
        cls.collisions = CollisionChecker(cls.model)
        cls.planner = Planner(cls.model, cls.collisions)
        cls.stages = cls.planner.approach(np.zeros(6), [.6,.1,.4])

    def test_known_forward_poses(self):
        np.testing.assert_allclose(self.model.fk(np.zeros(6))[:3,3], [0,0,1.4265], atol=1e-5)
        t = self.model.fk(OBSERVATION_Q)
        np.testing.assert_allclose(t[:3,3], [.6,0,.65], atol=2e-4)
        np.testing.assert_allclose(t[:3,:3], DOWN, atol=.002)

    def test_ik_endpoint_and_limits(self):
        q = self.model.ik([.6,.1,.44], OBSERVATION_Q)
        np.testing.assert_allclose(self.model.fk(q)[:3,3],[.6,.1,.44],atol=.0002)
        self.assertTrue(np.all(q>=self.model.lower) and np.all(q<=self.model.upper))
        with self.assertRaises(ValueError):self.model.ik([5,0,.4], OBSERVATION_Q)

    def test_stage_order(self):
        self.assertEqual([s.name for s in self.stages], ['OBSERVE','PREAPPROACH','NORMAL_APPROACH'])
        np.testing.assert_allclose(self.model.fk(self.stages[-2].positions[-1])[:3,3],[.6,.1,.55],atol=.0002)

    def test_interpolated_normal_line_and_limits(self):
        for stage in self.stages:
            spline = stage.spline()
            t = np.linspace(0,stage.times[-1],501)
            self.assertLessEqual(abs(spline(t,1)).max(),self.planner.max_velocity*1.001)
            self.assertLessEqual(abs(spline(t,2)).max(),self.planner.max_acceleration*1.001)
            np.testing.assert_allclose(spline([t[0],t[-1]],1),0,atol=1e-8)
        s = self.stages[-1]
        for q in s.spline()(np.linspace(0,s.times[-1],101)):
            p=self.model.fk(q)[:3,3]
            np.testing.assert_allclose(p[:2],[.6,.1],atol=.001)
            self.assertGreaterEqual(p[2],.4398)

    def test_collision_detection_and_joint_limits(self):
        self.collisions.check(np.zeros(6))
        with self.assertRaises(ValueError):self.collisions.check(self.model.upper+.01)
        # Move the ball into the pad: reject a geometrically colliding IK endpoint.
        q=self.model.ik([.6,.1,.35],self.stages[-1].positions[-1])
        with self.assertRaisesRegex(ValueError,'Collision'):self.collisions.check(q)

    def test_cancel_before_planning(self):
        event=Event();event.set()
        with self.assertRaises(InterruptedError):
            Planner(self.model,self.collisions,event.is_set).approach(np.zeros(6),[.6,.1,.4])

    def test_invalid_surface(self):
        with self.assertRaises(ValueError):self.planner.approach(np.zeros(6),[.6,.1,.2])
