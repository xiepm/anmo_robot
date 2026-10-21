"""Bounded review scheduling using only fake readers, clocks and futures."""
from concurrent.futures import Future
import threading
import time
import unittest
from unittest.mock import patch

from massage_controller.noncontact_worker import NoncontactWorker
from massage_controller.real_device import FeedbackWorker


CONFIG = dict(host='fake-no-network', port=1, read_timeout_s=.15,
              poll_interval_s=.1, reconnect_interval_s=.01,
              joint_names=[f'j{i}' for i in range(6)], source_joint_indices=list(range(6)),
              joint_signs=[1]*6, joint_offsets_deg=[0]*6)


class FakeReader:
    def __init__(self):
        self.connected = True
        self.audit = []
        self.calls = []
        self.sample_times = []
        self.first_sample = threading.Event()
        self.third_sample = threading.Event()
        self.on_math = lambda request: request['id']

    def record(self, name):
        self.calls.append((name, threading.get_ident(), time.monotonic()))

    def connect(self):
        self.record('connect')

    def sample(self):
        self.record('sample')
        now = time.monotonic()
        self.sample_times.append(now)
        self.first_sample.set()
        if len(self.sample_times) >= 3:
            self.third_sample.set()
        return dict(valid=True, connected=True, controller_simulation=False,
                    joint_positions_deg=[0]*6, tcp_pose=dict(xyz_mm=[1, 2, 3], rxyz_deg=[0]*3),
                    host_monotonic_after_s=now)

    def review_math(self, request):
        self.record('math')
        return self.on_math(request)

    def close(self):
        self.record('close')


class Clock:
    now = 0.

    def monotonic(self):
        return self.now


class DeadlineEvent:
    """Advance a fake clock only if an idle wait really needs to sleep."""
    def __init__(self, clock):
        self.clock = clock
        self.flag = False
        self.wait_states = []

    def set(self):
        self.flag = True

    def clear(self):
        self.flag = False

    def wait(self, timeout):
        self.wait_states.append(self.flag)
        if not self.flag:
            self.clock.now += timeout
        return self.flag


class ReviewSchedulingTests(unittest.TestCase):
    def setUp(self):
        guard = patch('socket.socket', side_effect=AssertionError('test must not open hardware or network'))
        guard.start()
        self.addCleanup(guard.stop)

    def worker(self):
        return NoncontactWorker(CONFIG, 'unused-fake-sdk', lambda message: None)

    def enqueue(self, worker, request_id):
        future = Future()
        worker.math_jobs.put_nowait((dict(kind='ik', id=request_id), future))
        worker.work_ready.set()
        return future

    def test_sequential_queries_use_idle_interval_and_preserve_feedback_and_single_owner(self):
        worker, reader = self.worker(), FakeReader()
        worker.reader_factory = lambda *args: reader
        worker.start()
        try:
            self.assertTrue(reader.first_sample.wait(1))
            began = time.monotonic()
            for i in range(12):
                self.assertEqual(worker.review_math(dict(kind='ik', id=i)), i)
            elapsed = time.monotonic()-began
            # The previous one-query-per-100-ms schedule takes about 1.2 s.
            # Leave generous scheduler headroom; this tests the eliminated wait.
            self.assertLess(elapsed, .5)
            self.assertTrue(reader.third_sample.wait(1))
            gaps = [b-a for a, b in zip(reader.sample_times, reader.sample_times[1:])]
            self.assertTrue(gaps)
            self.assertGreater(min(gaps), .07)
            self.assertLess(max(gaps), .25)
        finally:
            worker.close()
        self.assertFalse(worker.thread.is_alive())
        self.assertEqual({owner for _, owner, _ in reader.calls}, {worker.thread.ident})
        self.assertEqual(sum(name=='math' for name, _, _ in reader.calls), 12)

    def test_no_new_query_begins_after_feedback_deadline(self):
        worker, reader, clock = self.worker(), FakeReader(), Clock()
        worker.work_ready = DeadlineEvent(clock)
        futures = [self.enqueue(worker, i) for i in range(3)]
        def math_query(request):
            clock.now += .06
            return request['id']
        reader.on_math = math_query
        with patch('massage_controller.noncontact_worker.time.monotonic', clock.monotonic):
            worker.wait_for_next_poll(reader, .1)
        self.assertEqual([f.done() for f in futures], [True, True, False])
        self.assertAlmostEqual(clock.now, .12)  # Only the one in-flight item may finish late.
        self.assertEqual([at for name, _, at in reader.calls if name=='math'], [0., .06])

    def test_enqueue_between_empty_queue_check_and_wait_cannot_lose_wakeup(self):
        worker, reader, clock = self.worker(), FakeReader(), Clock()
        event = DeadlineEvent(clock)
        worker.work_ready = event
        process = worker._process_math_item
        arrived = []
        def race(reader):
            handled = process(reader)
            if not arrived:
                self.assertFalse(handled)
                arrived.append(self.enqueue(worker, 42))
            return handled
        with patch.object(worker, '_process_math_item', race), \
             patch('massage_controller.noncontact_worker.time.monotonic', clock.monotonic):
            worker.wait_for_next_poll(reader, .1)
        self.assertTrue(event.wait_states[0])
        self.assertEqual(arrived[0].result(), 42)
        self.assertEqual([at for name, _, at in reader.calls if name=='math'], [0.])

    def test_execution_session_stop_check_and_shutdown_precede_queued_math(self):
        for priority in ('job', 'session', 'stop_check', 'shutdown'):
            with self.subTest(priority=priority):
                worker, reader = self.worker(), FakeReader()
                future = self.enqueue(worker, 1)
                if priority=='job': worker.job = object()
                elif priority=='session': worker.session = object()
                elif priority=='stop_check': worker.stop_check_pending = True
                else: worker._stop.set()
                worker.wait_for_next_poll(reader, time.monotonic()+1)
                self.assertFalse(future.done())
                self.assertFalse(reader.calls)

    def test_priority_is_rechecked_between_math_items_without_holding_job_lock(self):
        worker, reader = self.worker(), FakeReader()
        first, second = self.enqueue(worker, 1), self.enqueue(worker, 2)
        def math_query(request):
            # submit acquires job_lock: calling it from the fake I/O proves
            # the scheduling code did not hold that lock over SDK work.
            worker.submit({}, [], 'new-revision')
            return request['id']
        reader.on_math = math_query
        worker.wait_for_next_poll(reader, time.monotonic()+1)
        self.assertEqual(first.result(), 1)
        self.assertFalse(second.done())
        self.assertIsNotNone(worker.job)

    def test_normal_poll_slot_prioritizes_execution_and_stop_check_over_math(self):
        worker, reader = self.worker(), FakeReader()
        # Missing review intentionally ends preparation before any motion API.
        worker.submit(dict(geometry_source='FROZEN_DEPTH'), [], 'not-reviewed')
        future = self.enqueue(worker, 1)
        worker.process_work(reader)
        self.assertEqual(worker.execution_snapshot()['state'], 'ERROR')
        self.assertFalse(future.done())
        self.assertEqual([name for name, _, _ in reader.calls], ['sample'])

        worker, reader = self.worker(), FakeReader()
        worker.request_stop_check()
        future = self.enqueue(worker, 2)
        with patch('massage_controller.noncontact_worker.check_stop',
                   return_value=dict(state='PASSED')) as check:
            worker.process_work(reader)
        check.assert_called_once()
        self.assertEqual(worker.stop_check_snapshot()['state'], 'PASSED')
        self.assertFalse(future.done())
        self.assertFalse(reader.calls)

    def test_new_job_stop_check_close_and_stop_wake_an_idle_wait(self):
        class ObservedEvent(threading.Event):
            def __init__(self):
                super().__init__()
                self.waiting = threading.Event()
            def wait(self, timeout=None):
                self.waiting.set()
                return super().wait(timeout)
        for action in ('submit', 'stop_check', 'close', 'stop'):
            with self.subTest(action=action):
                worker, reader = self.worker(), FakeReader()
                event = ObservedEvent()
                worker.work_ready = event
                waiter = threading.Thread(target=worker.wait_for_next_poll,
                                          args=(reader, time.monotonic()+10))
                waiter.start()
                try:
                    self.assertTrue(event.waiting.wait(1))
                    if action=='submit': worker.submit({}, [], 'new')
                    elif action=='stop_check': worker.request_stop_check()
                    elif action=='close': worker.close()
                    else:
                        class Session:
                            called = False
                            def request_stop(self): self.called = True
                        session = Session()
                        with worker.job_lock: worker.session = session
                        worker.stop_route()
                        self.assertTrue(session.called)
                    waiter.join(.3)
                    self.assertFalse(waiter.is_alive())
                    self.assertFalse(reader.calls)
                finally:
                    worker._stop.set()
                    event.set()
                    waiter.join(1)

    def test_timed_out_queued_query_is_cancelled_and_never_reaches_reader(self):
        worker, reader = self.worker(), FakeReader()
        created = []
        class ExpiringFuture(Future):
            def __init__(self):
                super().__init__()
                created.append(self)
            def result(self, timeout=None):
                self.assert_timeout = timeout
                raise TimeoutError('injected queued timeout')
        with patch('massage_controller.noncontact_worker.Future', ExpiringFuture):
            with self.assertRaises(TimeoutError): worker.review_math(dict(kind='ik', id=1))
        self.assertEqual(created[0].assert_timeout, 2.)
        self.assertTrue(created[0].cancelled())
        self.assertTrue(worker._process_math_item(reader))
        self.assertFalse(reader.calls)

    def test_math_exception_completes_its_future_and_does_not_strand_the_next_item(self):
        worker, reader = self.worker(), FakeReader()
        first, second = self.enqueue(worker, 1), self.enqueue(worker, 2)
        def math_query(request):
            if request['id']==1: raise ValueError('injected IK failure')
            return request['id']
        reader.on_math = math_query
        worker._process_math_item(reader)
        worker._process_math_item(reader)
        with self.assertRaisesRegex(ValueError, 'IK failure'): first.result()
        self.assertEqual(second.result(), 2)

    def test_readonly_wait_hook_keeps_the_original_poll_delay(self):
        worker, clock = FeedbackWorker(CONFIG, 'unused'), Clock()
        clock.now = .03
        worker._stop = DeadlineEvent(clock)
        with patch('massage_controller.real_device.time.monotonic', clock.monotonic):
            worker.wait_for_next_poll(None, .1)
        self.assertAlmostEqual(clock.now, .1)


if __name__=='__main__': unittest.main()
