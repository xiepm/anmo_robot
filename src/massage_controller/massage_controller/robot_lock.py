"""Cross-process ownership for this workspace's SDK clients (host and runtime)."""
import hashlib
from pathlib import Path
import sys
from .windows_lock import WindowsMutex


BUSY = '另一个本项目 SDK 进程已连接该机械臂；先退出已有读取/控制进程'


class RobotLock:
    def __init__(self, host, port):
        key = hashlib.sha256(f'{host}:{port}'.encode()).hexdigest()[:20]
        self.path = Path('/tmp') / f'rehabilitation-robot-{key}.lock'
        self.mutex = WindowsMutex('Global\\rehabilitation-robot-' + key, BUSY)
        self.file = None

    def acquire(self):
        if self.file is not None:
            raise RuntimeError('Robot session already owned by this client')
        if sys.platform == 'win32':
            return self.mutex.acquire()
        if not sys.platform.startswith('linux'):
            raise RuntimeError('当前平台尚无经过定义的机械臂独占锁，拒绝连接 SDK')
        import fcntl
        handle = self.path.open('a')
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            if isinstance(exc, BlockingIOError):
                raise RuntimeError(BUSY) from None
            raise
        self.file = handle

    def close(self):
        self.mutex.close()
        if self.file is not None:
            self.file.close()
            self.file = None
