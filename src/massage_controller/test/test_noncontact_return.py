"""Return-path regression tests. All motion and feedback are local test doubles."""
import copy
import json
import threading
import unittest

from massage_controller.commissioning_authorization import make_authorization
from massage_controller.noncontact_plan import (
    DIRECT_ROUTE, LEGACY_ROUTE, SEGMENTED_ROUTE, compile_route,
    profile_errors, reviewed_execution,
)
from massage_controller.route_review import Reviewer
from massage_controller.waypoint_motion import WaypointSession
from massage_controller.waypoint_plan import near, pose, validate
from test_noncontact import PROFILE, ROT, START
import test_noncontact
from test_simplified_route import fixture, query
from test_waypoint import Clock, FakeReader


POINTS = [
    dict(name='大椎', side='MIDLINE', base_point_mm=[-610., -220., 230.]),
    dict(name='肩井', side='LEFT', base_point_mm=[-620., -230., 240.]),
    dict(name='肩井', side='RIGHT', base_point_mm=[-630., -210., 250.]),
]


def route(mode=DIRECT_ROUTE, **changes):
    profile = dict(PROFILE, route_mode=mode, return_to_start=True,
                   dwell_per_point_s=0, **changes)
    return compile_route(profile, POINTS, START, ROT)[0]


class ReturnGeometryTests(unittest.TestCase):
    def test_return_flag_is_strict_bool_and_defaults_to_original_route(self):
        for invalid in (None, 0, 1, 'true', [], {}):
            with self.subTest(invalid=invalid):
                self.assertTrue(profile_errors(dict(PROFILE, return_to_start=invalid)))
                with self.assertRaises(ValueError):
                    compile_route(dict(PROFILE, return_to_start=invalid), POINTS, START, ROT)
        default, _ = compile_route(PROFILE, POINTS, START, ROT)
        disabled, _ = compile_route(dict(PROFILE, return_to_start=False), POINTS, START, ROT)
        self.assertEqual(default['points'], disabled['points'])
        self.assertNotIn('return_to_start', default)  # Preserve saved legacy candidate fingerprints.
        self.assertFalse(default.get('return_to_start', False))
        self.assertFalse(any(point['name'].startswith('RETURN_') for point in default['points']))

    def test_each_route_mode_returns_over_exact_reversed_outbound_vertices(self):
        for mode in (DIRECT_ROUTE, SEGMENTED_ROUTE, LEGACY_ROUTE):
            with self.subTest(mode=mode):
                profile = dict(PROFILE, route_mode=mode, dwell_per_point_s=.5)
                outbound, targets = compile_route(profile, POINTS, START, ROT)
                complete, returned_targets = compile_route(
                    dict(profile, return_to_start=True), POINTS, START, ROT)
                count = len(outbound['points'])
                self.assertEqual(complete['points'][:count], outbound['points'])
                self.assertEqual(returned_targets, targets)
                returning = complete['points'][count:]
                expected = [p['pose'] for p in reversed(outbound['points'][:-1])]
                expected.append(outbound['start_pose'])
                self.assertEqual([p['pose'] for p in returning], expected)
                self.assertTrue(all(p['dwell_s'] == 0 for p in returning))
                self.assertEqual(returning[-1]['name'], 'RETURN_TO_START')
                self.assertTrue(all(p['name'].startswith('RETURN_VIA_') for p in returning[:-1]))
                self.assertIs(complete['return_to_start'], True)
                validate(complete)

    def test_final_return_is_actual_flange_pose_without_second_tool_compensation(self):
        rotation = [[.96, 0, .28, 0], [0, -1, 0, 0], [.28, 0, -.96, 0], [0, 0, 0, 1]]
        plan, _ = compile_route(dict(PROFILE, route_mode=DIRECT_ROUTE, return_to_start=True),
                                POINTS, START, rotation)
        self.assertEqual(pose(plan['points'][-1]['pose'], 'return'), START)
        self.assertNotEqual(plan['points'][0]['pose']['x_mm'], POINTS[0]['base_point_mm'][0])

    def test_execution_plan_requires_boolean_flag_and_exact_final_start_marker(self):
        plan = route()
        for invalid in (None, 0, 1, 'true', [], {}):
            with self.subTest(flag=invalid), self.assertRaises(ValueError):
                validate(dict(plan, return_to_start=invalid))
        variants = []
        wrong_pose = copy.deepcopy(plan)
        wrong_pose['points'][-1]['pose']['x_mm'] += .01
        variants.append(wrong_pose)
        wrong_name = copy.deepcopy(plan)
        wrong_name['points'][-1]['name'] = 'RETURN_VIA_UNFINISHED'
        variants.append(wrong_name)
        extra_dwell = copy.deepcopy(plan)
        extra_dwell['points'][-1]['dwell_s'] = .1
        variants.append(extra_dwell)
        for invalid in variants:
            with self.subTest(final=invalid['points'][-1]), self.assertRaises(ValueError):
                validate(invalid)

    def test_review_checks_return_legs_and_rebuild_binds_whole_round_trip(self):
        data = fixture()
        data['profile'].update(return_to_start=True, execution_rxyz_deg='KEEP_CURRENT')
        data['capture'].update(calibration_sha256='a' * 64, frame_received_monotonic_s=10.)
        bundle = Reviewer(query).run(data, threading.Event())
        self.assertTrue(bundle['summary']['kinematics_checked'])
        self.assertTrue(any(s['stage'].startswith('RETURN_') for s in bundle['details']['samples']))
        self.assertEqual(bundle['details']['samples'][-1]['stage'], 'RETURN_TO_START')
        approval = make_authorization(bundle, 'offline test fixture only', now=11.)
        actual = [-600, -200, 500, 180, 0, 0]
        plan, _ = reviewed_execution(bundle, bundle['details']['profile'],
                                     data['confirmation']['points'], data['confirmation']['revision'],
                                     actual, authorization=approval, now=11.)
        self.assertEqual(pose(plan['points'][-1]['pose'], 'return'), actual)
        changed = dict(bundle['details']['profile'], return_to_start=False)
        with self.assertRaises(ValueError):
            reviewed_execution(bundle, changed, data['confirmation']['points'],
                               data['confirmation']['revision'], actual, authorization=approval, now=11.)

    def test_return_ik_failure_cannot_be_reported_as_checked_outbound_only(self):
        data = fixture()
        data['profile']['return_to_start'] = True

        def reject_return(request):
            if request['kind'] == 'ik' and request['pose'][:3] == [-600, -200, 500]:
                raise ValueError('test fixture return endpoint unreachable')
            return query(request)

        bundle = Reviewer(reject_return).run(data, threading.Event())
        self.assertFalse(bundle['summary']['kinematics_checked'])
        self.assertIsNone(bundle['details'])
        self.assertTrue(all('return endpoint' in a['error'] for a in bundle['summary']['attempts']))


class ReturnReader(FakeReader):
    def __init__(self, clock):
        super().__init__(clock)
        self.target = START[:]
        self.last_target = self.target
        self.drift_after_final_stop = False
        self.unsettled_after_final_stop = False

    def stop(self):
        super().stop()
        if self.stops >= 2 and self.moves and self.drift_after_final_stop:
            self.target = self.target[:]
            self.target[0] += 3.

    def sample(self):
        sample = super().sample()
        if self.stops >= 2 and self.unsettled_after_final_stop:
            sample['robot_state']['in_position'] = 0
            sample['robot_state']['blending_done'] = 0
        return sample


class NoopIdReader(ReturnReader):
    """Real-controller behavior: zero-distance commands need not publish an ID."""
    def move(self, target, joints, plan, command_id, radius=0):
        zero_distance = near(self.target, target, 1e-6, 1e-6)
        super().move(target, joints, plan, command_id, radius)
        if zero_distance:
            self.pending = 0
            self.command_id = ''


class ReturnSessionTests(unittest.TestCase):
    def make(self, reader_type=ReturnReader, requested_plan=None):
        clock = Clock()
        reader = reader_type(clock)
        events = []
        requested_plan = route() if requested_plan is None else requested_plan
        reader.target = pose(requested_plan['start_pose'], 'test start')
        reader.last_target = reader.target
        session = WaypointSession(requested_plan, reader, events.append, clock=clock, sleep=clock.sleep)
        reader.on_sample = lambda: session.request_start() if session.status_snapshot()['state'] == 'READY' else None
        return session, reader, events

    def assert_not_returned(self, session):
        self.assertIsNot(session.status_snapshot().get('returned_to_start'), True)

    def test_success_requires_actual_start_and_confirmed_stop(self):
        session, reader, events = self.make()
        session.run()
        status = session.status_snapshot()
        self.assertEqual(status['state'], 'COMPLETED', status)
        self.assertIs(status['stop_confirmed'], True)
        self.assertIs(status.get('returned_to_start'), True)
        self.assertEqual(reader.moves[-1], START)
        self.assertEqual(reader.target, START)
        self.assertEqual(reader.stops, 2)
        self.assertEqual(len(reader.moves), 6)
        self.assertFalse(any(e.get('returned_to_start') for e in events[:-1]))

    def test_stop_in_outbound_does_not_start_return(self):
        session, reader, _ = self.make()
        reader.on_move = session.request_stop
        session.run()
        self.assertEqual(session.status_snapshot()['state'], 'STOPPED')
        self.assertEqual(len(reader.moves), 1)
        self.assert_not_returned(session)

    def test_uncertain_ack_in_outbound_does_not_retry_or_return(self):
        session, reader, _ = self.make()
        reader.reject_move = True
        session.run()
        self.assertEqual(session.status_snapshot()['state'], 'ERROR')
        self.assertEqual(len(reader.moves), 1)
        self.assertTrue(session.status_snapshot()['stop_confirmed'])
        self.assert_not_returned(session)

    def test_stop_after_last_outbound_point_prevents_first_return_command(self):
        session, reader, _ = self.make()

        def stop_between_outbound_and_return():
            status = session.status_snapshot()
            if status['state'] == 'READY':
                session.request_start()
            elif status.get('step') == 'DWELL' and status.get('point') == '3_APPROACH':
                session.request_stop()

        reader.on_sample = stop_between_outbound_and_return
        session.run()
        self.assertEqual(session.status_snapshot()['state'], 'STOPPED')
        self.assertEqual(len(reader.moves), 3)
        self.assert_not_returned(session)

    def test_stop_during_return_does_not_send_remaining_return_points(self):
        session, reader, _ = self.make()

        def stop_on_return():
            if session.status_snapshot().get('point', '').startswith('RETURN_'):
                session.request_stop()

        reader.on_move = stop_on_return
        session.run()
        self.assertEqual(session.status_snapshot()['state'], 'STOPPED')
        self.assertEqual(len(reader.moves), 4)
        self.assertNotEqual(reader.moves[-1], START)
        self.assert_not_returned(session)

    def test_uncertain_ack_during_return_does_not_retry_or_finish(self):
        session, reader, _ = self.make()

        def reject_return():
            if session.status_snapshot().get('point', '').startswith('RETURN_'):
                reader.reject_move = True

        reader.on_move = reject_return
        session.run()
        self.assertEqual(session.status_snapshot()['state'], 'ERROR')
        self.assertEqual(len(reader.moves), 4)
        self.assert_not_returned(session)

    def test_stop_failure_cannot_claim_completed_return(self):
        session, reader, _ = self.make()
        reader.stop_failure = True
        session.run()
        self.assertEqual(reader.moves[-1], START)
        self.assertEqual(session.status_snapshot()['state'], 'STOP_UNCONFIRMED')
        self.assertFalse(session.status_snapshot()['stop_confirmed'])
        self.assert_not_returned(session)

    def test_drift_after_final_stop_invalidates_return_completion(self):
        session, reader, _ = self.make()
        reader.drift_after_final_stop = True
        session.run()
        self.assertEqual(reader.moves[-1], START)
        self.assertEqual(session.status_snapshot()['state'], 'ERROR')
        self.assert_not_returned(session)

    def test_moving_false_without_settled_feedback_is_not_return_completion(self):
        session, reader, _ = self.make()
        reader.unsettled_after_final_stop = True
        session.run()
        self.assertEqual(reader.moves[-1], START)
        self.assertEqual(session.status_snapshot()['state'], 'ERROR')
        self.assert_not_returned(session)

    def test_stop_before_start_sends_no_return_commands(self):
        session, reader, _ = self.make()
        reader.on_sample = lambda: session.request_stop() if session.status_snapshot()['state'] == 'READY' else None
        session.run()
        self.assertEqual(reader.moves, [])
        self.assertEqual(session.status_snapshot()['state'], 'STOPPED')
        self.assert_not_returned(session)

    def test_already_at_only_target_and_start_completes_without_zero_distance_command(self):
        start = [-600., -200., 310., 180., 0., 0.]
        point = dict(name='大椎', side='MIDLINE', base_point_mm=[-600., -200., 240.])
        plan, _ = compile_route(dict(PROFILE, route_mode=DIRECT_ROUTE, return_to_start=True,
                                    dwell_per_point_s=0), [point], start, ROT)
        session, reader, _ = self.make(NoopIdReader, plan)
        session.run()
        self.assertEqual(session.status_snapshot()['state'], 'COMPLETED', session.status_snapshot())
        self.assertEqual(reader.moves, [])
        self.assertTrue(session.status_snapshot()['returned_to_start'])
        self.assertTrue(session.status_snapshot()['stop_confirmed'])

    def test_return_endpoint_equal_to_previous_start_dwell_never_sends_noop(self):
        start = [-600., -200., 310., 180., 0., 0.]
        points = [dict(name='大椎', side='MIDLINE', base_point_mm=[-600., -200., 240.]), *POINTS[1:]]
        plan, _ = compile_route(dict(PROFILE, route_mode=DIRECT_ROUTE, return_to_start=True,
                                    dwell_per_point_s=.3), points, start, ROT)
        session, reader, events = self.make(NoopIdReader, plan)
        session.run()
        self.assertEqual(session.status_snapshot()['state'], 'COMPLETED', session.status_snapshot())
        previous = start
        for moved in reader.moves:
            self.assertFalse(near(previous, moved, 1e-6, 1e-6))
            previous = moved
        self.assertEqual(len(reader.moves), 4)
        self.assertEqual(reader.moves[-1], start)
        self.assertTrue(session.status_snapshot()['returned_to_start'])
        first_dwell = next(e for e in events if e.get('step') == 'DWELL' and e.get('point') == '1_APPROACH')
        next_move = next(e for e in events if e.get('step') == 'MOVE' and e.get('point') == '2_APPROACH')
        self.assertGreaterEqual(next_move['monotonic_s'] - first_dwell['monotonic_s'], .3 - 1e-8)


class ReturnControlTests(unittest.TestCase):
    def ready(self, required=True):
        helper = test_noncontact.NoncontactControlTests('test_status_matches_shared_contract')
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        helper.path.write_text(json.dumps(dict(PROFILE, return_to_start=required)))
        helper.ready()
        return helper

    def test_required_return_without_positive_confirmation_is_not_completed(self):
        for returned in (None, False):
            with self.subTest(returned=returned):
                helper = self.ready()
                helper.worker.state = dict(state='COMPLETED', stop_confirmed=True)
                if returned is not None:
                    helper.worker.state['returned_to_start'] = returned
                helper.core.tick()
                self.assertEqual((helper.core.state, helper.core.result), ('ERROR', 'FAILED'))

    def test_verified_return_clears_capture_and_allows_a_fresh_round(self):
        helper = self.ready()
        helper.worker.state = dict(state='COMPLETED', stop_confirmed=True, returned_to_start=True)
        helper.core.tick()
        self.assertEqual((helper.core.state, helper.core.result), ('IDLE', 'COMPLETED'))
        self.assertFalse(helper.nc.active)
        self.assertIsNone(helper.core.confirmation.value)
        self.assertIsNone(helper.core.scan_snapshot)
        self.assertEqual(helper.core.points, [])
        self.assertFalse(any(call[0] == 'start' for call in helper.worker.calls))

    def test_stop_at_intermediate_position_remains_stopped_without_auto_return(self):
        helper = self.ready()
        helper.worker.state = dict(state='STOPPED', stop_confirmed=True, returned_to_start=False)
        helper.core.tick()
        self.assertEqual((helper.core.state, helper.core.result), ('IDLE', 'STOPPED'))
        self.assertFalse(helper.nc.active)
        self.assertEqual([call[0] for call in helper.worker.calls], ['prepare'])

    def test_legacy_route_completion_does_not_require_return_field(self):
        helper = self.ready(required=False)
        helper.worker.state = dict(state='COMPLETED', stop_confirmed=True)
        helper.core.tick()
        self.assertEqual((helper.core.state, helper.core.result), ('IDLE', 'COMPLETED'))

    def test_completion_uses_prepared_return_requirement_despite_profile_edit(self):
        helper = self.ready()
        helper.path.write_text(json.dumps(dict(PROFILE, return_to_start=False)))
        helper.worker.state = dict(state='COMPLETED', stop_confirmed=True, returned_to_start=False)
        helper.core.tick()
        self.assertEqual((helper.core.state, helper.core.result), ('ERROR', 'FAILED'))

    def test_return_claim_without_confirmed_stop_is_not_completed(self):
        helper = self.ready()
        helper.worker.state = dict(state='COMPLETED', stop_confirmed=False, returned_to_start=True)
        helper.core.tick()
        self.assertEqual((helper.core.state, helper.core.result), ('ERROR', 'FAILED'))

    def test_accepted_new_scan_clears_previous_terminal_report(self):
        helper = self.ready()
        helper.worker.state = dict(state='COMPLETED', stop_confirmed=True,
                                   returned_to_start=True, plan_sha256='a' * 64)
        helper.core.tick()
        self.assertTrue(helper.nc.status()['returned_to_start'])
        response = helper.request('SCAN', dict(region='BACK'))
        self.assertTrue(response['ok'], response)
        state = helper.nc.status()
        self.assertEqual(helper.core.state, 'SCANNING')
        self.assertEqual(state['phase'], 'NONE')
        self.assertFalse(state['returned_to_start'])
        self.assertFalse(state['returning_to_start'])
        self.assertIsNone(state['revision'])
        self.assertIsNone(state['plan_sha256'])
        self.assertFalse(helper.nc.return_required)

    def test_rejected_new_scan_keeps_previous_terminal_report(self):
        helper = self.ready()
        helper.worker.state = dict(state='COMPLETED', stop_confirmed=True,
                                   returned_to_start=True, plan_sha256='a' * 64)
        helper.core.tick()
        helper.core.health['worker_state'] = 'BUSY'
        response = helper.request('SCAN', dict(region='BACK'))
        self.assertFalse(response['ok'])
        state = helper.nc.status()
        self.assertEqual(state['phase'], 'COMPLETED')
        self.assertTrue(state['returned_to_start'])
        self.assertEqual(state['plan_sha256'], 'a' * 64)

    def test_scan_during_active_route_cannot_clear_execution_report(self):
        helper = self.ready()
        before = copy.deepcopy(helper.nc.report)
        response = helper.request('SCAN', dict(region='BACK'))
        self.assertFalse(response['ok'])
        self.assertTrue(helper.nc.active)
        self.assertEqual(helper.nc.report, before)
        self.assertTrue(helper.nc.return_required)


if __name__ == '__main__':
    unittest.main()
