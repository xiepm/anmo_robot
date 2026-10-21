#!/usr/bin/env python3
"""Supervise the isolated browser demo; never open a robot SDK or camera.

Any child exit terminates the whole container. No process is restarted and no
application command or saved workflow is replayed. Commands are injectable only
through the Python API for offline tests, not through container environment.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import threading
import time
from urllib.error import URLError
from urllib.request import urlopen

PROJECT_ROOT = Path(__file__).resolve().parents[1]
READY_FILE = Path('/tmp/anmo-robot-ready.json')
CONTROL_PORTS = (8765, 8766)
HEALTH_URL = 'http://127.0.0.1:8080/healthz'


class ChildExited(RuntimeError):
    pass


class StopRequested(Exception):
    pass


def default_commands(python=sys.executable):
    return (
        ('control', (python, '-m', 'massage_controller.node', '--backend', 'simulation-test',
                     '--standalone', '--ui-port', '8765', '--vision-port', '8766')),
        ('vision', (python, '-m', 'massage_controller.vision_sim',
                    '--uri', 'ws://127.0.0.1:8766/vision')),
        ('nginx', ('nginx', '-g', 'daemon off;')),
    )


def port_ready(port):
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=.2):
            return True
    except OSError:
        return False


def http_ready():
    try:
        with urlopen(HEALTH_URL, timeout=.3) as response:
            return response.status == 200
    except (OSError, URLError):
        return False


class Supervisor:
    def __init__(self, *, commands=None, root=PROJECT_ROOT, ready_file=READY_FILE,
                 startup_timeout=20., shutdown_timeout=5., popen=subprocess.Popen,
                 probe_port=port_ready, probe_http=http_ready,
                 kill_group=os.killpg, clock=time.monotonic, wait=None, sleep=time.sleep):
        self.commands = tuple(commands if commands is not None else default_commands())
        if [name for name, _ in self.commands] != ['control', 'vision', 'nginx']:
            raise ValueError('Expected control, vision and nginx commands in that order')
        self.root, self.ready_file = Path(root), Path(ready_file)
        self.startup_timeout, self.shutdown_timeout = startup_timeout, shutdown_timeout
        self.popen, self.probe_port, self.probe_http = popen, probe_port, probe_http
        self.kill_group, self.clock = kill_group, clock
        self.sleep = sleep
        self.stopping = threading.Event()
        self.wait = wait or self.stopping.wait
        self.children = []

    def request_stop(self, _signum=None, _frame=None):
        self.stopping.set()

    def check_children(self):
        if self.stopping.is_set():
            raise StopRequested()
        for name, process in self.children:
            code = process.poll()
            if code is not None:
                raise ChildExited(f'{name} exited unexpectedly with status {code}; stopping container')

    def await_ready(self, predicate, label):
        deadline = self.clock() + self.startup_timeout
        while True:
            self.check_children()
            if predicate():
                self.check_children()
                return
            if self.clock() >= deadline:
                raise TimeoutError(f'Timed out waiting for {label}')
            self.wait(.1)

    def start_child(self, name, command):
        self.check_children()
        environment = dict(os.environ, PYTHONUNBUFFERED='1',
                           PYTHONPATH=str(self.root/'src/massage_controller'))
        process = self.popen(list(command), cwd=str(self.root), env=environment,
                             start_new_session=True)
        self.children.append((name, process))
        print(f'[demo] started {name} (pid {process.pid})', flush=True)

    def write_ready(self):
        self.check_children()
        payload = dict(state='ready', mode='SIMULATION',
                       children={name: process.pid for name, process in self.children})
        temporary = self.ready_file.with_suffix('.tmp')
        temporary.write_text(json.dumps(payload)+'\n')
        temporary.chmod(0o600)
        temporary.replace(self.ready_file)

    def remove_ready(self):
        self.ready_file.unlink(missing_ok=True)
        self.ready_file.with_suffix('.tmp').unlink(missing_ok=True)

    def shutdown(self):
        self.remove_ready()
        # Sessions/process groups include any descendants a child may create.
        for name, process in reversed(self.children):
            try:
                self.kill_group(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            print(f'[demo] stopping {name}', flush=True)
        deadline = self.clock() + self.shutdown_timeout
        while any(process.poll() is None for _, process in self.children) and self.clock() < deadline:
            # Event.wait returns immediately after TERM; sleep keeps this grace
            # period bounded without spinning while children finish cleanup.
            self.sleep(.05)
        for name, process in reversed(self.children):
            unresponsive = process.poll() is None
            # The process-group leader may already have exited while leaving
            # descendants behind. Retire the entire group before PID 1 exits.
            try:
                self.kill_group(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            if unresponsive:
                print(f'[demo] killed unresponsive {name}', flush=True)
            try:
                process.wait(timeout=1.)
            except subprocess.TimeoutExpired:
                print(f'[demo] could not reap {name} before exit', file=sys.stderr, flush=True)

    def run(self):
        self.remove_ready()
        try:
            self.start_child(*self.commands[0])
            self.await_ready(lambda: all(self.probe_port(port) for port in CONTROL_PORTS),
                             'control loopback ports')
            self.start_child(*self.commands[1])
            self.start_child(*self.commands[2])
            self.await_ready(self.probe_http, 'nginx health endpoint')
            self.write_ready()
            print('[demo] ready: simulation only; no robot or camera connection', flush=True)
            while True:
                self.check_children()
                self.wait(.1)
        except StopRequested:
            return 0
        except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
            print(f'[demo] {exc}', file=sys.stderr, flush=True)
            return 1
        finally:
            self.shutdown()


def main():
    supervisor = Supervisor()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, supervisor.request_stop)
    return supervisor.run()


if __name__ == '__main__':
    raise SystemExit(main())
