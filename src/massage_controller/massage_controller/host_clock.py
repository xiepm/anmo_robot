"""OS boot identity shared by control and vision; never estimate from wall time.

Windows uses the kernel's boot-environment GUID. If this query is unavailable,
fail closed instead of inventing an identity that could mix clock domains.
"""
import ctypes
from pathlib import Path
import sys
import uuid


def _windows_boot_id(query=None):
    # SYSTEM_BOOT_ENVIRONMENT_INFORMATION: GUID, firmware type, padding,
    # boot flags. Fixed-width fields avoid ctypes' host-dependent c_ulong.
    class BootEnvironment(ctypes.Structure):
        _fields_ = [('identifier', ctypes.c_ubyte * 16),
                    ('firmware_type', ctypes.c_uint32),
                    ('boot_flags', ctypes.c_uint64)]
    if query is None:
        query = ctypes.WinDLL('ntdll').NtQuerySystemInformation
        query.argtypes = (ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32,
                          ctypes.POINTER(ctypes.c_uint32))
        query.restype = ctypes.c_int32
    data, length = BootEnvironment(), ctypes.c_uint32()
    status = query(90, ctypes.byref(data), ctypes.sizeof(data), ctypes.byref(length))
    if status != 0 or length.value != ctypes.sizeof(data):
        raise RuntimeError('无法读取 Windows 本次启动时钟身份；拒绝混用坐标时钟域')
    result = uuid.UUID(bytes_le=bytes(data.identifier))
    if result.int == 0:
        raise RuntimeError('Windows 启动时钟身份无效')
    return str(result)


def get_host_boot_id():
    if sys.platform.startswith('linux'):
        return str(uuid.UUID(Path('/proc/sys/kernel/random/boot_id').read_text(encoding='ascii').strip()))
    if sys.platform == 'win32':
        return _windows_boot_id()
    raise RuntimeError('当前平台尚未定义统一启动时钟身份')
