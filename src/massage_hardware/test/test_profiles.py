import unittest,copy,tempfile
from pathlib import Path
import yaml
from massage_hardware.profile import load_profile,ordered_positions,readiness_gaps
ROOT=Path(__file__).resolve().parents[1]
class ProfileTests(unittest.TestCase):
    def test_default_requires_real_calibration_and_cabinet_confirmation(self):
        p=load_profile(ROOT/'config/workstation.yaml')
        self.assertEqual(p['mode'],'commissioning_read_only');self.assertGreaterEqual(len(readiness_gaps(p)),6)
        self.assertEqual(p['camera']['depth_topic'],'/sensors/d435i/aligned_depth_to_color/image_raw')
    def test_named_feedback_reordering_and_seven_axes(self):
        names=[f'axis_{i}' for i in range(7)]
        self.assertEqual(ordered_positions(names,names[::-1],list(range(7))[::-1]),list(range(7)))
        with self.assertRaises(ValueError):ordered_positions(names,['axis_0'],[0])
        with self.assertRaises(ValueError):ordered_positions(['a'],['a','a'],[0,1])
        with self.assertRaises(ValueError):ordered_positions(['a'],['a'],[float('nan')])
    def test_profile_cannot_enable_hardware_motion(self):
        p=load_profile(ROOT/'config/workstation.yaml');p['mode']='execute'
        with tempfile.TemporaryDirectory() as d:
            f=Path(d)/'profile.yaml';f.write_text(yaml.safe_dump(p))
            with self.assertRaises(ValueError):load_profile(f)
