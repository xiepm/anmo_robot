import unittest
from unittest.mock import Mock
from elfin_massage_sim.hmi_backend import HmiNode
import cv2
import numpy as np
from scipy.spatial.transform import Rotation
from elfin_massage_sim.registration import estimate_rgbd,MARKERS,compare_reference,registered_model,rigid_fit
from elfin_massage_sim.frames import points
from elfin_massage_sim.model import description
from elfin_massage_sim.kinematics import Kinematics

class RegistrationTests(unittest.TestCase):
    def test_no_preset_fallback_before_registration(self):
        node=object.__new__(HmiNode);node.registration=None;node.tf=Mock();node.motion=Mock()
        with self.assertRaises(ValueError):node.body_pose()
        node.tf.lookup_transform.assert_not_called();node.motion.model.transforms.assert_not_called()
    def scene(self):
        k=np.array([[900,0,400],[0,900,300],[0,0,1.]])
        t=np.eye(4);t[:3,:3]=Rotation.from_euler('xyz',[np.pi,.04,.13]).as_matrix();t[:3,3]=[.015,-.01,.8]
        bgr=np.zeros((600,800,3),np.uint8);depth=np.full((600,800),np.nan,np.float32)
        for xyz,color in zip(MARKERS.values(),[(0,140,255),(0,255,0),(255,0,0)]):
            c=points(t,xyz);u,v=np.rint((k@c)[:2]/c[2]).astype(int)
            cv2.circle(bgr,(u,v),12,color,-1);cv2.circle(depth,(u,v),12,float(c[2]),-1)
        world=np.eye(4);world[:3,:3]=Rotation.from_euler('xyz',[.1,.3,-.6]).as_matrix();world[:3,3]=[.4,.2,.9]
        return bgr,depth,k,world,t
    def test_rgbd_estimate_and_separate_reference(self):
        bgr,depth,k,world,t=self.scene();r=estimate_rgbd(bgr,depth,k,world)
        error=compare_reference(r['T_world_human'],world@t)
        self.assertLess(error['translation_error_mm'],1);self.assertLess(error['rotation_error_deg'],1)
        wrong=np.eye(4);wrong[:3,3]=[8,9,10]
        self.assertGreater(compare_reference(r['T_world_human'],wrong)['translation_error_mm'],1000)
        np.testing.assert_allclose(r['T_world_human'],estimate_rgbd(bgr,depth,k,world)['T_world_human'])
    def test_missing_marker_invalid_depth_and_ambiguity_rejected(self):
        bgr,depth,k,world,_=self.scene()
        broken=bgr.copy();broken[(broken[:,:,0]==255)]=0
        with self.assertRaises(ValueError):estimate_rgbd(broken,depth,k,world)
        with self.assertRaises(ValueError):estimate_rgbd(bgr,np.full_like(depth,np.nan),k,world)
        cv2.circle(bgr,(40,40),9,(255,0,0),-1)
        with self.assertRaises(ValueError):estimate_rgbd(bgr,depth,k,world)
    def test_depth_units_and_degenerate_fit(self):
        bgr,depth,k,world,_=self.scene()
        millimetres=np.nan_to_num(depth*1000).astype(np.uint16)
        a=estimate_rgbd(bgr,depth,k,world);b=estimate_rgbd(bgr,millimetres,k,world,'16UC1')
        self.assertLess(compare_reference(a['T_world_human'],b['T_world_human'])['translation_error_mm'],2)
        with self.assertRaises(ValueError):rigid_fit(list(MARKERS.values()),np.zeros((3,3)))
    def test_camera_modes_and_registered_model_do_not_mutate_truth(self):
        for mode in ['dual','wrist']:
            model=Kinematics(description('wrist',True,mode,(.61,-.02,.4,.08)))
            tf=model.transforms(np.zeros(6));self.assertEqual('global_camera_link' in tf,mode=='dual')
            target=np.eye(4);target[:3,:3]=Rotation.from_euler('z',.12).as_matrix();target[:3,3]=[.62,.01,.401]
            fitted=registered_model(model,target)
            np.testing.assert_allclose(fitted.transforms(np.zeros(6))['human_frame'],target,atol=1e-12)
            np.testing.assert_allclose(model.transforms(np.zeros(6))['human_frame'],tf['human_frame'])
            np.testing.assert_allclose(fitted.fk(np.zeros(6)),model.fk(np.zeros(6)))
