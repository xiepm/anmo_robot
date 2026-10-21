"""Loopback WebSocket transports with bounded per-peer outbound queues."""
import asyncio
import json
import logging
import time
import copy
from collections import deque
from websockets.legacy.server import serve
from websockets.exceptions import ConnectionClosed
from .core import Controller
from .protocol import decode
from pathlib import Path

LOG = logging.getLogger('control_node')


class Server:
    def __init__(self, contract, ui_port=8765, vision_port=8766, device=None, calibration=None):
        self.core = Controller(contract, time.monotonic, device=device)
        self.ui_port, self.vision_port = ui_port, vision_port
        self.ui = self.vision = None
        self.tasks = []
        self.servers = []
        self.last_status = None
        self.base_projector = None
        self.last_base_coordinates = None
        self.coordinate_history = deque(maxlen=64)
        self.core.snapshot_verified = lambda packet: any(packet == value for value in self.coordinate_history)
        if calibration:
            from .base_coordinates import BaseProjector
            self.base_projector = BaseProjector(calibration)
            self.core.confirmation.projector = self.base_projector
        schema = Path(__file__).resolve().parents[3] / 'config/rehab_protocol/base_coordinates_request.schema.json'
        if not schema.exists():
            from ament_index_python.packages import get_package_share_directory
            schema = Path(get_package_share_directory('massage_controller')) / 'rehab_protocol/base_coordinates_request.schema.json'
        self.base_request_schema = json.loads(schema.read_text(encoding='utf-8-sig'))

    async def coordinates(self, ws):
        """Same control process/SDK owner; this endpoint never calls motion APIs."""
        if self.base_projector is None or self.core.mode != 'REAL':
            await ws.close(1008, 'real calibration unavailable')
            return
        try:
            async for raw in ws:
                request = decode(raw, self.base_request_schema)
                deadline = time.monotonic()+.35
                while True:
                    answer = self.base_projector.project(request, self.core.device.worker.history(), time.monotonic())
                    sample = self.core.device.worker.snapshot()
                    if sample and not sample.get('valid'):
                        answer['data']['error'] = '机械臂反馈不可用: '+sample.get('error', '无有效反馈')
                        break
                    if answer['data']['error'] != '等待图像前后停稳反馈' or time.monotonic() >= deadline:
                        break
                    await asyncio.sleep(.02)
                self.last_base_coordinates = answer
                self.coordinate_history.append(copy.deepcopy(answer))
                await asyncio.wait_for(ws.send(json.dumps(answer, ensure_ascii=False, allow_nan=False)), .3)
        except (ConnectionClosed, asyncio.TimeoutError):
            pass
        except (ValueError, TypeError, KeyError):
            await ws.close(1008, 'invalid coordinate request')

    def emit(self, peer, msg):
        if peer:
            try:
                peer[1].put_nowait(json.dumps(msg, ensure_ascii=False, allow_nan=False))
            except asyncio.QueueFull:
                asyncio.create_task(peer[0].close(1013, 'slow consumer'))
                if peer is self.ui:
                    self.core.ui_lost()

    async def writer(self, peer):
        while True:
            message = await peer[1].get()
            await asyncio.wait_for(peer[0].send(message), 0.2)

    def publish(self):
        msg = self.core.status()
        self.emit(self.ui, msg)
        self.last_status = msg

    async def connection(self, ws, path, kind):
        expected = '/' if kind == 'ui' else '/vision'
        if path != expected or getattr(self, kind) is not None:
            await ws.close(1008, 'wrong path or occupied')
            return
        peer = (ws, asyncio.Queue(maxsize=32))
        setattr(self, kind, peer)
        if kind == 'vision':
            self.core.vision_connected = True
        else:
            self.core.ui_at = time.monotonic()
            self.publish()
        writer = asyncio.create_task(self.writer(peer))
        def writer_done(task):
            if not task.cancelled() and task.exception():
                asyncio.create_task(ws.close(1011, 'send timeout'))
        writer.add_done_callback(writer_done)
        try:
            async for raw in ws:
                if kind == 'ui':
                    response = self.core.request(raw)
                    self.emit(peer, response)
                    if response['type'] != 'STATUS':
                        LOG.info('request=%s ok=%s state=%s', response['cmd'], response['ok'], self.core.state)
                        self.publish()
                else:
                    try:
                        self.core.vision_message(raw)
                    except (ValueError, TypeError, KeyError) as exc:
                        LOG.warning('invalid vision message: %s', exc)
                        await ws.close(1008, 'invalid vision message')
                        break
                    self.publish()
                self.flush_vision()
        except ConnectionClosed:
            pass
        finally:
            writer.cancel()
            await asyncio.gather(writer, return_exceptions=True)
            if getattr(self, kind) is peer:
                setattr(self, kind, None)
                if kind == 'ui': self.core.ui_lost()
                else: self.core.vision_lost()
                self.publish()

    def flush_vision(self):
        while self.core.outbox:
            self.emit(self.vision, self.core.outbox.pop(0))

    async def loop(self):
        last_publish = 0.0
        while True:
            self.core.tick()
            self.flush_vision()
            now = time.monotonic()
            status = self.core.status()
            # Discrete transitions immediate; countdown still has the periodic push.
            if self.last_status is None or any(status['data'][k] != self.last_status['data'][k]
                    for k in ('state','step','error','positioning_valid','result')) or now-last_publish >= 0.2:
                self.publish()
                last_publish = now
            if self.core.close_vision:
                self.core.close_vision = False
                if self.vision:
                    asyncio.create_task(self.vision[0].close(1008, 'cancel timeout'))
            await asyncio.sleep(0.02)

    async def start(self):
        async def ui(ws, path): await self.connection(ws, path, 'ui')
        async def vision(ws, path):
            if path == '/coordinates':
                await self.coordinates(ws)
            else:
                await self.connection(ws, path, 'vision')
        try:
            for handler, port in ((ui,self.ui_port),(vision,self.vision_port)):
                self.servers.append(await serve(handler, '127.0.0.1', port, max_size=65536,
                                               max_queue=16, close_timeout=0.3))
        except Exception:
            await self.close()
            raise
        self.tasks.append(asyncio.create_task(self.loop()))
        LOG.info('%s backend: UI=%s vision=%s', self.core.mode, self.ui_port, self.vision_port)

    async def close(self):
        self.core.stop('控制进程退出')
        # Continue polling while waiting for simulated stop feedback.
        deadline = time.monotonic()+self.core.cfg['stop_timeout_s']+0.1
        while self.core.state == 'STOPPING' and time.monotonic() < deadline:
            self.core.tick()
            await asyncio.sleep(0.02)
        for s in self.servers: s.close()
        for s in self.servers: await s.wait_closed()
        for t in self.tasks: t.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
