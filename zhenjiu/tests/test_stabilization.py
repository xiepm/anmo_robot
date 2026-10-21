"""Regression checks for tracking loss, geometry mapping, and display smoothing."""
from pathlib import Path
import sys
import unittest
import numpy as np
import cv2
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from stabilization import AdaptiveFilter, StablePoints, TorsoDisplayTracker, BaseDisplayFilter
from pose_backends import affine_input, landmarks_from_coco, rotation_matrix, transform_points
from pose_backends import select_startup_rotation
from acupoint_locator_refactored import Body, render_frame, make_rules
from types import SimpleNamespace


def point(x, y=100):
    return dict(name='命门', code='GV-4', side=None, tier='低', x=x, y=y)


class StabilityTests(unittest.TestCase):
    def test_weak_tracking_is_display_only_and_does_not_forge_visibility(self):
        tracker=TorsoDisplayTracker()
        def observed(score=.8):
            lm=[SimpleNamespace(x=.5,y=.5,visibility=0.,raw_visibility=0.) for _ in range(33)]
            for i,xy in {11:(.3,.2),12:(.7,.2),23:(.35,.8),24:(.65,.8)}.items():
                lm[i]=SimpleNamespace(x=xy[0],y=xy[1],visibility=score if score>=.5 else 0.,raw_visibility=score)
            return lm
        for i in range(3):tracker.update(observed(),(480,640),at=i*.1)
        lm=observed(.46);out=tracker.update(lm,(480,640),at=.3)
        self.assertEqual(tracker.state,'TRACKING_WEAK')
        self.assertEqual(out[23].visibility,0.)
        self.assertFalse(Body(lm).visible(11,12,23,24))
        self.assertFalse(Body(out).visible(11,12,23,24))
        self.assertTrue(Body(out,allow_display_tracking=True).visible(11,12,23,24))
        out=tracker.update(observed(.3),(480,640),at=.4)
        self.assertFalse(Body(out,allow_display_tracking=True).visible(11,12,23,24))
        self.assertEqual(tracker.state,'LOST')
        # Weak-only frames cannot acquire a new torso.
        for i in range(10):out=tracker.update(observed(.46),(480,640),at=.5+i*.1)
        self.assertFalse(Body(out,allow_display_tracking=True).visible(11,12,23,24))

    def test_tracking_breaks_on_large_weak_motion_and_stale_gap(self):
        def observed():
            return [SimpleNamespace(x=.3,y=.4,visibility=.8,raw_visibility=.8) for _ in range(33)]
        for kind in ('jump','gap','outside'):
            tracker=TorsoDisplayTracker()
            for i in range(3):tracker.update(observed(),(480,640),at=i*.1)
            lm=observed()
            for p in lm:p.visibility=0.;p.raw_visibility=.45
            if kind=='jump':lm[23].x+=.15
            if kind=='outside':lm[23].x=1.05
            out=tracker.update(lm,(480,640),at=1. if kind=='gap' else .3)
            self.assertFalse(Body(out,allow_display_tracking=True).visible(11,12,23,24))

    def test_base_display_smoothing_keeps_raw_and_clears_invalid_immediately(self):
        f=BaseDisplayFilter();rng=np.random.default_rng(17);raw=rng.normal(250,2,100)
        filtered=[]
        for i,z in enumerate(raw):
            p=dict(point(10),base_coordinate_valid=True,base_point_mm=[10,20,z])
            q=f.update([p],at=i*.1)[0]
            self.assertEqual(p['base_point_mm'][2],z)
            if q['base_coordinate_valid']:filtered.append(q['base_point_mm'][2])
        self.assertLess(np.std(filtered[10:]),np.std(raw[10:])*.65)
        invalid=dict(point(10),base_coordinate_valid=False,base_point_mm=None)
        self.assertIsNone(f.update([invalid],at=10)[0]['base_point_mm'])
        self.assertFalse(f.filter.states)

    def test_fixed_back_canvas_and_table_do_not_resize_on_point_loss(self):
        rgb=np.zeros((480,640,3),np.uint8)
        sizes=[]
        for count in (0,1,8,18):
            points=[dict(point(100+i*8),preview_id=i+1) for i in range(count)]
            sizes.append(render_frame(rgb,points,upscale=1,back_panel=True).shape)
        self.assertEqual(set(sizes),{(578,1090,3)})

    def test_front_tianshu_rotates_with_torso_and_preserves_sides(self):
        xy = np.full((33, 2), .5)
        xy[11], xy[12] = [.7, .2], [.3, .2]
        xy[23], xy[24] = [.65, .8], [.35, .8]
        def body(coords):
            return Body([SimpleNamespace(x=x, y=y, visibility=1.) for x, y in coords])
        rule = next(r for r in make_rules() if r['name'] == '天枢')
        original = body(xy)
        rotated = body(np.column_stack((1 - xy[:, 1], xy[:, 0])))
        for side in ('L', 'R'):
            p = rule['fn'](original, side)
            q = rule['fn'](rotated, side)
            np.testing.assert_allclose(q, [1 - p[1], p[0]], atol=1e-7)
        self.assertGreater(rule['fn'](original, 'L')[0], original.navel[0])
        self.assertLess(rule['fn'](original, 'R')[0], original.navel[0])

    def test_static_noise_reduced_without_freezing_real_motion(self):
        f=AdaptiveFilter()
        rng=np.random.default_rng(42)
        raw=rng.normal(100, 2, size=120)
        output=[f.update('p',[x,100],i/30) for i,x in enumerate(raw)]
        values=np.array([x[0] for x in output[15:] if x is not None])
        self.assertLess(values.std(), raw[15:].std()*0.5)
        for i in range(60): value=f.update('p',[100+i,100],(120+i)/30)
        self.assertGreater(value[0],150)  # Follows a sustained 60-pixel movement.

    def test_single_frame_outlier_hidden_not_held_valid(self):
        f=StablePoints()
        for i in range(4): out=f.update([point(100)],at=i*.03)
        self.assertTrue(out)
        self.assertEqual(f.update([point(300)],at=.12),[])
        self.assertLess(f.update([point(100)],at=.15)[0]['x'],101)

    def test_no_ghost_after_loss_or_reacquisition(self):
        f=StablePoints()
        for i in range(4): f.update([point(100)],at=i*.03)
        self.assertEqual(f.update([],at=.2),[])
        self.assertEqual(f.update([point(300)],at=1),[])
        self.assertEqual(f.update([point(300)],at=1.03),[])
        self.assertEqual(f.update([point(300)],at=1.06)[0]['x'],300)

    def test_coherent_large_motion_reacquires(self):
        f=StablePoints()
        for i in range(4): f.update([point(100)],at=i*.03)
        for i in range(3): out=f.update([point(300)],at=.12+i*.03)
        self.assertEqual(out[0]['x'],300)

    def test_invalid_depth_never_retains_old_xyz(self):
        f=StablePoints()
        good=dict(point(100),coordinate_valid=True,camera_point_mm=[10,20,800],depth_mm=800)
        for i in range(4): out=f.depth([good],at=i*.03)
        self.assertTrue(out[0]['coordinate_valid'])
        invalid=dict(good,coordinate_valid=False,camera_point_mm=None,depth_mm=None)
        out=f.depth([invalid],at=.12)
        self.assertFalse(out[0]['coordinate_valid'])
        self.assertIsNone(out[0]['camera_point_mm'])
        self.assertFalse(f.depth([good],at=.15)[0]['coordinate_valid'])

    def test_label_candidate_stays_fixed(self):
        layout={}
        rgb=np.zeros((480,640,3),np.uint8)
        render_frame(rgb,[point(300)],upscale=1,label_layout=layout)
        first=layout.copy()
        render_frame(rgb,[point(310),dict(point(311),name='肾俞',side='L')],upscale=1,label_layout=layout)
        self.assertEqual(first[('命门',None)],layout[('命门',None)])


class GeometryTests(unittest.TestCase):
    def test_startup_direction_rejects_collapsed_high_confidence_geometry(self):
        class Model:
            rotation=90
            threshold=.5
            def infer(self,frame):
                lm=[SimpleNamespace(x=.5,y=.5,visibility=0.) for _ in range(33)]
                score=.65 if self.rotation==0 else (.9 if self.rotation==90 else .2)
                for idx,xy in {11:(.3,.2),12:(.7,.2),23:(.35,.8),24:(.65,.8)}.items():
                    lm[idx]=SimpleNamespace(x=xy[0],y=xy[1],visibility=score)
                if self.rotation==90:lm[11]=lm[12]  # invalid shoulder geometry
                return lm
        m=Model();report=select_startup_rotation(m,[None]*3)
        self.assertEqual(m.rotation,0)
        self.assertEqual(next(r for r in report if r['rotation']==0)['valid_frames'],3)
        self.assertEqual(next(r for r in report if r['rotation']==90)['valid_frames'],0)

    def test_rotation_roundtrip_preserves_depth_pixel(self):
        pts=np.array([[30,60],[250,160],[400,300]],dtype=float)
        for angle in [0,90,180,270]:
            m=rotation_matrix((480,640,3),angle)
            rotated=transform_points(pts,m)
            back=transform_points(rotated,cv2.invertAffineTransform(m))
            np.testing.assert_allclose(back,pts,atol=1e-5)
        np.testing.assert_allclose(transform_points([[10,240]],rotation_matrix((480,640,3),90)),[[239,10]])

    def test_partial_body_uses_only_observed_anchors(self):
        kpts=np.full((133,2),200.);scores=np.full(133,.9)
        kpts[11]=[-10,200]  # Left hip outside frame despite high score.
        lm=landmarks_from_coco(kpts,scores,(480,640,3))
        self.assertFalse(Body(lm).visible(11,12,23,24))
        self.assertEqual(lm[17].visibility,0)  # No fabricated hand points.

    def test_affine_center_decodes_to_original_center(self):
        image=np.zeros((480,640,3),np.uint8)
        tensor,center,scale=affine_input(image,(192,256),[100,50,400,400])
        self.assertEqual(tensor.shape,(1,3,256,192))
        np.testing.assert_allclose(np.array([96,128])/[192,256]*scale+center-scale/2,[250,225])


if __name__=='__main__': unittest.main()
