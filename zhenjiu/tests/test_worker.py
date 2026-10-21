import asyncio
import json
import math
from pathlib import Path
import sys
import threading
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rehab_vision.contract import Contract, strict_json
from rehab_vision.engine import Engine, Frame
from rehab_vision.client import Session, run_client
from rehab_vision.backends import ReplaySource, ReplayModel
from websockets.legacy.server import serve
from websockets.legacy.client import connect

DATA = {'region': 'UPPER_LIMB', 'view': 'front'}
RAW = [{'name': '曲池', 'side': 'L', 'coordinate_valid': True, 'camera_point_mm': [10, 20, 800]}]


class Model:
    def __init__(self, points=None, gate=None):
        self.points = RAW if points is None else points
        self.gate = gate
        self.entered = threading.Event()
        self.closed = False

    def open(self): pass

    def infer(self, frame, data):
        self.entered.set()
        if self.gate:
            self.gate.wait(4)
        return self.points

    def close(self): self.closed = True


class Source:
    mode = 'SIMULATION'
    def __init__(self):
        self.closed = False
        self.pause = threading.Event()

    def open(self): pass

    def read(self):
        time.sleep(0.01)
        if self.pause.is_set():
            time.sleep(0.6)
            raise RuntimeError('test camera disconnected')
        return Frame(None)

    def close(self): self.closed = True


class MappingTests(unittest.TestCase):
    def setUp(self): self.c = Contract()

    def test_names_sides_and_mm(self):
        points = self.c.points('UPPER_LIMB', RAW + [dict(RAW[0], name='合谷', side='R')])
        self.assertEqual([p['side'] for p in points], ['LEFT', 'RIGHT'])
        self.assertEqual(points[0]['camera_point_mm'], [10, 20, 800])
        self.assertFalse(points[0]['normal_valid'])
        self.assertIsNone(points[0]['normal_camera'])
        p = self.c.points('BACK', [dict(RAW[0], name='大椎', side=None)])[0]
        self.assertEqual(p['side'], 'MIDLINE')

    def test_filter_wrong_region_unknown_and_wrong_side(self):
        self.assertEqual(self.c.points('BACK', RAW), [])
        self.assertEqual(self.c.points('UPPER_LIMB', [dict(RAW[0], side='MIDLINE')]), [])
        self.assertEqual(self.c.points('UPPER_LIMB', [dict(RAW[0], name='unknown')]), [])

    def test_invalid_depth_not_promoted(self):
        for xyz in (None, [0, 1], [0, 0, 0], [0, math.nan, 2], [True, 0, 5], [0, 1, math.inf]):
            p = self.c.points('UPPER_LIMB', [dict(RAW[0], camera_point_mm=xyz)])[0]
            self.assertFalse(p['coordinate_valid'])
            self.assertIsNone(p['camera_point_mm'])

    def test_duplicate_rejected(self):
        with self.assertRaises(ValueError): self.c.points('UPPER_LIMB', RAW * 2)

    def test_strict_requests(self):
        for raw in ('{"cmd":"CANCEL","cmd":"LOCATE","data":{}}',
                    '{"cmd":"CANCEL","data":{"x":NaN}}',
                    '{"cmd":"LOCATE","data":{"region":"BACK"}}',
                    '{"cmd":"CANCEL","data":{},"id":1}',
                    '{"cmd":"START","data":{}}', '[]', b'{}',
                    '{"cmd":"CANCEL","data":{"x":1e999}}'):
            with self.assertRaises((ValueError, TypeError)): self.c.decode(raw)
        self.assertEqual(self.c.decode('{"cmd":"CANCEL","data":{}}')['cmd'], 'CANCEL')


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.c, self.engines, self.sessions = Contract(), [], []

    async def asyncTearDown(self):
        for s in self.sessions:
            if s.active: s.active.cancel.set()
            for t in s.pending: t.cancel()
            await asyncio.gather(*s.pending, return_exceptions=True)
        for e in self.engines:
            if getattr(e.model, 'gate', None): e.model.gate.set()
            await asyncio.to_thread(e.close)

    async def make(self, model=None, timeout=1, source=None):
        e = Engine(source or Source(), model or Model(), self.c)
        self.engines.append(e)
        e.start()
        await self.until(lambda: e.health()['camera_connected'] and e.health()['model_ready'])
        s = Session(e, self.c, timeout)
        self.sessions.append(s)
        return e, s

    async def until(self, predicate, timeout=3):
        end = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() > end: self.fail('condition timed out')
            await asyncio.sleep(0.01)

    async def command(self, s, cmd, data=None):
        await s.handle(json.dumps(dict(cmd=cmd, data=DATA if data is None else data)))

    async def get(self, s):
        msg = strict_json(await asyncio.wait_for(s.outgoing.get(), 2))
        self.c.message.validate(msg)
        return msg

    async def test_success_once_no_exposure_or_normal_fabrication(self):
        e, s = await self.make()
        await self.command(s, 'LOCATE')
        self.assertTrue((await self.get(s))['ok'])
        msg = await self.get(s)
        self.assertEqual(msg['data']['status'], 'SUCCEEDED')
        self.assertFalse(msg['data']['capture_time_valid'])
        self.assertIsNone(msg['data']['capture_host_monotonic_ms'])
        self.assertEqual(msg['data']['frame'], 'camera_color_optical_frame')
        self.assertEqual(msg['data']['points'][0]['side'], 'LEFT')
        await asyncio.sleep(0.05)
        self.assertTrue(s.outgoing.empty())

    async def test_duplicate_cancel_late_result_and_busy_barrier(self):
        gate = threading.Event()
        e, s = await self.make(Model(gate=gate))
        await self.command(s, 'LOCATE')
        await self.get(s)
        await self.until(e.model.entered.is_set)
        await self.command(s, 'LOCATE')
        self.assertFalse((await self.get(s))['ok'])
        start = time.monotonic()
        await self.command(s, 'CANCEL', {})
        self.assertTrue((await self.get(s))['ok'])
        canceled = await self.get(s)
        self.assertEqual(canceled['data']['status'], 'CANCELED')
        self.assertEqual(canceled['data']['points'], [])
        self.assertLess(time.monotonic() - start, 0.1)
        await self.command(s, 'LOCATE')
        self.assertFalse((await self.get(s))['ok'])
        gate.set()
        await self.until(lambda: not e.busy)
        await asyncio.sleep(0.05)
        self.assertTrue(s.outgoing.empty())
        await self.command(s, 'LOCATE')
        self.assertTrue((await self.get(s))['ok'])
        self.assertEqual((await self.get(s))['data']['status'], 'SUCCEEDED')

    async def test_cancel_before_finish_coroutine_started(self):
        e, s = await self.make()
        await self.command(s, 'LOCATE')
        await self.command(s, 'CANCEL', {})
        self.assertEqual((await self.get(s))['cmd'], 'LOCATE')
        self.assertEqual((await self.get(s))['cmd'], 'CANCEL')
        self.assertEqual((await self.get(s))['data']['status'], 'CANCELED')
        await asyncio.sleep(0.15)
        self.assertTrue(s.outgoing.empty())

    async def test_cancel_idle_has_no_terminal(self):
        _, s = await self.make()
        await self.command(s, 'CANCEL', {})
        self.assertTrue((await self.get(s))['ok'])
        self.assertTrue(s.outgoing.empty())

    async def test_no_points_fails(self):
        _, s = await self.make(Model(points=[]), timeout=0.1)
        await self.command(s, 'LOCATE')
        await self.get(s)
        self.assertEqual((await self.get(s))['data']['status'], 'FAILED')

    async def test_timeout_suppresses_late_inference(self):
        gate = threading.Event()
        e, s = await self.make(Model(gate=gate), timeout=0.1)
        await self.command(s, 'LOCATE')
        await self.get(s)
        self.assertEqual((await self.get(s))['data']['status'], 'FAILED')
        gate.set()
        await asyncio.sleep(0.15)
        self.assertTrue(s.outgoing.empty())

    async def test_capture_and_heartbeat_continue_during_slow_inference(self):
        gate = threading.Event()
        e, s = await self.make(Model(gate=gate), timeout=3)
        await self.command(s, 'LOCATE')
        await self.get(s)
        await self.until(e.model.entered.is_set)
        frames = e.metrics['frames']
        heart = asyncio.create_task(s.health())
        try:
            first = await self.get(s)
            second = await self.get(s)
            self.assertEqual(first['type'], 'VISION_STATUS')
            self.assertEqual(second['type'], 'VISION_STATUS')
            self.assertTrue(second['data']['camera_connected'])
            self.assertGreater(e.metrics['frames'], frames + 20)
        finally:
            heart.cancel()
            await asyncio.gather(heart, return_exceptions=True)

    async def test_stale_camera_rejects_locate(self):
        e, s = await self.make()
        e.source.pause.set()
        await asyncio.sleep(0.56)
        self.assertFalse(e.health()['camera_connected'])
        await self.command(s, 'LOCATE')
        self.assertFalse((await self.get(s))['ok'])

    async def test_completed_then_cancel_does_not_duplicate_terminal(self):
        _, s = await self.make()
        await self.command(s, 'LOCATE')
        await self.get(s)
        await self.get(s)
        await self.command(s, 'CANCEL', {})
        self.assertEqual((await self.get(s))['cmd'], 'CANCEL')
        self.assertTrue(s.outgoing.empty())

    async def test_replay_uses_same_engine_and_contract(self):
        path = Path(__file__).parent / 'fixtures/points_replay.json'
        e, s = await self.make(ReplayModel(), source=ReplaySource(path))
        await self.command(s, 'LOCATE')
        await self.get(s)
        points = (await self.get(s))['data']['points']
        self.assertEqual({p['name'] for p in points}, {'曲池', '合谷'})
        self.assertEqual(e.health()['mode'], 'SIMULATION')

    async def test_wire_disconnect_and_reconnect_never_replay_old_result(self):
        gate = threading.Event()
        e, _ = await self.make(Model(gate=gate), timeout=2)
        received = [[], []]
        first_closed, second_ready, release_second = asyncio.Event(), asyncio.Event(), asyncio.Event()
        count = 0
        async def handler(ws, path):
            nonlocal count
            index = count
            count += 1
            if index > 1: return
            if index == 0:
                await ws.recv()  # Initial health.
                await ws.send(json.dumps(dict(cmd='LOCATE', data=DATA)))
                while True:
                    m = strict_json(await ws.recv())
                    if m['type'] == 'REPLY': break
                await self.until(e.model.entered.is_set)
                await ws.close()
                first_closed.set()
            else:
                second_ready.set()
                async for raw in ws:
                    received[1].append(strict_json(raw))
                    if release_second.is_set(): break
        stop = asyncio.Event()
        async with serve(handler, '127.0.0.1', 0) as server:
            port = server.sockets[0].getsockname()[1]
            client = asyncio.create_task(run_client(e, self.c, f'ws://127.0.0.1:{port}/vision', stop, 2))
            try:
                await asyncio.wait_for(first_closed.wait(), 3)
                gate.set()
                await asyncio.wait_for(second_ready.wait(), 3)
                await asyncio.sleep(0.2)
                self.assertTrue(received[1])
                self.assertFalse(any(m['type'] == 'LOCATE_RESULT' for m in received[1]))
            finally:
                release_second.set()
                stop.set()
                await asyncio.wait_for(client, 3)


class ControllerBoundaryTests(unittest.TestCase):
    """Use the existing controller's pure simulation core; never create SDK clients."""
    def setUp(self):
        root = Path(__file__).resolve().parents[2]
        sys.path.insert(0, str(root / 'src/massage_controller'))
        from massage_controller.core import Controller
        from massage_controller.protocol import load_contract
        self.now = 100.0
        self.core = Controller(load_contract(root / 'config/rehab_protocol'), lambda: self.now)
        self.core.vision_connected = True
        self.health = dict(type='VISION_STATUS', data=dict(mode='SIMULATION', camera_connected=True,
                           model_ready=True, worker_state='IDLE', last_frame_age_ms=10, error=''))
        self.core.vision_message(json.dumps(self.health))

    def test_controller_rejects_missing_capture_time(self):
        from rehab_vision.contract import result
        reply = self.core.request(json.dumps(dict(cmd='SCAN', data={'region': 'UPPER_LIMB'})))
        self.assertTrue(reply['ok'])
        points = Contract().points('UPPER_LIMB', RAW)
        self.core.vision_message(json.dumps(result(DATA, 'SUCCEEDED', 'test missing exposure time', points)))
        self.assertEqual(self.core.result, 'FAILED')
        self.assertEqual(self.core.points, [])
        self.assertIn('采集窗口', self.core.error)

    def test_controller_heartbeat_timeout_and_mode_mismatch(self):
        self.assertTrue(self.core.vision_valid(self.now))
        self.now += 3.1
        self.assertFalse(self.core.vision_valid(self.now))
        self.health['data']['mode'] = 'REAL'
        self.core.vision_message(json.dumps(self.health))
        self.assertFalse(self.core.vision_valid(self.now))

    def test_controller_scan_timeout_detected(self):
        self.assertTrue(self.core.request(json.dumps(dict(cmd='SCAN', data={'region': 'UPPER_LIMB'})))['ok'])
        self.now += 30.1
        self.core.ui_at = self.now
        self.core.vision_message(json.dumps(self.health))
        self.core.tick()
        self.assertNotEqual(self.core.state, 'SCANNING')
        self.assertFalse(self.core.points)


class FailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_model_remains_unready(self):
        class Broken(Model):
            def open(self): raise FileNotFoundError('test model absent')
        c = Contract()
        e = Engine(Source(), Broken(), c)
        e.start()
        try:
            await asyncio.sleep(0.05)
            h = e.health()
            self.assertFalse(h['model_ready'])
            self.assertEqual(h['worker_state'], 'ERROR')
            with self.assertRaises(ValueError): e.submit(DATA, 1)
        finally:
            await asyncio.to_thread(e.close)

    async def test_stale_result_never_success(self):
        gate = threading.Event()
        e = Engine(Source(), Model(gate=gate), Contract(), stale_s=0.04)
        e.start()
        try:
            while not (e.health()['camera_connected'] and e.health()['model_ready']):
                await asyncio.sleep(0.01)
            job = e.submit(DATA, 0.15)
            while not e.model.entered.is_set(): await asyncio.sleep(0.005)
            await asyncio.sleep(0.18)
            gate.set()
            answer = await asyncio.wrap_future(job.future)
            self.assertEqual(answer['data']['status'], 'FAILED')
            self.assertEqual(answer['data']['points'], [])
        finally:
            gate.set()
            await asyncio.to_thread(e.close)


if __name__ == '__main__': unittest.main()
