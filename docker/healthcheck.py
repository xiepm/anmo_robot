#!/usr/bin/env python3
"""Read-only demo liveness/readiness check; never claim the UI WebSocket slot."""
import json
import os
from pathlib import Path
import socket
import sys
from urllib.request import urlopen

READY_FILE = Path('/tmp/anmo-robot-ready.json')
HEALTH_URL = 'http://127.0.0.1:8080/healthz'


def process_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def check_health(*, ready_file=READY_FILE, alive=process_alive,
                 connect=socket.create_connection, request=urlopen):
    data = json.loads(Path(ready_file).read_text())
    if not isinstance(data, dict):
        raise ValueError('Invalid simulator readiness marker')
    children = data.get('children')
    if (data.get('state') != 'ready' or data.get('mode') != 'SIMULATION'
            or not isinstance(children, dict) or set(children) != {'control', 'vision', 'nginx'}
            or any(type(pid) is not int or pid <= 0 or not alive(pid) for pid in children.values())):
        raise ValueError('Simulator children are not ready')
    # Raw TCP probes do not send a WebSocket handshake or application command.
    for port in (8765, 8766):
        with connect(('127.0.0.1', port), timeout=.5):
            pass
    with request(HEALTH_URL, timeout=.5) as response:
        if response.status != 200:
            raise ValueError('Nginx is not ready')
    return True


def main():
    try:
        check_health()
    except (OSError, ValueError, TypeError) as exc:
        print(f'unhealthy: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
