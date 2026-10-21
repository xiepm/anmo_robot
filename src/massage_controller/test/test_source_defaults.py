"""Clean-checkout defaults without ROS, SDK connections, or real services."""
import contextlib
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from massage_controller import node


class SourceDefaultsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        package = self.root/'src/massage_controller'
        package.mkdir(parents=True)
        (package/'package.xml').write_text('<package/>')
        self.module_path = package/'massage_controller/node.py'
        self.expected = package/'config/real_robot.json'

    def no_ros(self):
        return patch.dict(sys.modules, {'ament_index_python': None, 'ament_index_python.packages': None})

    def test_missing_machine_config_in_checkout_does_not_require_ament(self):
        with patch.object(node, '__file__', str(self.module_path)), self.no_ros():
            self.assertEqual(node.default_robot_config(), str(self.expected))
        self.assertFalse(self.expected.exists())

    def test_help_works_without_config_or_ros(self):
        with patch.object(node, '__file__', str(self.module_path)), self.no_ros(), \
                patch.object(node, 'default_config', return_value=str(self.root/'config/rehab_protocol')), \
                patch.object(sys, 'argv', ['control_node', '--help']), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as exited:
                node.main()
        self.assertEqual(exited.exception.code, 0)

    def test_sim_arguments_reach_standalone_runner_without_machine_files(self):
        received = []
        async def fake_run(args):
            received.append(args)
        with patch.object(node, '__file__', str(self.module_path)), self.no_ros(), \
                patch.object(node, 'default_config', return_value=str(self.root/'config/rehab_protocol')), \
                patch.object(sys, 'argv', ['control_node', '--backend', 'simulation-test', '--standalone']), \
                patch.object(node, 'run', side_effect=fake_run):
            node.main()
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].backend, 'simulation-test')
        self.assertTrue(received[0].standalone)
        self.assertEqual(received[0].robot_config, str(self.expected))

    def test_installed_package_still_uses_ament_lookup(self):
        packages = types.ModuleType('ament_index_python.packages')
        packages.get_package_share_directory = lambda name: '/installed/share/'+name
        with patch.object(node, '__file__', str(self.root/'site-packages/massage_controller/node.py')), \
                patch.dict(sys.modules, {'ament_index_python.packages': packages}):
            self.assertEqual(node.default_robot_config(), '/installed/share/massage_controller/config/real_robot.json')

    def test_real_launcher_reports_missing_config_before_runtime(self):
        project = Path(__file__).resolve().parents[3]
        scripts = self.root/'scripts'
        scripts.mkdir()
        shutil.copyfile(project/'scripts/start_control.sh', scripts/'start_control.sh')
        marker = self.root/'runtime-was-called'
        (scripts/'controller_runtime.sh').write_text('#!/bin/sh\ntouch "'+str(marker)+'"\nexit 99\n')
        (scripts/'controller_runtime.sh').chmod(0o755)
        env = {key:value for key,value in os.environ.items() if not key.startswith('REHAB_')}
        result = subprocess.run(['bash',str(scripts/'start_control.sh'),'--backend','real'],
                                env=env,capture_output=True,text=True,timeout=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(str(self.expected),result.stderr)
        self.assertIn('缺少配置/资源',result.stderr)
        self.assertNotIn('ament_index_python',result.stderr)
        self.assertFalse(marker.exists())


if __name__ == '__main__':
    unittest.main()
