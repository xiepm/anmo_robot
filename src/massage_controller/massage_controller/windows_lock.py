"""Named Windows exclusion shared by device owners, with no SDK dependency."""
import threading


_claims = set()
_claims_lock = threading.Lock()


def _windows_api():
    import ctypes
    from ctypes import wintypes
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
    api.CreateMutexW.restype = wintypes.HANDLE
    api.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    api.WaitForSingleObject.restype = wintypes.DWORD
    api.ReleaseMutex.argtypes = (wintypes.HANDLE,)
    api.ReleaseMutex.restype = wintypes.BOOL
    api.CloseHandle.argtypes = (wintypes.HANDLE,)
    api.CloseHandle.restype = wintypes.BOOL
    return api


class WindowsMutex:
    def __init__(self, name, busy_message='另一个进程已占用此设备资源'):
        if not name.startswith('Global\\') or '\\' in name[7:] or not name[7:]:
            raise ValueError('设备锁必须使用 Global 命名空间中的独立名称')
        self.name, self.busy_message = name, busy_message
        self.handle = self.api = self.owner = None

    def acquire(self):
        if self.handle is not None:
            raise RuntimeError(self.busy_message)
        # A named Win32 mutex is recursive for its owning thread. Prevent a
        # second reader in this process from exploiting that recursion.
        with _claims_lock:
            if self.name in _claims:
                raise RuntimeError(self.busy_message)
            _claims.add(self.name)
        handle = None
        try:
            api = _windows_api()
            handle = api.CreateMutexW(None, False, self.name)
            if not handle:
                raise OSError('无法创建全局设备独占锁，拒绝访问设备')
            result = api.WaitForSingleObject(handle, 0)
            if result == 0x102:  # WAIT_TIMEOUT: another process owns it.
                raise RuntimeError(self.busy_message)
            if result == 0x80:  # WAIT_ABANDONED grants ownership, but no SDK use.
                self.api, self.handle, self.owner = api, handle, threading.get_ident()
                self.close()
                handle = None
                raise RuntimeError('上一设备进程异常退出；本次连接已拒绝，请核对设备状态后重试')
            if result != 0:
                raise OSError(f'设备独占锁查询失败（{result:#x}），拒绝访问设备')
            self.api, self.handle, self.owner = api, handle, threading.get_ident()
        except BaseException:
            if self.handle is None:
                if handle:
                    api.CloseHandle(handle)
                with _claims_lock:
                    _claims.discard(self.name)
            raise

    def close(self):
        if self.handle is not None:
            if self.owner != threading.get_ident():
                raise RuntimeError('设备独占锁必须由获取锁的线程释放')
            if not self.api.ReleaseMutex(self.handle):
                # Keep the handle and local claim on failure, never pretend
                # another SDK client can safely take ownership.
                raise OSError('无法释放设备独占锁')
            self.api.CloseHandle(self.handle)
            self.handle = self.api = self.owner = None
            with _claims_lock:
                _claims.discard(self.name)
