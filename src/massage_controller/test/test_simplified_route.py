import copy
import math
import threading
import unittest

from massage_controller.noncontact_plan import (
    DIRECT_ROUTE, SEGMENTED_ROUTE, automatic_profile, compile_route,
    fingerprint, profile_errors, reviewed_execution,
)
from massage_controller.route_review import Reviewer, candidate_samples
from massage_controller.waypoint_plan import compile_groups, pose, validate
from test_route_review import inputs, ROT


def fixture():
    data = inputs()
    data['profile'].update(route_mode=DIRECT_ROUTE, virtual_model_check='DEFERRED')
    data['confirmation']['points'] = [
        dict(name='大椎', side='MIDLINE', base_point_mm=[-550., -150., 240.]),
        dict(name='肩井', side='LEFT', base_point_mm=[-560., -120., 245.]),
        dict(name='肩井', side='RIGHT', base_point_mm=[-570., -170., 248.]),
    ]
    return data


def query(request):
    if request['kind'] == 'rotation':
        return ROT
    target = request['pose']
    return dict(joints_deg=[v / 10 for v in target[:3]] + [0, 0, 0], pose=target)


def review(data, math_query=query):
    return Reviewer(math_query, model_factory=lambda: None).run(data, threading.Event())


class SimplifiedRouteTests(unittest.TestCase):
    def test_direct_route_has_only_final_ordered_above_point_targets(self):
        data = fixture()
        result = review(data)
        self.assertTrue(result['summary']['kinematics_checked'])
        self.assertEqual(len(result['summary']['attempts']), 1)
        plan = result['details']['candidate']
        self.assertEqual([p['name'] for p in plan['points']],
                         ['1_APPROACH', '2_APPROACH', '3_APPROACH'])
        self.assertEqual(plan['start_pose']['z_mm'], 500.)
        self.assertEqual(plan['points'][0]['pose']['x_mm'], -550.)
        self.assertEqual(plan['points'][0]['pose']['y_mm'], -150.)
        self.assertEqual(plan['points'][0]['pose']['z_mm'], 300.)
        for point, target in zip(plan['points'], result['summary']['targets']):
            self.assertEqual(pose(point['pose'], point['name'])[:3], target['flange_base_mm'])
            self.assertEqual(point['dwell_s'], data['profile']['dwell_per_point_s'])
        self.assertFalse(plan['execution_eligible'])
        with self.assertRaises(ValueError):
            validate(plan)

    def test_full_direct_segment_stays_above_clearance_plane_without_initial_drop(self):
        result = review(fixture())
        plan = result['details']['candidate']
        first = next(candidate_samples(plan))[1]
        self.assertNotEqual(first[0], plan['start_pose']['x_mm'])
        self.assertNotEqual(first[1], plan['start_pose']['y_mm'])
        for _, sample in candidate_samples(plan):
            self.assertGreaterEqual(sample[2] - 20., 280.)
            self.assertEqual(sample[3:], [180, 0, 0])

    def test_sdk_failure_tries_horizontal_entry_at_actual_tip_height(self):
        def reject_diagonal(request):
            if request['kind'] == 'ik':
                previous = [v * 10 for v in request['seed'][:3]]
                target = request['pose']
                if abs(previous[0] - target[0]) > .001 and abs(previous[2] - target[2]) > .001:
                    raise ValueError('fixture: diagonal branch unavailable')
            return query(request)
        result = review(fixture(), reject_diagonal)
        self.assertTrue(result['summary']['kinematics_checked'])
        self.assertEqual(len(result['summary']['attempts']), 2)
        self.assertEqual(result['details']['profile']['route_mode'], SEGMENTED_ROUTE)
        self.assertEqual(result['summary']['transfer_height_mm'], 480.)
        plan = result['details']['candidate']
        self.assertEqual([p['name'] for p in plan['points']],
                         ['1_TRANSFER', '1_APPROACH', '2_APPROACH', '3_APPROACH'])
        self.assertEqual(pose(plan['points'][0]['pose'], 'entry')[:3], [-550., -150., 500.])
        self.assertEqual(plan['points'][1]['pose']['z_mm'], 300.)

    def test_actual_high_start_is_retained_in_automatic_bounds(self):
        data = fixture()
        data['context']['start_flange_mm'][2] = 801.
        data['sample']['tcp_pose']['xyz_mm'][2] = 801.
        data['sample']['joint_positions_deg'][2] = 80.1
        data['scene']['context_sha256'] = fingerprint(data['context'])
        derived = automatic_profile(data['profile'], data['scene'], data['confirmation']['points'], data['context'])
        self.assertEqual(derived['safe_transfer_height_base_mm'], 280.)
        self.assertGreater(derived['workspace_base_mm']['max'][2], 801.)
        plan, _ = compile_route(dict(derived, path_confirmed=True), data['confirmation']['points'],
                                [-600, -200, 801, 180, 0, 0], ROT)
        self.assertEqual(plan['start_pose']['z_mm'], 801.)
        self.assertEqual(len(compile_groups(plan)[0]), 2)
        self.assertEqual(len(plan['points']), 3)

    def test_final_doctor_adjustment_changes_sdk_target_without_reordering(self):
        data = fixture()
        before = review(data)
        data['confirmation']['points'][1]['base_point_mm'][0] -= 7.
        after = review(data)
        self.assertEqual(after['summary']['targets'][1]['flange_base_mm'][0], -567.)
        self.assertEqual(after['summary']['targets'][0], before['summary']['targets'][0])
        self.assertEqual(after['summary']['targets'][2], before['summary']['targets'][2])

    def test_tool_offset_uses_sdk_rotation_not_plus_length_height(self):
        data = fixture()
        angle = .1
        rotation = [[math.cos(angle), 0, math.sin(angle), 0], [0, -1, 0, 0],
                    [math.sin(angle), 0, -math.cos(angle), 0], [0, 0, 0, 1]]
        derived = automatic_profile(data['profile'], data['scene'], data['confirmation']['points'], data['context'])
        plan, targets = compile_route(derived, data['confirmation']['points'],
                                     [-600, -200, 500, 180, 0, 0], rotation, review_only=True)
        tip = targets[0]['tool_tip_base_mm']
        actual_flange = pose(plan['points'][0]['pose'], 'target')[:3]
        for i in range(3):
            self.assertAlmostEqual(actual_flange[i] + 20 * rotation[i][2], tip[i])

    def test_unknown_start_cell_is_not_deleted_by_simplification(self):
        data = fixture()
        data['scene'].update(unknown_cells=1, unknown_cell_examples_base_xy_mm=[[-600., -200.]])
        result = review(data)
        self.assertTrue(result['summary']['kinematics_checked'])
        coverage = result['summary']['unknown_route_clearance']
        self.assertEqual(coverage['assessment'], 'CENTERLINE_NEAR_UNKNOWN')
        self.assertEqual(coverage['possibly_intersecting_cells'], 1)
        self.assertFalse(result['summary']['execution_eligible'])

    def test_start_below_clearance_and_invalid_mode_rejected(self):
        data = fixture()
        derived = automatic_profile(data['profile'], data['scene'], data['confirmation']['points'], data['context'])
        for mode in (DIRECT_ROUTE, SEGMENTED_ROUTE):
            with self.assertRaises(ValueError):
                compile_route(dict(derived, route_mode=mode), data['confirmation']['points'],
                              [-600, -200, 270, 180, 0, 0], ROT, review_only=True)
        self.assertIn('未知无接触路线生成方式', profile_errors(dict(derived, route_mode='typo')))
        data['profile']['route_mode'] = 'typo'
        result = review(data)
        self.assertIsNone(result['details'])
        self.assertIn('未知无接触路线生成方式', result['summary']['reason'])

    def test_execution_rebuild_matches_review_and_rejects_changed_start_or_points(self):
        data = fixture()
        data['profile']['path_confirmed'] = True  # Synthetic fixture only.
        bundle = review(data)
        profile = bundle['details']['profile']
        points = data['confirmation']['points']
        revision = data['confirmation']['revision']
        actual = [-600, -200, 500, 180, 0, 0]
        plan, _ = reviewed_execution(bundle, profile, points, revision, actual)
        self.assertEqual(len(plan['points']), 3)
        with self.assertRaises(ValueError):
            reviewed_execution(bundle, profile, points, revision, [-590, *actual[1:]])
        edited = copy.deepcopy(points)
        edited[0]['base_point_mm'][0] += 1.
        with self.assertRaises(ValueError):
            reviewed_execution(bundle, profile, edited, revision, actual)


if __name__ == '__main__':
    unittest.main()
