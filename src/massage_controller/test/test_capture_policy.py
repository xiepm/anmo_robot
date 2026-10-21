import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from massage_controller.capture_policy import SUPPORTED, load_capture_policy, policy_identity


class CapturePolicyTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)/'policy.json'
        self.policy = dict(SUPPORTED, version=1, verified=True, camera_serial='SYNTHETIC-TEST-CAMERA')

    def write(self, policy):
        self.path.write_text(json.dumps(policy))

    def test_explicit_policy_and_environment_override(self):
        self.write(self.policy)
        with patch.dict(os.environ, {'REHAB_CAPTURE_TIMING': str(self.path)}):
            self.assertEqual(load_capture_policy(), self.policy)
        self.assertEqual(policy_identity(load_capture_policy(self.path)),
                         dict(camera_serial='SYNTHETIC-TEST-CAMERA', firmware='5.15.1.55', sdk_version='2.58.4'))

    def test_missing_invalid_and_unverified_policy_fail_closed(self):
        with self.assertRaises(ValueError):
            load_capture_policy(self.path)
        self.path.write_text('not JSON')
        with self.assertRaises(ValueError):
            load_capture_policy(self.path)
        for key, value in [('verified', False), ('verified', 1), ('camera_serial', ''),
                           ('camera_serial', None), ('model', 'unknown'), ('firmware', 'other'),
                           ('sdk_version', 'other'), ('fps', 60), ('color_exposure_unit_us', 1),
                           ('depth_exposure_unit_us', 100), ('color_exposure_raw_range', [1, 1000])]:
            with self.subTest(key=key, value=value):
                policy = copy.deepcopy(self.policy)
                policy[key] = value
                self.write(policy)
                with self.assertRaises(ValueError):
                    load_capture_policy(self.path)

    def test_published_example_is_not_an_enrolled_device(self):
        example = Path(__file__).resolve().parents[3]/'config/commissioning/capture_timing.example.json'
        with self.assertRaises(ValueError):
            load_capture_policy(example)
