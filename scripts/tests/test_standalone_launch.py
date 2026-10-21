"""Launcher contract tests: temporary trees and fake executables, no hardware."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]


class StandaloneLaunchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='rehab launch ')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for relative in ('scripts/controller_runtime.sh', 'scripts/start_control.sh',
                         'scripts/start_system.sh', 'scripts/install_standalone.sh',
                         'web/scripts/start_vision_preview.sh'):
            dest = self.root/relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT/relative, dest)
            dest.chmod(0o755)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith('REHAB_')}
        self.env['CAPTURE_FILE'] = str(self.root/'called.json')

    def executable(self, relative, body):
        path = self.root/relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
        path.chmod(0o755)
        return path

    def recorder(self, relative):
        return self.executable(relative, f'#!{sys.executable}\n'
            'import json, os, sys\n'
            'open(os.environ["CAPTURE_FILE"], "w").write(json.dumps('
            '{"args":sys.argv[1:], "path":os.environ.get("PYTHONPATH", ""), '
            '"timing":os.environ.get("REHAB_CAPTURE_TIMING", "")}))\n')

    def run_script(self, relative, *args, env=None):
        return subprocess.run([str(self.root/relative), *args], cwd=self.root,
                              env=dict(self.env, **(env or {})), text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)

    def called(self):
        return json.loads((self.root/'called.json').read_text())

    def real_files(self):
        config = self.root/'src/massage_controller/config/real_robot.json'
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(json.dumps({'sdk_relative_path': 'SDK/vendor sdk.py'}))
        sdk = self.root/'SDK/vendor sdk.py'
        sdk.parent.mkdir(parents=True, exist_ok=True)
        sdk.write_text('# never imported\n')
        hand_eye = self.root/'zhenjiu/hand_eye/calibration_20261013.json'
        hand_eye.parent.mkdir(parents=True, exist_ok=True)
        hand_eye.write_text('{}')
        return config, sdk, hand_eye

    def test_clean_clone_sim_uses_native_and_needs_no_sdk_or_calibration(self):
        self.recorder('.venv-control/bin/python')
        result = self.run_script('scripts/start_control.sh', '--backend', 'simulation-test',
                                 env={'REHAB_NONCONTACT_CONFIG': '/missing/local-profile.json'})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.called()['args'], ['-m', 'massage_controller.node', '--backend',
                                               'simulation-test', '--standalone'])
        self.assertTrue(self.called()['path'].startswith(str(self.root/'src/massage_controller')))

    def test_existing_runtime_wins_but_explicit_interpreter_overrides(self):
        self.recorder('.venv-control/bin/python')
        self.executable('.runtime/jammy/bin/bash', '#!/bin/sh\nexit 0\n')
        self.recorder('scripts/runtime.sh')
        result = self.run_script('scripts/start_control.sh', '--backend=simulation-test')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.called()['args'][0], 'bash')
        self.assertNotIn('--standalone', self.called()['args'])
        explicit = self.recorder('custom python')
        result = self.run_script('scripts/start_control.sh', '--backend=simulation-test',
                                 env={'REHAB_CONTROL_PYTHON': str(explicit)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.called()['args'][0], '-m')
        self.assertIn('--standalone', self.called()['args'])

    def test_missing_real_configuration_fails_before_interpreter(self):
        self.recorder('.venv-control/bin/python')
        result = self.run_script('scripts/start_control.sh', '--backend', 'real')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('REHAB_ROBOT_CONFIG', result.stderr)
        self.assertFalse((self.root/'called.json').exists())

    def test_env_paths_keep_spaces_and_cli_hand_eye_takes_precedence(self):
        self.recorder('.venv-control/bin/python')
        config, sdk, hand_eye = self.real_files()
        result = self.run_script('scripts/start_control.sh', '--hand-eye', str(hand_eye),
                                 env={'REHAB_HAND_EYE': '/not-used', 'REHAB_SDK': str(sdk),
                                      'REHAB_ROBOT_CONFIG': str(config)})
        self.assertEqual(result.returncode, 0, result.stderr)
        args = self.called()['args']
        self.assertEqual(args[args.index('--sdk')+1], str(sdk))
        self.assertEqual(args.count('--hand-eye'), 1)
        self.assertNotIn('/not-used', args)

    def test_runtime_translates_host_paths_without_changing_robot_flags(self):
        self.executable('.runtime/jammy/bin/bash', '#!/bin/sh\nexit 0\n')
        self.recorder('scripts/runtime.sh')
        config, sdk, hand_eye = self.real_files()
        result = self.run_script('scripts/start_control.sh', '--enable-real-scan',
                                 env={'REHAB_HAND_EYE': str(hand_eye), 'REHAB_SDK': str(sdk),
                                      'REHAB_ROBOT_CONFIG': str(config)})
        self.assertEqual(result.returncode, 0, result.stderr)
        args = self.called()['args']
        self.assertIn('/workspace/SDK/vendor sdk.py', args)
        self.assertIn('/workspace/zhenjiu/hand_eye/calibration_20261013.json', args)
        self.assertIn('--enable-real-scan', args)
        self.assertNotIn('--execute', args)

    def test_missing_native_runtime_reports_installer(self):
        result = self.run_script('scripts/start_control.sh', '--backend=simulation-test')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('install_standalone.sh', result.stderr)

    def test_runtime_translates_timing_override_but_native_keeps_host_path(self):
        self.executable('.runtime/jammy/bin/bash', '#!/bin/sh\nexit 0\n')
        self.recorder('scripts/runtime.sh')
        python = self.recorder('.venv-control/bin/python')
        timing = str(self.root/'config/custom timing.json')
        result = self.run_script('scripts/start_control.sh', '--backend=simulation-test',
                                 env={'REHAB_CAPTURE_TIMING': timing})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.called()['timing'], '/workspace/config/custom timing.json')
        result = self.run_script('scripts/start_control.sh', '--backend=simulation-test',
                                 env={'REHAB_CAPTURE_TIMING': timing, 'REHAB_CONTROL_PYTHON': str(python)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.called()['timing'], timing)

    def test_vision_override_and_missing_environment(self):
        result = self.run_script('web/scripts/start_vision_preview.sh', '--help')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('REHAB_VISION_PYTHON', result.stderr)
        python = self.recorder('vision python')
        result = self.run_script('web/scripts/start_vision_preview.sh', '--help',
                                 env={'REHAB_VISION_PYTHON': str(python)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.called()['args'], ['-u', str(self.root/'web/scripts/vision_preview.py'), '--help'])

    def test_installer_help_has_no_install_side_effects(self):
        self.recorder('bin/npm')
        result = self.run_script('scripts/install_standalone.sh', '--help',
                                 env={'PATH': str(self.root/'bin')+os.pathsep+self.env['PATH']})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--vision-python', result.stdout)
        self.assertFalse((self.root/'.venv-control').exists())
        self.assertFalse((self.root/'called.json').exists())

    def mock_system(self, active=False):
        self.executable('bin/systemctl', '#!/bin/sh\n'
                        'case "$*" in *is-active*) echo '+('active' if active else 'inactive')+';; esac\n')
        self.executable('bin/systemd-run', '#!/bin/sh\nprintf "%s\\n" "$@" >> "$LAUNCH_LOG"\n')
        for command in ('ss', 'node', 'python3'):
            self.executable('bin/'+command, '#!/bin/sh\nexit 0\n')
        return {'PATH': str(self.root/'bin')+os.pathsep+self.env['PATH'],
                'LAUNCH_LOG': str(self.root/'launch.log'),
                'REHAB_HAND_EYE': str(self.root/'local hand eye.json'),
                'REHAB_CAPTURE_TIMING': str(self.root/'local camera timing.json'),
                'REHAB_NONCONTACT_CONFIG': str(self.root/'local route.json')}

    def test_systemd_receives_local_overrides_as_single_arguments(self):
        env = self.mock_system()
        result = self.run_script('scripts/start_system.sh', env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        args = (self.root/'launch.log').read_text().splitlines()
        self.assertIn('--setenv=REHAB_HAND_EYE='+env['REHAB_HAND_EYE'], args)
        self.assertIn('--setenv=REHAB_CAPTURE_TIMING='+env['REHAB_CAPTURE_TIMING'], args)
        self.assertIn(env['REHAB_NONCONTACT_CONFIG'], args)
        self.assertIn('--property=Restart=no', args)
        self.assertNotIn('--execute', args)

    def test_running_services_are_not_restarted_for_new_configuration(self):
        result = self.run_script('scripts/start_system.sh', env=self.mock_system(active=True))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count('保持现有进程'), 3)
        self.assertFalse((self.root/'launch.log').exists())


if __name__ == '__main__':
    unittest.main()
