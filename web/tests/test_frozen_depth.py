import sys,unittest
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from frozen_depth import FrozenDepth
P=dict(name='大椎',side='MIDLINE',x=5.,y=5.)
def pinhole(_, pixel, depth):return [pixel[0]*depth/100,pixel[1]*depth/100,depth]
class FrozenDepthTests(unittest.TestCase):
    def make(self,depth):return FrozenDepth(depth,None,pinhole,[dict(P,coordinate_valid=True)])
    def test_edited_pixel_changes_xyz_and_new_live_frame_cannot_replace_depth(self):
        depth=np.full((20,20),.6);f=self.make(depth);depth[:]=.9
        original=f.resolve([P])[0]['camera_point_mm']
        moved=f.resolve([dict(P,x=8.25)])[0]['camera_point_mm']
        self.assertEqual(original,[30.,30.,600.])
        self.assertAlmostEqual(moved[0],49.5)
        self.assertEqual(moved[2],600.)
    def test_holes_edges_out_of_bounds_and_unknown_names_fail_closed(self):
        for depth in (np.zeros((20,20)),np.pad(np.ones((10,10))*.6,5),np.full((20,20),np.nan)):
            with self.assertRaises(ValueError):self.make(depth).resolve([P])
        f=self.make(np.full((20,20),.6))
        for points in ([dict(P,x=20)],[dict(P,x=-1)],[dict(P,name='肩井')],[P,P]):
            with self.assertRaises(ValueError):f.resolve(points)
if __name__=='__main__':unittest.main()
