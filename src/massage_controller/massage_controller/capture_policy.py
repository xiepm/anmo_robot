"""Shared, explicit device enrollment for the already validated exposure model.

Configuration selects a measured device identity; it cannot relax the firmware,
SDK, frame-rate or exposure-unit assumptions implemented by capture_timing.
"""
import json
import os
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parents[3] / 'config/commissioning/capture_timing.local.json'
EXPOSURE_MODEL = 'D435I_MANUAL_RGB_INTERVAL_V1'
SUPPORTED = dict(model=EXPOSURE_MODEL, firmware='5.15.1.55', sdk_version='2.58.4',
                 fps=30, color_exposure_unit_us=100, depth_exposure_unit_us=1,
                 color_exposure_raw_range=[1, 300])


def load_capture_policy(path=None):
    selected = Path(path or os.environ.get('REHAB_CAPTURE_TIMING') or DEFAULT_PATH)
    try:
        policy = json.loads(selected.read_text())
    except (OSError, ValueError) as exc:
        raise ValueError('缺少或无法读取本机曝光配置，请配置 capture_timing.local.json') from exc
    if (not isinstance(policy, dict) or type(policy.get('version')) is not int or policy['version'] != 1
            or policy.get('verified') is not True
            or not isinstance(policy.get('camera_serial'), str)
            or not policy['camera_serial'].strip()
            or any(type(policy.get(key)) is not type(value) or policy[key] != value
                   for key, value in SUPPORTED.items())):
        raise ValueError('本机曝光配置未核对或与支持的曝光模型不匹配')
    return policy


def policy_identity(policy):
    return {key: policy[key] for key in ('camera_serial', 'firmware', 'sdk_version')}
