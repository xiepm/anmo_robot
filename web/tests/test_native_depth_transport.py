"""Native depth capture/transport checks with fake frames and no devices."""
import ast
import io
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'web/scripts'))
from frozen_depth import FrozenDepth
from vision_preview import LatestFrame, configure_capture_geometry, native_geometry_for_snapshot


def intrinsics(width=4, height=3, fx=100.):
    return SimpleNamespace(width=width, height=height, fx=fx, fy=fx,
                           ppx=0., ppy=0., model='none', coeffs=[0.] * 5)


def deproject(intrinsic, pixel, depth):
    return [pixel[0] * depth / intrinsic.fx, pixel[1] * depth / intrinsic.fy, depth]


class Profile:
    def __init__(self, intrinsic, extrinsics=None):
        self.intrinsics = intrinsic
        self.extrinsics = extrinsics
        self.target = None

    def as_video_stream_profile(self):
        return self

    def get_extrinsics_to(self, target):
        self.target = target
        return self.extrinsics


class Frame:
    def __init__(self, data=None, number=7, timestamp=1234.5, domain='global', units=.001):
        self.data = np.full((3, 4), 600, dtype=np.uint16) if data is None else data
        self.number, self.timestamp, self.domain, self.units = number, timestamp, domain, units
        self.profile = Profile(intrinsics())

    def get_data(self): return self.data
    def get_frame_number(self): return self.number
    def get_timestamp(self): return self.timestamp
    def get_frame_timestamp_domain(self): return self.domain
    def get_units(self): return self.units


def geometry():
    rays = np.zeros((3, 4, 3)); rays[..., 2] = 1.
    return dict(native_depth_m=np.full((3, 4), .6), native_rays=rays,
                T_color_depth=np.eye(4), native_depth_frame_number=7,
                aligned_depth_frame_number=7, native_depth_timestamp_ms=1234.5,
                aligned_depth_timestamp_ms=1234.5, geometry_frame='DEPTH_OPTICAL_NATIVE')


def frozen(native=None):
    rays = np.zeros((20, 20, 3)); rays[..., 2] = 1.; rays.setflags(write=False)
    return FrozenDepth(np.full((20, 20), .5), intrinsics(20, 20), deproject,
                       [dict(name='大椎', side='MIDLINE', coordinate_valid=True)],
                       rays=rays, native_geometry=native)


class NativeFreezeTests(unittest.TestCase):
    def test_freezes_native_arrays_without_changing_aligned_localization(self):
        source = geometry()
        source['native_depth_m'][0, 0] = 0.
        source['native_depth_m'][0, 1] = np.nan
        capture = frozen(source)
        source['native_depth_m'][:] = .9
        source['native_rays'][:] = 99.
        source['T_color_depth'][:] = 99.
        source['native_depth_frame_number'] = 99
        native = capture.native_geometry
        self.assertEqual(native['native_depth_m'][1, 1], .6)
        self.assertEqual(native['native_depth_m'][0, 0], 0.)
        self.assertTrue(np.isnan(native['native_depth_m'][0, 1]))
        self.assertEqual(native['native_depth_frame_number'], 7)
        np.testing.assert_array_equal(native['T_color_depth'], np.eye(4))
        for key in ('native_depth_m', 'native_rays', 'T_color_depth'):
            self.assertFalse(native[key].flags.writeable)
            with self.assertRaises(ValueError): native[key].flat[0] = 99.
        with self.assertRaises(TypeError): native['native_depth_frame_number'] = 99
        result = capture.resolve([dict(name='大椎', side='MIDLINE', x=5., y=5.)])[0]
        self.assertEqual(result['camera_point_mm'], [25., 25., 500.])

    def test_legacy_or_incomplete_geometry_does_not_get_a_native_tag(self):
        for missing in (None, *geometry().keys()):
            with self.subTest(missing=missing):
                native = None if missing is None else geometry()
                if native is not None: native.pop(missing)
                capture = frozen(native)
                self.assertIsNone(capture.native_geometry)
                frames = LatestFrame()
                frames.put_snapshot(b'photo', 88, 12., depth_resolver=capture)
                with np.load(io.BytesIO(frames.get_geometry(88, 12.)), allow_pickle=False) as archive:
                    self.assertEqual(set(archive.files), {'depth_m', 'rays', 'frame_number', 'received_monotonic_s'})

    def test_mismatched_exposure_or_invalid_model_never_gets_frozen(self):
        cases = {'aligned_depth_frame_number': 8, 'native_depth_frame_number': True,
                 'aligned_depth_timestamp_ms': 1234.6, 'native_depth_timestamp_ms': np.nan,
                 'geometry_frame': 'COLOR_ALIGNED', 'native_rays': np.ones((2, 4, 3)),
                 'T_color_depth': np.zeros((4, 4))}
        for key, value in cases.items():
            with self.subTest(key=key):
                data = geometry(); data[key] = value
                with self.assertRaises(ValueError): frozen(data)

    def test_transport_contains_independent_native_frame_and_retires_on_disconnect(self):
        frames = LatestFrame()
        frames.put_snapshot(b'photo', 88, 12., depth_resolver=frozen(geometry()))
        with np.load(io.BytesIO(frames.get_geometry(88, 12.)), allow_pickle=False) as archive:
            self.assertEqual(archive['geometry_frame'].item(), 'DEPTH_OPTICAL_NATIVE')
            self.assertEqual(archive['frame_number'].item(), 88)  # RGB counter may differ.
            self.assertEqual(archive['native_depth_frame_number'].item(), 7)
            self.assertEqual(archive['aligned_depth_frame_number'].item(), 7)
            self.assertEqual(archive['native_depth_timestamp_ms'].item(), 1234.5)
            self.assertEqual(archive['aligned_depth_timestamp_ms'].item(), 1234.5)
            self.assertEqual(archive['native_depth_m'].shape, (3, 4))
            self.assertEqual(archive['depth_m'].shape, (20, 20))
            np.testing.assert_array_equal(archive['T_color_depth'], np.eye(4))
        self.assertIsNone(frames.get_geometry(89, 12.))
        self.assertIsNone(frames.get_geometry(88, 13.))
        frames.invalidate('USB disconnected')
        self.assertIsNone(frames.get_geometry(88, 12.))

    def test_snapshot_uses_original_units_and_cannot_guess_missing_fields(self):
        native = Frame(units=.002)
        aligned = Frame(data=np.full((20, 20), 999, dtype=np.uint16))
        data = geometry()
        capture = dict(native_depth=native, depth=aligned, native_rays=data['native_rays'],
                       T_color_depth=data['T_color_depth'])
        result = frozen(native_geometry_for_snapshot(capture)).native_geometry
        np.testing.assert_allclose(result['native_depth_m'], 1.2)
        self.assertIsNone(native_geometry_for_snapshot({}))
        aligned.domain = 'hardware'
        with self.assertRaisesRegex(ValueError, '时钟域'): native_geometry_for_snapshot(capture)
        aligned.domain = 'global'; aligned.number = 8
        with self.assertRaisesRegex(ValueError, '曝光'): frozen(native_geometry_for_snapshot(capture))
        native.units = 0.
        with self.assertRaisesRegex(ValueError, '单位'): native_geometry_for_snapshot(capture)


class NativeProfileTests(unittest.TestCase):
    def test_reopen_refreshes_one_pipeline_models_and_column_major_extrinsics(self):
        rotation = np.array([[0., -1., 0.], [1., 0., 0.], [0., 0., 1.]])
        extrinsics = SimpleNamespace(rotation=rotation.flatten(order='F'), translation=[.02, .03, .04])
        depth_profile = Profile(intrinsics(), extrinsics)
        color_profile = Profile(intrinsics(8, 6, 200.))
        active = SimpleNamespace(get_stream=lambda stream: {'depth': depth_profile, 'color': color_profile}[stream])
        pipeline = SimpleNamespace(get_active_profile=Mock(return_value=active))
        rs = SimpleNamespace(stream=SimpleNamespace(depth='depth', color='color'),
                             rs2_deproject_pixel_to_point=deproject)
        capture = SimpleNamespace(pipeline=pipeline, rs=rs, raw_depth_frame=object())
        configure_capture_geometry(capture)
        self.assertIs(depth_profile.target, color_profile)
        self.assertIsNone(capture.raw_depth_frame)
        np.testing.assert_allclose(capture.T_color_depth[:3, :3] @ [1., 0., 0.], [0., 1., 0.])
        np.testing.assert_allclose(capture.T_color_depth[:3, 3], [.02, .03, .04])
        self.assertEqual(capture.native_scene_rays.shape, (3, 4, 3))
        self.assertEqual(capture.scene_rays.shape, (6, 8, 3))
        self.assertFalse(capture.native_scene_rays.flags.writeable)
        first = capture.native_scene_rays
        depth_profile.intrinsics = intrinsics(5, 2, 50.)
        extrinsics.translation = [.04, .05, .06]
        configure_capture_geometry(capture)
        self.assertIsNot(first, capture.native_scene_rays)
        self.assertEqual(capture.native_scene_rays.shape, (2, 5, 3))
        np.testing.assert_allclose(capture.T_color_depth[:3, 3], [.04, .05, .06])
        self.assertEqual(pipeline.get_active_profile.call_count, 2)
        pipeline.get_active_profile.side_effect = RuntimeError('USB disconnected')
        with self.assertRaises(RuntimeError): configure_capture_geometry(capture)
        for key in ('native_scene_rays', 'native_scene_intrinsics_key', 'native_intrinsics',
                    'T_color_depth', 'raw_depth_frame', 'scene_rays', 'scene_intrinsics_key'):
            self.assertIsNone(getattr(capture, key))


class OriginalReadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Execute the real read method without importing the locator's optional
        # inference stack, and without constructing any pyrealsense2 pipeline.
        path = ROOT / 'zhenjiu/acupoint_locator_refactored.py'
        source = ast.parse(path.read_text())
        capture = next(node for node in source.body if isinstance(node, ast.ClassDef) and node.name == 'RealSenseCapture')
        method = next(node for node in capture.body if isinstance(node, ast.FunctionDef) and node.name == 'read')
        namespace = dict(np=np, time=time, sys=sys)
        exec(compile(ast.Module(body=[method], type_ignores=[]), str(path), 'exec'), namespace)
        cls.read = staticmethod(namespace['read'])

    def make(self, raw=None, aligned=None):
        raw, aligned = raw or Frame(), aligned or Frame()
        color = Frame(data=np.zeros((3, 4, 3), dtype=np.uint8), number=88)
        raw_frames = SimpleNamespace(get_depth_frame=Mock(return_value=raw))
        aligned_frames = SimpleNamespace(get_depth_frame=lambda: aligned, get_color_frame=lambda: color)
        def align(frames):
            self.assertIs(frames, raw_frames)
            raw_frames.get_depth_frame.assert_called_once_with()
            return aligned_frames
        capture = SimpleNamespace(pipeline=SimpleNamespace(wait_for_frames=Mock(return_value=raw_frames)),
            align=SimpleNamespace(process=Mock(side_effect=align)), capture_clock=SimpleNamespace(read=Mock(return_value={})),
            serial='fake', raw_depth_frame=object(), depth_frame=object())
        return capture, raw, aligned

    def test_read_keeps_raw_exposure_from_the_exact_frameset_before_alignment(self):
        capture, raw, aligned = self.make()
        ok, image = self.read(capture)
        self.assertTrue(ok)
        self.assertEqual(image.shape, (3, 4, 3))
        self.assertIs(capture.raw_depth_frame, raw)
        self.assertIs(capture.depth_frame, aligned)
        self.assertEqual(capture.frame_metadata['color_frame_number'], 88)
        capture.pipeline.wait_for_frames.assert_called_once_with(5000)
        capture.pipeline.wait_for_frames.side_effect = RuntimeError('USB disconnected')
        with patch('sys.stderr', io.StringIO()): self.assertEqual(self.read(capture), (False, None))
        self.assertIsNone(capture.raw_depth_frame)
        self.assertIsNone(capture.depth_frame)

    def test_frame_timestamp_or_domain_mismatch_fails_without_publishing_clock_evidence(self):
        for kwargs in (dict(number=8), dict(timestamp=1234.6), dict(timestamp=np.nan), dict(domain='hardware')):
            with self.subTest(kwargs=kwargs):
                capture, _, _ = self.make(aligned=Frame(**kwargs))
                with patch('sys.stderr', io.StringIO()): self.assertEqual(self.read(capture), (False, None))
                self.assertIsNone(capture.raw_depth_frame)
                self.assertIsNone(capture.depth_frame)
                capture.capture_clock.read.assert_not_called()


if __name__ == '__main__':
    unittest.main()
