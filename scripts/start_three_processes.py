#!/usr/bin/env python3
"""Optional launcher/supervisor; child commands use argv, never shell=True."""
import argparse
import os
from pathlib import Path
import shlex
import signal
import socket
import subprocess
import time

ROOT=Path(__file__).resolve().parents[1]

def main():
    p=argparse.ArgumentParser(description='Launch controller and independently developed UI/vision')
    choice=p.add_mutually_exclusive_group(required=True)
    choice.add_argument('--vision-sim',action='store_true')
    choice.add_argument('--vision-command',help='quoted argv for the real vision worker (requires REAL vision mode)')
    p.add_argument('--offline-simulation-test',action='store_true',help='explicit legacy test backend; no robot connection')
    p.add_argument('--ui-command',help='quoted argv for the UI; omit for protocol-client testing')
    args=p.parse_args()
    if args.vision_sim and not args.offline_simulation_test:
        p.error('--vision-sim is only allowed with --offline-simulation-test; real debugging cannot use simulated vision')
    for port in (8765,8766):
        with socket.socket() as s:
            s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
            s.bind(('127.0.0.1',port))
    children=[]
    def interrupted(*_):raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,interrupted)
    try:
        children.append(subprocess.Popen([str(ROOT/'scripts/start_control.sh'),'--backend','simulation-test' if args.offline_simulation_test else 'real'],cwd=ROOT,start_new_session=True))
        deadline=time.monotonic()+15
        while True:
            if children[0].poll() is not None:raise RuntimeError('controller exited during startup')
            try:
                with socket.create_connection(('127.0.0.1',8766),timeout=0.1):break
            except OSError:
                if time.monotonic()>deadline:raise RuntimeError('controller startup timeout')
                time.sleep(0.1)
        vision=[str(ROOT/'scripts/start_vision_sim.sh')] if args.vision_sim else shlex.split(args.vision_command)
        children.append(subprocess.Popen(vision,cwd=ROOT,start_new_session=True))
        if args.ui_command:
            children.append(subprocess.Popen(shlex.split(args.ui_command),cwd=ROOT,start_new_session=True))
        else:print('Backend ready at ws://127.0.0.1:8765; UI command not configured',flush=True)
        while all(child.poll() is None for child in children):time.sleep(0.2)
        raise RuntimeError('one business process exited; stopping remaining processes')
    except KeyboardInterrupt:pass
    finally:
        # Ask control to stop before shutting down other children.
        for child in children:
            if child.poll() is None:os.killpg(child.pid,signal.SIGTERM)
        for child in children:
            try:child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid,signal.SIGKILL);child.wait()

if __name__=='__main__':main()
