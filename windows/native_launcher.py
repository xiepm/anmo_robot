"""Windows native service supervisor. Startup never sends enable or START."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import threading
import time
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
PORTS = (5173, 8765, 8766, 8767)


def read_settings(root=ROOT):
    path = root / 'windows/device.local.json'
    if not path.is_file():
        raise ValueError('Missing windows/device.local.json. Copy device.example.json and configure this installation first.')
    settings = json.loads(path.read_text(encoding='utf-8-sig'))
    fields = ('robot_config', 'hand_eye', 'capture_timing', 'noncontact_config')
    if not isinstance(settings, dict) or set(settings) != set(fields):
        raise ValueError('device.local.json requires: '+', '.join(fields))
    resolved = {}
    for key in fields:
        if not isinstance(settings[key], str) or not settings[key].strip():
            raise ValueError(f'Missing path: {key}')
        value = (root / settings[key]).resolve()
        if not value.is_file():
            raise ValueError(f'Missing {key}: {value}. See docs/WINDOWS_NATIVE.md.')
        resolved[key] = value
    cfg = json.loads(resolved['robot_config'].read_text(encoding='utf-8-sig'))
    sdk = root / cfg['sdk_relative_path']
    if not sdk.is_file():
        raise ValueError(f'Missing vendor Python SDK: {sdk}. See SDK/README.md.')
    resolved['sdk'] = sdk.resolve()
    return resolved


def commands(settings, root=ROOT):
    control = root / '.venv-control-windows/Scripts/python.exe'
    vision = root / '.venv-vision-windows/Scripts/python.exe'
    for path in (control, vision, root / 'web/dist-native/index.html', root / 'zhenjiu/models/wholebody/rtmw-l.onnx'):
        if not path.is_file():
            raise ValueError(f'Missing installed file: {path}. Run Install-Native.cmd.')
    return [
        ('control', [str(control), '-X', 'utf8', '-u', '-m', 'massage_controller.node',
                     '--standalone', '--backend', 'real', '--enable-real-scan',
                     '--robot-config', str(settings['robot_config']), '--hand-eye', str(settings['hand_eye']),
                     '--sdk', str(settings['sdk']), '--noncontact-config', str(settings['noncontact_config'])]),
        ('vision', [str(vision), '-X', 'utf8', '-u', str(root/'web/scripts/vision_preview.py')]),
        ('web', [str(control), '-X', 'utf8', '-u', str(root/'windows/native_web.py')]),
    ]


def ensure_ports_free():
    # Never terminate a different service, simulator or SSH port forward.
    held = []
    try:
        for port in PORTS:
            s = socket.socket()
            held.append(s)
            if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            s.bind(('127.0.0.1', port))
    finally:
        for s in held:
            s.close()


def wait_port(port, child, stopping, timeout=30):
    deadline = time.monotonic() + timeout
    while not stopping.is_set():
        if child.poll() is not None:
            raise RuntimeError(f'Service exited before port {port} became ready; see logs.')
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=.2):
                return
        except OSError:
            if time.monotonic() > deadline:
                raise RuntimeError(f'Timed out waiting for port {port}; see logs.')
            stopping.wait(.1)
    raise InterruptedError('Startup cancelled')


def stop_child(name, process, timeout=15):
    if process.poll() is not None:
        return True
    try:
        # Windows terminate()/kill() cannot run the controller stop cleanup.
        process.send_signal(signal.CTRL_BREAK_EVENT)
        process.wait(timeout=timeout)
        return True
    except (OSError, subprocess.TimeoutExpired):
        print(f'WARNING: {name} has not exited. Use the physical stop if needed; no force-kill or successful stop is assumed.', flush=True)
        return False


def run(settings, child_commands, *, open_browser=True):
    ensure_ports_free()
    logs = ROOT/'windows/reports/services'
    logs.mkdir(parents=True, exist_ok=True)
    stopping = threading.Event()
    def request_stop(*_):
        stopping.set()
    for name in ('SIGINT', 'SIGTERM', 'SIGBREAK'):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), request_stop)
    env = dict(os.environ, PYTHONPATH=str(ROOT/'src/massage_controller'),
               PYTHONUTF8='1', PYTHONUNBUFFERED='1', REHAB_CAPTURE_TIMING=str(settings['capture_timing']))
    children, handles = [], []
    code = 0
    try:
        for name, command in child_commands:
            if stopping.is_set():
                break
            handle = (logs/f'{name}.log').open('ab', buffering=0)
            handles.append(handle)
            process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=handle, stderr=subprocess.STDOUT,
                                       creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
            children.append((name, process))
            print(f'Started {name} PID {process.pid}; log: {logs/name}.log', flush=True)
            if name == 'control':
                wait_port(8765, process, stopping)
                wait_port(8766, process, stopping)
            elif name == 'vision':
                wait_port(8767, process, stopping)
            elif name == 'web':
                wait_port(5173, process, stopping)
        if not stopping.is_set():
            print('UI: http://127.0.0.1:5173/ . Keep this window open. Ctrl+C requests graceful shutdown.', flush=True)
            print('Startup does not enable the robot or execute a route. Camera/robot readiness is reported by the UI.', flush=True)
            if open_browser:
                webbrowser.open('http://127.0.0.1:5173/')
            while not stopping.wait(.2):
                for name, process in children:
                    if process.poll() is not None:
                        raise RuntimeError(f'{name} exited. No automatic restart or route replay. See {logs/name}.log')
    except InterruptedError:
        pass
    except (OSError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        code = 1
    finally:
        # Controller receives graceful shutdown first, retaining SDK ownership
        # until its stop/feedback cleanup completes. Never force-kill it.
        pending = [(name, p) for name, p in children if not stop_child(name, p)]
        while any(p.poll() is None for _, p in pending):
            stopping.wait(.5) if not stopping.is_set() else time.sleep(.5)
        if pending:
            code = 1
        for handle in handles:
            handle.close()
    return code


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--check', action='store_true', help='Validate installed files only; no robot/camera connection')
    p.add_argument('--no-browser', action='store_true')
    args = p.parse_args()
    if os.name != 'nt':
        p.error('This launcher requires native Windows; Linux uses scripts/start_system.sh.')
    try:
        settings = read_settings()
        child_commands = commands(settings)
        if args.check:
            print('Installed files/config paths exist. This is not camera/robot/calibration validation.')
            return 0
        return run(settings, child_commands, open_browser=not args.no_browser)
    except (OSError, ValueError, KeyError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
