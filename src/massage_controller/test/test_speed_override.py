"""Temporary speed-ratio lifecycle, using only the existing fake SDK reader."""
import copy
from collections import deque
import unittest

from massage_controller.speed_override import TemporaryOverride
from massage_controller.waypoint_motion import MotionReader, WaypointSession
import test_waypoint as waypoint_tests


class OverrideReader(waypoint_tests.FakeReader):
    def __init__(self, clock, timeline):
        super().__init__(clock)
        self.timeline = timeline
        self.override = .01
        self.read_values = deque()
        self.fail_apply_ack = False
        self.fail_restore = False

    def read_override(self):
        value = self.read_values.popleft() if self.read_values else self.override
        self.timeline.append(('read_override', value))
        return value

    def set_override(self, value):
        self.timeline.append(('set_override', value))
        if value == .01 and self.fail_restore:
            raise RuntimeError('restore rejected')
        self.override = value
        if value == 1.0 and self.fail_apply_ack:
            # A missing ACK does not prove the setting was not applied.
            raise RuntimeError('SetOverride ACK uncertain')

    def stop(self):
        self.timeline.append(('stop', self.stops + 1))
        return super().stop()

    def sample(self):
        sample = super().sample()
        self.timeline.append(('sample', sample['robot_state']['moving'],
                              sample['host_monotonic_before_s']))
        return sample

    def move(self, target, joints, plan, command_id, radius=0):
        self.timeline.append(('move', copy.deepcopy(target)))
        return super().move(target, joints, plan, command_id, radius)


class SpeedOverrideTests(unittest.TestCase):
    def plan(self):
        return dict(waypoint_tests.plan(), target_reference='FLANGE', speed=10,
                    acceleration=20, tool_pose_mm_deg=[0, 0, 20, 0, 0, 0])

    def make(self, *, opt_in=True, start=True):
        clock = waypoint_tests.Clock()
        timeline, events = [], []
        reader = OverrideReader(clock, timeline)
        def record(event):
            events.append(event)
            if event['type'] == 'WAYPOINT_STATUS':
                timeline.append(('status', event['state']))
        session = WaypointSession(self.plan(), reader, record, clock, clock.sleep,
                                  temporary_speed_override=1.0 if opt_in else None)
        def sample_hook():
            if session.status_snapshot()['state'] == 'READY' and not session.start_event.is_set():
                if start:
                    timeline.append(('START',))
                    session.request_start()
                else:
                    session.request_stop()
        reader.on_sample = sample_hook
        return session, reader, timeline, events

    def assert_restored_after_confirmed_stop(self, reader, timeline):
        restore_index = timeline.index(('set_override', .01))
        stop_index = max(i for i, event in enumerate(timeline[:restore_index]) if event[0] == 'stop')
        confirmed = [event for event in timeline[stop_index + 1:restore_index] if event[0] == 'sample']
        self.assertGreaterEqual(len(confirmed), 3)
        self.assertTrue(all(event[1] == 0 for event in confirmed[-3:]))
        self.assertIn(('read_override', .01), timeline[restore_index + 1:])
        self.assertEqual(reader.override, .01)

    def test_opt_in_requires_flange_and_bounded_speed_acceleration(self):
        clock = waypoint_tests.Clock()
        for changes in [dict(target_reference='TCP'), dict(target_reference=None),
                        dict(speed=10.01), dict(acceleration=20.01)]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                WaypointSession(dict(self.plan(), **changes), OverrideReader(clock, []),
                                lambda event: None, clock, clock.sleep,
                                temporary_speed_override=1.0)
        for target in (True, .01, .5, 1.1, '1', float('nan')):
            with self.subTest(target=target), self.assertRaises(ValueError):
                WaypointSession(self.plan(), OverrideReader(clock, []), lambda event: None,
                                clock, clock.sleep, temporary_speed_override=target)

    def test_default_session_does_not_read_or_set_global_override(self):
        session, reader, timeline, _ = self.make(opt_in=False)
        session.run()
        self.assertEqual(session.status_snapshot()['state'], 'COMPLETED')
        self.assertFalse(any(event[0] in ('read_override', 'set_override') for event in timeline))
        self.assertEqual(reader.override, .01)

    def test_stop_in_ready_never_touches_override_or_moves(self):
        session, reader, timeline, _ = self.make(start=False)
        session.run()
        self.assertEqual(session.status_snapshot()['state'], 'STOPPED')
        self.assertFalse(reader.moves)
        self.assertFalse(any(event[0] in ('read_override', 'set_override') for event in timeline))

    def test_stop_start_read_set_readback_move_and_final_restore_order(self):
        session, reader, timeline, events = self.make()
        self.assertFalse(session.request_start())
        session.run()
        self.assertEqual(session.status_snapshot()['state'], 'COMPLETED')
        keys = [('stop', 1), ('status', 'READY'), ('START',),
                ('read_override', .01), ('set_override', 1.0), ('read_override', 1.0)]
        indices = [timeline.index(key) for key in keys]
        self.assertEqual(indices, sorted(indices))
        first_move = next(i for i, event in enumerate(timeline) if event[0] == 'move')
        self.assertGreater(first_move, indices[-1])
        preflight_samples = [event for event in timeline[indices[0]:indices[1]] if event[0] == 'sample']
        self.assertGreaterEqual(len(preflight_samples), 3)
        self.assertTrue(all(event[1] == 0 for event in preflight_samples))
        self.assert_restored_after_confirmed_stop(reader, timeline)
        self.assertTrue(session.status_snapshot()['stop_confirmed'])
        self.assertEqual([event['type'] for event in events if event['type'].startswith('SPEED_OVERRIDE_')],
                         ['SPEED_OVERRIDE_BEFORE', 'SPEED_OVERRIDE_APPLIED', 'SPEED_OVERRIDE_RESTORED'])

    def test_operator_stop_confirms_stop_before_restoring_original_ratio(self):
        session, reader, timeline, _ = self.make()
        reader.on_move = session.request_stop
        session.run()
        self.assertEqual(session.status_snapshot()['state'], 'STOPPED')
        self.assertEqual(len(reader.moves), 1)
        self.assert_restored_after_confirmed_stop(reader, timeline)

    def test_uncertain_motion_ack_is_not_retried_and_ratio_is_restored_after_stop(self):
        session, reader, timeline, _ = self.make()
        reader.reject_move = True
        session.run()
        self.assertEqual(session.status_snapshot()['state'], 'ERROR')
        self.assertEqual(len(reader.moves), 1)
        self.assert_restored_after_confirmed_stop(reader, timeline)

    def test_uncertain_set_ack_never_moves_and_attempts_original_ratio_restore(self):
        session, reader, timeline, _ = self.make()
        reader.fail_apply_ack = True
        session.run()
        self.assertEqual(session.status_snapshot()['state'], 'ERROR')
        self.assertFalse(reader.moves)
        self.assertEqual([event for event in timeline if event[0] == 'set_override'],
                         [('set_override', 1.0), ('set_override', .01)])
        self.assert_restored_after_confirmed_stop(reader, timeline)

    def test_apply_readback_mismatch_never_moves_and_restores(self):
        session, reader, timeline, _ = self.make()
        reader.read_values.extend([.01, .5, .01])
        session.run()
        self.assertEqual(session.status_snapshot()['state'], 'ERROR')
        self.assertIn('不匹配', session.status_snapshot()['error'])
        self.assertFalse(reader.moves)
        self.assert_restored_after_confirmed_stop(reader, timeline)

    def test_restore_failure_is_error_even_after_completed_waypoints(self):
        session, reader, timeline, events = self.make()
        reader.fail_restore = True
        session.run()
        status = session.status_snapshot()
        self.assertEqual(len(reader.moves), 2)
        self.assertEqual(status['state'], 'ERROR')
        self.assertTrue(status['stop_confirmed'])
        self.assertIn('速度倍率恢复失败', status['error'])
        self.assertEqual(reader.override, 1.0)
        self.assertFalse(any(event.get('state') == 'COMPLETED' for event in events))
        self.assertFalse(any(event['type'] == 'SPEED_OVERRIDE_RESTORED' for event in events))
        restore_index = timeline.index(('set_override', .01))
        self.assertGreaterEqual(len([event for event in timeline[timeline.index(('stop', 2)):restore_index]
                                     if event[0] == 'sample' and event[1] == 0]), 3)

    def test_unconfirmed_stop_does_not_change_override_while_motion_may_continue(self):
        session, reader, timeline, _ = self.make()
        reader.stop_failure = True
        reader.on_move = session.request_stop
        session.run()
        status = session.status_snapshot()
        self.assertEqual(status['state'], 'STOP_UNCONFIRMED')
        self.assertFalse(status['stop_confirmed'])
        self.assertIn('未改写运动中的倍率', status['error'])
        self.assertEqual([event for event in timeline if event[0] == 'set_override'], [('set_override', 1.0)])
        self.assertEqual(reader.override, 1.0)

    def test_existing_full_ratio_is_observed_but_never_rewritten(self):
        session, reader, timeline, _ = self.make()
        reader.override = 1.0
        session.run()
        self.assertEqual(session.status_snapshot()['state'], 'COMPLETED')
        self.assertFalse(any(event[0] == 'set_override' for event in timeline))
        self.assertEqual([event for event in timeline if event[0] == 'read_override'],
                         [('read_override', 1.0)] * 3)

    def test_invalid_initial_read_is_not_used_as_a_restore_target(self):
        for value in (True, None, '0.01', 0., 1.01, float('nan'), float('inf')):
            with self.subTest(value=value):
                session, reader, timeline, _ = self.make()
                reader.read_values.append(value)
                session.run()
                self.assertEqual(session.status_snapshot()['state'], 'ERROR')
                self.assertFalse(reader.moves)
                self.assertFalse(any(event[0] == 'set_override' for event in timeline))

    def test_restore_readback_mismatch_is_error(self):
        session, reader, _, _ = self.make()
        reader.read_values.extend([.01, 1., .5])
        session.run()
        self.assertEqual(session.status_snapshot()['state'], 'ERROR')
        self.assertIn('尚未恢复', session.status_snapshot()['error'])

    def test_sdk_adapter_parses_ratio_and_rejects_invalid_writes_without_transport(self):
        calls = []
        class SDK:
            def HRIF_ReadOverride(self, box, robot, result):
                calls.append(('read', box, robot))
                result.append('0.010')
                return 0
            def HRIF_SetOverride(self, box, robot, value):
                calls.append(('set', box, robot, value))
                return 0
        reader = object.__new__(MotionReader)
        reader.sdk = SDK()
        self.assertEqual(reader.read_override(), .01)
        reader.set_override(1.0)
        for value in (True, 0, 1.01, float('nan')):
            with self.subTest(value=value), self.assertRaises(ValueError):
                reader.set_override(value)
        self.assertEqual(calls, [('read', 0, 0), ('set', 0, 0, 1.0)])


if __name__ == '__main__':
    unittest.main()
