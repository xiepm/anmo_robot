import asyncio
import copy
import json
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'web/scripts'), str(ROOT/'zhenjiu'), str(ROOT/'src/massage_controller')]
from live_locate_bridge import LiveLocateBridge
from massage_controller.base_coordinates import BaseProjector, BOOT_ID
from rehab_vision.client import Session


class BridgeFixture:
    def setUp(self):
        self.now = 10.
        self.bridge = LiveLocateBridge(clock=lambda: self.now)
        self.points = [dict(name='大椎', side=None, camera_point_mm=[0., 0., 600.], coordinate_valid=True)]
        self.metadata = dict(camera_serial='SYNTHETIC-TEST-CAMERA', color_frame_number=1)
        projector = BaseProjector(ROOT/'src/massage_controller/test/fixtures/calibration.synthetic.json')
        self.packet = projector.project(dict(data=dict(
            camera_serial=self.metadata['camera_serial'], frame_number=1, host_boot_id=BOOT_ID,
            frame_received_monotonic_s=self.now, points=[dict(self.points[0], side='MIDLINE')])), [], self.now)
        self.bridge.publish(self.points, self.metadata, self.now, self.packet)


class BridgeTests(BridgeFixture, unittest.TestCase):
    def test_only_a_new_frame_completes_locate_and_no_motion_validity_is_invented(self):
        self.now += .1
        job = self.bridge.submit(dict(region='BACK', view='back'), 30)
        self.bridge.publish(self.points, self.metadata, 10., self.packet)
        self.assertFalse(job.future.done())
        self.now += .1
        self.bridge.publish(self.points, self.metadata, self.now, self.packet)
        answer = job.future.result()['data']
        self.assertEqual(answer['status'], 'SUCCEEDED')
        self.assertFalse(answer['capture_time_valid'])
        self.assertIsNone(answer['capture_host_monotonic_ms'])
        self.assertFalse(answer['base_preview']['data']['motion_eligible'])
        self.assertFalse(answer['points'][0]['normal_valid'])

    def test_cancel_discards_late_results_and_allows_new_job(self):
        job = self.bridge.submit(dict(region='BACK', view='back'), 30)
        job.cancel.set()
        self.now += .1
        self.bridge.publish(self.points, self.metadata, self.now, self.packet)
        self.assertEqual(job.future.result()['data']['status'], 'CANCELED')
        new = self.bridge.submit(dict(region='BACK', view='back'), 30)
        self.assertFalse(new.future.done())

    def test_stale_empty_points_timeout_and_unsupported_region(self):
        with self.assertRaises(ValueError):
            self.bridge.submit(dict(region='LOWER_LIMB', view='front'), 30)
        job = self.bridge.submit(dict(region='BACK', view='back'), 1)
        self.now += .1
        self.bridge.publish([], self.metadata, self.now, self.packet)
        self.assertFalse(job.future.done())
        self.now += 2
        health = self.bridge.health()
        self.assertEqual(health['worker_state'], 'IDLE')
        self.assertTrue(health['camera_connected'])
        self.assertGreater(health['last_frame_age_ms'], 500)
        self.assertEqual(job.future.result()['data']['status'], 'FAILED')
        with self.assertRaises(ValueError):
            self.bridge.submit(dict(region='BACK', view='back'), 1)

    def test_slow_frame_keeps_camera_session_but_cannot_start_new_scan(self):
        job = self.bridge.submit(dict(region='BACK', view='back'), 30)
        self.now += .1
        self.bridge.publish(self.points, self.metadata, self.now, self.packet)
        answer = job.future.result()
        original_generation = self.bridge.capture_generation
        self.now += .7
        health = self.bridge.health()
        self.assertTrue(health['camera_connected'])
        self.assertTrue(health['model_ready'])
        self.assertEqual(health['worker_state'], 'IDLE')
        self.assertEqual(health['error'], '')
        self.assertAlmostEqual(health['last_frame_age_ms'], 700)
        self.assertEqual(self.bridge.prepare_delivery(answer), answer)
        self.assertEqual(self.bridge.capture_generation, original_generation)
        with self.assertRaisesRegex(ValueError, '实时画面已过期'):
            self.bridge.submit(dict(region='BACK', view='back'), 30)
        self.bridge.publish(self.points, self.metadata, self.now, self.packet)
        self.assertEqual(self.bridge.health()['last_frame_age_ms'], 0)
        self.assertEqual(self.bridge.capture_generation, original_generation)

    def test_real_disconnect_during_slow_frame_retires_capture(self):
        job = self.bridge.submit(dict(region='BACK', view='back'), 30)
        self.now += .1
        self.bridge.publish(self.points, self.metadata, self.now, self.packet)
        answer = job.future.result()
        self.now += .7
        self.bridge.camera_disconnected('USB removed')
        self.assertFalse(self.bridge.health()['camera_connected'])
        self.assertEqual(self.bridge.prepare_delivery(answer)['data']['status'], 'CANCELED')

    def test_close_cancels_pending_job(self):
        job = self.bridge.submit(dict(region='BACK', view='back'), 30)
        self.bridge.close()
        self.assertEqual(job.future.result()['data']['status'], 'CANCELED')
        self.assertFalse(self.bridge.health()['camera_connected'])

    def test_close_after_business_loop_has_exited_still_joins_and_is_repeatable(self):
        job = self.bridge.submit(dict(region='BACK', view='back'), 30)
        self.bridge.loop = asyncio.new_event_loop()
        self.bridge.stop = asyncio.Event()
        self.bridge.thread = Mock()
        self.bridge.loop.close()
        self.bridge.close()
        self.bridge.close()
        self.assertEqual(job.future.result()['data']['status'], 'CANCELED')
        self.assertFalse(self.bridge.health()['camera_connected'])
        self.assertEqual(self.bridge.thread.join.call_count, 2)
        self.bridge.thread.join.assert_called_with(timeout=4)

    def test_close_from_business_thread_does_not_join_itself(self):
        self.bridge.thread = threading.current_thread()
        self.bridge.close()
        self.assertTrue(self.bridge.closed)

    def test_unplug_cancels_old_scan_and_replug_needs_a_new_request(self):
        old=self.bridge.submit(dict(region='BACK',view='back'),30)
        self.bridge.camera_disconnected('USB removed')
        self.assertFalse(self.bridge.health()['camera_connected'])
        self.assertEqual(old.future.result()['data']['status'],'CANCELED')
        with self.assertRaises(ValueError):self.bridge.submit(dict(region='BACK',view='back'),30)
        self.now+=2
        self.bridge.publish(self.points,self.metadata,self.now,self.packet)
        self.assertTrue(self.bridge.health()['camera_connected'])
        self.assertEqual(old.future.result()['data']['status'],'CANCELED')
        new=self.bridge.submit(dict(region='BACK',view='back'),30)
        self.assertFalse(new.future.done())
        self.now+=.1
        self.bridge.publish(self.points,self.metadata,self.now,self.packet)
        self.assertEqual(new.future.result()['data']['status'],'SUCCEEDED')


class DeliveryTests(BridgeFixture, unittest.IsolatedAsyncioTestCase):
    async def test_freshness_crossings_publish_without_waiting_for_heartbeat(self):
        health = self.bridge.health()
        ages = [0., 499., 500., 501., 800., 1499., 1500., 1501., 100.]
        engine = Mock()
        engine.prepare_delivery = None
        engine.health.side_effect = [dict(health, last_frame_age_ms=age) for age in ages] + [asyncio.CancelledError()]
        session = Session(engine, self.bridge.contract)
        with patch('rehab_vision.client.asyncio.sleep', new=AsyncMock()):
            with self.assertRaises(asyncio.CancelledError):
                await session.health()
        messages = []
        while not session.outgoing.empty():
            messages.append(json.loads(session.outgoing.get_nowait()))
        self.assertEqual([m['data']['last_frame_age_ms'] for m in messages], [0., 501., 1500., 100.])

    def complete_scan(self, session):
        job = self.bridge.submit(dict(region='BACK', view='back'), 30)
        session.active = job
        self.publish_new_frame()
        self.assertEqual(job.future.result()['data']['status'], 'SUCCEEDED')
        return job

    def publish_new_frame(self):
        self.now += .1
        packet = copy.deepcopy(self.packet)
        packet['data']['frame_received_monotonic_s'] = self.now
        # Keep frame number 1: USB reconnection may restart the camera counter.
        self.bridge.publish(self.points, self.metadata, self.now, packet)

    def read_message(self, raw):
        self.assertIs(type(raw), str)
        message = json.loads(raw)
        self.bridge.contract.message.validate(message)
        return message

    async def send_queued(self, session, count=1):
        messages = []
        ready = asyncio.Event()
        fixture = self
        class Socket:
            async def send(self, raw):
                messages.append(fixture.read_message(raw))
                if len(messages) >= count:
                    ready.set()
        writer = asyncio.create_task(session.writer(Socket()))
        try:
            await asyncio.wait_for(ready.wait(), 1)
        finally:
            writer.cancel()
            await asyncio.gather(writer, return_exceptions=True)
        return messages

    def assert_retired(self, message):
        self.assertEqual(message['data']['status'], 'CANCELED')
        self.assertEqual(message['data']['points'], [])
        self.assertFalse(message['data']['capture_time_valid'])
        self.assertNotIn('base_preview', message['data'])

    async def test_completed_future_is_canceled_if_camera_disconnects_before_finish(self):
        session = Session(self.bridge, self.bridge.contract)
        job = self.complete_scan(session)
        self.bridge.camera_disconnected('USB removed')
        self.publish_new_frame()
        self.assertTrue(self.bridge.health()['camera_connected'])
        await session.finish(job)
        self.assert_retired(self.read_message(session.outgoing.get_nowait()))
        # The guard replaces the delivery; it never edits a completed Future.
        self.assertEqual(job.future.result()['data']['status'], 'SUCCEEDED')

    async def test_queued_success_is_rechecked_and_new_generation_scan_can_succeed(self):
        session = Session(self.bridge, self.bridge.contract)
        old = self.complete_scan(session)
        await session.finish(old)
        queued = session.outgoing.get_nowait()
        self.assertEqual(self.read_message(queued)['data']['status'], 'SUCCEEDED')
        session.outgoing.put_nowait(queued)
        self.bridge.camera_disconnected('USB removed after JSON queueing')
        self.publish_new_frame()
        new = self.complete_scan(session)
        await session.finish(new)
        messages = await self.send_queued(session, 2)
        self.assert_retired(messages[0])
        self.assertEqual(messages[1]['data']['status'], 'SUCCEEDED')
        self.assertEqual(messages[1]['data']['base_preview']['data']['frame_received_monotonic_s'], self.now)

    async def test_valid_same_generation_success_reaches_wire_unchanged(self):
        session = Session(self.bridge, self.bridge.contract)
        job = self.complete_scan(session)
        await session.finish(job)
        messages = await self.send_queued(session)
        self.assertEqual(messages, [job.future.result()])

    async def test_close_invalidates_result_already_in_transport_queue(self):
        session = Session(self.bridge, self.bridge.contract)
        job = self.complete_scan(session)
        await session.finish(job)
        self.bridge.close()
        messages = await self.send_queued(session)
        self.assert_retired(messages[0])


if __name__ == '__main__':
    unittest.main()
