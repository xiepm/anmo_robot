#!/usr/bin/env python3
"""One real control process and optional passive model viewers. No vision simulator."""
import argparse
import os
from pathlib import Path
import signal
import socket
import subprocess
import time

ROOT=Path(__file__).resolve().parents[1]

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--no-mirror',action='store_true')
    p.add_argument('--no-rviz',action='store_true',help='publish mirror TF without opening RViz')
    args=p.parse_args()
    for port in (8765,8766):
        with socket.socket() as sock:
            sock.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
            sock.bind(('127.0.0.1',port))
    pid_file=ROOT/'.runtime/real-debug.pid'
    pid_file.parent.mkdir(parents=True,exist_ok=True)
    pid_file.write_text(str(os.getpid())+'\n')
    children=[]
    def stop(*_):raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,stop)
    signal.signal(signal.SIGINT,stop)
    try:
        children.append(subprocess.Popen([str(ROOT/'scripts/start_control.sh'),'--backend','real'],cwd=ROOT,start_new_session=True))
        if not args.no_mirror:
            children.append(subprocess.Popen([str(ROOT/'scripts/start_robot_mirror.sh'),
                'rviz:=false' if args.no_rviz else 'rviz:=true'],cwd=ROOT,start_new_session=True))
        print('REAL feedback control + passive elfin5_l mirror. Ctrl+C stops these processes.',flush=True)
        while all(c.poll() is None for c in children):time.sleep(.2)
        raise RuntimeError('a debug process exited')
    except KeyboardInterrupt:pass
    finally:
        for child in children:
            if child.poll() is None:os.killpg(child.pid,signal.SIGTERM)
        for child in children:
            try:child.wait(timeout=6)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid,signal.SIGKILL);child.wait()
        if pid_file.exists() and pid_file.read_text().strip()==str(os.getpid()):
            pid_file.unlink()

if __name__=='__main__':main()
