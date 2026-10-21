#!/usr/bin/env python3
"""Stop only the recorded real-debug supervisor and its owned child processes."""
import os
from pathlib import Path
import signal
import time
ROOT=Path(__file__).resolve().parents[1]
record=ROOT/'.runtime/real-debug.pid'
if not record.exists():raise SystemExit('No recorded real-debug launcher. Stop a manually launched controller in its own terminal.')
pid=int(record.read_text().strip())
proc=Path(f'/proc/{pid}')
if not proc.exists():record.unlink();raise SystemExit('Recorded launcher has already exited.')
argv=(proc/'cmdline').read_bytes().split(b'\0')
if len(argv)<2 or Path(os.fsdecode(argv[1])).name!='start_real_debug.py' or (proc/'cwd').resolve()!=ROOT:
    raise SystemExit('PID does not match this workspace launcher; nothing stopped.')
os.kill(pid,signal.SIGTERM)
for _ in range(100):
    if not proc.exists():
        if record.exists() and record.read_text().strip()==str(pid):record.unlink()
        print('Real-debug processes stopped.');break
    time.sleep(.1)
else:raise SystemExit('Launcher has not exited; inspect the log. No unrelated process was killed.')
