#!/usr/bin/env python3
"""Bounded RealSense RGB-D diagnostics; no robot SDK, reset or exposure changes."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'zhenjiu'))
from rehab_vision.camera_diagnostics import frame_diagnostics

MARKER = 'REHAB_CAMERA_DIAGNOSTIC='


def collect(rs, timeout=10., frames=10, serial=None):
    """One pipeline, unaligned native depth metadata, and a bounded frame wait."""
    report = dict(status='error', diagnostic_only=True, capture_time_valid=False,
                  platform=sys.platform, python=platform.python_version(),
                  sdk_version=getattr(rs, '__version__', 'unknown'),
                  started_at=datetime.now(timezone.utc).isoformat(),
                  requested_streams=dict(width=640, height=480, fps=30),
                  exposure_units='unverified; metadata_raw is not converted',
                  devices=[], frames=[], error='')
    pipeline = None
    try:
        devices = list(rs.context().query_devices())
        for device in devices:
            item = {}
            for field in ('name', 'serial_number', 'firmware_version', 'usb_type_descriptor'):
                key = getattr(rs.camera_info, field, None)
                try:
                    item[field] = device.get_info(key) if key is not None and device.supports(key) else None
                except RuntimeError as exc:
                    item[field] = None
                    item[field+'_error'] = str(exc)
            report['devices'].append(item)
        if not devices:
            raise RuntimeError('未枚举到 RealSense 相机；请检查 USB 连接、驱动及相机供电')
        choices = [item for item in report['devices'] if not serial or item['serial_number'] == serial]
        if len(choices) != 1 or not choices[0]['serial_number']:
            raise RuntimeError('无法唯一选择相机；多相机时请提供 --serial，且序列号必须已枚举')
        report['selected_serial'] = choices[0]['serial_number']
        config = rs.config()
        config.enable_device(report['selected_serial'])
        config.enable_stream(rs.stream.depth, 640, 480, rs.format.z16, 30)
        config.enable_stream(rs.stream.color, 640, 480, rs.format.bgr8, 30)
        pipeline = rs.pipeline()
        deadline = time.monotonic() + timeout
        pipeline.start(config)
        while len(report['frames']) < frames:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError('RGB-D 采帧超过诊断时限')
            pair = pipeline.wait_for_frames(max(1, min(1000, int(remaining * 1000))))
            color, depth = pair.get_color_frame(), pair.get_depth_frame()
            if not color or not depth:
                continue
            report['frames'].append(dict(color=frame_diagnostics(rs, color),
                                         native_depth=frame_diagnostics(rs, depth)))
        report['status'] = 'frames_received'
        report['message'] = '已采到真实 RGB-D 帧；此报告仅记录元数据，不代表曝光配对或机械臂坐标验收通过'
    except (OSError, RuntimeError, ValueError) as exc:
        report['error'] = str(exc)
    finally:
        if pipeline is not None:
            try:
                pipeline.stop()
            except RuntimeError as exc:
                report['close_error'] = str(exc)
                report['status'] = 'error'
    return report


def run_worker(timeout, frames, serial):
    from rehab_vision.camera_lock import CameraLock
    lock = CameraLock()
    try:
        lock.acquire()
        import pyrealsense2 as rs
        return collect(rs, timeout, frames, serial)
    except (ImportError, OSError, RuntimeError) as exc:
        return dict(status='error', diagnostic_only=True, capture_time_valid=False, error=str(exc))
    finally:
        lock.close()


def bounded_report(timeout, frames, serial=None):
    # A native SDK start/enumeration can block before wait_for_frames. Run the
    # diagnostic owner in a disposable process to bound those calls as well.
    command = [sys.executable, '-u', str(Path(__file__).resolve()), '--worker',
               '--timeout', str(timeout), '--frames', str(frames)]
    if serial:
        command += ['--serial', serial]
    try:
        result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8',
                                errors='replace', timeout=timeout + 5)
        payload = next(line[len(MARKER):] for line in reversed(result.stdout.splitlines())
                       if line.startswith(MARKER))
        report = json.loads(payload)
        if result.stderr:
            report['sdk_stderr'] = result.stderr[-4000:]
        return report
    except subprocess.TimeoutExpired:
        return dict(status='error', diagnostic_only=True, capture_time_valid=False,
                    error='相机 SDK 超过诊断时限，诊断子进程已结束；未执行硬件复位')
    except (OSError, StopIteration, ValueError) as exc:
        return dict(status='error', diagnostic_only=True, capture_time_valid=False,
                    error=f'无法完成相机诊断: {exc}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--timeout', type=float, default=10., help='采帧时限秒数（1–30）；SDK 总时限另加 5 秒')
    parser.add_argument('--frames', type=int, default=10, help='记录 RGB-D 帧数（1–60）')
    parser.add_argument('--serial', help='有多个相机时指定已枚举的序列号')
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not 1 <= args.timeout <= 30 or not 1 <= args.frames <= 60:
        parser.error('timeout 必须为 1–30 秒，frames 必须为 1–60 帧')
    if args.worker:
        report = run_worker(args.timeout, args.frames, args.serial)
        print(MARKER + json.dumps(report, ensure_ascii=True, allow_nan=False))
        return
    report = bounded_report(args.timeout, args.frames, args.serial)
    directory = ROOT / 'windows/reports'
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f'camera-{datetime.now():%Y%m%d-%H%M%S-%f}.json'
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    print(report.get('error') or report.get('message'))
    print(f'诊断报告: {path}')
    if report['status'] != 'frames_received':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
