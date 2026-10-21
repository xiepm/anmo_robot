import unittest

from massage_controller.stop_preflight import check_stop, initial_report
from test_waypoint import Clock, FakeReader


class StopPreflightTests(unittest.TestCase):
    def setUp(self):
        self.clock=Clock();self.reader=FakeReader(self.clock);self.reader.connect()
        self.samples=[];self.events=[]

    def run_check(self,record=None,cancelled=lambda:False):
        return check_stop(self.reader,initial_report('CHECKING'),self.samples.append,
                          record or self.events.append,cancelled,self.clock,self.clock.sleep)

    def test_stationary_stop_checks_three_new_frames_without_any_move(self):
        result=self.run_check()
        self.assertEqual(result['state'],'PASSED')
        self.assertTrue(result['stop_confirmed']);self.assertEqual(result['samples_after_stop'],3)
        self.assertEqual(self.reader.stops,1);self.assertEqual(self.reader.moves,[])
        self.assertFalse(result['execution_eligible'])
        self.assertTrue(self.reader.connected)
        self.assertEqual(result['start_pose'],result['final_pose'])
        self.assertTrue(all('received_unix_ns' in sample for sample in self.samples))

    def test_moving_simulated_stale_or_faulted_robot_is_not_touched(self):
        for key,value in [('pending',2),('sim',True),('stale',True),('fault',True),('bad_tcp',True)]:
            with self.subTest(key=key):
                self.setUp();setattr(self.reader,key,value)
                result=self.run_check()
                self.assertEqual(result['state'],'FAILED');self.assertEqual(self.reader.stops,0)
                self.assertFalse(result['stop_command_sent']);self.assertFalse(result['stop_confirmed'])

    def test_uncertain_stop_ack_is_not_retried(self):
        calls=[]
        def uncertain(): calls.append('STOP');raise RuntimeError('uncertain STOP ack')
        self.reader.stop=uncertain
        result=self.run_check()
        self.assertEqual(calls,['STOP']);self.assertEqual(result['state'],'FAILED')
        self.assertTrue(result['stop_command_sent']);self.assertFalse(result['stop_confirmed'])

    def test_position_change_and_repeated_timestamps_cannot_confirm_stop(self):
        original=self.reader.stop
        def changed(): original();self.reader.target[0]+=2
        self.reader.stop=changed
        self.assertEqual(self.run_check()['state'],'FAILED')
        self.setUp();sample=self.reader.sample
        def repeated():
            value=sample();value.update(host_monotonic_before_s=10.,host_monotonic_after_s=10.)
            return value
        self.reader.sample=repeated
        result=self.run_check()
        self.assertFalse(result['stop_confirmed']);self.assertEqual(result['samples_after_stop'],1)

    def test_cancellation_and_command_log_failure_send_no_command(self):
        self.assertEqual(self.run_check(cancelled=lambda:True)['state'],'FAILED')
        def record(value):
            if value['type']=='NONCONTACT_STOP_PREFLIGHT_COMMAND': raise OSError('log unavailable')
        result=self.run_check(record=record)
        self.assertFalse(result['stop_command_sent']);self.assertEqual(self.reader.stops,0)


if __name__=='__main__':unittest.main()
