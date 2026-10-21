"""Offline checks for a single captured fixture route; never opens the SDK."""
import copy
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from massage_controller.commissioning_authorization import (
    authorization_path, binding, read_authorization, validate_authorization,
)
from massage_controller.noncontact_plan import eligibility_errors, reviewed_execution
from massage_controller.noncontact_control import NoncontactControl
from massage_controller.waypoint_plan import pose
import test_route_review as review_tests


class CommissioningAuthorizationTests(unittest.TestCase):
    def setUp(self):
        inputs = review_tests.inputs()
        inputs['profile'].update(execution_rxyz_deg='KEEP_CURRENT',
                                 virtual_model_check='DEFERRED')
        inputs['capture'].update(frame_received_monotonic_s=10.,
                                 calibration_sha256='a' * 64,
                                 motion_eligible=False)
        inputs['scene']['corridor_cells'] = 50
        helper = review_tests.ReviewTests()
        self.bundle = helper.reviewer().run(inputs, threading.Event())
        self.assertTrue(self.bundle['summary']['kinematics_checked'])
        self.assertIsNotNone(self.bundle['details'])
        self.calibration = dict(suitability=dict(
            absolute_base_accuracy_verified=False, robot_motion_approved=False,
            preview_approved=True))

    def approval(self, bundle=None):
        return dict(kind='SINGLE_CAPTURE_FIXTURE_AIR_TRIAL',
                    binding=binding(bundle or self.bundle),
                    operator_observed_no_obstacles=True,
                    reuse_existing_calibration=True,
                    operator_statement='假人工装，现场已确认没有障碍物，沿用现有标定。',
                    issued_monotonic_s=20., expires_monotonic_s=310.)

    def execute(self, bundle=None, approval=None, **overrides):
        bundle = self.bundle if bundle is None else bundle
        args = dict(profile=bundle['details']['profile'],
                    final_points=bundle['inputs']['confirmation']['points'],
                    revision=bundle['summary']['revision'],
                    actual=pose(bundle['details']['candidate']['start_pose'], 'start'),
                    authorization=approval, now=30.)
        args.update(overrides)
        return reviewed_execution(bundle, **args)

    def test_valid_authorization_only_converts_execution_plan(self):
        original = copy.deepcopy(self.bundle)
        calibration = copy.deepcopy(self.calibration)
        approval = self.approval()
        validate_authorization(approval, self.bundle, now=30.)
        self.assertEqual(eligibility_errors(self.bundle['details']['profile'],
                         self.calibration, self.bundle['inputs']['capture'],
                         limited_authorization=True), [])
        plan, targets = self.execute(approval=approval)
        candidate = original['details']['candidate']
        expected = copy.deepcopy(candidate)
        expected.update(mode='FREE_SPACE_WAYPOINTS', path_confirmed=True)
        expected.pop('execution_eligible')
        self.assertEqual(plan, expected)
        self.assertEqual(targets, original['summary']['targets'])
        self.assertEqual(self.bundle, original)
        self.assertEqual(self.calibration, calibration)
        self.assertFalse(self.bundle['summary']['execution_eligible'])
        self.assertFalse(self.bundle['summary']['full_collision_checked'])
        self.assertFalse(self.bundle['inputs']['capture']['motion_eligible'])

    def test_no_authorization_keeps_path_and_calibration_blocks(self):
        errors = eligibility_errors(self.bundle['details']['profile'],
                                    self.calibration, self.bundle['inputs']['capture'])
        self.assertTrue(any('现场确认' in error for error in errors))
        self.assertTrue(any('独立绝对坐标精度' in error for error in errors))
        self.assertTrue(any('运动使用验收' in error for error in errors))
        with self.assertRaises(ValueError):
            validate_authorization(None, self.bundle, now=30.)
        with self.assertRaises(ValueError):
            self.execute()

    def test_all_binding_fields_reject_reuse_after_changes(self):
        changes = [
            ('coordinate revision', lambda b: b['inputs']['confirmation'].update(revision='new')),
            ('photograph', lambda b: b['inputs']['confirmation'].update(frame_number=4)),
            ('calibration', lambda b: b['inputs']['capture'].update(calibration_sha256='b' * 64)),
            ('capture timing', lambda b: b['inputs']['capture'].update(frame_received_monotonic_s=11.)),
            ('final point', lambda b: b['inputs']['confirmation']['points'][0]['base_point_mm'].__setitem__(0, -551)),
            ('candidate geometry', lambda b: b['details']['candidate']['points'][0]['pose'].update(z_mm=400)),
            ('selected profile', lambda b: b['details']['profile'].update(speed_mm_s=4)),
            ('scene', lambda b: b['inputs']['scene'].update(surface_ceiling_base_mm=251)),
            ('source profile', lambda b: b['inputs']['profile'].update(dwell_per_point_s=3)),
        ]
        approval = self.approval()
        for name, change in changes:
            with self.subTest(name=name):
                changed = copy.deepcopy(self.bundle)
                change(changed)
                with self.assertRaisesRegex(ValueError, '不一致'):
                    validate_authorization(approval, changed, now=30.)

    def test_operator_evidence_cannot_be_omitted_or_coerced(self):
        for fields in [dict(kind='ANY_ROUTE'),
                       dict(operator_observed_no_obstacles=False),
                       dict(operator_observed_no_obstacles=1),
                       dict(reuse_existing_calibration=False),
                       dict(operator_statement='  '),
                       dict(operator_statement=None)]:
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                validate_authorization(dict(self.approval(), **fields), self.bundle, now=30.)

    def test_expiry_is_bounded_by_capture_and_monotonic_order(self):
        cases = [(dict(), 310.), (dict(), 311.),
                 (dict(issued_monotonic_s=9.), 30.),
                 (dict(issued_monotonic_s=31.), 30.),
                 (dict(expires_monotonic_s=311.), 30.),
                 (dict(expires_monotonic_s=20.), 30.),
                 (dict(issued_monotonic_s=True), 30.),
                 (dict(expires_monotonic_s=float('inf')), 30.),
                 (dict(issued_monotonic_s=float('nan')), 30.)]
        for fields, now in cases:
            with self.subTest(fields=fields, now=now), self.assertRaises(ValueError):
                validate_authorization(dict(self.approval(), **fields), self.bundle, now=now)

    def test_unsynchronized_capture_still_blocks_with_fresh_authorization(self):
        changed = copy.deepcopy(self.bundle)
        changed['inputs']['capture']['capture_time_valid'] = False
        with self.assertRaisesRegex(ValueError, '同步'):
            validate_authorization(self.approval(changed), changed, now=30.)
        errors = eligibility_errors(changed['details']['profile'], self.calibration,
                                    changed['inputs']['capture'], limited_authorization=True)
        self.assertTrue(any('同步' in error for error in errors))

    def test_sdk_failure_cannot_be_authorized(self):
        for fields in [dict(kinematics_checked=False), dict(revision='other revision')]:
            with self.subTest(fields=fields):
                changed = copy.deepcopy(self.bundle)
                changed['summary'].update(fields)
                with self.assertRaisesRegex(ValueError, 'SDK'):
                    validate_authorization(self.approval(changed), changed, now=30.)

    def test_limited_scope_speed_attitude_and_clearance_are_enforced(self):
        for fields in [dict(scope='human_contact'), dict(speed_mm_s=10.01),
                       dict(acceleration_mm_s2=20.01),
                       dict(execution_rxyz_deg=[180, 0, 0]),
                       dict(extra_clearance_mm=9.9),
                       dict(minimum_surface_clearance_mm=19.9),
                       dict(physical_tool_offset_flange_mm=[0, 0, 21]),
                       dict(geometry_source='MANUAL')]:
            with self.subTest(fields=fields):
                changed = copy.deepcopy(self.bundle)
                changed['details']['profile'].update(fields)
                with self.assertRaises(ValueError):
                    validate_authorization(self.approval(changed), changed, now=30.)

    def test_unknown_cell_crossing_centerline_is_rejected_even_if_summary_claims_clear(self):
        changed = copy.deepcopy(self.bundle)
        changed['inputs']['scene'].update(unknown_cells=2,
            unknown_cell_examples_base_xy_mm=[[-575., -175.], [-600., -250.]])
        # The pre-existing summary remains off-centerline; validation must
        # recompute from candidate geometry and the bound scene.
        self.assertEqual(changed['summary']['unknown_route_clearance']['assessment'],
                         'UNKNOWN_OFF_CENTERLINE')
        # The message counts possibly intersecting cells, not all unknown cells.
        with self.assertRaisesRegex(ValueError, '1 个深度未测清区域重叠.*补充观测后重新确认'):
            validate_authorization(self.approval(changed), changed, now=30.)

    def test_truncated_and_duplicate_unknown_cells_are_not_treated_as_clear(self):
        for examples in [[[-600., -250.]], [[-600., -250.], [-600., -250.]]]:
            with self.subTest(examples=examples):
                changed = copy.deepcopy(self.bundle)
                changed['inputs']['scene']['unknown_cell_examples_base_xy_mm'] = examples
                with self.assertRaisesRegex(ValueError, '未知区域记录不完整.*补充拍摄后重新确认'):
                    validate_authorization(self.approval(changed), changed, now=30.)

    def test_no_unknown_cells_can_be_authorized_without_claiming_full_collision_check(self):
        changed = copy.deepcopy(self.bundle)
        changed['inputs']['scene'].update(unknown_cells=0, unknown_cell_examples_base_xy_mm=[])
        validate_authorization(self.approval(changed), changed, now=30.)
        self.assertFalse(changed['summary']['full_collision_checked'])

    def test_authorization_does_not_replace_execution_version_or_start_checks(self):
        approval = self.approval()
        points = copy.deepcopy(self.bundle['inputs']['confirmation']['points'])
        points[0]['base_point_mm'][0] += 1
        for fields in [dict(revision='wrong'), dict(final_points=points),
                       dict(actual=[-590, -200, 500, 180, 0, 0]),
                       dict(profile=dict(self.bundle['details']['profile'], speed_mm_s=4))]:
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                self.execute(approval=approval, **fields)

    def test_rebound_candidate_tampering_still_fails_geometry_reconstruction(self):
        changed = copy.deepcopy(self.bundle)
        changed['details']['candidate']['points'][0]['pose']['z_mm'] += 5
        with self.assertRaisesRegex(ValueError, '几何'):
            self.execute(bundle=changed, approval=self.approval(changed))

    def test_sidecar_is_optional_and_readonly_but_invalid_file_does_not_authorize(self):
        with tempfile.TemporaryDirectory() as directory:
            profile_path = Path(directory) / 'noncontact.json'
            profile_path.write_text(json.dumps(self.bundle['inputs']['profile']))
            sidecar = authorization_path(profile_path)
            self.assertEqual(sidecar.name, 'noncontact.authorization.json')
            self.assertIsNone(read_authorization(profile_path, self.bundle, now=30.))
            self.assertFalse(sidecar.exists())
            text = json.dumps(self.approval(), ensure_ascii=False)
            sidecar.write_text(text)
            self.assertEqual(read_authorization(profile_path, self.bundle, now=30.), self.approval())
            self.assertEqual(sidecar.read_text(), text)
            with self.assertRaises(ValueError):
                read_authorization(profile_path, self.bundle, now=310.)
            sidecar.write_text('{}')
            with self.assertRaises(ValueError):
                read_authorization(profile_path, self.bundle, now=30.)

    def test_previous_capture_sidecar_is_ignored_without_mutation_or_reuse(self):
        with tempfile.TemporaryDirectory() as directory:
            profile_path = Path(directory) / 'noncontact.json'
            sidecar = authorization_path(profile_path)
            approval = self.approval()
            text = json.dumps(approval, ensure_ascii=False)
            sidecar.write_text(text)
            changed = copy.deepcopy(self.bundle)
            changed['inputs']['confirmation'].update(revision='new-photo', frame_number=99)
            self.assertIsNone(read_authorization(profile_path, changed, now=30.))
            self.assertEqual(sidecar.read_text(), text)
            with self.assertRaisesRegex(ValueError, '不一致'):
                validate_authorization(approval, changed, now=30.)
            with self.assertRaises(ValueError):
                self.execute(bundle=changed)

    def test_matching_expired_sidecar_remains_an_explicit_rescan_error(self):
        with tempfile.TemporaryDirectory() as directory:
            profile_path = Path(directory) / 'noncontact.json'
            sidecar = authorization_path(profile_path)
            text = json.dumps(self.approval(), ensure_ascii=False)
            sidecar.write_text(text)
            with self.assertRaisesRegex(ValueError, '过期.*重新拍照'):
                read_authorization(profile_path, self.bundle, now=310.)
            self.assertEqual(sidecar.read_text(), text)

    def control(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / 'noncontact.json'
        path.write_text(json.dumps(self.bundle['inputs']['profile']))
        points = self.bundle['inputs']['confirmation']['points']
        value = dict(self.bundle['inputs']['confirmation'], requested_points=copy.deepcopy(points))
        confirmation = SimpleNamespace(value=value, scene=self.bundle['inputs']['scene'],
            packet=self.bundle['inputs']['capture'],
            projector=SimpleNamespace(calibration=self.calibration),
            check_capture=Mock(return_value=self.bundle['inputs']['capture']),
            final_surface_points=Mock(return_value=copy.deepcopy(points)))
        self.now = 30.
        core = SimpleNamespace(state='IDLE', confirmation=confirmation,
            clock=lambda: self.now, clear_plan=Mock(), result='NONE',
            device=SimpleNamespace(sample=self.bundle['inputs']['sample']))
        review = SimpleNamespace(candidate=lambda *args: copy.deepcopy(self.bundle),
            sync=Mock(return_value=copy.deepcopy(self.bundle['summary'])))
        worker = Mock(spec=['submit', 'start_route', 'stop_route'])
        worker.start_route.return_value = True
        return NoncontactControl(core, path, worker, review=review), path, worker

    def test_control_passes_exact_approval_and_points_to_worker_only_after_authorization(self):
        control, path, worker = self.control()
        command = dict(revision=self.bundle['summary']['revision'],
                       points=self.bundle['inputs']['confirmation']['points'])
        self.assertTrue(control.blockers())
        with self.assertRaises(ValueError):
            control.prepare(command)
        worker.submit.assert_not_called()
        authorization_path(path).write_text(json.dumps(self.approval()))
        self.assertEqual(control.blockers(), [])
        control.prepare(command)
        worker.submit.assert_called_once_with(self.bundle['details']['profile'],
            self.bundle['inputs']['confirmation']['points'], self.bundle['summary']['revision'],
            reviewed=self.bundle, authorization=self.approval())
        worker.start_route.assert_not_called()
        self.assertTrue(control.active)
        self.assertFalse(self.calibration['suitability']['robot_motion_approved'])

    def test_old_capture_does_not_obstruct_fresh_explicit_authorization_or_allow_prepare(self):
        control, path, worker = self.control()
        previous = self.approval()
        previous['binding']['revision'] = 'old-photo'
        text = json.dumps(previous, ensure_ascii=False)
        authorization_path(path).write_text(text)
        status = control.status()
        self.assertTrue(status['authorization_available'])
        self.assertFalse(status['available'])
        self.assertFalse(any('不一致' in reason for reason in status['blockers']))
        with self.assertRaises(ValueError):
            control.prepare(dict(revision=self.bundle['summary']['revision'],
                                 points=self.bundle['inputs']['confirmation']['points']))
        worker.submit.assert_not_called()
        worker.start_route.assert_not_called()
        self.assertEqual(authorization_path(path).read_text(), text)

    def test_revoked_sidecar_cannot_be_reused_with_the_same_revision(self):
        control,path,worker=self.control()
        sidecar=authorization_path(path)
        saved=json.dumps(self.approval())
        sidecar.write_text(saved)
        self.assertEqual(control.authorization(),self.approval())
        control.revoke_authorization(self.bundle['summary']['revision'])
        self.assertFalse(sidecar.exists());self.assertTrue(control.authorization_revoked)
        # Even an unchanged candidate/UUID and externally restored sidecar must
        # not restore permission without a new explicit operator action.
        sidecar.write_text(saved)
        self.assertIsNone(control.authorization())
        command=dict(revision=self.bundle['summary']['revision'],
                     points=self.bundle['inputs']['confirmation']['points'])
        with self.assertRaises(ValueError):control.prepare(command)
        worker.submit.assert_not_called();worker.start_route.assert_not_called()
        worker.log=Mock()
        control.authorize(dict(command,operator_observed_no_obstacles=True,reuse_existing_calibration=True))
        self.assertFalse(control.authorization_revoked)
        self.assertEqual(control.authorization()['issued_monotonic_s'],self.now)
        control.prepare(command)
        worker.submit.assert_called_once();worker.start_route.assert_not_called()

    def test_failed_sidecar_removal_or_reauthorization_write_stays_revoked(self):
        control,path,worker=self.control()
        sidecar=authorization_path(path);sidecar.write_text(json.dumps(self.approval()))
        with patch.object(Path,'unlink',side_effect=PermissionError('read only')):
            control.revoke_authorization(self.bundle['summary']['revision'])
        self.assertTrue(sidecar.exists());self.assertIsNone(control.authorization())
        worker.log=Mock()
        command=dict(revision=self.bundle['summary']['revision'],
                     points=self.bundle['inputs']['confirmation']['points'],
                     operator_observed_no_obstacles=True,reuse_existing_calibration=True)
        with patch('massage_controller.noncontact_control.atomic_json',side_effect=PermissionError('read only')):
            with self.assertRaises(PermissionError):control.authorize(command)
        self.assertTrue(control.authorization_revoked);self.assertIsNone(control.authorization())
        worker.submit.assert_not_called();worker.start_route.assert_not_called()

    def test_revocation_does_not_delete_a_different_route_record(self):
        control,path,worker=self.control()
        sidecar=authorization_path(path);saved=json.dumps(self.approval())
        sidecar.write_text(saved)
        control.revoke_authorization('different-revision')
        self.assertEqual(sidecar.read_text(),saved)
        self.assertTrue(control.authorization_revoked);self.assertIsNone(control.authorization())
        with patch.object(Path,'read_text',side_effect=AssertionError('duplicate revocation read')):
            control.revoke_authorization('different-revision')

    def test_status_exposes_unknown_coverage_rejection_without_authorizing(self):
        cases = [
            ([[-600., -250.]], '未知区域记录不完整，无法核对工具尖端路线，请补充拍摄后重新确认'),
            ([[-575., -175.], [-600., -250.]], '路线平面投影与 1 个深度未测清区域重叠（不代表检测到障碍物），请补充观测后重新确认'),
        ]
        for cells, message in cases:
            with self.subTest(cells=cells):
                self.bundle['inputs']['scene']['unknown_cell_examples_base_xy_mm'] = cells
                control, path, worker = self.control()
                status = control.status()
                self.assertFalse(status['available'])
                self.assertFalse(status['authorization_available'])
                self.assertEqual(status['blockers'][0], message)
                self.assertFalse(authorization_path(path).exists())
                worker.submit.assert_not_called()
                worker.start_route.assert_not_called()

    def test_status_exposes_current_sdk_failure_or_checking_reason(self):
        for state, reason in [('BLOCKED', '所有候选高度均未通过路线预检：控制器逆解失败：40001'),
                              ('CHECKING', '正在进行控制器逆解和路线预检')]:
            with self.subTest(state=state):
                control, path, worker = self.control()
                control.review.sync.return_value.update(state=state, reason=reason, kinematics_checked=False)
                control.review.candidate = Mock(side_effect=ValueError('本次 SDK 路线预检尚未通过或已失效'))
                status = control.status()
                self.assertFalse(status['available'])
                self.assertFalse(status['authorization_available'])
                self.assertEqual(status['blockers'][0], reason)
                self.assertFalse(authorization_path(path).exists())
                worker.submit.assert_not_called()
                worker.start_route.assert_not_called()

    def test_expired_current_authorization_status_requires_rescan_and_cannot_prepare(self):
        control, path, worker = self.control()
        authorization_path(path).write_text(json.dumps(self.approval()))
        self.now = 310.
        status = control.status()
        self.assertFalse(status['available'])
        self.assertFalse(status['authorization_available'])
        self.assertRegex(status['blockers'][0], '过期.*重新拍照')
        with self.assertRaisesRegex(ValueError, '过期'):
            control.prepare(dict(revision=self.bundle['summary']['revision'],
                                 points=self.bundle['inputs']['confirmation']['points']))
        worker.submit.assert_not_called()
        worker.start_route.assert_not_called()

    def test_active_authorized_route_does_not_gain_new_authorization_blocker(self):
        control, path, worker = self.control()
        authorization_path(path).write_text(json.dumps(self.approval()))
        control.prepare(dict(revision=self.bundle['summary']['revision'],
                             points=self.bundle['inputs']['confirmation']['points']))
        status = control.status()
        self.assertTrue(status['available'])
        self.assertFalse(status['authorization_available'])
        self.assertEqual(status['blockers'], [])
        worker.start_route.assert_not_called()

    def test_control_rechecks_expiry_before_start_and_requests_stop(self):
        control, path, worker = self.control()
        authorization_path(path).write_text(json.dumps(self.approval()))
        control.prepare(dict(revision=self.bundle['summary']['revision'],
                             points=self.bundle['inputs']['confirmation']['points']))
        control.report = dict(state='READY')
        self.now = 310.
        with self.assertRaisesRegex(ValueError, '过期'):
            control.start(dict(plan_sha256='a' * 64))
        worker.start_route.assert_not_called()
        worker.stop_route.assert_called_once()
        self.assertEqual(control.core.state, 'STOPPING')

    def test_control_rechecks_removed_authorization_before_start(self):
        control, path, worker = self.control()
        authorization_path(path).write_text(json.dumps(self.approval()))
        control.prepare(dict(revision=self.bundle['summary']['revision'],
                             points=self.bundle['inputs']['confirmation']['points']))
        control.report = dict(state='READY')
        authorization_path(path).unlink()
        with self.assertRaises(ValueError):
            control.start(dict(plan_sha256='a' * 64))
        worker.start_route.assert_not_called()
        worker.stop_route.assert_called_once()


if __name__ == '__main__':
    unittest.main()
