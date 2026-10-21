import unittest
import numpy as np
from elfin_massage_sim.kinematics import Kinematics

def description(n=7):
    parts=['<robot name="generic"><link name="base"/>']
    for i in range(n):
        parent='base' if i==0 else f'link{i-1}'
        kind='prismatic' if i==0 else 'revolute'
        parts.append(f'<link name="link{i}"/><joint name="axis{i}" type="{kind}"><parent link="{parent}"/><child link="link{i}"/><axis xyz="0 0 1"/><origin xyz="0 0 0.1"/><limit lower="-1" upper="1"/></joint>')
    return ''.join(parts)+'</robot>'
class GenericKinematicsTests(unittest.TestCase):
    def test_seven_axis_and_prismatic_forward_kinematics(self):
        m=Kinematics(description(),[f'axis{i}' for i in range(7)],'base','link6')
        q=np.zeros(7);q[0]=.2
        np.testing.assert_allclose(m.fk(q)[:3,3],[0,0,.9],atol=1e-12)
        with self.assertRaises(ValueError):m.fk(np.zeros(6))
    def test_joint_order_and_frames_must_match_actual_model(self):
        with self.assertRaises(ValueError):Kinematics(description(),['unknown'],'base','link6')
        with self.assertRaises(ValueError):Kinematics(description(),['axis0','axis0'],'base','link6')
        with self.assertRaises(ValueError):Kinematics(description(),['axis0'],'world','wrong_tcp')
