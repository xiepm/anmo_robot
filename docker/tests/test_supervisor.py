import contextlib
import importlib.util
import io
import json
from pathlib import Path
import signal
import subprocess
import tempfile
import unittest
from unittest.mock import patch


def load_module(name):
    path = Path(__file__).resolve().parents[1]/f'{name}.py'
    spec = importlib.util.spec_from_file_location(f'demo_{name}', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


entrypoint = load_module('entrypoint')


class FakeClock:
    def __init__(self): self.value = 0.
    def __call__(self): return self.value
    def sleep(self, duration): self.value += duration


class FakeProcess:
    def __init__(self, pid, stubborn=False):
        self.pid, self.stubborn = pid, stubborn
        self.code, self.reaped = None, False
    def poll(self): return self.code
    def wait(self, timeout):
        if self.code is None: raise subprocess.TimeoutExpired('fake', timeout)
        self.reaped = True
        return self.code


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.ready = Path(self.directory.name)/'ready.json'
        self.clock = FakeClock()
        self.calls, self.processes, self.signals, self.probes = [], [], [], []
        self.fail_spawn = None
        self.stubborn = False
        self.on_wait = lambda: self.supervisor.request_stop()
        self.supervisor = entrypoint.Supervisor(
            commands=[(name, (name,)) for name in ('control', 'vision', 'nginx')],
            root=self.directory.name, ready_file=self.ready,
            startup_timeout=.3, shutdown_timeout=.2,
            popen=self.spawn, probe_port=self.probe_port, probe_http=lambda: True,
            kill_group=self.kill, clock=self.clock, wait=self.wait, sleep=self.clock.sleep)

    def spawn(self, command, **kwargs):
        self.calls.append((command, kwargs))
        if command[0] == self.fail_spawn: raise FileNotFoundError('missing executable')
        process = FakeProcess(1000+len(self.processes), stubborn=self.stubborn)
        self.processes.append(process)
        return process

    def kill(self, pid, signum):
        self.signals.append((pid, signum))
        process = next(p for p in self.processes if p.pid == pid)
        if signum == signal.SIGKILL or not process.stubborn:
            process.code = -signum

    def probe_port(self, port):
        self.probes.append(port)
        return True

    def wait(self, duration):
        self.clock.sleep(duration)
        self.on_wait()

    def run_demo(self):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return self.supervisor.run()

    def test_default_commands_only_select_simulation_and_loopback(self):
        commands = dict(entrypoint.default_commands('/python'))
        self.assertIn('simulation-test', commands['control'])
        self.assertIn('--standalone', commands['control'])
        self.assertIn('ws://127.0.0.1:8766/vision', commands['vision'])
        self.assertEqual(commands['nginx'], ('nginx', '-g', 'daemon off;'))
        for command in commands.values():
            self.assertFalse({'--sdk', '--execute', '--enable-real-scan', '--noncontact-config'} & set(command))

    def test_order_readiness_marker_and_signal_cleanup(self):
        captured = []
        def stop_after_ready():
            captured.append(json.loads(self.ready.read_text()))
            self.supervisor.request_stop(signal.SIGTERM)
        self.on_wait = stop_after_ready
        self.assertEqual(self.run_demo(), 0)
        self.assertEqual([c[0][0] for c in self.calls], ['control', 'vision', 'nginx'])
        self.assertEqual(self.probes, [8765, 8766])
        self.assertEqual(captured[0]['mode'], 'SIMULATION')
        self.assertEqual(captured[0]['children'], dict(control=1000, vision=1001, nginx=1002))
        self.assertFalse(self.ready.exists())
        self.assertTrue(all(p.reaped for p in self.processes))
        self.assertEqual(self.signals[:3], [(1002, signal.SIGTERM), (1001, signal.SIGTERM), (1000, signal.SIGTERM)])
        for _, kwargs in self.calls:
            self.assertTrue(kwargs['start_new_session'])
            self.assertEqual(kwargs['env']['PYTHONPATH'], str(Path(self.directory.name)/'src/massage_controller'))

    def test_waits_for_control_before_starting_vision(self):
        attempts = []
        def probe(port):
            attempts.append(port)
            self.assertEqual(len(self.processes), 1)
            return self.clock() >= .2
        self.supervisor.probe_port = probe
        def wait_until_ready():
            if self.ready.exists(): self.supervisor.request_stop()
        self.on_wait = wait_until_ready
        self.assertEqual(self.run_demo(), 0)
        self.assertGreater(attempts.count(8765), 1)
        self.assertIn(8766, attempts)
        self.assertEqual(len(self.processes), 3)

    def test_any_child_exit_even_zero_is_failure_without_restart(self):
        for index in range(3):
            with self.subTest(child=index):
                # Fresh fake state per case: no actual child processes exist.
                self.calls.clear(); self.processes.clear(); self.signals.clear()
                self.supervisor.children.clear(); self.supervisor.stopping.clear()
                self.on_wait = lambda: setattr(self.processes[index], 'code', 0)
                self.assertEqual(self.run_demo(), 1)
                self.assertEqual(len(self.calls), 3)
                self.assertFalse(self.ready.exists())
                self.assertTrue(all(p.reaped for p in self.processes))

    def test_control_timeout_never_starts_other_children(self):
        self.supervisor.probe_port = lambda _port: False
        self.on_wait = lambda: None
        self.assertEqual(self.run_demo(), 1)
        self.assertEqual(len(self.calls), 1)
        self.assertTrue(self.processes[0].reaped)

    def test_control_exit_during_startup_never_starts_other_children(self):
        def fail_control(command, **kwargs):
            process = self.spawn(command, **kwargs)
            process.code = 3
            return process
        self.supervisor.popen = fail_control
        self.assertEqual(self.run_demo(), 1)
        self.assertEqual(len(self.calls), 1)
        self.assertFalse(self.ready.exists())
        self.assertTrue(self.processes[0].reaped)

    def test_nginx_timeout_retires_all_children_and_marker(self):
        self.supervisor.probe_http = lambda: False
        self.on_wait = lambda: None
        self.assertEqual(self.run_demo(), 1)
        self.assertEqual(len(self.calls), 3)
        self.assertFalse(self.ready.exists())
        self.assertTrue(all(p.reaped for p in self.processes))

    def test_signal_during_startup_does_not_start_later_children(self):
        self.supervisor.probe_port = lambda _port: False
        self.assertEqual(self.run_demo(), 0)
        self.assertEqual(len(self.calls), 1)

    def test_failed_spawn_cleans_up_already_started_children(self):
        self.fail_spawn = 'nginx'
        self.assertEqual(self.run_demo(), 1)
        self.assertEqual(len(self.processes), 2)
        self.assertTrue(all(p.reaped for p in self.processes))

    def test_unresponsive_children_are_killed_after_bounded_grace_period(self):
        self.stubborn = True
        self.assertEqual(self.run_demo(), 0)
        self.assertTrue(all(p.code == -signal.SIGKILL for p in self.processes))
        self.assertGreaterEqual(self.clock(), .3)
        self.assertLess(self.clock(), .5)

    def test_previous_ready_marker_is_not_reused(self):
        self.ready.write_text('{"state":"ready"}')
        self.supervisor.probe_port = lambda _port: False
        self.assertEqual(self.run_demo(), 0)
        self.assertFalse(self.ready.exists())

    def test_main_installs_term_and_int_handlers(self):
        with patch.object(entrypoint, 'Supervisor', return_value=self.supervisor), \
             patch.object(entrypoint.signal, 'signal') as install, \
             patch.object(self.supervisor, 'run', return_value=0):
            self.assertEqual(entrypoint.main(), 0)
        self.assertEqual([call.args[0] for call in install.call_args_list], [signal.SIGINT, signal.SIGTERM])


if __name__ == '__main__':
    unittest.main()
