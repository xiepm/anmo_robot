"""Acquisition recovery must not restore motion or reuse a stale treatment plan."""
import unittest
import test_real_scan

class FeedbackRecoveryTests(unittest.TestCase):
    setUp = test_real_scan.RealScanTests.setUp
    request = test_real_scan.RealScanTests.request

    def outage(self):
        self.cache.value['valid'] = False
        self.now += .12
        self.core.tick()
        self.assertEqual(self.core.state, 'ERROR')

    def fresh(self, ticks=1, **state):
        for _ in range(ticks):
            self.now += .2
            self.cache.value.update(valid=True, host_monotonic_after_s=self.now)
            self.cache.value['robot_state'].update(state)
            self.core.vision_at = self.now
            self.core.tick()

    def test_feedback_outage_recovers_to_idle_without_restoring_targets(self):
        self.core.scan_snapshot = {'old': True}
        self.core.plan = {'old': True}
        self.outage()
        self.assertIn('反馈失效', self.request('SCAN', {'region':'BACK'})['message'])
        self.fresh(5)
        self.assertEqual(self.core.state, 'ERROR')
        self.fresh(8)
        self.assertEqual(self.core.state, 'IDLE')
        self.assertIsNone(self.core.plan)
        self.assertIsNone(self.core.scan_snapshot)
        self.assertFalse(self.core.points)
        self.assertIsNone(self.core.confirmation.value)
        self.assertTrue(self.request('SCAN', {'region':'BACK'})['ok'])
        self.assertFalse(self.request('START')['ok'])
        self.assertEqual([call[1] for call in self.device.calls], ['output_unavailable','stop_unavailable'])

    def test_unsettled_robot_or_pending_cancellation_blocks_recovery(self):
        for field, value in [('moving',1), ('paused',1), ('in_position',0), ('blending_done',0)]:
            with self.subTest(field=field):
                self.setUp();self.outage();self.fresh(15, **{field:value})
                self.assertEqual(self.core.state, 'ERROR')
        self.setUp();self.outage()
        self.core.pending = {'start':self.now,'region':'BACK','view':'back'}
        self.core.cancel_at = self.now
        self.fresh(15)
        self.assertEqual(self.core.state, 'ERROR')

    def test_estop_and_hardware_fault_latch_even_when_feedback_returns(self):
        for field in ('emergency_stop','error','safety_guard'):
            with self.subTest(field=field):
                self.setUp();self.outage()
                self.fresh(**{field:1})
                self.fresh(15, **{field:0})
                self.assertEqual(self.core.state, 'ERROR')
                self.assertFalse(self.core.feedback_recoverable)
                self.assertFalse(self.request('SCAN', {'region':'BACK'})['ok'])

    def test_stale_cache_and_another_outage_restart_stability_window(self):
        self.outage();self.fresh(8)
        self.now += .6;self.core.tick()
        self.assertEqual(self.core.state, 'ERROR')
        self.fresh(5)
        self.assertEqual(self.core.state, 'ERROR')
        self.fresh(8)
        self.assertEqual(self.core.state, 'IDLE')

    def test_motion_enabled_and_other_failures_do_not_auto_recover(self):
        self.outage();self.device.motion_enabled = True;self.fresh(15)
        self.assertEqual(self.core.state, 'ERROR')
        self.setUp();self.core.fail('停止确认超时，实际停止未知');self.fresh(15)
        self.assertEqual(self.core.state, 'ERROR')

if __name__ == '__main__': unittest.main()
