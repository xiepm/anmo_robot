"""Cross-process ownership for this workspace's SDK clients (host and runtime)."""
import fcntl
import hashlib
from pathlib import Path


class RobotLock:
    def __init__(self, host, port):
        key = hashlib.sha256(f'{host}:{port}'.encode()).hexdigest()[:20]
        self.path = Path('/tmp') / f'rehabilitation-robot-{key}.lock'
        self.file = None

    def acquire(self):
        if self.file is not None:
            raise RuntimeError('Robot session already owned by this client')
        handle = self.path.open('a')
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            raise RuntimeError('另一个本项目 SDK 进程已连接该机械臂；先退出已有读取/控制进程') from None
        self.file = handle

    def close(self):
        if self.file is not None:
            self.file.close()
            self.file = None
