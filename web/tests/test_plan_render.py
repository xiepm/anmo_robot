import sys
import unittest
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'zhenjiu'))
from acupoint_locator_refactored import render_frame

class PlanRenderTests(unittest.TestCase):
    def test_unselected_markers_and_rows_are_absent_from_photo(self):
        rgb=np.zeros((360,640,3),dtype=np.uint8)
        point=dict(name='大椎',side=None,tier='低',code='GV-14',x=200,y=180,preview_id=1)
        other=dict(name='肩井',side='L',tier='低',code='GB-21',x=100,y=80,preview_id=20)
        kwargs=dict(upscale=1.,back_panel=True,panel_keys=[('大椎',None)],show_coordinates=True)
        selected=render_frame(rgb,[point,other],**kwargs)
        expected=render_frame(rgb,[point],**kwargs)
        np.testing.assert_array_equal(selected,expected)
        np.testing.assert_array_equal(selected[65:95,85:115],rgb[65:95,85:115])
        self.assertFalse(np.array_equal(selected[165:195,185:215],rgb[165:195,185:215]))

    def test_missing_points_only_get_table_rows_no_image_markers(self):
        rgb=np.zeros((360,640,3),dtype=np.uint8)
        image=render_frame(rgb,[],upscale=1.,back_panel=True,panel_keys=[('风池','L')])
        np.testing.assert_array_equal(image[:360,:640],rgb)

if __name__=='__main__': unittest.main()
