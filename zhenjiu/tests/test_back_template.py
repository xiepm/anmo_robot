import unittest
from pathlib import Path
import sys
from types import SimpleNamespace
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from acupoint_locator_refactored import Body,make_rules,compute_points
from rehab_vision.contract import Contract


def landmarks():
    lm=[SimpleNamespace(x=.5,y=.5,visibility=0.) for _ in range(33)]
    for i,(x,y) in {7:(.6,.1),8:(.4,.1),11:(.75,.2),12:(.25,.2),23:(.7,.8),24:(.3,.8)}.items():
        lm[i]=SimpleNamespace(x=x,y=y,visibility=.8)
    return lm


def locate(lm,view='back'):
    return compute_points(Body(lm),[r for r in make_rules() if r['region']=='back'],
                          {'高','中','低'},set(),(640,640,3),view=view)


class BackTemplateTests(unittest.TestCase):
    def test_candidates_are_unique_explicitly_unvalidated_and_in_contract(self):
        points=locate(landmarks())
        self.assertEqual(len(points),25)
        self.assertEqual(len({(p['name'],p['side']) for p in points}),25)
        self.assertEqual({p['preview_id'] for p in points},set(range(1,26)))
        self.assertTrue(all(p['localization_method']=='pose_proportion_estimate'
                            and p['anatomical_location_validated'] is False for p in points))
        measured=[dict(p,coordinate_valid=True,camera_point_mm=[1.,2.,500.]) for p in points]
        self.assertEqual(len(Contract().points('BACK',measured)),25)

    def test_neck_requires_visible_consistent_head_and_no_invented_waist_acupoint(self):
        for idx in (7,8):
            lm=landmarks();lm[idx].visibility=.49
            pts=locate(lm)
            self.assertEqual(len(pts),23)
            self.assertFalse(any(p['name']=='风池' for p in pts))
        for idx in (7,8):
            lm=landmarks();lm[idx].y=.5
            self.assertFalse(any(p['name']=='风池' for p in locate(lm)))
        lm=landmarks();lm[7],lm[8]=lm[8],lm[7]
        self.assertFalse(any(p['name']=='风池' for p in locate(lm)))
        self.assertFalse(any(p['name']=='腰背' for p in locate(landmarks())))

    def test_required_plan_points_and_relative_candidate_positions(self):
        pts={(p['name'],p['side']):p for p in locate(landmarks())}
        for name in ('肩井','天宗','风池'):
            self.assertGreater(pts[name,'L']['x'],pts[name,'R']['x'])
        self.assertEqual(pts['腰阳关',None]['y'],pts['大肠俞','L']['y'])
        self.assertLess(pts['肩井','L']['y'],pts['天宗','L']['y'])
        self.assertLess(pts['风池','L']['y'],pts['大椎',None]['y'])

    def test_no_points_from_missing_or_crossed_anchors(self):
        for idx in (11,12,23,24):
            lm=landmarks();lm[idx].visibility=.49
            self.assertEqual(locate(lm),[])
        lm=landmarks();lm[23],lm[24]=lm[24],lm[23]
        self.assertEqual(locate(lm),[])

    def test_rotation_preserves_left_right_and_all_coordinates(self):
        lm=landmarks();original=locate(lm)
        rotated=[SimpleNamespace(x=1-p.y,y=p.x,visibility=p.visibility) for p in lm]
        for p,q in zip(original,locate(rotated)):
            self.assertEqual((p['name'],p['side']),(q['name'],q['side']))
            np.testing.assert_allclose([q['x'],q['y']],[640-p['y'],p['x']],atol=.11)

    def test_original_anchor_positions_preserved(self):
        body=Body(landmarks());points=locate(landmarks())
        for name,fraction in [('大椎',.05),('命门',.60)]:
            p=next(p for p in points if p['name']==name)
            np.testing.assert_allclose([p['x'],p['y']],np.array(body.torso_center(fraction))*640,atol=.1)

    def test_back_template_does_not_leak_into_front(self):
        self.assertEqual(locate(landmarks(),view='front'),[])


if __name__=='__main__':unittest.main()
