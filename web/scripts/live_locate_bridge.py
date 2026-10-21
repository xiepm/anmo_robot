"""LOCATE/CANCEL using new, unfiltered measurements from the existing camera loop.

The shared vision Session owns cancellation and disconnect barriers. No second
camera, inference process or robot SDK. Receipt time is never exposure time.
"""
import asyncio
from collections import deque
import copy
import logging
import threading
import time

from rehab_vision.client import run_client
from rehab_vision.contract import Contract, result
from rehab_vision.engine import Job


class LiveLocateBridge:
    mode = 'REAL'

    def __init__(self, uri='ws://127.0.0.1:8766/vision', clock=time.monotonic):
        self.uri, self.clock = uri, clock
        self.contract = Contract()
        self.lock = threading.RLock()
        self.received_at = None
        self.job = None
        self.closed = False
        self.camera_error = ''
        self.capture_generation = 0
        # Match the transport's bounded queue without retaining old image data.
        self.completed_captures = deque(maxlen=32)
        self.refine_snapshot = None
        self.loop = self.stop = self.thread = None

    def _retire(self):
        job = self.job
        if job and not job.future.done() and (job.cancel.is_set() or self.clock() >= job.deadline):
            status = 'CANCELED' if job.cancel.is_set() else 'FAILED'
            job.future.set_result(result(job.data, status, '定位已取消或超时'))
        if job and job.future.done():
            self.job = None

    def refine(self, data):
        with self.lock:
            if self.closed or self.job or self.refine_snapshot is None:
                raise ValueError('照片不可用于调整确认，请重新扫描')
            refine = self.refine_snapshot
        # Dense scene analysis must not hold the camera publication/health lock.
        return refine(data)

    def health(self):
        with self.lock:
            self._retire()
            age = None if self.received_at is None else max(0., self.clock()-self.received_at)*1000
            # Session availability and frame freshness are different facts. A
            # slow inference/RPC must not masquerade as a USB disconnect and
            # retire a separately frozen photo. Consumers still require age
            # <= 500 ms for scanning, confirmation and all motion operations.
            ready = not self.closed and age is not None and not self.camera_error
            return dict(mode=self.mode, camera_connected=ready, model_ready=ready,
                        worker_state=('LOCATING' if self.job else 'IDLE') if ready else 'ERROR',
                        last_frame_age_ms=age, error='' if ready else self.camera_error or '采集或推理尚未就绪')

    def camera_disconnected(self,reason):
        with self.lock:
            self.capture_generation += 1
            self.completed_captures.clear()
            self.received_at = None
            self.camera_error = '摄像头已断开，等待自动重连：'+reason
            if self.job:
                self.job.cancel.set()
                self._retire()

    def prepare_delivery(self, message):
        if message['type'] != 'LOCATE_RESULT' or message['data']['status'] != 'SUCCEEDED':
            return message
        data = message['data']
        capture = data.get('base_preview', {}).get('data', {})
        with self.lock:
            identity = (self.capture_generation, capture.get('frame_number'),
                        capture.get('frame_received_monotonic_s'))
            if not self.closed and identity in self.completed_captures:
                return message
        return result(data, 'CANCELED', '采集会话已失效，请重新扫描')

    def submit(self, data, timeout):
        with self.lock:
            self._retire()
            if data != {'region': 'BACK', 'view': 'back'}:
                raise ValueError('当前实际相机与模型配置仅支持背部背面')
            if self.job:
                raise ValueError('已有定位任务，不排队')
            health = self.health()
            if health['error']:
                raise ValueError(health['error'])
            if health['last_frame_age_ms'] is None or health['last_frame_age_ms'] > 500:
                raise ValueError('实时画面已过期，请等待新帧后再扫描')
            self.job = Job(dict(data), self.clock()+timeout, requested_at=self.clock())
            logging.info('LOCATE accepted: %s', data)
            return self.job

    def publish(self, raw_points, metadata, received_at, base_packet, snapshot_factory=None):
        with self.lock:
            if self.closed:
                return
            self.received_at = received_at
            self.camera_error = ''
            self._retire()
            job = self.job
            if not job or received_at < job.requested_at or not 0 <= self.clock()-received_at <= .5:
                return
            capture=base_packet['data']
            if capture.get('capture_time_valid') and capture['capture_window_monotonic_s']['start']<job.requested_at:
                return  # Wait for an exposure wholly after this scan request.
            points = self.contract.points(job.data['region'], raw_points)
            if not any(p['coordinate_valid'] for p in points):
                return
            answer = result(job.data, 'SUCCEEDED',
                            '本次真实采集完成；曝光时间及表面法向未验证，执行资格由控制端校验', points)
            answer['data']['base_preview'] = copy.deepcopy(base_packet)
            if capture.get('capture_time_valid'):
                window=capture['capture_window_monotonic_s']
                answer['data']['capture_time_valid']=True
                answer['data']['capture_host_monotonic_ms']=(window['start']+window['end'])*500
                answer['data']['message']='本次彩色/深度曝光已与停稳反馈配对；运动资格另行校验'
            self.contract.message.validate(answer)
            if snapshot_factory is not None:
                snapshot_factory()
            self.completed_captures.append((self.capture_generation, capture['frame_number'],
                                            capture['frame_received_monotonic_s']))
            job.future.set_result(answer)
            self.job = None
            logging.info('LOCATE_RESULT: points=%s frame=%s capture_time_valid=%s',
                         len(points), metadata.get('color_frame_number'),capture.get('capture_time_valid',False))

    async def _run(self):
        self.loop = asyncio.get_running_loop()
        self.stop = asyncio.Event()
        if self.closed:
            self.stop.set()
        await run_client(self, self.contract, self.uri, self.stop)

    def start(self):
        self.thread = threading.Thread(target=lambda: asyncio.run(self._run()),
                                       name='vision-business', daemon=True)
        self.thread.start()

    def close(self):
        with self.lock:
            self.closed = True
            self.capture_generation += 1
            self.completed_captures.clear()
            if self.job:
                self.job.cancel.set()
                self._retire()
        if self.loop and self.stop:
            try:
                self.loop.call_soon_threadsafe(self.stop.set)
            except RuntimeError:
                # The business worker may already have exited after an error.
                # Its closed loop must not interrupt the caller's other cleanup.
                if not self.loop.is_closed():
                    raise
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=4)
