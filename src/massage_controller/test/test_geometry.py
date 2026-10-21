import unittest
from massage_controller.geometry import camera_to_base_m,rigid,SimulationGeometry

class GeometryTests(unittest.TestCase):
    def test_chain_units_and_rotation(self):
        base=[[0,-1,0,1],[1,0,0,2],[0,0,1,3],[0,0,0,1]]
        eye=[[1,0,0,.1],[0,1,0,.2],[0,0,1,.3],[0,0,0,1]]
        p=camera_to_base_m([100,200,300],base,eye)
        for a,b in zip(p,[.6,2.2,3.6]):self.assertAlmostEqual(a,b)
        base[0][3]=100
        self.assertAlmostEqual(p[0],.6) # stored base point independent of later pose

    def test_reject_bad_matrix_and_point(self):
        for m in ([[1,0],[0,1]],[[1,0,0,0],[0,1,0,0],[0,0,-1,0],[0,0,0,1]],
                  [[2,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]]):
            with self.assertRaises(ValueError):rigid(m)
        g=SimulationGeometry('SIMULATION')
        with self.assertRaises(ValueError):camera_to_base_m([float('nan'),0,0],g.identity,g.identity)
        with self.assertRaises(ValueError):SimulationGeometry('REAL')

    def test_fixture_bounds(self):
        g=SimulationGeometry('SIMULATION')
        with self.assertRaises(ValueError):g.locate([dict(name='a',side='LEFT',coordinate_valid=True,camera_point_mm=[3000,0,0])])
