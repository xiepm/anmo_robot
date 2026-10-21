"""Native frozen-depth review/authorization integration, using only fake SDK math."""
import copy
import json
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock

from jsonschema import validate as validate_schema

from massage_controller.commissioning_authorization import (
    binding, make_authorization, validate_authorization,
)
from massage_controller.noncontact_plan import fingerprint, reviewed_execution
from massage_controller.route_review import Reviewer
from massage_controller.route_visibility import validated_visibility
from massage_controller.waypoint_plan import pose
import test_route_review as review_tests
from test_route_visibility import fixture as visibility_fixture


def fixture():
    inputs = review_tests.inputs()
    inputs['profile'].update(execution_rxyz_deg='KEEP_CURRENT', virtual_model_check='DEFERRED',
                             route_mode='DIRECT_ABOVE_POINTS', return_to_start=True,
                             unknown_check_mode='FROZEN_3D_VISIBILITY')
    # Camera looks down at a plane at Base Z=250 mm. Its native depth is 750 mm.
    inputs['context']['T_base_camera'] = [[1., 0., 0., -.575], [0., -1., 0., -.175],
                                         [0., 0., -1., 1.], [0., 0., 0., 1.]]
    context = inputs['context']
    inputs['scene'].update(context_sha256=fingerprint(context),
        calibration_sha256=context['calibration_sha256'], frame_number=context['frame_number'],
        received_monotonic_s=context['received_monotonic_s'], corridor_cells=50,
        unknown_cells=1, unknown_cell_examples_base_xy_mm=[[-575., -175.]])
    inputs['capture'].update(frame_number=3, frame_received_monotonic_s=10., calibration_sha256='a'*64,
        motion_eligible=False, exposure_clock=dict(color=dict(frame_number=3, global_timestamp_ms=1234.5),
            depth=dict(frame_number=41, global_timestamp_ms=1234.5)))
    geometry = visibility_fixture(32)['geometry']
    geometry['native_depth_m'][:] = .75
    geometry['frame_number'] = context['frame_number']
    return inputs, geometry


class NativeVisibilityAuthorizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        schema = json.loads((Path(__file__).resolve().parents[3] /
                             'config/rehab_protocol/ui_response.schema.json').read_text())
        cls.summary_schema = schema['oneOf'][0]['properties']['data']['properties']['noncontact']['properties']['route_review']

    def review(self, inputs, geometry, *, loader_error=None):
        loader = Mock(return_value=geometry, side_effect=loader_error)
        helper = review_tests.ReviewTests()
        reviewer = Reviewer(helper.query,
            model_factory=lambda: self.fail('deferred virtual model must not be opened'),
            cloud_loader=lambda _: self.fail('collision service must not be opened'),
            geometry_loader=loader)
        bundle = reviewer.run(inputs, threading.Event())
        self.assertTrue(bundle['summary']['kinematics_checked'], bundle['summary'])
        self.assertIsNotNone(bundle['details'])
        self.assertIs(bundle['summary']['execution_eligible'], False)
        self.assertIs(bundle['summary']['full_collision_checked'], False)
        self.assertIs(bundle['summary']['self_collision_checked'], False)
        self.assertIs(bundle['summary']['observed_collision_checked'], False)
        validate_schema(bundle['summary'], self.summary_schema)
        json.dumps(bundle, allow_nan=False)
        return bundle, loader

    def approval(self, bundle):
        return make_authorization(bundle, '假人工装，本次路线现场确认无障碍。', now=20.)

    def execute(self, bundle, approval):
        return reviewed_execution(bundle, profile=bundle['details']['profile'],
            final_points=bundle['inputs']['confirmation']['points'],
            revision=bundle['summary']['revision'],
            actual=pose(bundle['details']['candidate']['start_pose'], 'start'),
            authorization=approval, now=30.)

    def test_native_geometry_loader_populates_bound_details_and_public_summary(self):
        inputs, geometry = fixture()
        bundle, loader = self.review(inputs, geometry)
        loader.assert_called_once_with(inputs['context'])
        summary, details = bundle['summary'], bundle['details']
        evidence = details['depth_visibility']
        self.assertEqual(summary['unknown_route_clearance']['assessment'], 'CENTERLINE_NEAR_UNKNOWN')
        self.assertEqual(evidence['assessment'], 'OBSERVED_CLEAR')
        self.assertEqual(evidence['method'], 'FROZEN_NATIVE_DEPTH_PROJECTED_SEGMENTS_V1')
        self.assertEqual(evidence['geometry_frame'], 'DEPTH_OPTICAL_NATIVE')
        self.assertEqual(evidence['binding']['tool_offset_base_mm'], [0., 0., -20.])
        self.assertEqual(evidence['binding']['candidate_sha256'], fingerprint(details['candidate']))
        self.assertEqual(evidence['capture_sha256'], fingerprint(inputs['capture']))
        self.assertIs(evidence['execution_eligible'], False)
        for key, value in summary['depth_visibility'].items():
            self.assertEqual(value, evidence[key], key)
        self.assertTrue(validated_visibility(bundle))
        # Returning along the reviewed path is part of this same evidence.
        self.assertGreaterEqual(evidence['checked_intervals'], 2)
        self.assertFalse(any('深度栅格未知' in item for item in summary['blockers']))

    def test_valid_native_evidence_allows_only_the_exact_fixture_execution_plan(self):
        bundle, _ = self.review(*fixture())
        before = copy.deepcopy(bundle)
        approval = self.approval(bundle)
        validate_authorization(approval, bundle, now=30.)
        self.assertEqual(approval['binding']['depth_visibility_sha256'],
                         fingerprint(bundle['details']['depth_visibility']))
        plan, targets = self.execute(bundle, approval)
        self.assertEqual(plan['mode'], 'FREE_SPACE_WAYPOINTS')
        self.assertIs(plan['path_confirmed'], True)
        self.assertIs(plan['return_to_start'], True)
        self.assertEqual(plan['points'][-1]['pose'], plan['start_pose'])
        self.assertEqual(targets, bundle['summary']['targets'])
        self.assertEqual(bundle, before)
        self.assertIs(bundle['inputs']['capture']['motion_eligible'], False)

    def test_a_real_native_hole_blocks_even_after_sdk_math_passes(self):
        inputs, geometry = fixture()
        geometry['native_depth_m'][15, 15] = 0.
        bundle, _ = self.review(inputs, geometry)
        self.assertEqual(bundle['details']['depth_visibility']['assessment'], 'UNRESOLVED')
        self.assertFalse(validated_visibility(bundle))
        with self.assertRaisesRegex(ValueError, '三维深度核对尚未通过'):
            self.approval(bundle)

    def test_missing_geometry_or_loader_failure_cannot_authorize(self):
        for failure in (None, OSError('synthetic frozen snapshot unavailable')):
            with self.subTest(loader_error=failure):
                inputs, _ = fixture()
                bundle, _ = self.review(inputs, None, loader_error=failure)
                self.assertEqual(bundle['details']['depth_visibility']['assessment'], 'UNAVAILABLE')
                with self.assertRaisesRegex(ValueError, '三维深度核对尚未通过'):
                    self.approval(bundle)

    def test_legacy_aligned_geometry_and_stale_native_frame_are_rejected(self):
        for variant in ('aligned_only', 'old_capture', 'unaligned_native_frame'):
            inputs, geometry = fixture()
            if variant == 'aligned_only':
                geometry = dict(depth_m=geometry['native_depth_m'], rays=geometry['native_rays'],
                                frame_number=3, received_monotonic_s=10.)
            elif variant == 'old_capture':
                geometry['frame_number'] = 2
            else:
                geometry['native_depth_frame_number'] -= 1
            with self.subTest(variant=variant):
                bundle, _ = self.review(inputs, geometry)
                self.assertEqual(bundle['details']['depth_visibility']['assessment'], 'UNAVAILABLE')
                with self.assertRaisesRegex(ValueError, '三维深度核对尚未通过'):
                    self.approval(bundle)

    def test_verified_capture_exposure_clock_must_match_native_geometry(self):
        for variant in ('depth_frame', 'depth_time', 'color_frame', 'missing_clock'):
            inputs, geometry = fixture()
            clock = inputs['capture']['exposure_clock']
            if variant == 'depth_frame':
                clock['depth']['frame_number'] += 1
            elif variant == 'depth_time':
                clock['depth']['global_timestamp_ms'] += 1.
            elif variant == 'color_frame':
                clock['color']['frame_number'] += 1
            else:
                del inputs['capture']['exposure_clock']
            with self.subTest(variant=variant):
                bundle, _ = self.review(inputs, geometry)
                self.assertEqual(bundle['details']['depth_visibility']['assessment'], 'UNAVAILABLE')
                with self.assertRaisesRegex(ValueError, '三维深度核对尚未通过'):
                    self.approval(bundle)

    def test_self_consistent_native_aligned_pair_cannot_replace_verified_exposure(self):
        # Matching the two geometry counters is insufficient: both could belong
        # to a different exposure while the outer color frame label is retained.
        for variant in ('pair_frame', 'pair_time'):
            inputs, geometry = fixture()
            if variant == 'pair_frame':
                geometry.update(native_depth_frame_number=42, aligned_depth_frame_number=42)
            else:
                geometry.update(native_depth_timestamp_ms=1267.8, aligned_depth_timestamp_ms=1267.8)
            with self.subTest(variant=variant):
                bundle, _ = self.review(inputs, geometry)
                self.assertEqual(bundle['details']['depth_visibility']['assessment'], 'UNAVAILABLE')
                with self.assertRaisesRegex(ValueError, '三维深度核对尚未通过'):
                    self.approval(bundle)

    def test_old_xy_policy_still_rejects_even_with_valid_native_evidence_attached(self):
        clear_bundle, _ = self.review(*fixture())
        for mode in (None, 'XY_PROJECTION'):
            inputs, geometry = fixture()
            if mode is None:
                del inputs['profile']['unknown_check_mode']
            else:
                inputs['profile']['unknown_check_mode'] = mode
            with self.subTest(mode=mode):
                bundle, loader = self.review(inputs, geometry)
                loader.assert_not_called()
                self.assertNotIn('depth_visibility', bundle['details'])
                bundle['details']['depth_visibility'] = clear_bundle['details']['depth_visibility']
                with self.assertRaisesRegex(ValueError, '平面投影'):
                    self.approval(bundle)
                self.assertNotIn('depth_visibility_sha256', binding(bundle))

    def test_changed_final_points_reject_both_old_approval_and_old_native_evidence(self):
        original, _ = self.review(*fixture())
        approval = self.approval(original)
        inputs, geometry = fixture()
        inputs['confirmation']['points'][0]['base_point_mm'][0] += 1.
        changed, _ = self.review(inputs, geometry)
        with self.assertRaisesRegex(ValueError, '不一致'):
            validate_authorization(approval, changed, now=30.)
        changed['details']['depth_visibility'] = original['details']['depth_visibility']
        self.assertFalse(validated_visibility(changed))
        with self.assertRaisesRegex(ValueError, '三维深度核对尚未通过'):
            self.approval(changed)

    def test_new_photo_cannot_reuse_previous_native_visibility_or_authorization(self):
        original, _ = self.review(*fixture())
        approval = self.approval(original)
        inputs, geometry = fixture()
        inputs['confirmation']['frame_number'] = 4
        inputs['context'].update(frame_number=4, received_monotonic_s=11.)
        inputs['scene'].update(frame_number=4, received_monotonic_s=11.,
                               context_sha256=fingerprint(inputs['context']))
        inputs['capture'].update(frame_number=4, frame_received_monotonic_s=11.,
            exposure_clock=dict(color=dict(frame_number=4, global_timestamp_ms=1267.8),
                                depth=dict(frame_number=42, global_timestamp_ms=1267.8)))
        geometry.update(frame_number=4, received_monotonic_s=11., native_depth_frame_number=42,
                        aligned_depth_frame_number=42, native_depth_timestamp_ms=1267.8,
                        aligned_depth_timestamp_ms=1267.8)
        changed, _ = self.review(inputs, geometry)
        self.assertEqual(changed['details']['depth_visibility']['assessment'], 'OBSERVED_CLEAR')
        with self.assertRaisesRegex(ValueError, '不一致'):
            validate_authorization(approval, changed, now=30.)
        changed['details']['depth_visibility'] = original['details']['depth_visibility']
        self.assertFalse(validated_visibility(changed))
        with self.assertRaisesRegex(ValueError, '三维深度核对尚未通过'):
            self.approval(changed)

    def test_authorization_binds_entire_native_evidence_not_only_its_clear_flag(self):
        bundle, _ = self.review(*fixture())
        approval = self.approval(bundle)
        for key, value in (('geometry_sha256', 'b'*64), ('reason', 'different evidence metadata')):
            changed = copy.deepcopy(bundle)
            changed['details']['depth_visibility'][key] = value
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, '不一致'):
                validate_authorization(approval, changed, now=30.)

    def test_changed_capture_clock_invalidates_saved_evidence_and_approval(self):
        bundle, _ = self.review(*fixture())
        approval = self.approval(bundle)
        bundle['inputs']['capture']['exposure_clock']['depth']['global_timestamp_ms'] += 1.
        self.assertFalse(validated_visibility(bundle))
        with self.assertRaisesRegex(ValueError, '不一致'):
            validate_authorization(approval, bundle, now=30.)
        with self.assertRaisesRegex(ValueError, '三维深度核对尚未通过'):
            self.approval(bundle)

    def test_public_summary_alone_cannot_authorize_without_bound_details(self):
        bundle, _ = self.review(*fixture())
        self.assertEqual(bundle['summary']['depth_visibility']['assessment'], 'OBSERVED_CLEAR')
        del bundle['details']['depth_visibility']
        with self.assertRaisesRegex(ValueError, '三维深度核对尚未通过'):
            self.approval(bundle)

    def test_incomplete_unknown_records_are_not_overridden_by_clear_evidence(self):
        original, _ = self.review(*fixture())
        inputs, geometry = fixture()
        inputs['scene']['unknown_cells'] = 2
        bundle, loader = self.review(inputs, geometry)
        loader.assert_not_called()
        self.assertEqual(bundle['summary']['unknown_route_clearance']['assessment'], 'INCOMPLETE')
        bundle['details']['depth_visibility'] = original['details']['depth_visibility']
        with self.assertRaisesRegex(ValueError, '未知区域记录不完整'):
            self.approval(bundle)


if __name__ == '__main__':
    unittest.main()
