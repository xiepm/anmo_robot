import math
import unittest

from massage_controller.route_coverage import unknown_clearance


def plan():
    return dict(start_pose=dict(x_mm=0,y_mm=0),points=[dict(pose=dict(x_mm=100,y_mm=0))])


def route(vertices):
    return dict(start_pose=dict(zip(('x_mm','y_mm'),vertices[0])),
                points=[dict(pose=dict(zip(('x_mm','y_mm'),p))) for p in vertices[1:]])


def scene(points, count=None):
    return dict(unknown_cells=len(points) if count is None else count,
                unknown_cell_examples_base_xy_mm=points,cell_size_mm=10)


class CoverageTests(unittest.TestCase):
    def test_checks_segment_interior_and_full_cell_extent(self):
        result=unknown_clearance(plan(),scene([[50,50],[50,5]]),[0,0,0])
        self.assertEqual(result['assessment'],'CENTERLINE_NEAR_UNKNOWN')
        self.assertEqual(result['minimum_xy_distance_lower_bound_mm'],0)
        self.assertEqual(result['possibly_intersecting_cells'],1)
        self.assertFalse(result['execution_eligible'])

    def test_offset_and_vertical_segments_are_handled(self):
        p=plan();p['points'].insert(0,dict(pose=dict(x_mm=0,y_mm=0)))
        result=unknown_clearance(p,scene([[50,50]]),[0,20,0])
        self.assertAlmostEqual(result['minimum_xy_distance_lower_bound_mm'],25)
        self.assertEqual(result['assessment'],'UNKNOWN_OFF_CENTERLINE')
        self.assertFalse(result['execution_eligible'])

    def test_near_square_but_inside_circumscribed_circle_is_not_intersection(self):
        result=unknown_clearance(plan(),scene([[50,6]]),[0,0,0])
        self.assertEqual(result['assessment'],'UNKNOWN_OFF_CENTERLINE')
        self.assertEqual(result['minimum_xy_distance_lower_bound_mm'],1)
        self.assertEqual(result['possibly_intersecting_cells'],0)

    def test_corner_contact_counts_but_diagonal_gap_is_exact(self):
        tangent=route([[-10,0],[10,20]])
        result=unknown_clearance(tangent,scene([[5,5]]),[0,0,0])
        self.assertEqual(result['possibly_intersecting_cells'],1)
        self.assertEqual(result['minimum_xy_distance_lower_bound_mm'],0)
        result=unknown_clearance(route([[0,0],[10,10]]),scene([[16,4]]),[0,0,0])
        self.assertAlmostEqual(result['minimum_xy_distance_lower_bound_mm'],math.sqrt(2))
        self.assertEqual(result['possibly_intersecting_cells'],0)

    def test_vertical_and_stationary_xy_segments_use_square_extent(self):
        vertical=route([[0,0],[0,100]])
        self.assertEqual(unknown_clearance(vertical,scene([[5,50]]),[0,0,0])['possibly_intersecting_cells'],1)
        self.assertEqual(unknown_clearance(vertical,scene([[6,50]]),[0,0,0])['minimum_xy_distance_lower_bound_mm'],1)
        stationary=route([[0,0],[0,0]])  # Also represents purely vertical Base Z travel.
        self.assertEqual(unknown_clearance(stationary,scene([[2,2]]),[0,0,0])['possibly_intersecting_cells'],1)
        result=unknown_clearance(stationary,scene([[10,10]]),[0,0,0])
        self.assertAlmostEqual(result['minimum_xy_distance_lower_bound_mm'],math.sqrt(50))

    def test_reversing_segments_does_not_change_coverage(self):
        vertices=[[-20,-10],[40,30],[70,-40]]
        cells=scene([[20,10],[50,-20],[10,40],[75,-45]])
        self.assertEqual(unknown_clearance(route(vertices),cells,[2,-3,0]),
                         unknown_clearance(route(list(reversed(vertices))),cells,[2,-3,0]))

    def test_real_frame_109955_eight_circle_candidates_contain_six_square_intersections(self):
        # Exact flange XY, SDK tool offset, and the eight circle candidates from
        # archived route fc52e9df-49c3-4b33-9d62-5dc016a9484b / frame 109955.
        vertices=[[-553.978,-307.828],[-607.828372508403,-447.58042265502627],
                  [-525.8526513413874,-535.4371854868857],[-685.1003896622453,-519.069508437729]]
        vertices+=list(reversed(vertices[:-1]))
        cells=[[-595,-535],[-585,-535],[-575,-535],[-565,-535],
               [-595,-525],[-585,-525],[-575,-495],[-575,-485]]
        offset=[.13259314828707194,-2.535713928482121,-19.838159539884973]
        result=unknown_clearance(route(vertices),scene(cells),offset)
        self.assertEqual(result['possibly_intersecting_cells'],6)
        self.assertEqual(result['assessment'],'CENTERLINE_NEAR_UNKNOWN')
        self.assertFalse(result['execution_eligible'])
        for cell,gap in [([-595,-525],.33654332290533967),([-585,-525],1.3589692811500094)]:
            with self.subTest(cell=cell):
                exact=unknown_clearance(route(vertices),scene([cell]),offset)
                self.assertEqual(exact['assessment'],'UNKNOWN_OFF_CENTERLINE')
                self.assertAlmostEqual(exact['minimum_xy_distance_lower_bound_mm'],gap,places=10)

    def test_truncated_and_duplicate_examples_never_claim_complete_coverage(self):
        for s in [scene([[50,50]],2),scene([[50,50],[50,50]])]:
            result=unknown_clearance(plan(),s,[0,0,0])
            self.assertEqual(result['assessment'],'INCOMPLETE')
            self.assertIsNone(result['minimum_xy_distance_lower_bound_mm'])

    def test_no_unknown_cells_does_not_approve_execution(self):
        result=unknown_clearance(plan(),scene([]),[0,0,0])
        self.assertEqual(result['assessment'],'NO_UNKNOWN_REPORTED')
        self.assertFalse(result['execution_eligible'])
        self.assertIsNone(result['minimum_xy_distance_lower_bound_mm'])


if __name__=='__main__': unittest.main()
