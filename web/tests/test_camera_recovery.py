"""USB lifecycle regression checks without a physical camera or robot."""
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'web/scripts'))
from camera_recovery import CameraRecovery
from vision_preview import LatestFrame


class NoSleepStop:
    def __init__(self): self.stopped=False; self.waits=[]
    def is_set(self): return self.stopped
    def wait(self,seconds): self.waits.append(seconds); return self.stopped


class CameraRecoveryTests(unittest.TestCase):
    def recovery(self,opens=None,reads=None):
        self.events=[];self.stop=NoSleepStop()
        self.open=Mock(side_effect=opens)
        self.read=Mock(side_effect=reads)
        def close():self.events.append('close')
        def lost(reason):self.events.append(('lost',reason))
        return CameraRecovery(self.open,self.read,close,lost,self.stop)

    def test_starts_without_camera_and_recovers_when_device_appears(self):
        recovery=self.recovery([RuntimeError('No device connected'),OSError('USB pending'),None],[(True,b'new')])
        self.assertEqual(recovery.read(),(True,b'new'))
        self.assertEqual(self.open.call_count,3)
        self.assertEqual(self.events.count('close'),2)
        self.assertEqual(self.stop.waits,[1.,1.])
        self.assertEqual(recovery.generation,1)

    def test_repeated_unplug_replug_rebuilds_one_owner_in_order(self):
        recovery=self.recovery(reads=[(True,b'first'),(False,None),(True,b'second'),
                                      RuntimeError('device disconnected'),(True,b'third')])
        for expected in (b'first',b'second',b'third'):
            self.assertEqual(recovery.read(),(True,expected))
        self.assertEqual(self.open.call_count,3)
        self.assertEqual(self.events.count('close'),2)
        self.assertEqual(recovery.generation,3)
        self.assertTrue(recovery.opened)

    def test_partial_open_is_closed_before_the_next_open(self):
        recovery=self.recovery([RuntimeError('failed after pipeline.start'),None],[(True,b'ok')])
        def open_again():
            if not self.events:
                self.events.append('open1');raise RuntimeError('partial open')
            self.assertEqual(self.events[-1],'close')
        self.open.side_effect=open_again
        self.assertEqual(recovery.read(),(True,b'ok'))

    def test_stopping_while_waiting_does_not_reopen_or_spin(self):
        recovery=self.recovery()
        self.stop.stopped=True
        with self.assertRaises(KeyboardInterrupt):recovery.read()
        self.open.assert_not_called();self.read.assert_not_called()
        self.assertEqual(self.stop.waits,[])

    def test_missing_color_or_depth_does_not_escape_as_end_of_video(self):
        recovery=self.recovery(reads=[(True,None),(False,None),(True,b'new')])
        self.assertEqual(recovery.read(),(True,b'new'))
        self.assertEqual(self.open.call_count,3)

    def test_programming_error_is_not_silently_retried(self):
        recovery=self.recovery(reads=[TypeError('bad implementation')])
        with self.assertRaises(TypeError):recovery.read()
        self.assertEqual(self.open.call_count,1)


class CameraResetTests(unittest.TestCase):
    def setUp(self):
        self.now = 0.
        self.events = []
        self.stop = NoSleepStop()

    def recovery(self, reads, resets, opens=None):
        self.reset = Mock(side_effect=resets)
        def close():
            self.events.append('close')
        def reset():
            self.assertEqual(self.events[-1], 'close')
            self.events.append('reset')
            return self.reset()
        def read():
            value = next(reads)
            if isinstance(value, tuple) and isinstance(value[0], float):
                self.now, value = value
            if isinstance(value, Exception):
                raise value
            return value
        return CameraRecovery(Mock(side_effect=opens), read, close,
                              lambda reason: None, self.stop,
                              clock=lambda: self.now, reset_camera=reset)

    def test_mixed_start_and_read_failures_reset_only_after_three_and_close_first(self):
        reads = iter([(False, None), (True, b'recovered')])
        recovery = self.recovery(reads, [True],
                                 [RuntimeError('partial start'), OSError('open'), None, None])
        self.assertEqual(recovery.read(), (True, b'recovered'))
        self.assertEqual(self.events, ['close', 'close', 'close', 'reset'])
        self.assertEqual(self.reset.call_count, 1)

    def test_successful_reset_occurs_once_until_a_valid_frame(self):
        reads = iter([(False, None)] * 3
                     + [(31., (False, None)), (True, None), (False, None), (True, b'first')]
                     + [(32., (False, None)), (False, None), (False, None), (True, b'second')])
        recovery = self.recovery(reads, [True, True])
        self.assertEqual(recovery.read(), (True, b'first'))
        self.assertEqual(self.reset.call_count, 1)
        self.assertEqual(recovery.read(), (True, b'second'))
        self.assertEqual(self.reset.call_count, 2)

    def test_absent_device_does_not_consume_the_reset_or_start_cooldown(self):
        reads = iter([(False, None)] * 8 + [(True, b'new')])
        recovery = self.recovery(reads, [False, False, True])
        self.assertEqual(recovery.read(), (True, b'new'))
        # All failures occur at the same timestamp: absence must not defer the
        # actual reset when the third callback finally discovers the device.
        self.assertEqual(self.reset.call_count, 3)

    def test_cooldown_survives_a_valid_frame_and_reopens(self):
        reads = iter([(False, None)] * 3 + [(True, b'first')]
                     + [(20., (False, None)), (False, None), (False, None),
                        (29., (False, None)), (True, b'second')]
                     + [(30., (False, None)), (False, None), (False, None), (True, b'third')])
        recovery = self.recovery(reads, [True, True])
        self.assertEqual(recovery.read(), (True, b'first'))
        self.assertEqual(recovery.read(), (True, b'second'))
        self.assertEqual(self.reset.call_count, 1)
        self.assertEqual(recovery.read(), (True, b'third'))
        self.assertEqual(self.reset.call_count, 2)

    def test_reset_io_errors_keep_retrying_but_are_rate_limited(self):
        reads = iter([(False, None)] * 3 + [(29., (False, None)),
                     (30., (False, None)), (59., (False, None)),
                     (60., (False, None)), (True, b'new')])
        recovery = self.recovery(reads, [RuntimeError('reset disconnected'),
                                        OSError('USB reset pending'), True])
        self.assertEqual(recovery.read(), (True, b'new'))
        self.assertEqual(self.reset.call_count, 3)

    def test_reset_programming_error_is_not_swallowed(self):
        recovery = self.recovery(iter([(False, None)] * 3), [TypeError('bad reset hook')])
        with self.assertRaisesRegex(TypeError, 'bad reset hook'):
            recovery.read()
        self.assertEqual(self.reset.call_count, 1)

    def test_no_reset_hook_preserves_repeated_reopen_behavior(self):
        reads = iter([(False, None)] * 7 + [(True, b'new')])
        recovery = self.recovery(reads, [])
        recovery.reset_camera = None
        self.assertEqual(recovery.read(), (True, b'new'))
        self.reset.assert_not_called()
        self.assertEqual(self.events, ['close'] * 7)


class FrameInvalidationTests(unittest.TestCase):
    def test_disconnect_retires_live_photo_depth_and_timing(self):
        frames=LatestFrame();frames.put(b'live')
        frames.put_snapshot(b'photo',1,10.,raw_jpeg=b'raw',editor_data={'points':[]},depth_resolver=object())
        frames.timing={'capture_time_valid':True}
        sequence=frames.get()[2]
        frames.invalidate('USB disconnected')
        self.assertIsNone(frames.get()[0]);self.assertGreater(frames.get()[2],sequence)
        self.assertIsNone(frames.get_snapshot(1,10.));self.assertIsNone(frames.get_editor(1,10.))
        self.assertIsNone(frames.get_geometry(1,10.))
        self.assertFalse(frames.timing['capture_time_valid'])
        self.assertEqual(frames.camera_state,'RECONNECTING')
        frames.put(b'reconnected')
        self.assertEqual(frames.get()[0],b'reconnected')
        self.assertEqual(frames.camera_state,'STREAMING')
        self.assertIsNone(frames.get_snapshot(1,10.))

    def test_inflight_refinement_is_rejected_after_disconnect(self):
        frames=LatestFrame();started=threading.Event();finish=threading.Event();errors=[]
        class Resolver:
            def resolve(self,points):
                started.set();finish.wait(2);return points
        frames.put_snapshot(b'photo',1,10.,depth_resolver=Resolver())
        def refine():
            try:frames.resolve_points(dict(frame_number=1,received_monotonic_s=10.,points=[]))
            except ValueError as exc:errors.append(str(exc))
        thread=threading.Thread(target=refine);thread.start()
        self.assertTrue(started.wait(1));frames.invalidate('unplugged');finish.set();thread.join(2)
        self.assertFalse(thread.is_alive());self.assertEqual(len(errors),1)

    def test_inflight_selected_photo_is_retired_on_disconnect_or_new_scan(self):
        for transition in ('disconnect', 'new_scan'):
            with self.subTest(transition=transition):
                frames=LatestFrame();started=threading.Event();finish=threading.Event();results=[];errors=[]
                def render(selected):
                    started.set()
                    if not finish.wait(2): raise RuntimeError('test renderer timed out')
                    return b'old_selected_photo'
                frames.put_snapshot(b'old_photo',1,10.,render_selected=render)
                def request():
                    try:results.append(frames.get_snapshot(1,10.,[('大椎',None)]))
                    except Exception as exc:errors.append(exc)
                thread=threading.Thread(target=request);thread.start()
                try:
                    self.assertTrue(started.wait(1))
                    if transition=='disconnect':
                        frames.invalidate('unplugged')
                        frames.put(b'reconnected_live')
                    else:
                        frames.put_snapshot(b'new_photo',2,11.)
                finally:
                    finish.set();thread.join(2)
                self.assertFalse(thread.is_alive());self.assertEqual(errors,[])
                self.assertEqual(results,[None])
                if transition=='new_scan':
                    self.assertEqual(frames.get_snapshot(2,11.),b'new_photo')

    def test_inflight_geometry_is_retired_on_disconnect_or_new_scan(self):
        import numpy as np
        for transition in ('disconnect', 'new_scan'):
            with self.subTest(transition=transition):
                frames=LatestFrame();started=threading.Event();finish=threading.Event();results=[];errors=[]
                frozen=SimpleNamespace(depth=np.ones((1,1)),rays=np.zeros((1,1,3)))
                frames.put_snapshot(b'old_photo',1,10.,depth_resolver=frozen)
                original=np.savez_compressed
                def compress(*args,**kwargs):
                    started.set()
                    if not finish.wait(2): raise RuntimeError('test compression timed out')
                    return original(*args,**kwargs)
                def request():
                    try:results.append(frames.get_geometry(1,10.))
                    except Exception as exc:errors.append(exc)
                with patch.object(np,'savez_compressed',side_effect=compress):
                    thread=threading.Thread(target=request);thread.start()
                    try:
                        self.assertTrue(started.wait(1))
                        if transition=='disconnect':
                            frames.invalidate('unplugged')
                            frames.put(b'reconnected_live')
                        else:
                            # A reused resolver must not preserve a retired
                            # frame binding when a new snapshot replaces it.
                            frames.put_snapshot(b'new_photo',2,11.,depth_resolver=frozen)
                    finally:
                        finish.set();thread.join(2)
                self.assertFalse(thread.is_alive());self.assertEqual(errors,[])
                self.assertEqual(results,[None])
                if transition=='new_scan':
                    self.assertIsNotNone(frames.get_geometry(2,11.))


if __name__=='__main__':unittest.main()
