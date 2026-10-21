"""Pre-START UI-loss recovery with fake feedback only; never opens the SDK."""
import unittest

import test_noncontact
from test_waypoint import Clock, FakeReader, plan
from massage_controller.waypoint_motion import WaypointSession


class NoncontactUIRecoveryTests(unittest.TestCase):
    # Reuse setup/helpers without inheriting and rerunning the source test cases.
    setUp = test_noncontact.NoncontactControlTests.setUp
    request = test_noncontact.NoncontactControlTests.request
    result = test_noncontact.NoncontactControlTests.result
    send = test_noncontact.NoncontactControlTests.send
    authorized_fixture = test_noncontact.NoncontactControlTests.authorized_fixture
    ready = test_noncontact.NoncontactControlTests.ready

    def fresh(self, ticks=1, *, ui=True, vision=True, feedback=True, **robot_state):
        for _ in range(ticks):
            self.now += .2
            if feedback:
                self.cache.value.update(valid=True, host_monotonic_after_s=self.now)
            self.cache.value['robot_state'].update(robot_state)
            if vision:
                self.core.vision_at = self.now
            if ui:
                self.core.ui_at = self.now
            self.core.tick()

    def terminal_after_ui_loss(self, status=None, *, reconnect_before_tick=False):
        self.core.ui_lost()
        self.assertEqual(self.nc.fault_kinds, {'UI_CONNECTION'})
        self.assertEqual(self.core.state, 'STOPPING')
        self.assertEqual(self.worker.calls[-1], ('stop',))
        if reconnect_before_tick:
            self.request('GET_STATUS')
        self.worker.state = (dict(state='STOPPED', stop_confirmed=True, motion_attempted=False)
                             if status is None else status)
        self.fresh(ui=reconnect_before_tick)
        self.assertFalse(self.nc.active)
        self.assertEqual(self.core.state, 'ERROR')

    def assert_old_route_cleared(self):
        self.assertIsNone(self.core.confirmation.value)
        self.assertIsNone(self.core.scan_snapshot)
        self.assertIsNone(self.core.plan)
        self.assertFalse(self.core.points)

    def test_prepared_route_ui_loss_recovers_only_acquisition_and_accepts_new_scan(self):
        self.ready()
        self.terminal_after_ui_loss()
        self.assertTrue(self.core.feedback_recoverable)
        self.assertTrue(self.core.recovery_requires_ui)
        self.assert_old_route_cleared()
        self.fresh(5)
        self.assertEqual(self.core.state, 'ERROR')
        self.fresh(8)
        self.assertEqual((self.core.state, self.core.result, self.core.error), ('IDLE', 'NONE', ''))
        self.assertFalse(self.core.feedback_recoverable)
        self.assertFalse(self.core.recovery_requires_ui)
        self.assert_old_route_cleared()
        self.assertFalse(self.request('START_NONCONTACT', {'plan_sha256': 'a'*64})['ok'])
        self.assertFalse(any(call[0] == 'start' for call in self.worker.calls))
        self.assertTrue(self.request('SCAN', {'region': 'BACK'})['ok'])
        self.assertEqual(self.core.state, 'SCANNING')
        self.assertEqual(self.core.outbox[-1], {'cmd': 'LOCATE', 'data': {'region': 'BACK', 'view': 'back'}})
        self.assertEqual(self.nc.report, {'state': 'NONE'})
        self.assertIsNone(self.nc.revision)
        self.assertFalse(self.device.calls)

    def test_reconnect_before_next_tick_does_not_erase_typed_ui_fault(self):
        self.ready()
        self.terminal_after_ui_loss(reconnect_before_tick=True)
        self.assertEqual(self.nc.fault_kinds, {'UI_CONNECTION'})
        self.assertTrue(self.core.feedback_recoverable)
        self.fresh(13)
        self.assertEqual(self.core.state, 'IDLE')
        self.assert_old_route_cleared()
        self.assertFalse(any(call[0] == 'start' for call in self.worker.calls))

    def test_healthy_robot_and_camera_do_not_recover_without_new_ui_heartbeat(self):
        self.ready()
        self.terminal_after_ui_loss()
        self.fresh(16, ui=False)
        self.assertIsNone(self.core.ui_at)
        self.assertEqual(self.core.state, 'ERROR')
        self.assertIsNone(self.core.recovery_since)
        self.request('GET_STATUS')
        self.fresh(5)
        self.assertEqual(self.core.state, 'ERROR')
        self.fresh(8)
        self.assertEqual(self.core.state, 'IDLE')

    def test_heartbeat_timeout_without_socket_close_also_recovers(self):
        self.ready()
        self.core.ui_at = self.now-self.core.cfg['ui_timeout_s']-.1
        self.fresh(ui=False)
        self.assertEqual(self.nc.fault_kinds, {'UI_CONNECTION'})
        self.assertEqual(self.worker.calls[-1], ('stop',))
        self.worker.state = dict(state='STOPPED', stop_confirmed=True, motion_attempted=False)
        self.fresh(ui=False)
        self.assertTrue(self.core.feedback_recoverable)
        self.fresh(13)
        self.assertEqual(self.core.state, 'IDLE')
        self.assert_old_route_cleared()

    def test_second_fast_disconnect_restarts_entire_stability_window(self):
        self.ready()
        self.terminal_after_ui_loss()
        self.fresh(6)
        self.assertIsNotNone(self.core.recovery_since)
        self.core.ui_lost()
        self.request('GET_STATUS')  # Reconnect before the next controller tick.
        self.assertIsNone(self.core.recovery_since)
        self.fresh(6)
        self.assertEqual(self.core.state, 'ERROR')
        self.fresh(7)
        self.assertEqual(self.core.state, 'IDLE')

    def test_unsettled_feedback_blocks_and_restarts_two_second_stability_window(self):
        for field, value in (('moving', 1), ('paused', 1), ('in_position', 0), ('blending_done', 0)):
            with self.subTest(field=field):
                self.setUp()
                self.ready()
                self.terminal_after_ui_loss()
                self.fresh(16, **{field: value})
                self.assertEqual(self.core.state, 'ERROR')
                self.assertIsNone(self.core.recovery_since)
                self.fresh(5, moving=0, paused=0, in_position=1, blending_done=1)
                self.assertEqual(self.core.state, 'ERROR')
                self.fresh(8)
                self.assertEqual(self.core.state, 'IDLE')

    def test_vision_must_be_healthy_idle_and_fresh_during_recovery(self):
        for variant in ('busy', 'camera_disconnected', 'stale'):
            with self.subTest(variant=variant):
                self.setUp()
                self.ready()
                self.terminal_after_ui_loss()
                if variant == 'busy':
                    self.core.health['worker_state'] = 'LOCATING'
                elif variant == 'camera_disconnected':
                    self.core.health['camera_connected'] = False
                else:
                    self.core.vision_at = self.now-self.core.cfg['vision_timeout_s']-.1
                self.fresh(16, vision=variant != 'stale')
                self.assertEqual(self.core.state, 'ERROR')
                self.assertIsNone(self.core.recovery_since)

    def test_stale_feedback_restarts_recovery_window(self):
        self.ready()
        self.terminal_after_ui_loss()
        self.fresh(6)
        self.fresh(4, feedback=False)
        self.assertEqual(self.core.state, 'ERROR')
        self.assertIsNone(self.core.recovery_since)
        self.fresh(5)
        self.assertEqual(self.core.state, 'ERROR')
        self.fresh(8)
        self.assertEqual(self.core.state, 'IDLE')

    def test_attempted_or_missing_motion_proof_never_auto_recovers(self):
        for fields in ({'motion_attempted': True}, {}, {'motion_attempted': None}, {'motion_attempted': 0}):
            with self.subTest(fields=fields):
                self.setUp()
                self.ready()
                self.terminal_after_ui_loss(dict(state='STOPPED', stop_confirmed=True, **fields))
                self.assertFalse(self.core.feedback_recoverable)
                self.fresh(16)
                self.assertEqual(self.core.state, 'ERROR')
                self.assertFalse(self.request('SCAN', {'region': 'BACK'})['ok'])

    def test_unknown_stop_nonliteral_confirmation_and_other_terminal_states_latch(self):
        statuses = [dict(state='STOP_UNCONFIRMED', stop_confirmed=False, motion_attempted=False),
                    dict(state='STOPPED', motion_attempted=False),
                    dict(state='STOPPED', stop_confirmed=1, motion_attempted=False),
                    dict(state='CANCELLED_BEFORE_MOTION', stop_confirmed=True, motion_attempted=False),
                    dict(state='ERROR', stop_confirmed=True, motion_attempted=False)]
        for status in statuses:
            with self.subTest(status=status):
                self.setUp()
                self.ready()
                self.terminal_after_ui_loss(status)
                self.assertFalse(self.core.feedback_recoverable)
                self.fresh(16)
                self.assertEqual(self.core.state, 'ERROR')

    def test_other_fault_mixed_with_ui_fault_is_never_recoverable(self):
        for ui_first in (True, False):
            with self.subTest(ui_first=ui_first):
                self.setUp()
                self.ready()
                if ui_first:
                    self.core.ui_lost()
                self.nc.stop('synthetic hardware/SDK fault', fault=True, fault_kind='OTHER')
                if not ui_first:
                    self.core.ui_lost()
                self.worker.state = dict(state='STOPPED', stop_confirmed=True, motion_attempted=False)
                self.fresh()
                self.assertEqual(self.nc.fault_kinds, {'UI_CONNECTION', 'OTHER'})
                self.assertFalse(self.core.feedback_recoverable)
                self.fresh(16)
                self.assertEqual(self.core.state, 'ERROR')

    def test_start_request_is_latched_before_worker_call_even_before_running_status(self):
        self.ready()
        def start_route(sha):
            self.assertTrue(self.nc.start_requested)
            self.worker.calls.append(('start', sha))
            return True
        self.worker.start_route = start_route
        self.assertTrue(self.request('START_NONCONTACT', {'plan_sha256': 'a'*64})['ok'])
        self.assertEqual(self.worker.state['state'], 'READY')
        self.terminal_after_ui_loss()
        self.assertFalse(self.core.feedback_recoverable)
        self.fresh(16)
        self.assertEqual(self.core.state, 'ERROR')
        self.assertEqual(sum(call[0] == 'start' for call in self.worker.calls), 1)

    def test_uncertain_or_rejected_start_ack_does_not_restore_prestart_permission(self):
        for raises in (False, True):
            with self.subTest(raises=raises):
                self.setUp()
                self.ready()
                def start_route(_sha):
                    self.assertTrue(self.nc.start_requested)
                    if raises:
                        raise RuntimeError('uncertain synthetic START acknowledgment')
                    return False
                self.worker.start_route = start_route
                with self.assertRaises((ValueError, RuntimeError)):
                    self.nc.start({'plan_sha256': 'a'*64})
                self.terminal_after_ui_loss()
                self.assertFalse(self.core.feedback_recoverable)
                self.fresh(16)
                self.assertEqual(self.core.state, 'ERROR')

    def test_hardware_fault_after_stop_revokes_recovery_permission(self):
        for field in ('emergency_stop', 'error', 'safety_guard'):
            with self.subTest(field=field):
                self.setUp()
                self.ready()
                self.terminal_after_ui_loss()
                self.assertTrue(self.core.feedback_recoverable)
                self.fresh(**{field: 1})
                self.assertFalse(self.core.feedback_recoverable)
                self.assertFalse(self.core.recovery_requires_ui)
                self.fresh(16, **{field: 0})
                self.assertEqual(self.core.state, 'ERROR')
                self.assertFalse(self.request('SCAN', {'region': 'BACK'})['ok'])


class SessionMotionAttemptEvidenceTests(unittest.TestCase):
    def make(self):
        clock = Clock()
        reader = FakeReader(clock)
        session = WaypointSession(plan(), reader, lambda _event: None, clock=clock, sleep=clock.sleep)
        return session, reader

    def test_ready_stop_preflight_does_not_count_as_attempted_motion(self):
        session, reader = self.make()
        def sample_hook():
            if session.status_snapshot()['state'] == 'READY':
                self.assertIs(session.status_snapshot()['motion_attempted'], False)
                session.request_stop()
        reader.on_sample = sample_hook
        session.run()
        terminal = session.status_snapshot()
        self.assertEqual(terminal['state'], 'STOPPED')
        self.assertIs(terminal['stop_confirmed'], True)
        self.assertIs(terminal['motion_attempted'], False)
        self.assertFalse(reader.moves)
        self.assertGreater(reader.stops, 0)

    def test_uncertain_move_ack_has_motion_attempted_set_before_reader_move(self):
        session, reader = self.make()
        reader.reject_move = True
        reader.on_sample = lambda: session.request_start() if session.status_snapshot()['state'] == 'READY' else None
        observed = []
        reader.on_move = lambda: observed.append(session.motion_attempted)
        session.run()
        self.assertEqual(observed, [True])
        self.assertEqual(len(reader.moves), 1)
        self.assertIs(session.status_snapshot()['motion_attempted'], True)
        self.assertEqual(session.status_snapshot()['state'], 'ERROR')

    def test_stopped_after_one_move_retains_motion_attempted_evidence(self):
        session, reader = self.make()
        reader.on_sample = lambda: session.request_start() if session.status_snapshot()['state'] == 'READY' else None
        reader.on_move = session.request_stop
        session.run()
        self.assertEqual(len(reader.moves), 1)
        self.assertEqual(session.status_snapshot()['state'], 'STOPPED')
        self.assertIs(session.status_snapshot()['stop_confirmed'], True)
        self.assertIs(session.status_snapshot()['motion_attempted'], True)


if __name__ == '__main__':
    unittest.main()
