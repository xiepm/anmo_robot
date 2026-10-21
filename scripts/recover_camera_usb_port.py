#!/usr/bin/env python3
"""One-shot recovery of the verified, empty camera USB port; inspect by default.

No hub/controller reset, permissions change, service restart or camera opening.
The execute path always restores through the same open sysfs file descriptor.
"""
import argparse
import json
import os
from pathlib import Path
import signal
import time


USB_DEVICES = Path('/sys/bus/usb/devices')
HUB = '1-2.1'
COMPANION = '2-2.1'
PORT = Path('1-2.1:1.0/1-2.1-port3/disable')
EXPECTED_DEVNUM = 20
SIGNALS = (signal.SIGINT, signal.SIGTERM)


class RecoveryError(RuntimeError):
    def __init__(self, message, restored=False):
        super().__init__(message)
        self.port_restored_enabled = restored


def inspect(expected_hub_devnum, root=USB_DEVICES):
    root = Path(root)
    if expected_hub_devnum != EXPECTED_DEVNUM:
        raise RecoveryError('仅允许本次已核对的 Hub devnum=20')
    hub = root / HUB
    expected = {'idVendor': '05e3', 'idProduct': '0610',
                'bDeviceClass': '09', 'maxchild': '4', 'devnum': str(expected_hub_devnum)}
    try:
        for key, value in expected.items():
            if (hub / key).read_text().strip().lower() != value:
                raise RecoveryError(f'Hub {key} 与本次核对结果不一致')
        identity = hub.stat()
        for entry in root.iterdir():
            name = entry.name
            if name.startswith(HUB + '.'):
                raise RecoveryError(f'目标 Hub 下仍有子设备：{name}')
            # The companion hub was absent when this one-shot recovery was
            # authorized. Its appearance is a topology change, even if empty.
            if name == COMPANION or name.startswith((COMPANION + '.', COMPANION + ':')):
                raise RecoveryError(f'USB3 伴随分支出现：{name}')
            vendor = entry / 'idVendor'
            if vendor.exists() and vendor.read_text().strip().lower() == '8086':
                raise RecoveryError(f'已枚举 Intel/RealSense USB 设备：{name}')
        interface = root / (HUB + ':1.0')
        for port in interface.glob(HUB + '-port*'):
            if os.path.lexists(port / 'peer'):
                raise RecoveryError(f'目标 Hub 端口仍有 USB peer：{port.name}')
        path = root / PORT
        if path.read_text().strip() != '0':
            raise RecoveryError('目标端口初始不是 enabled（disable 必须为 0）')
        port_identity = path.stat()
    except OSError as exc:
        raise RecoveryError(f'无法核对固定 USB 端口：{exc}') from exc
    return {'hub_identity': (identity.st_dev, identity.st_ino),
            'port_identity': (port_identity.st_dev, port_identity.st_ino),
            'hub_devnum': expected_hub_devnum, 'disable': '0'}


def _read(fd):
    os.lseek(fd, 0, os.SEEK_SET)
    return os.read(fd, 64).decode('ascii').strip()


def _write(fd, value):
    os.lseek(fd, 0, os.SEEK_SET)
    data = (value + '\n').encode('ascii')
    if os.write(fd, data) != len(data):
        raise OSError('USB 端口属性写入不完整')


def recover(expected_hub_devnum, execute=False, *, root=USB_DEVICES, sleep=time.sleep):
    before = inspect(expected_hub_devnum, root)
    result = dict(port=str(USB_DEVICES / PORT), hub_devnum=expected_hub_devnum,
                  port_enabled=True, camera_recovery_verified=False)
    if not execute:
        return dict(result, action='inspect', message='只读核对通过；尚未执行端口恢复')
    fd = os.open(Path(root) / PORT, os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW)
    previous_handlers = {}
    attempted = False
    failure = restore_failure = None
    def interrupted(signum, _frame):
        raise InterruptedError(f'收到 {signal.Signals(signum).name}')
    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino) != before['port_identity']:
            raise RecoveryError('打开的端口属性已经变化')
        if inspect(expected_hub_devnum, root) != before or _read(fd) != '0':
            raise RecoveryError('执行前 USB 拓扑或端口状态已经变化')
        for sig in SIGNALS:
            previous_handlers[sig] = signal.getsignal(sig)
            signal.signal(sig, interrupted)
        attempted = True
        _write(fd, '1')
        if _read(fd) != '1':
            raise RecoveryError('未确认目标端口进入 disabled 状态')
        sleep(1.)
        # Do not reopen the attribute if unplugging replaced the parent hub.
        hub = Path(root) / HUB
        current = hub.stat()
        if ((current.st_dev, current.st_ino) != before['hub_identity']
                or (hub / 'devnum').read_text().strip() != str(expected_hub_devnum)):
            raise RecoveryError('恢复期间父 Hub 已变化，禁止操作新路径')
    except BaseException as exc:
        failure = exc
    finally:
        # A second INT/TERM must not interrupt the same-fd restoration itself.
        for sig in previous_handlers:
            signal.signal(sig, signal.SIG_IGN)
        if attempted:
            try:
                _write(fd, '0')
                if _read(fd) != '0':
                    raise RecoveryError('原端口 disable 回读不是 0')
            except BaseException as exc:
                restore_failure = exc
        os.close(fd)
        for sig, handler in previous_handlers.items():
            signal.signal(sig, handler)
    if restore_failure:
        raise RecoveryError(f'无法确认原端口已恢复 enabled：{restore_failure}') from restore_failure
    if failure:
        raise RecoveryError(f'{failure}；原端口已恢复 enabled' if attempted else str(failure),
                            restored=attempted) from failure
    return dict(result, action='execute', message='原端口已恢复 enabled；仍须另行验证相机枚举与出图')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-hub-devnum', type=int, choices=[EXPECTED_DEVNUM], required=True)
    parser.add_argument('--execute', action='store_true', help='执行一次端口禁用/恢复；默认只读')
    args = parser.parse_args()
    try:
        result = recover(args.expected_hub_devnum, args.execute)
    except (RecoveryError, OSError) as exc:
        print(json.dumps(dict(ok=False, error=str(exc),
            port_restored_enabled=getattr(exc, 'port_restored_enabled', False),
            camera_recovery_verified=False), ensure_ascii=False))
        return 1
    print(json.dumps(dict(ok=True, **result), ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
