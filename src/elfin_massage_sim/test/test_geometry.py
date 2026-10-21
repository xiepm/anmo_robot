import math
import unittest
from elfin_massage_sim.geometry import deproject, approach


class ProjectionTests(unittest.TestCase):
    k = [500., 0., 320., 0., 500., 240., 0., 0., 1.]

    def test_center(self):
        self.assertEqual(deproject(320, 240, 1.1, self.k, 640, 480), (0., 0., 1.1))

    def test_off_axis(self):
        self.assertEqual(deproject(420, 290, 2., self.k, 640, 480), (0.4, 0.2, 2.))

    def test_bad_depth(self):
        for depth in (0., -1., float('nan'), float('inf'), 5.):
            with self.assertRaises(ValueError):
                deproject(320, 240, depth, self.k, 640, 480)

    def test_bad_pixel_and_calibration(self):
        for u, v in ((640, 0), (-1, 0), (0, 480)):
            with self.assertRaises(ValueError):
                deproject(u, v, 1., self.k, 640, 480)
        with self.assertRaises(ValueError):
            deproject(0, 0, 1., [0.] * 9, 640, 480)

    def test_approach(self):
        self.assertAlmostEqual(approach((0.6, 0., 0.4), 0.04)[2], 0.44)

    def test_reject_non_pad(self):
        for point in ((0., 0., 0.), (0.6, 0., 0.2), (0.9, 0., 0.4), (math.nan, 0., 0.4)):
            with self.assertRaises(ValueError):
                approach(point, 0.04)
        with self.assertRaises(ValueError):
            approach((0.6, 0., 0.4), -0.1)
