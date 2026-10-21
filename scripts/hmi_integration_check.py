#!/usr/bin/env python3
"""Compatibility entry point; current HMI requires RGB-D body registration."""
import runpy,sys
from pathlib import Path
sys.argv=[str(Path(__file__).with_name('vision_registration_check.py')),'--mode','dual']
runpy.run_path(sys.argv[0],run_name='__main__')
