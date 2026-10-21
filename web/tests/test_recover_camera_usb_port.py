"""Fake sysfs only: never touches USB devices, services or the camera SDK."""
import importlib.util
from pathlib import Path
import signal
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[2] / 'scripts/recover_camera_usb_port.py'
spec = importlib.util.spec_from_file_location('recover_camera_usb_port', SCRIPT)
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)


class PortRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.hub = self.root / recovery.HUB
        self.hub.mkdir()
        for key, value in {'idVendor': '05e3', 'idProduct': '0610',
                           'bDeviceClass': '09', 'maxchild': '4', 'devnum': '20'}.items():
            (self.hub / key).write_text(value + '\n')
        self.port = self.root / recovery.PORT
        self.port.parent.mkdir(parents=True)
        self.port.write_text('0\n')

    def run_recovery(self, **kwargs):
        return recovery.recover(20, root=self.root, **kwargs)

    def test_default_is_readonly_and_execute_restores_same_fd(self):
        with patch.object(recovery, '_write', wraps=recovery._write) as write:
            result = self.run_recovery()
            write.assert_not_called()
            self.assertEqual(result['action'], 'inspect')
            def wait(seconds):
                self.assertEqual(seconds, 1.)
                self.assertEqual(self.port.read_text(), '1\n')
            result = self.run_recovery(execute=True, sleep=wait)
            self.assertEqual([call.args[1] for call in write.call_args_list], ['1', '0'])
            self.assertEqual(write.call_args_list[0].args[0], write.call_args_list[1].args[0])
        self.assertEqual(self.port.read_text(), '0\n')
        self.assertTrue(result['port_enabled'])
        self.assertFalse(result['camera_recovery_verified'])

    def test_hub_identity_and_devnum_scope_guards(self):
        with self.assertRaises(recovery.RecoveryError):
            recovery.recover(21, execute=True, root=self.root)
        for key, bad in [('idVendor', '0000'), ('idProduct', '0000'),
                         ('bDeviceClass', '00'), ('maxchild', '7'), ('devnum', '21')]:
            path = self.hub / key
            old = path.read_text()
            with self.subTest(key=key):
                path.write_text(bad)
                with self.assertRaises(recovery.RecoveryError):
                    self.run_recovery(execute=True)
                self.assertEqual(self.port.read_text(), '0\n')
            path.write_text(old)

    def test_initially_disabled_port_is_not_enabled_by_cleanup(self):
        self.port.write_text('1\n')
        with patch.object(recovery, '_write') as write:
            with self.assertRaises(recovery.RecoveryError):
                self.run_recovery(execute=True)
            write.assert_not_called()
        self.assertEqual(self.port.read_text(), '1\n')

    def test_any_child_companion_or_peer_blocks(self):
        for name in ['1-2.1.3', '1-2.1.4.1', '2-2.1', '2-2.1.3']:
            with self.subTest(name=name):
                path = self.root / name
                path.mkdir()
                with self.assertRaises(recovery.RecoveryError):
                    self.run_recovery(execute=True)
                path.rmdir()
        peer = self.port.parent / 'peer'
        peer.symlink_to(self.root / 'missing-peer')
        with self.assertRaises(recovery.RecoveryError):
            self.run_recovery(execute=True)
        self.assertEqual(self.port.read_text(), '0\n')

    def test_already_enumerated_realsense_on_other_port_blocks(self):
        device = self.root / '3-7'
        device.mkdir()
        (device / 'idVendor').write_text('8086\n')
        (device / 'idProduct').write_text('0b3a\n')
        with self.assertRaisesRegex(recovery.RecoveryError, 'RealSense'):
            self.run_recovery(execute=True)
        self.assertEqual(self.port.read_text(), '0\n')

    def test_error_during_wait_restores_and_does_not_claim_camera_recovered(self):
        def wait(_):
            raise OSError('test wait failure')
        with self.assertRaises(recovery.RecoveryError) as caught:
            self.run_recovery(execute=True, sleep=wait)
        self.assertTrue(caught.exception.port_restored_enabled)
        self.assertEqual(self.port.read_text(), '0\n')

    def test_sigint_and_sigterm_restore_and_restore_handlers(self):
        for sig in recovery.SIGNALS:
            with self.subTest(signal=sig):
                old = signal.getsignal(sig)
                def interrupt(_):
                    signal.raise_signal(sig)
                with self.assertRaises(recovery.RecoveryError) as caught:
                    self.run_recovery(execute=True, sleep=interrupt)
                self.assertTrue(caught.exception.port_restored_enabled)
                self.assertEqual(self.port.read_text(), '0\n')
                self.assertEqual(signal.getsignal(sig), old)

    def test_changed_parent_and_replaced_path_restore_only_original_fd(self):
        original = self.port.with_name('original-disable')
        def replace_parent(_):
            (self.hub / 'devnum').write_text('21\n')
            self.port.rename(original)
            self.port.write_text('1\n')
        with self.assertRaisesRegex(recovery.RecoveryError, '父 Hub 已变化') as caught:
            self.run_recovery(execute=True, sleep=replace_parent)
        self.assertTrue(caught.exception.port_restored_enabled)
        self.assertEqual(original.read_text(), '0\n')
        self.assertEqual(self.port.read_text(), '1\n')

    def test_restore_write_failure_is_reported_not_claimed_enabled(self):
        original = recovery._write
        def fail_restore(fd, value):
            if value == '0':
                raise OSError('test restore denied')
            original(fd, value)
        with patch.object(recovery, '_write', side_effect=fail_restore):
            with self.assertRaisesRegex(recovery.RecoveryError, '无法确认原端口已恢复') as caught:
                self.run_recovery(execute=True, sleep=lambda _: None)
        self.assertFalse(caught.exception.port_restored_enabled)


if __name__ == '__main__':
    unittest.main()
