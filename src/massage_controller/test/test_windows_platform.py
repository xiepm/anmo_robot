"""Offline native-Windows branches; fake kernel calls never access a device."""
import asyncio
import ctypes
import io
import json
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch
import uuid

from massage_controller import host_clock, node, platform_runtime, robot_lock, windows_lock
from massage_controller.capture_policy import SUPPORTED, load_capture_policy
from massage_controller.protocol import load_contract
from massage_controller.sdk_readonly import ReadOnlyReader, atomic_json


class Kernel:
    def __init__(self, result=0, create=True, release=True):
        self.result, self.create, self.release = result, create, release
        self.calls = []

    def CreateMutexW(self, security, owned, name):
        self.calls.append(('create', security, owned, name))
        return 123 if self.create else 0

    def WaitForSingleObject(self, handle, timeout):
        self.calls.append(('wait', handle, timeout))
        return self.result

    def ReleaseMutex(self, handle):
        self.calls.append(('release', handle))
        return self.release

    def CloseHandle(self, handle):
        self.calls.append(('close', handle))
        return True


class WindowsLockTests(unittest.TestCase):
    def lock(self):
        lock = robot_lock.RobotLock('synthetic-no-robot.invalid', 12345)
        self.addCleanup(lock.close)
        return lock

    def test_global_exclusion_including_same_thread_and_reacquire(self):
        api = Kernel()
        first, second = self.lock(), self.lock()
        with patch.object(robot_lock.sys, 'platform', 'win32'), \
                patch.object(windows_lock, '_windows_api', return_value=api):
            first.acquire()
            with self.assertRaisesRegex(RuntimeError, '另一个'):
                second.acquire()
            self.assertEqual(len([c for c in api.calls if c[0]=='create']), 1)
            first.close()
            second.acquire()
            second.close()
        self.assertTrue(api.calls[0][3].startswith('Global\\rehabilitation-robot-'))
        self.assertEqual(api.calls[1], ('wait', 123, 0))

    def test_other_process_timeout_query_failure_and_permission_denial_fail_closed(self):
        for api, error in [(Kernel(result=0x102), RuntimeError),
                           (Kernel(result=0xffffffff), OSError), (Kernel(create=False), OSError)]:
            with self.subTest(api=api):
                lock = self.lock()
                with patch.object(robot_lock.sys, 'platform', 'win32'), \
                        patch.object(windows_lock, '_windows_api', return_value=api):
                    with self.assertRaises(error): lock.acquire()
                    self.assertIsNone(lock.mutex.handle)
                    self.assertNotIn(lock.mutex.name, windows_lock._claims)
                    if api.create: self.assertEqual(api.calls[-1], ('close', 123))
                    api.result, api.create = 0, True
                    lock.acquire()
                    lock.close()

    def test_abandoned_owner_is_rejected_and_handle_released(self):
        lock, api = self.lock(), Kernel(result=0x80)
        with patch.object(robot_lock.sys, 'platform', 'win32'), \
                patch.object(windows_lock, '_windows_api', return_value=api):
            with self.assertRaisesRegex(RuntimeError, '异常退出'): lock.acquire()
        self.assertEqual(api.calls[-2:], [('release',123), ('close',123)])
        self.assertNotIn(lock.mutex.name, windows_lock._claims)

    def test_abandoned_owner_release_failure_stays_exclusive(self):
        lock, api = self.lock(), Kernel(result=0x80, release=False)
        with patch.object(robot_lock.sys, 'platform', 'win32'), \
                patch.object(windows_lock, '_windows_api', return_value=api):
            with self.assertRaisesRegex(OSError, '释放'): lock.acquire()
            self.assertIn(lock.mutex.name, windows_lock._claims)
            with self.assertRaises(RuntimeError): self.lock().acquire()
            self.assertNotIn(('close',123), api.calls)
            api.release = True
            lock.close()

    def test_release_requires_owner_and_failure_does_not_remove_claim(self):
        lock, api = self.lock(), Kernel()
        with patch.object(robot_lock.sys, 'platform', 'win32'), \
                patch.object(windows_lock, '_windows_api', return_value=api):
            lock.acquire()
            with patch.object(windows_lock.threading, 'get_ident', return_value=-1):
                with self.assertRaisesRegex(RuntimeError, '所在线程|获取锁的线程'): lock.close()
            api.release = False
            with self.assertRaisesRegex(OSError, '释放'): lock.close()
            self.assertIn(lock.mutex.name, windows_lock._claims)
            with self.assertRaises(RuntimeError): self.lock().acquire()
            api.release = True
            lock.close()

    def test_api_unavailable_never_calls_sdk_connect(self):
        reader = ReadOnlyReader.__new__(ReadOnlyReader)
        reader.robot_lock, reader.sdk = self.lock(), Mock()
        with patch.object(robot_lock.sys, 'platform', 'win32'), \
                patch.object(windows_lock, '_windows_api', side_effect=OSError('unavailable')):
            with self.assertRaises(OSError): reader.connect()
        reader.sdk.HRIF_Connect.assert_not_called()
        self.assertNotIn(reader.robot_lock.mutex.name, windows_lock._claims)

    def test_unsupported_platform_never_uses_an_unlocked_fallback(self):
        with patch.object(robot_lock.sys, 'platform', 'unknown'):
            with self.assertRaisesRegex(RuntimeError, '拒绝连接'): self.lock().acquire()

    @unittest.skipUnless(sys.platform.startswith('linux'), 'Linux flock regression')
    def test_linux_lock_still_excludes_other_file_description(self):
        first, second = self.lock(), self.lock()
        first.acquire()
        with self.assertRaises(RuntimeError): second.acquire()
        first.close(); second.acquire(); second.close()

    @unittest.skipUnless(sys.platform == 'win32', 'Runs real Win32 lock API on Windows CI only')
    def test_native_windows_mutex_excludes_another_process(self):
        name = 'Global\\rehabilitation-offline-test-' + uuid.uuid4().hex
        lock = windows_lock.WindowsMutex(name)
        child = ('import sys; from massage_controller.windows_lock import WindowsMutex; '
                 'lock=WindowsMutex(sys.argv[1]); lock.acquire(); lock.close(); print("released")')
        lock.acquire()
        try:
            busy = subprocess.run([sys.executable, '-c', child, name], capture_output=True, timeout=10)
            self.assertNotEqual(busy.returncode, 0)
        finally:
            lock.close()
        free = subprocess.run([sys.executable, '-c', child, name], capture_output=True, timeout=10)
        self.assertEqual(free.returncode, 0, free.stderr)
        self.assertIn(b'released', free.stdout)


class BootIdentityTests(unittest.TestCase):
    def query(self, identity, status=0, length=None):
        def query(info, data, size, returned):
            self.assertEqual(info, 90)
            self.assertEqual(size, 32)
            data._obj.identifier[:] = identity.bytes_le
            returned._obj.value = size if length is None else length
            return status
        return query

    def test_windows_query_has_same_uuid_across_independent_calls(self):
        identity = uuid.UUID('af530b09-2345-4123-9824-fedcba987654')
        self.assertEqual(host_clock._windows_boot_id(self.query(identity)), str(identity))
        self.assertEqual(host_clock._windows_boot_id(self.query(identity)), str(identity))

    def test_query_failure_bad_length_or_empty_guid_never_estimates_boot_time(self):
        for query in (self.query(uuid.uuid4(), status=-1),
                      self.query(uuid.uuid4(), length=16), self.query(uuid.UUID(int=0))):
            with self.assertRaises(RuntimeError): host_clock._windows_boot_id(query)

    def test_windows_dispatch_and_linux_identity_are_separate(self):
        with patch.object(host_clock.sys, 'platform', 'win32'), \
                patch.object(host_clock, '_windows_boot_id', return_value='same-kernel-id') as query, \
                patch.object(Path, 'read_text', side_effect=AssertionError('/proc on Windows')):
            self.assertEqual(host_clock.get_host_boot_id(), 'same-kernel-id')
            query.assert_called_once_with()
        with patch.object(host_clock.sys, 'platform', 'linux'), \
                patch.object(Path, 'read_text', return_value='af530b09-2345-4123-9824-fedcba987654\n'):
            self.assertEqual(host_clock.get_host_boot_id(), 'af530b09-2345-4123-9824-fedcba987654')

    @unittest.skipUnless(sys.platform == 'win32', 'Runs real kernel boot query on Windows CI only')
    def test_native_windows_boot_id_matches_a_separate_python_process(self):
        own = host_clock.get_host_boot_id()
        child = subprocess.run([sys.executable, '-c',
                'from massage_controller.host_clock import get_host_boot_id; print(get_host_boot_id())'],
                capture_output=True, text=True, timeout=10)
        self.assertEqual(child.returncode, 0, child.stderr)
        self.assertEqual(child.stdout.strip(), own)


class SignalsAndConsoleTests(unittest.TestCase):
    def test_windows_signal_dispatch_restores_handlers_and_never_calls_asyncio_signal_api(self):
        loop, stop = Mock(), Mock()
        registrations = {}
        def register(sig, handler): registrations[sig] = handler
        with patch.object(platform_runtime.sys, 'platform', 'win32'), \
                patch.object(signal, 'getsignal', return_value=signal.SIG_DFL), \
                patch.object(signal, 'signal', side_effect=register):
            handlers = platform_runtime.StopSignals(loop, stop)
            registrations[signal.SIGINT](signal.SIGINT, None)
            loop.call_soon_threadsafe.assert_called_once_with(stop)
            loop.add_signal_handler.assert_not_called()
            stop.assert_not_called()
            handlers.close()
            self.assertTrue(all(value==signal.SIG_DFL for value in registrations.values()))

    def test_linux_uses_loop_handlers_and_restores_them(self):
        loop, stop = Mock(), Mock()
        with patch.object(platform_runtime.sys, 'platform', 'linux'), \
                patch.object(signal, 'getsignal', return_value=signal.SIG_IGN), \
                patch.object(signal, 'signal') as restore:
            handlers = platform_runtime.StopSignals(loop, stop)
            self.assertEqual(loop.add_signal_handler.call_count, 2)
            handlers.close()
            self.assertEqual(loop.remove_signal_handler.call_count, 2)
            self.assertEqual(restore.call_count, 2)

    def test_failed_signal_registration_rolls_back_before_any_worker_starts(self):
        loop = Mock()
        loop.add_signal_handler.side_effect = [None, RuntimeError('unavailable')]
        with patch.object(platform_runtime.sys, 'platform', 'linux'), \
                patch.object(signal, 'getsignal', return_value=signal.SIG_DFL), \
                patch.object(signal, 'signal') as restore:
            with self.assertRaises(RuntimeError): platform_runtime.StopSignals(loop, Mock())
            loop.remove_signal_handler.assert_called_once_with(signal.SIGINT)
            restore.assert_called_once_with(signal.SIGINT, signal.SIG_DFL)

    def test_windows_console_partial_start_backspace_special_keys_and_stop(self):
        chars, echoes = list('STA'), []
        console = types.SimpleNamespace(kbhit=lambda: bool(chars), getwch=lambda: chars.pop(0), putwch=echoes.append)
        lines = platform_runtime.ConsoleLines(io.StringIO())
        with patch.object(platform_runtime.sys, 'platform', 'win32'), patch.dict(sys.modules, {'msvcrt':console}):
            self.assertEqual(lines.poll(), [])
            chars.extend('X\bRT\r\xe0HSTOP\r')
            self.assertEqual(lines.poll(), ['START\n', 'STOP\n'])
            chars.extend('STA\x03')
            self.assertEqual(lines.poll(), ['STOP\n'])
            self.assertEqual(lines.buffer, '')

    def test_controller_closes_transport_before_restoring_signal_handlers(self):
        events = []
        class FakeSignals:
            def __init__(self, loop, callback):
                events.append('handlers')
                loop.call_soon(callback)
            def close(self): events.append('restore')
        class FakeServer:
            def __init__(self, *args, **kwargs): pass
            async def start(self): events.append('start')
            async def close(self): events.append('close')
        args = types.SimpleNamespace(backend='simulation-test', noncontact_config=None,
                    config='unused', ui_port=8765, vision_port=8766, hand_eye=None)
        with patch.object(node, 'StopSignals', FakeSignals), patch.object(node, 'Server', FakeServer), \
                patch.object(node, 'load_contract', return_value={}):
            asyncio.run(node.run(args))
        self.assertEqual(events, ['handlers','start','close','restore'])


class PortableConfigurationTests(unittest.TestCase):
    def test_utf8_bom_contract_and_utf8_output_work_in_chinese_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'中文配置'; path.mkdir()
            for name in ('acupoints','simulation','ui_request.schema','vision_message.schema'):
                (path/(name+'.json')).write_text('{"说明":"中文"}', encoding='utf-8-sig')
            self.assertEqual(load_contract(path)['acupoints'], {'说明':'中文'})
            out = path/'状态.json'
            atomic_json(out, {'状态':'已停止'})
            self.assertEqual(json.loads(out.read_bytes()), {'状态':'已停止'})

    def test_windows_does_not_reuse_linux_verified_exposure_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'policy.json'
            path.write_text(json.dumps(dict(SUPPORTED, version=1, verified=True,
                                           camera_serial='SYNTHETIC-TEST-CAMERA')), encoding='utf-8-sig')
            with patch('massage_controller.capture_policy.sys.platform', 'win32'):
                with self.assertRaisesRegex(ValueError, 'Windows.*尚未验证'): load_capture_policy(path)
            with patch('massage_controller.capture_policy.sys.platform', 'linux'):
                self.assertTrue(load_capture_policy(path)['verified'])


if __name__ == '__main__': unittest.main()
