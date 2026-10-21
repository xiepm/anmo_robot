"""Synthetic frozen-depth visibility checks; never connects to camera or robot."""
import copy
import json
import unittest

import numpy as np

from massage_controller.noncontact_plan import fingerprint
from massage_controller.route_visibility import assess_visibility, validated_visibility


def fixture(size=16, vertices=None, cells=None):
    principal = (size-1)/2
    yy, xx = np.mgrid[:size, :size]
    rays = np.stack([(xx-principal)/100., (yy-principal)/100., np.ones_like(xx)], axis=-1)
    geometry = dict(geometry_frame='DEPTH_OPTICAL_NATIVE',
                    native_depth_m=np.ones((size, size), dtype=float), native_rays=rays,
                    T_color_depth=np.eye(4), native_depth_frame_number=41,
                    aligned_depth_frame_number=41, native_depth_timestamp_ms=1234.5,
                    aligned_depth_timestamp_ms=1234.5,
                    frame_number=7, received_monotonic_s=10.)
    vertices = [[-4., 0., 500.], [4., 0., 500.]] if vertices is None else vertices
    def pose(xyz):
        return dict(zip(('x_mm', 'y_mm', 'z_mm', 'rx_deg', 'ry_deg', 'rz_deg'), [*xyz, 0., 0., 0.]))
    plan = dict(version=1, mode='NONCONTACT_ROUTE_REVIEW', frame='Base',
                target_reference='FLANGE', tool_pose_mm_deg=[0.]*6,
                start_pose=pose(vertices[0]), path_confirmed=False, execution_eligible=False,
                points=[dict(name=f'{i}_APPROACH', pose=pose(xyz), dwell_s=0)
                        for i, xyz in enumerate(vertices[1:], 1)])
    context = dict(revision='00000000-0000-0000-0000-000000000007',
                   frame_number=7, received_monotonic_s=10., calibration_sha256='a'*64,
                   T_base_camera=np.eye(4).tolist(), start_flange_mm=vertices[0],
                   corridor_radius_mm=50, cell_size_mm=10)
    cells = [[0., 0.]] if cells is None else cells
    scene = dict(version=1, source='FROZEN_ALIGNED_DEPTH', state='PARTIAL',
                 revision=context['revision'], context_sha256=fingerprint(context),
                 calibration_sha256=context['calibration_sha256'],
                 frame_number=7, received_monotonic_s=10.,
                 cell_size_mm=10, corridor_radius_mm=50,
                 unknown_cells=len(cells), unknown_cell_examples_base_xy_mm=cells,
                 collision_checked=False, execution_eligible=False)
    return dict(plan=plan, scene=scene, offset=[0., 0., 0.], context=context, geometry=geometry)


class VisibilityTests(unittest.TestCase):
    def assess(self, inputs, expected):
        result = assess_visibility(**inputs)
        self.assertEqual(result['assessment'], expected, result)
        self.assertIs(result['execution_eligible'], False)
        json.dumps(result, allow_nan=False)
        return result

    def assert_binding(self, result, inputs):
        expected = dict(candidate_sha256=fingerprint(inputs['plan']),
                        scene_sha256=fingerprint(inputs['scene']),
                        context_sha256=fingerprint(inputs['context']),
                        tool_offset_base_mm=inputs['offset'],
                        frame_number=inputs['context']['frame_number'],
                        received_monotonic_s=inputs['context']['received_monotonic_s'])
        for key, value in expected.items():
            self.assertEqual(result['binding'][key], value, key)

    def test_unknown_xy_cell_can_have_observed_clear_path_at_different_height(self):
        inputs = fixture()
        result = self.assess(inputs, 'OBSERVED_CLEAR')
        self.assertEqual(result['method'], 'FROZEN_NATIVE_DEPTH_PROJECTED_SEGMENTS_V1')
        self.assertEqual(result['cell_count'], 1)
        self.assertEqual(result['unresolved_cells'], 0)
        self.assertGreaterEqual(result['minimum_depth_margin_mm'], 10.)
        self.assertRegex(result['geometry_sha256'], r'^[0-9a-f]{64}$')
        self.assert_binding(result, inputs)

    def test_a_real_hole_in_the_required_neighborhood_stays_unresolved(self):
        for missing in (0., float('nan'), float('inf'), -.1):
            inputs = fixture()
            inputs['geometry']['native_depth_m'][8, 8] = missing
            with self.subTest(missing=missing):
                result = self.assess(inputs, 'UNRESOLVED')
                self.assertGreater(result['unresolved_cells'], 0)

    def test_foreground_surface_in_front_of_tool_path_stays_unresolved(self):
        inputs = fixture()
        inputs['geometry']['native_depth_m'][8, 8] = .4
        result = self.assess(inputs, 'UNRESOLVED')
        self.assertGreater(result['unresolved_cells'], 0)

    def test_less_than_ten_mm_depth_margin_does_not_pass(self):
        self.assess(fixture(vertices=[[-1., 0., 995.], [1., 0., 995.]]), 'UNRESOLVED')
        self.assess(fixture(vertices=[[-1., 0., 985.], [1., 0., 985.]]), 'OBSERVED_CLEAR')

    def test_full_neighborhood_must_be_valid_not_only_center_pixel(self):
        inputs = fixture(vertices=[[0., 0., 500.], [0., 0., 500.]])
        inputs['geometry']['native_depth_m'][9, 9] = 0.
        self.assertEqual(inputs['geometry']['native_depth_m'][7, 7], 1.)
        self.assess(inputs, 'UNRESOLVED')

    def test_outside_camera_view_is_unresolved(self):
        inputs = fixture(vertices=[[98., 0., 500.], [102., 0., 500.]], cells=[[100., 0.]])
        self.assess(inputs, 'UNRESOLVED')

    def test_in_image_without_full_edge_neighborhood_is_unresolved(self):
        # Projects near column 0.5: a visible center pixel is insufficient.
        inputs = fixture(vertices=[[-35., 0., 500.], [-34.5, 0., 500.]], cells=[[-35., 0.]])
        self.assess(inputs, 'UNRESOLVED')

    def test_behind_lens_and_crossing_camera_plane_are_unresolved(self):
        for vertices in ([[0., 0., -500.], [1., 0., -500.]],
                         [[0., 0., -100.], [0., 0., 500.]]):
            with self.subTest(vertices=vertices):
                self.assess(fixture(vertices=vertices), 'UNRESOLVED')

    def test_clear_endpoints_cannot_hide_an_interior_hole(self):
        cells = [[float(x), 0.] for x in range(-45, 46, 10)]
        inputs = fixture(32, vertices=[[-50., 0., 500.], [50., 0., 500.]], cells=cells)
        inputs['geometry']['native_depth_m'][15, 15] = 0.
        for endpoints, cell in [([[-50., 0., 500.], [-45., 0., 500.]], [-45., 0.]),
                                ([[45., 0., 500.], [50., 0., 500.]], [45., 0.])]:
            endpoint = fixture(32, vertices=endpoints, cells=[cell])
            endpoint['geometry'] = inputs['geometry']
            self.assess(endpoint, 'OBSERVED_CLEAR')
        result = self.assess(inputs, 'UNRESOLVED')
        self.assertGreater(result['unresolved_cells'], 0)

    def test_center_hole_is_detected_with_reversed_travel_too(self):
        cells = [[float(x), 0.] for x in range(-45, 46, 10)]
        for vertices in ([[-50., 0., 500.], [50., 0., 500.]],
                         [[50., 0., 500.], [-50., 0., 500.]]):
            inputs = fixture(32, vertices=vertices, cells=cells)
            inputs['geometry']['native_depth_m'][15, 15] = 0.
            self.assess(inputs, 'UNRESOLVED')

    def test_sub_ten_mm_segment_cannot_skip_a_narrow_interior_pixel_hole(self):
        # Only 8 mm of physical travel, but 16 pixels at fx=1000. Endpoints and
        # their 4x4 neighborhoods are clear; fixed 10 mm sampling would miss it.
        inputs = fixture(64)
        inputs['geometry']['native_rays'][..., :2] /= 10.
        inputs['geometry']['native_depth_m'][31, 31] = 0.
        for vertices in ([[-4., 0., 500.], [-3.9, 0., 500.]],
                         [[3.9, 0., 500.], [4., 0., 500.]]):
            endpoint = fixture(64, vertices=vertices)
            endpoint['geometry'] = inputs['geometry']
            self.assess(endpoint, 'OBSERVED_CLEAR')
        self.assess(inputs, 'UNRESOLVED')

    def test_all_segments_of_a_bent_route_are_checked(self):
        # The hole lies on the second leg, away from all waypoints and from the
        # straight start-to-final shortcut; the actual plan contains both legs.
        inputs = fixture(64, vertices=[[-4., -4., 500.], [-4., 4., 500.], [4., 4., 500.]])
        inputs['geometry']['native_rays'][..., :2] /= 10.
        inputs['geometry']['native_depth_m'][39, 31] = 0.
        self.assess(inputs, 'UNRESOLVED')

    def test_single_point_tangent_to_unknown_cell_is_checked(self):
        inputs = fixture(vertices=[[-10., 0., 500.], [0., 10., 500.]])
        inputs['geometry']['native_depth_m'][8, 6] = 0.
        result = self.assess(inputs, 'UNRESOLVED')
        self.assertEqual(result['cell_count'], 1)

    def test_closed_pixel_boundary_endpoint_patch_is_checked_in_both_directions(self):
        # At b, u=7 exactly and its 4x4 patch includes column 9. Interior
        # samples have floor(u)=6, whose patch ends at column 8 instead.
        a, b = [-5., 0., 500.], [-2.5, 0., 500.]
        for vertices in ([a, b], [b, a], [b, b]):
            inputs = fixture(vertices=vertices)
            inputs['geometry']['native_depth_m'][7, 9] = 0.
            with self.subTest(vertices=vertices):
                result = self.assess(inputs, 'UNRESOLVED')
                self.assertEqual(result['unresolved_cells'], 1)

    def test_extremely_short_segment_crossing_unknown_boundary_is_not_parallel(self):
        a, b = [-5.-2e-13, 0., 500.], [-5.+2e-13, 0., 500.]
        for vertices in ([a, b], [b, a]):
            inputs = fixture(vertices=vertices)
            inputs['geometry']['native_depth_m'][7, 7] = 0.
            with self.subTest(vertices=vertices):
                result = self.assess(inputs, 'UNRESOLVED')
                self.assertEqual(result['cell_count'], 1)
                self.assertGreaterEqual(result['checked_intervals'], 1)

    def test_depth_margin_uses_the_clipped_unknown_interval(self):
        # This method evaluates only the recorded unknown-cell interval. Its
        # camera Z ranges 725..875 mm; it does not approve the rest of the route.
        inputs = fixture(vertices=[[-20., 0., 500.], [20., 0., 1100.]])
        result = self.assess(inputs, 'OBSERVED_CLEAR')
        self.assertAlmostEqual(result['minimum_depth_margin_mm'], 125.)

    def test_tool_offset_is_applied_before_unknown_and_pixel_checks(self):
        inputs = fixture(vertices=[[-14., 0., 500.], [-6., 0., 500.]])
        inputs['offset'] = [10., 0., 0.]
        result = self.assess(inputs, 'OBSERVED_CLEAR')
        self.assertEqual(result['cell_count'], 1)

    def test_rigid_base_camera_transform_is_applied(self):
        inputs = fixture(vertices=[[96., 0., 500.], [104., 0., 500.]], cells=[[100., 0.]])
        inputs['context']['T_base_camera'][0][3] = .1  # Transform translation is metres.
        inputs['scene']['context_sha256'] = fingerprint(inputs['context'])
        self.assess(inputs, 'OBSERVED_CLEAR')

    def test_native_depth_baseline_translation_is_applied_in_metres(self):
        inputs = fixture(vertices=[[146., 0., 700.], [154., 0., 700.]], cells=[[150., 0.]])
        inputs['geometry']['T_color_depth'][:3, 3] = [.15, 0., .2]
        result = self.assess(inputs, 'OBSERVED_CLEAR')
        self.assertAlmostEqual(result['minimum_depth_margin_mm'], 500.)

    def test_native_depth_rotation_is_composed_after_color_to_base(self):
        # Native camera looks along color +X. Color +X then maps to Base +Y.
        # Native points [-4,0,500] and [4,0,500] map to Base [100,750,104/96].
        inputs = fixture(vertices=[[100., 750., 104.], [100., 750., 96.]], cells=[[100., 750.]])
        inputs['context']['T_base_camera'] = [[0., -1., 0., .1], [1., 0., 0., .2],
                                             [0., 0., 1., 0.], [0., 0., 0., 1.]]
        inputs['geometry']['T_color_depth'] = np.array([[0., 0., 1., .05], [0., 1., 0., 0.],
                                                        [-1., 0., 0., .1], [0., 0., 0., 1.]])
        inputs['scene']['context_sha256'] = fingerprint(inputs['context'])
        result = self.assess(inputs, 'OBSERVED_CLEAR')
        self.assertAlmostEqual(result['minimum_depth_margin_mm'], 500.)

    def test_invalid_native_extrinsics_are_unavailable(self):
        for transform in (np.eye(3), np.diag([2., 1., 1., 1.]),
                          np.diag([-1., 1., 1., 1.]), np.full((4, 4), np.nan)):
            inputs = fixture()
            inputs['geometry']['T_color_depth'] = transform
            with self.subTest(transform=transform.tolist()):
                self.assess(inputs, 'UNAVAILABLE')

    def test_no_unknown_or_no_intersection_does_not_require_visibility_approval(self):
        for cells in ([], [[100., 100.]]):
            with self.subTest(cells=cells):
                result = self.assess(fixture(cells=cells), 'NOT_REQUIRED')
                self.assertEqual(result['cell_count'], 0)
                self.assertEqual(result['unresolved_cells'], 0)

    def test_truncated_and_duplicate_unknown_records_fail_closed(self):
        for coordinates, count in (([[0., 0.]], 2), ([[0., 0.], [0., 0.]], 2)):
            inputs = fixture(cells=coordinates)
            inputs['scene']['unknown_cells'] = count
            result = assess_visibility(**inputs)
            self.assertIn(result['assessment'], ('UNRESOLVED', 'UNAVAILABLE'), result)
            self.assertIs(result['execution_eligible'], False)

    def test_missing_and_invalid_geometry_is_unavailable(self):
        variants = [None, {}]
        wrong_shape = fixture()['geometry']
        wrong_shape['native_rays'] = wrong_shape['native_rays'][:-1]
        variants.append(wrong_shape)
        nonfinite = fixture()['geometry']
        nonfinite['native_rays'][0, 0, 0] = float('nan')
        variants.append(nonfinite)
        for geometry in variants:
            with self.subTest(geometry_type=type(geometry).__name__):
                inputs = fixture()
                inputs['geometry'] = geometry
                self.assess(inputs, 'UNAVAILABLE')

    def test_legacy_aligned_depth_cannot_be_used_as_native_evidence(self):
        original = fixture()['geometry']
        legacy = dict(depth_m=original['native_depth_m'], rays=original['native_rays'],
                      frame_number=7, received_monotonic_s=10.)
        variants = [legacy]
        for field in ('geometry_frame', 'T_color_depth', 'native_depth_m', 'native_rays',
                      'native_depth_frame_number', 'aligned_depth_frame_number',
                      'native_depth_timestamp_ms', 'aligned_depth_timestamp_ms'):
            missing = copy.deepcopy(original)
            del missing[field]
            variants.append(missing)
        mislabeled = copy.deepcopy(original)
        mislabeled['geometry_frame'] = 'COLOR_OPTICAL_ALIGNED'
        variants.append(mislabeled)
        for index, geometry in enumerate(variants):
            inputs = fixture()
            inputs['geometry'] = geometry
            with self.subTest(variant=index):
                self.assess(inputs, 'UNAVAILABLE')

    def test_aligned_valid_pixels_cannot_override_a_native_depth_hole(self):
        inputs = fixture()
        inputs['geometry']['depth_m'] = np.ones((16, 16))
        inputs['geometry']['rays'] = inputs['geometry']['native_rays'].copy()
        inputs['geometry']['native_depth_m'][8, 8] = 0.
        self.assess(inputs, 'UNRESOLVED')

    def test_nonpinhole_frozen_ray_grid_is_not_silently_approximated(self):
        inputs = fixture()
        inputs['geometry']['native_rays'][5, 5, 0] += .002  # 0.2 px distortion, beyond 0.01 px limit.
        self.assess(inputs, 'UNAVAILABLE')

    def test_frame_or_timestamp_mismatch_is_unavailable(self):
        for field, value in (('frame_number', 8), ('received_monotonic_s', 10.001)):
            inputs = fixture()
            inputs['geometry'][field] = value
            with self.subTest(field=field):
                self.assess(inputs, 'UNAVAILABLE')

    def test_native_and_aligned_depth_frame_ids_must_match_each_other(self):
        for field in ('native_depth_frame_number', 'aligned_depth_frame_number'):
            inputs = fixture()
            inputs['geometry'][field] = 42
            with self.subTest(field=field):
                self.assess(inputs, 'UNAVAILABLE')

    def test_fractional_frame_ids_are_not_truncated_into_a_matching_frame(self):
        inputs = fixture()
        inputs['geometry']['native_depth_frame_number'] = 41.1
        inputs['geometry']['aligned_depth_frame_number'] = 41.9
        self.assess(inputs, 'UNAVAILABLE')

    def test_native_and_aligned_depth_timestamps_must_match(self):
        for field in ('native_depth_timestamp_ms', 'aligned_depth_timestamp_ms'):
            for value in (1235.5, float('nan'), float('inf')):
                inputs = fixture()
                inputs['geometry'][field] = value
                with self.subTest(field=field, value=value):
                    self.assess(inputs, 'UNAVAILABLE')

    def test_context_and_scene_identity_mismatch_cannot_clear_route(self):
        inputs = fixture()
        inputs['scene']['context_sha256'] = 'b'*64
        result = assess_visibility(**inputs)
        self.assertIn(result['assessment'], ('UNRESOLVED', 'UNAVAILABLE'), result)
        self.assertIs(result['execution_eligible'], False)

    def test_binding_changes_when_confirmed_point_or_scene_changes(self):
        inputs = fixture()
        initial = self.assess(inputs, 'OBSERVED_CLEAR')
        inputs['plan']['points'][0]['pose']['x_mm'] += .1
        changed_point = self.assess(inputs, 'OBSERVED_CLEAR')
        self.assertNotEqual(initial['binding']['candidate_sha256'], changed_point['binding']['candidate_sha256'])
        inputs['scene']['note'] = 'new scene metadata'
        changed_scene = self.assess(inputs, 'OBSERVED_CLEAR')
        self.assertNotEqual(changed_point['binding']['scene_sha256'], changed_scene['binding']['scene_sha256'])
        self.assert_binding(changed_scene, inputs)

    def test_geometry_digest_covers_even_pixels_outside_inspected_route(self):
        inputs = fixture()
        initial = self.assess(inputs, 'OBSERVED_CLEAR')
        inputs['geometry']['native_depth_m'][0, 0] = .9
        changed = self.assess(inputs, 'OBSERVED_CLEAR')
        self.assertNotEqual(initial['geometry_sha256'], changed['geometry_sha256'])

    def test_geometry_digest_binds_native_color_extrinsics(self):
        inputs = fixture()
        initial = self.assess(inputs, 'OBSERVED_CLEAR')
        inputs['geometry']['T_color_depth'][0, 3] = .001
        changed = self.assess(inputs, 'OBSERVED_CLEAR')
        self.assertNotEqual(initial['geometry_sha256'], changed['geometry_sha256'])

    def test_actual_tool_offset_changes_evidence_binding(self):
        inputs = fixture()
        initial = self.assess(inputs, 'OBSERVED_CLEAR')
        inputs['offset'] = [0., 0., 20.]
        changed = self.assess(inputs, 'OBSERVED_CLEAR')
        self.assertNotEqual(initial['binding'], changed['binding'])

    def test_validation_rederives_actual_offset_from_profile_and_rotation(self):
        inputs = fixture()
        inputs['offset'] = [0., 0., 20.]
        evidence = self.assess(inputs, 'OBSERVED_CLEAR')
        bundle = dict(inputs=dict(scene=inputs['scene'], context=inputs['context']),
                      details=dict(candidate=inputs['plan'], depth_visibility=evidence,
                                   profile=dict(physical_tool_offset_flange_mm=[0., 0., 20.]),
                                   tool_rotation=np.eye(3).tolist()))
        self.assertTrue(validated_visibility(bundle))
        changed_profile = copy.deepcopy(bundle)
        changed_profile['details']['profile']['physical_tool_offset_flange_mm'][2] = 21.
        self.assertFalse(validated_visibility(changed_profile))
        changed_rotation = copy.deepcopy(bundle)
        changed_rotation['details']['tool_rotation'] = [[1., 0., 0.], [0., -1., 0.], [0., 0., -1.]]
        self.assertFalse(validated_visibility(changed_rotation))
        # A caller-supplied zero offset is not equivalent to the configured tool.
        inputs['offset'] = [0., 0., 0.]
        bundle['details']['depth_visibility'] = self.assess(inputs, 'OBSERVED_CLEAR')
        self.assertFalse(validated_visibility(bundle))

    def test_validation_rejects_tampered_native_evidence_metadata(self):
        inputs = fixture()
        evidence = self.assess(inputs, 'OBSERVED_CLEAR')
        bundle = dict(inputs=dict(scene=inputs['scene'], context=inputs['context']),
                      details=dict(candidate=inputs['plan'], depth_visibility=evidence,
                                   profile=dict(physical_tool_offset_flange_mm=[0., 0., 0.]),
                                   tool_rotation=np.eye(3).tolist()))
        self.assertTrue(validated_visibility(bundle))
        for key, value in (('method', 'FROZEN_DEPTH_PROJECTED_SEGMENTS_V1'),
                           ('geometry_frame', 'COLOR_OPTICAL_ALIGNED'),
                           ('native_depth_frame_number', -1),
                           ('native_depth_timestamp_ms', float('nan')),
                           ('cell_count', 2), ('checked_intervals', 0),
                           ('geometry_sha256', 'z'*64),
                           ('minimum_depth_margin_mm', 9.999)):
            changed = copy.deepcopy(bundle)
            changed['details']['depth_visibility'][key] = value
            with self.subTest(key=key, value=value):
                self.assertFalse(validated_visibility(changed))

    def test_new_photo_and_context_change_binding(self):
        inputs = fixture()
        initial = self.assess(inputs, 'OBSERVED_CLEAR')
        inputs['context']['frame_number'] = inputs['scene']['frame_number'] = inputs['geometry']['frame_number'] = 8
        inputs['context']['received_monotonic_s'] = inputs['scene']['received_monotonic_s'] = inputs['geometry']['received_monotonic_s'] = 11.
        inputs['scene']['context_sha256'] = fingerprint(inputs['context'])
        changed = self.assess(inputs, 'OBSERVED_CLEAR')
        self.assertNotEqual(initial['binding'], changed['binding'])
        self.assert_binding(changed, inputs)

    def test_assessment_does_not_modify_or_fill_frozen_data(self):
        inputs = fixture()
        inputs['geometry']['native_depth_m'][8, 8] = 0.
        before = copy.deepcopy(inputs)
        inputs['geometry']['native_depth_m'].setflags(write=False)
        inputs['geometry']['native_rays'].setflags(write=False)
        self.assess(inputs, 'UNRESOLVED')
        np.testing.assert_array_equal(inputs['geometry']['native_depth_m'], before['geometry']['native_depth_m'])
        np.testing.assert_array_equal(inputs['geometry']['native_rays'], before['geometry']['native_rays'])
        for key in ('plan', 'scene', 'context', 'offset'):
            self.assertEqual(inputs[key], before[key])


if __name__ == '__main__':
    unittest.main()
