"""Windows camera/clock boundary checks; never imports an actual camera SDK."""
import ast
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'zhenjiu'))
from rehab_vision.base_client import BaseClient
from rehab_vision.camera_diagnostics import frame_diagnostics
from rehab_vision.camera_lock import CameraLock
from rehab_vision.capture_clock import CaptureClock

spec = importlib.util.spec_from_file_location('diagnose_camera', ROOT / 'windows/diagnose_camera.py')
diagnose = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diagnose)


class Frame:
    def __init__(self):
        self.values = dict(frame_timestamp=100000, sensor_timestamp=99917, actual_exposure=166, auto_exposure=1)
        self.profile = SimpleNamespace(as_video_stream_profile=lambda: SimpleNamespace(intrinsics='fake-intrinsics'))
    def get_frame_number(self): return 42
    def get_timestamp(self): return 1234.
    def get_frame_timestamp_domain(self): return 'hardware_clock'
    def supports_frame_metadata(self, field): return field in self.values
    def get_frame_metadata(self, field): return self.values[field]
    def get_data(self): return np.zeros((3, 4, 3), dtype=np.uint8)


def fake_rs():
    identity = dict(name='Intel RealSense D435I', serial_number='SYNTHETIC', firmware_version='5.15.1.55', usb_type_descriptor='3.2')
    sensor = SimpleNamespace(supports=lambda key: True, set_option=Mock(), get_option=Mock(return_value=0))
    device = SimpleNamespace(supports=lambda key: key in identity, get_info=lambda key: identity[key],
                             query_sensors=lambda: [sensor], first_color_sensor=lambda: sensor)
    frame = Frame()
    pair = SimpleNamespace(get_color_frame=lambda: frame, get_depth_frame=lambda: frame)
    pipeline = SimpleNamespace(start=Mock(return_value=SimpleNamespace(get_device=lambda: device)),
                               wait_for_frames=Mock(return_value=pair), stop=Mock())
    config = SimpleNamespace(enable_device=Mock(), enable_stream=Mock())
    rs = SimpleNamespace(__version__='2.58.4', camera_info=SimpleNamespace(**{key:key for key in identity}),
        frame_metadata_value=SimpleNamespace(**{key:key for key in frame.values}),
        context=lambda: SimpleNamespace(query_devices=lambda: [device]),
        pipeline=lambda: pipeline, config=lambda: config,
        stream=SimpleNamespace(depth='depth', color='color'), format=SimpleNamespace(z16='z16', bgr8='bgr8'),
        option=SimpleNamespace(global_time_enabled='global', enable_auto_exposure='auto', exposure='exposure'),
        align=lambda stream: SimpleNamespace(process=lambda frames: frames))
    return rs, sensor, pipeline, config, frame


class BaseHostIdentityTests(unittest.TestCase):
    def test_unavailable_boot_identity_keeps_preview_packet_and_never_opens_rpc(self):
        with patch('rehab_vision.base_client.get_host_boot_id', side_effect=RuntimeError('Windows boot query failed')):
            client = BaseClient()
        point = dict(name='大椎', side=None, coordinate_valid=True, camera_point_mm=[1.,2.,500.])
        with patch('rehab_vision.base_client.connect') as connect:
            packet = client.project([point], {}, 1.)['data']
        connect.assert_not_called()
        self.assertFalse(packet['capture_time_valid'])
        self.assertFalse(packet['points'][0]['base_coordinate_valid'])
        self.assertEqual(packet['points'][0]['camera_point_mm'], [1.,2.,500.])
        self.assertIn('本次系统启动标识', packet['error'])

    def test_request_uses_same_shared_boot_identity_without_import_time_proc_access(self):
        identity = '01234567-89ab-cdef-0123-456789abcdef'
        with patch('rehab_vision.base_client.get_host_boot_id', return_value=identity):
            client = BaseClient()
        ws = Mock()
        ws.recv.side_effect = RuntimeError('offline')
        with patch('rehab_vision.base_client.connect', return_value=ws):
            client.project([], {}, 1.)
        self.assertEqual(json.loads(ws.send.call_args.args[0])['data']['host_boot_id'], identity)


class WindowsClockTests(unittest.TestCase):
    def test_windows_frames_are_diagnostic_and_cannot_reuse_linux_exposure_model(self):
        rs, sensor, pipeline, _, frame = fake_rs()
        with patch('sys.platform', 'win32'):
            clock = CaptureClock(rs, pipeline.start.return_value, 166, 30)
            info = clock.diagnostics(frame, frame)
        sensor.set_option.assert_not_called()
        self.assertIn('Windows', clock.error)
        with self.assertRaisesRegex(ValueError, '尚未验证'):
            clock.read(frame, frame)
        self.assertTrue(info['diagnostic_only'])
        self.assertEqual(info['platform'], 'win32')
        self.assertEqual(info['color']['metadata_raw']['actual_exposure'], 166)
        self.assertNotIn('actual_exposure_us', info['color'])

    def test_missing_metadata_reports_the_exact_field_without_invented_value(self):
        rs, _, _, _, frame = fake_rs()
        frame.values.pop('sensor_timestamp')
        result = frame_diagnostics(rs, frame)
        self.assertEqual(result['missing_metadata'], ['sensor_timestamp'])
        self.assertNotIn('sensor_timestamp', result['metadata_raw'])
        self.assertEqual(result['timestamp_domain'], 'hardware_clock')

    def test_device_identity_read_error_does_not_abort_camera_construction(self):
        rs, _, pipeline, _, _ = fake_rs()
        pipeline.start.return_value.get_device = Mock(side_effect=RuntimeError('USB metadata unavailable'))
        clock = CaptureClock(rs, pipeline.start.return_value, 166, 30)
        self.assertIn('USB metadata unavailable', clock.error)


class CameraLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Compile only the capture class, avoiding the optional inference stack.
        path = ROOT / 'zhenjiu/acupoint_locator_refactored.py'
        source = ast.parse(path.read_text(encoding='utf-8'))
        node = next(node for node in source.body if isinstance(node, ast.ClassDef) and node.name == 'RealSenseCapture')
        namespace = dict(np=np, time=time, sys=sys)
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), namespace)
        cls.Capture = namespace['RealSenseCapture']

    def test_global_clock_option_failure_preserves_rgbd_and_suppresses_old_evidence(self):
        rs, sensor, pipeline, _, _ = fake_rs()
        sensor.set_option.side_effect = RuntimeError('option unsupported by driver')
        clock = SimpleNamespace(read=Mock(return_value={'unsafe': True}), restore=Mock(return_value=[]))
        with patch.dict(sys.modules, pyrealsense2=rs), \
             patch('rehab_vision.capture_clock.CaptureClock', return_value=clock):
            capture = self.Capture(manual_rgb_exposure=166)
        self.assertTrue(capture.isOpened())
        with patch('sys.stderr', io.StringIO()):
            ok, image = capture.read()
        self.assertTrue(ok)
        self.assertEqual(image.shape, (3,4,3))
        clock.read.assert_not_called()
        self.assertIn('全局时钟设置失败', capture.frame_metadata['exposure_clock_error'])
        self.assertNotIn('exposure_clock', capture.frame_metadata)
        pipeline.wait_for_frames.side_effect = RuntimeError('unplugged')
        with patch('sys.stderr', io.StringIO()):
            self.assertEqual(capture.read(), (False, None))
        self.assertEqual(capture.frame_metadata, {})
        self.assertIsNone(capture.raw_depth_frame)
        capture.release()
        pipeline.stop.assert_called_once()

    def test_windows_uses_the_same_global_camera_lock_and_releases_once(self):
        with patch('sys.platform', 'win32'), patch('rehab_vision.camera_lock.WindowsMutex') as mutex:
            lock = CameraLock().acquire()
            self.assertEqual(mutex.call_args.args[0], r'Global\anmo_robot_vision_worker')
            mutex.return_value.acquire.assert_called_once_with()
            with self.assertRaises(RuntimeError): lock.acquire()
            lock.close(); lock.close()
            mutex.return_value.close.assert_called_once_with()


class CameraDiagnosticTests(unittest.TestCase):
    def test_collects_raw_rgbd_without_exposure_changes_reset_or_inference(self):
        rs, sensor, pipeline, config, _ = fake_rs()
        report = diagnose.collect(rs, frames=3)
        self.assertEqual(report['status'], 'frames_received')
        self.assertEqual(len(report['frames']), 3)
        self.assertFalse(report['capture_time_valid'])
        self.assertEqual(report['frames'][0]['native_depth']['metadata_raw']['actual_exposure'], 166)
        config.enable_stream.assert_any_call('depth',640,480,'z16',30)
        sensor.set_option.assert_not_called()
        pipeline.stop.assert_called_once_with()
        self.assertTrue(all(0 < call.args[0] <= 1000 for call in pipeline.wait_for_frames.call_args_list))

    def test_missing_camera_does_not_start_a_pipeline(self):
        rs, _, pipeline, _, _ = fake_rs()
        rs.context = lambda: SimpleNamespace(query_devices=lambda: [])
        report = diagnose.collect(rs)
        self.assertIn('未枚举', report['error'])
        pipeline.start.assert_not_called()

    def test_unplug_during_diagnostic_releases_pipeline_and_reports_failure(self):
        rs, _, pipeline, _, _ = fake_rs()
        pipeline.wait_for_frames.side_effect = RuntimeError('USB disconnected')
        report = diagnose.collect(rs)
        self.assertEqual(report['status'], 'error')
        self.assertIn('USB disconnected', report['error'])
        pipeline.stop.assert_called_once_with()

    def test_native_sdk_hang_is_bounded_outside_the_camera_process(self):
        with patch.object(diagnose.subprocess, 'run', side_effect=subprocess.TimeoutExpired('fake', 15)) as run:
            report = diagnose.bounded_report(10, 3)
        self.assertEqual(run.call_args.kwargs['timeout'], 15)
        self.assertIn('超过诊断时限', report['error'])
        self.assertFalse(report['capture_time_valid'])


if __name__ == '__main__': unittest.main()
