"""One repository camera owner across preview, worker and diagnostics."""
from pathlib import Path
import sys

CONTROL_SOURCE = Path(__file__).resolve().parents[2] / 'src/massage_controller'
if str(CONTROL_SOURCE) not in sys.path:
    sys.path.insert(0, str(CONTROL_SOURCE))
from massage_controller.windows_lock import WindowsMutex


class CameraLock:
    def __init__(self):
        self.lock = None

    def acquire(self):
        message = '相机已被预览、视觉进程或诊断占用；请先正常退出原进程'
        if self.lock is not None:
            raise RuntimeError(message)
        if sys.platform == 'win32':
            lock = WindowsMutex(r'Global\anmo_robot_vision_worker', message)
            lock.acquire()
        else:
            import fcntl
            lock = (Path(__file__).resolve().parents[1] / '.vision_worker.lock').open('a')
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                lock.close()
                raise RuntimeError(f'{message}（{exc}）') from exc
            except BaseException:
                lock.close()
                raise
        self.lock = lock
        return self

    def close(self):
        if self.lock is not None:
            self.lock.close()
            self.lock = None
