"""Explicit offline robot settings; never loads a workstation's real config."""
import json
from pathlib import Path


def robot_config():
    # Read anew so one test's mutations cannot affect another. Confirmation
    # flags describe only the synthetic fake readers, never an actual device.
    return json.loads((Path(__file__).parent / 'fixtures/robot.synthetic.json').read_text())
