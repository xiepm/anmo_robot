"""Async transport owns all logical terminal states, independently of inference."""
import asyncio
import json
import logging
import time
from websockets.legacy.client import connect
from websockets.exceptions import ConnectionClosed

from .contract import reply, result

LOG = logging.getLogger(__name__)


class Session:
    def __init__(self, engine, contract, timeout=30.0):
        self.engine, self.contract, self.timeout = engine, contract, timeout
        self.outgoing = asyncio.Queue(maxsize=32)
        self.active = None
        self.pending = set()

    def emit(self, msg):
        prepare = getattr(self.engine, 'prepare_delivery', None)
        if prepare is not None:
            msg = prepare(msg)
        self.contract.message.validate(msg)
        self.outgoing.put_nowait(json.dumps(msg, ensure_ascii=False, allow_nan=False))

    async def handle(self, raw):
        # Malformed messages close the connection: the shared schema cannot
        # represent an error REPLY for an unknown/non-string command.
        msg = self.contract.decode(raw)
        cmd, data = msg['cmd'], msg['data']
        if cmd == 'REFINE_POINTS':
            LOG.info('REFINE_POINTS received: revision=%s scene=%s', data['revision'], 'scene_context' in data)
            answer = {k:data[k] for k in ('revision','frame_number','received_monotonic_s')}
            try:
                if self.active or not hasattr(self.engine, 'refine'):
                    raise ValueError('当前视觉进程不支持照片调整确认或正在采集')
                refined = await asyncio.to_thread(self.engine.refine, data)
                if isinstance(refined, dict):
                    answer.update(ok=True, error='', points=refined['points'], scene=refined['scene'])
                else:
                    answer.update(ok=True, error='', points=refined)
            except (ValueError, KeyError, TypeError) as exc:
                answer.update(ok=False, error=str(exc), points=[])
            self.emit(dict(type='REFINE_RESULT',data=answer))
            LOG.info('REFINE_RESULT: revision=%s ok=%s error=%s', data['revision'], answer['ok'], answer['error'])
            return
        if cmd == 'CANCEL':
            job = self.active
            self.emit(reply(cmd, True, '取消请求已受理' if job else '当前没有定位任务'))
            if job:
                self.active = None  # Invalidate before yielding or publishing anything.
                job.cancel.set()
                self.emit(result(job.data, 'CANCELED', '任务已取消，迟到结果将丢弃'))
            return
        if self.active:
            self.emit(reply(cmd, False, '已有定位任务，不排队'))
            return
        try:
            job = self.engine.submit(data, self.timeout)
        except ValueError as exc:
            self.emit(reply(cmd, False, str(exc)))
            return
        self.active = job
        self.emit(reply(cmd, True, '定位请求已受理'))
        task = asyncio.create_task(self.finish(job))
        self.pending.add(task)
        # Keep references until teardown so any publication error is observed.

    async def finish(self, job):
        try:
            answer = await asyncio.wait_for(asyncio.shield(asyncio.wrap_future(job.future)), self.timeout)
        except asyncio.TimeoutError:
            job.cancel.set()
            answer = result(job.data, 'FAILED', '定位超时；旧推理结果已失效')
        if self.active is job:
            self.active = None
            self.emit(answer)

    async def _send(self, ws, raw):
        prepare = getattr(self.engine, 'prepare_delivery', None)
        if prepare is not None:
            # A capture can be invalidated after its success was serialized and
            # queued. Recheck inside the sending coroutine, just before ws.send.
            msg = prepare(json.loads(raw))
            self.contract.message.validate(msg)
            raw = json.dumps(msg, ensure_ascii=False, allow_nan=False)
        await ws.send(raw)

    async def writer(self, ws):
        while True:
            raw = await self.outgoing.get()
            await asyncio.wait_for(self._send(ws, raw), 0.5)

    async def receiver(self, ws):
        async for raw in ws:
            await self.handle(raw)

    async def health(self):
        previous, sent = None, 0.0
        while True:
            d = self.engine.health()
            # Publish freshness crossings immediately even when the camera
            # session remains connected. 500 ms pauses operations; 1.5 s is
            # the preview capture-gap boundary that retires frozen photos.
            key = tuple(d[k] for k in ('mode', 'camera_connected', 'model_ready', 'worker_state', 'error'))
            age = d['last_frame_age_ms']
            key += (age is not None and age <= 500, age is not None and age < 1500)
            now = time.monotonic()
            if key != previous or now - sent >= 1.0:
                self.emit(dict(type='VISION_STATUS', data=d))
                previous, sent = key, now
            for task in list(self.pending):
                if task.done():
                    self.pending.remove(task)
                    task.result()  # Publication failures close this session too.
            await asyncio.sleep(0.02)

    async def run(self, ws):
        tasks = [asyncio.create_task(fn()) for fn in
                 (lambda: self.writer(ws), lambda: self.receiver(ws), self.health)]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for t in done:
                t.result()
        finally:
            # Session-local queue and tasks are discarded, never replayed on reconnect.
            if self.active:
                self.active.cancel.set()
                self.active = None
            for task in tasks + list(self.pending):
                task.cancel()
            await asyncio.gather(*tasks, *self.pending, return_exceptions=True)


async def run_client(engine, contract, uri, stop, timeout=30.0):
    last_wait_log = float('-inf')
    while not stop.is_set():
        connected = False
        try:
            async with connect(uri, max_size=65536, max_queue=16, open_timeout=2,
                               close_timeout=0.3, ping_interval=1, ping_timeout=2) as ws:
                connected = True
                last_wait_log = float('-inf')
                LOG.info('已连接控制端 %s；mode=%s', uri, engine.mode)
                session = asyncio.create_task(Session(engine, contract, timeout).run(ws))
                stopping = asyncio.create_task(stop.wait())
                try:
                    done, _ = await asyncio.wait([session, stopping], return_when=asyncio.FIRST_COMPLETED)
                    if session in done:
                        await session
                finally:
                    session.cancel()
                    stopping.cancel()
                    await asyncio.gather(session, stopping, return_exceptions=True)
        except (OSError, ConnectionClosed, TimeoutError, ValueError, asyncio.QueueFull) as exc:
            if connected:
                LOG.warning('控制连接已断开，本连接任务已失效: %s', exc)
            elif time.monotonic() - last_wait_log >= 10:
                LOG.warning('等待控制服务 %s；请先启动 control_node。'
                            '每秒自动重连，此提示每10秒显示一次。原因: %s', uri, exc)
                last_wait_log = time.monotonic()
        if not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), 1)
            except asyncio.TimeoutError:
                pass
