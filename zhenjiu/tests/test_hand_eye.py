from pathlib import Path
from types import SimpleNamespace
import sys
import unittest

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'hand_eye'))
from capture import board_pose, object_points, check_stopped
from solve import solve_pairs
from auto_capture import make_plan, orient, constrain_workspace, observation_steps
from coordinates import camera_to_base_mm, REFERENCE_FRAME


class HandEyeTests(unittest.TestCase):
    def test_base_conversion_units_and_wrong_reference_rejection(self):
        X=np.eye(4);X[:3,3]=[.1,.2,.3]
        base=np.eye(4);base[0,3]=.4
        cal=dict(reference_frame=REFERENCE_FRAME,units='m',camera_serial='test',T_reference_camera=X.tolist())
        result=camera_to_base_mm([10,20,500],base,cal,reference_frame=REFERENCE_FRAME,camera_serial='test')
        np.testing.assert_allclose(result,[510,220,800])
        with self.assertRaises(ValueError):
            camera_to_base_mm([10,20,500],base,cal,reference_frame='flange',camera_serial='test')
        with self.assertRaises(ValueError):
            camera_to_base_mm([10,20,0],base,cal,reference_frame=REFERENCE_FRAME,camera_serial='test')

    def test_observation_substeps_keep_rotation_and_translation_small(self):
        a=np.array([0,0,100,-179,1,2.])
        b=np.array([8,5,105,177,5,8.])
        steps=observation_steps(a,b)
        for value in steps:
            d=np.array(value)-a
            self.assertLessEqual(np.linalg.norm(d[:3]),3.00001)
            self.assertLessEqual(np.linalg.norm((d[3:]+180)%360-180),2.00001)
            a=np.array(value)
        np.testing.assert_allclose(steps[-1],b)

    def test_workspace_conflict_prevents_motion_plan(self):
        plan=make_plan([-633.,-51.,423.,-159.,10.,-154.])
        constraint=dict(applies_to_current_calibration=None)
        with self.assertRaises(ValueError): constrain_workspace(plan,constraint)
        constraint.update(applies_to_current_calibration=True,frame='Base',reference_point='FLANGE',
                          workspace_mm=dict(min=[-878,-384,90],max=[-444,543,212]))
        with self.assertRaises(ValueError): constrain_workspace(plan,constraint)

    def test_automatic_plan_stays_in_small_envelope(self):
        start=[-593., -79., 540., -170., 7., -155.]
        plan=make_plan(start)
        self.assertEqual(len(plan['points']),25)
        for p in plan['points']:
            v=p['pose']
            self.assertLessEqual(abs(v['x_mm']-start[0]),5)
            self.assertLessEqual(abs(v['y_mm']-start[1]),5)
            self.assertGreaterEqual(v['z_mm'],start[2])
            self.assertLessEqual(v['z_mm'],start[2]+5)
        self.assertEqual(plan['speed'],3.)

    def test_corner_tracking_corrects_reversal_and_rejects_large_jump(self):
        pts=object_points()[:,:2]*1500
        corrected,cost=orient((pts+[5,3])[::-1],pts)
        np.testing.assert_allclose(corrected,pts+[5,3])
        self.assertLess(cost,6)
        with self.assertRaises(ValueError): orient(pts+100,pts)

    def test_measured_rectangular_grid_and_pnp(self):
        obj = object_points()
        self.assertAlmostEqual(obj[8, 0], .192)
        self.assertAlmostEqual(obj[-1, 1], .170/7*5)
        intr = SimpleNamespace(fx=606., fy=605., ppx=325., ppy=247., coeffs=[0]*5)
        K = np.array([[606,0,325],[0,605,247],[0,0,1.]])
        rv, tv = np.array([.2,-.15,.1]), np.array([-.1,-.06,.6])
        corners, _ = cv2.projectPoints(obj, rv, tv, K, None)
        T, error = board_pose(corners.reshape(-1,2), intr)
        np.testing.assert_allclose(T[:3,3], tv, atol=1e-6)
        self.assertLess(error, 1e-5)

    def test_eye_in_hand_direction_and_units(self):
        rng = np.random.default_rng(9)
        def transform():
            T = np.eye(4)
            T[:3,:3] = cv2.Rodrigues(rng.normal(0,.4,3))[0]
            T[:3,3] = rng.normal(0,.2,3)
            return T
        X, board = transform(), transform()
        base = [transform() for _ in range(20)]
        target = [np.linalg.inv(X) @ np.linalg.inv(b) @ board for b in base]
        result, _ = solve_pairs(base, target)
        np.testing.assert_allclose(result, X, atol=1e-7)

    def test_translation_only_is_rejected(self):
        base = [np.eye(4) for _ in range(10)]
        for i,b in enumerate(base): b[0,3] = i*.01
        with self.assertRaisesRegex(ValueError, 'Rotation diversity'):
            solve_pairs(base, [np.linalg.inv(b) for b in base])

    def test_moving_feedback_is_rejected(self):
        s = dict(controller_simulation=False, robot_state=dict(moving=1,error=0,emergency_stop=0))
        with self.assertRaises(ValueError): check_stopped(s,s)


if __name__ == '__main__': unittest.main()
