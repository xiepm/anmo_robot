"""No hardware tests for native packaging and process ownership boundaries."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'windows'))
import native_launcher as launcher
import native_web


class NativeLauncherTests(unittest.TestCase):
    def fixture(self, root):
        (root/'windows').mkdir()
        settings = {key: key+'.json' for key in ('robot_config','hand_eye','capture_timing','noncontact_config')}
        for name in settings.values():
            (root/name).write_text('{}', encoding='utf-8')
        (root/'robot_config.json').write_text(json.dumps({'sdk_relative_path':'CPS.py'}), encoding='utf-8')
        (root/'CPS.py').write_text('raise RuntimeError("SDK must not be imported by file check")')
        (root/'windows/device.local.json').write_text(json.dumps(settings), encoding='utf-8-sig')
        for name in ('.venv-control-windows/Scripts/python.exe', '.venv-vision-windows/Scripts/python.exe',
                     'web/dist-native/index.html', 'zhenjiu/models/wholebody/rtmw-l.onnx'):
            path=root/name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
        return settings

    def test_missing_local_configuration_is_not_replaced_with_example(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, 'device.local.json'):
                launcher.read_settings(Path(temp))

    def test_configuration_accepts_bom_and_spaces_without_importing_sdk(self):
        with tempfile.TemporaryDirectory(prefix='native test ') as temp:
            root=Path(temp)
            self.fixture(root)
            settings=launcher.read_settings(root)
            commands=launcher.commands(settings, root)
            self.assertEqual([n for n,_ in commands], ['control','vision','web'])
            self.assertIn('--backend',commands[0][1])
            self.assertIn('real',commands[0][1])
            self.assertIn('--standalone',commands[0][1])
            self.assertNotIn('--execute',commands[0][1])
            self.assertTrue(all(isinstance(cmd, list) for _,cmd in commands))

    def test_missing_sdk_fails_before_process_launch(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); self.fixture(root); (root/'CPS.py').unlink()
            with self.assertRaisesRegex(ValueError, 'SDK'):
                launcher.read_settings(root)

    def test_missing_motion_profile_is_not_silently_optional(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); self.fixture(root); (root/'noncontact_config.json').unlink()
            with self.assertRaisesRegex(ValueError, 'noncontact_config'):
                launcher.read_settings(root)

    def test_ctrl_break_normal_stop_never_uses_terminate(self):
        child=Mock(); child.poll.return_value=None
        with patch.object(launcher.signal, 'CTRL_BREAK_EVENT', 1, create=True):
            self.assertTrue(launcher.stop_child('control', child))
        child.send_signal.assert_called_once_with(1)
        child.terminate.assert_not_called(); child.kill.assert_not_called()

    def test_stop_timeout_does_not_claim_success_or_force_kill(self):
        child=Mock(); child.poll.return_value=None; child.wait.side_effect=subprocess.TimeoutExpired('control',15)
        with patch.object(launcher.signal, 'CTRL_BREAK_EVENT', 1, create=True):
            self.assertFalse(launcher.stop_child('control', child))
        child.terminate.assert_not_called(); child.kill.assert_not_called()

    def test_spa_routes_cover_vue_routes(self):
        import re
        routes=(ROOT/'web/src/router/index.ts').read_text(encoding='utf-8')
        expected={'/'+p.lstrip('/') for p in re.findall(r"path: '([^']*)'", routes)}
        self.assertEqual(native_web.ROUTES, expected)


if __name__ == '__main__':
    unittest.main()
