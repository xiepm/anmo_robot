"""One process: bounded latest-frame capture and serialized inference threads.

Receipt time is used only for freshness, NEVER reported as exposure time.
"""
import concurrent.futures
from dataclasses import dataclass, field
import logging
import queue
import threading
import time

from .contract import result

LOG = logging.getLogger(__name__)


@dataclass
class Frame:
    payload: object
    received_at: float = 0.0
    sequence: int = 0
    metadata: dict = field(default_factory=dict)


@dataclass
class Job:
    data: dict
    deadline: float
    requested_at: float = field(default_factory=time.monotonic)
    cancel: threading.Event = field(default_factory=threading.Event)
    future: concurrent.futures.Future = field(default_factory=concurrent.futures.Future)


class Engine:
    def __init__(self, source, model, contract, stale_s=0.5, base_client=None):
        self.source, self.model, self.contract = source, model, contract
        self.mode = source.mode
        self.base_client = base_client
        self.stale_s = stale_s
        self.cv = threading.Condition()
        self.stop = threading.Event()
        self.jobs = queue.Queue(maxsize=1)
        self.latest = None
        self.camera_connected = self.model_ready = self.busy = False
        self.camera_error = self.model_error = ''
        self.current = None
        self.threads = []
        self.metrics = dict(frames=0, inferences=0, last_inference_ms=None)

    def start(self):
        for target in (self._capture, self._inference):
            t = threading.Thread(target=target, daemon=True, name=target.__name__)
            self.threads.append(t)
            t.start()

    def health(self):
        with self.cv:
            age = (time.monotonic() - self.latest.received_at) * 1000 if self.latest else None
            fresh = age is not None and age <= self.stale_s * 1000
            error = self.camera_error or self.model_error
            if not fresh and not error:
                error = '尚无新鲜帧或采集已过期'
            return dict(mode=self.mode, camera_connected=self.camera_connected and fresh,
                        model_ready=self.model_ready,
                        worker_state='ERROR' if error else ('LOCATING' if self.busy else 'IDLE'),
                        last_frame_age_ms=age, error=error)

    def diagnostics(self):
        with self.cv:
            return dict(mode=self.mode, **self.metrics,
                        frame_metadata=dict(self.latest.metadata) if self.latest else None)

    def submit(self, data, timeout):
        with self.cv:
            h = self.health()
            if self.busy:
                raise ValueError('旧任务仍在退出或推理中，不接受新任务')
            if not h['camera_connected'] or not h['model_ready'] or h['error']:
                raise ValueError(h['error'] or '相机或模型未就绪')
            job = Job(dict(data), time.monotonic() + timeout)
            self.busy, self.current = True, job
            self.jobs.put_nowait(job)
            self.cv.notify_all()
            return job

    def _capture(self):
        sequence = 0
        try:
            while not self.stop.is_set():
                try:
                    self.source.open()
                    while not self.stop.is_set():
                        frame = self.source.read()
                        if frame is None:
                            raise RuntimeError('相机未返回彩色/对齐深度帧')
                        sequence += 1
                        frame.sequence = sequence
                        frame.received_at = time.monotonic()
                        with self.cv:
                            self.latest = frame  # Single-slot cache; frames cannot accumulate.
                            self.camera_connected, self.camera_error = True, ''
                            self.metrics['frames'] += 1
                            self.cv.notify_all()
                except Exception as exc:
                    LOG.warning('采集失败: %s', exc)
                    with self.cv:
                        self.camera_connected, self.camera_error = False, str(exc)
                        self.latest = None
                        self.cv.notify_all()
                finally:
                    self.source.close()
                self.stop.wait(1)
        finally:
            with self.cv:
                self.camera_connected = False
                self.cv.notify_all()

    def _inference(self):
        try:
            self.model.open()
            with self.cv:
                self.model_ready = True
            while not self.stop.is_set():
                try:
                    job = self.jobs.get(timeout=0.1)
                except queue.Empty:
                    continue
                answer = result(job.data, 'FAILED', '超时；未获得有效人体深度坐标')
                last_sequence = -1
                try:
                    while not job.cancel.is_set() and not self.stop.is_set() and time.monotonic() < job.deadline:
                        with self.cv:
                            frame = self.latest
                            if (frame is None or frame.sequence <= last_sequence
                                    or frame.received_at < job.requested_at):
                                self.cv.wait(0.05)
                                continue
                            if time.monotonic() - frame.received_at > self.stale_s:
                                self.cv.wait(0.05)
                                continue
                        last_sequence = frame.sequence
                        started = time.monotonic()
                        raw = self.model.infer(frame, job.data)
                        with self.cv:
                            self.metrics['inferences'] += 1
                            self.metrics['last_inference_ms'] = (time.monotonic() - started) * 1000
                        if job.cancel.is_set() or time.monotonic() >= job.deadline:
                            break
                        # A stalled inference must not promote an old frame to current data.
                        if time.monotonic() - frame.received_at > self.stale_s:
                            continue
                        points = self.contract.points(job.data['region'], raw)
                        if any(p['coordinate_valid'] for p in points):
                            answer = result(job.data, 'SUCCEEDED',
                                '姿态+比例规则估算；曝光时间映射及表面法向未验证，不能用于真实运动', points)
                            if self.base_client and self.mode == 'REAL':
                                answer['data']['base_preview'] = self.base_client.project(
                                    points, frame.metadata, frame.received_at)
                                if time.monotonic() >= job.deadline:
                                    answer = result(job.data, 'FAILED', '坐标转换期间超时')
                                elif time.monotonic()-frame.received_at > self.stale_s:
                                    answer = result(job.data, 'FAILED', '坐标转换后图像已过期')
                                    continue
                            break
                    if job.cancel.is_set() or self.stop.is_set():
                        answer = result(job.data, 'CANCELED', '任务已取消')
                except Exception as exc:
                    LOG.exception('定位失败')
                    answer = result(job.data, 'FAILED', f'定位异常: {exc}')
                    with self.cv:
                        self.model_error = f'推理异常，需重启视觉进程: {exc}'
                        self.model_ready = False
                finally:
                    with self.cv:
                        self.busy, self.current = False, None
                    job.future.set_result(answer)
        except Exception as exc:
            LOG.exception('模型启动失败')
            with self.cv:
                self.model_error, self.model_ready = str(exc), False
        finally:
            if self.base_client:
                self.base_client.close()
            self.model.close()
            with self.cv:
                self.model_ready = False

    def close(self):
        self.stop.set()
        with self.cv:
            if self.current:
                self.current.cancel.set()
            self.cv.notify_all()
        for t in self.threads:
            t.join(timeout=6)
            if t.is_alive():
                LOG.error('底层调用未退出: %s；结果已失效', t.name)
