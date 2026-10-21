"""Keep the existing camera owner alive across USB removal and re-enumeration.

No second pipeline, inference worker, or queued frame is created. Callbacks run
on the camera thread, while HTTP and vision health remain independently alive.
"""
import logging
import threading
import time


class CameraRecovery:
    def __init__(self, open_camera, read_camera, close_camera, disconnected,
                 stop=None, retry_s=1., clock=time.monotonic, reset_camera=None):
        self.open_camera, self.read_camera = open_camera, read_camera
        self.close_camera, self.disconnected = close_camera, disconnected
        self.stop = stop if stop is not None else threading.Event()
        self.retry_s, self.clock = retry_s, clock
        self.opened = False
        self.generation = 0
        self.last_log = float('-inf')
        # Optional device-specific recovery. False means no device was present,
        # so no hardware reset was requested and the cooldown must not start.
        self.reset_camera = reset_camera
        self.consecutive_failures = 0
        self.reset_in_failure_cycle = False
        self.last_reset_attempt = float('-inf')

    def _try_reset(self):
        if (self.reset_camera is None or self.stop.is_set()
                or self.consecutive_failures < 3 or self.reset_in_failure_cycle
                or self.clock() - self.last_reset_attempt < 30.):
            return
        try:
            requested = self.reset_camera()
        except (RuntimeError, OSError) as exc:
            # The device may have accepted the request before raising, so even
            # unsuccessful attempts must be rate limited. Other errors escape.
            self.last_reset_attempt = self.clock()
            logging.warning('相机复位未完成，将继续等待重连: %s', exc)
            return
        if requested:
            self.last_reset_attempt = self.clock()
            self.reset_in_failure_cycle = True
            logging.warning('相机连续采集失败，已请求设备复位并等待重新连接')

    def _lost(self, reason):
        self.opened = False
        self.disconnected(reason)
        try:
            self.close_camera()
        except (RuntimeError, OSError) as exc:
            logging.debug('释放断开的相机: %s', exc)
        self.consecutive_failures += 1
        self._try_reset()
        if self.clock()-self.last_log >= 10:
            logging.warning('摄像头不可用，每秒自动重连: %s', reason)
            self.last_log = self.clock()

    def ensure_open(self):
        while not self.opened:
            if self.stop.is_set(): raise KeyboardInterrupt
            try:
                self.open_camera()
                self.opened = True
                self.generation += 1
                self.last_log = float('-inf')
                logging.info('相机已连接，等待新画面；采集会话 %s', self.generation)
            except (RuntimeError, OSError) as exc:
                self._lost(str(exc))
                if self.stop.wait(self.retry_s): raise KeyboardInterrupt

    def read(self):
        while not self.stop.is_set():
            self.ensure_open()
            try:
                ok, frame = self.read_camera()
                if ok and frame is not None:
                    self.consecutive_failures = 0
                    self.reset_in_failure_cycle = False
                    return True, frame
                reason = '未收到有效彩色/深度帧'
            except (RuntimeError, OSError) as exc:
                reason = str(exc)
            self._lost(reason)
            if self.stop.wait(self.retry_s): raise KeyboardInterrupt
        raise KeyboardInterrupt
