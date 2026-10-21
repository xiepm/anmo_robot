import sys
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'zhenjiu'))
from rehab_vision.capture_clock import CaptureClock
from massage_controller.capture_policy import SUPPORTED

class Frame:
 def __init__(self):self.values=dict(frame_timestamp=100000,sensor_timestamp=99917,actual_exposure=166,auto_exposure=0)
 def supports_frame_metadata(self,k):return k in self.values
 def get_frame_metadata(self,k):return self.values[k]
 def get_frame_timestamp_domain(self):return 'global'
 def get_frame_number(self):return 42
 def get_timestamp(self):return 123456.

class ClockTests(unittest.TestCase):
 def setUp(self):
  self.clock=CaptureClock.__new__(CaptureClock);self.clock.exposure=166
  self.clock.rs=SimpleNamespace(timestamp_domain=SimpleNamespace(global_time='global'),
   frame_metadata_value=SimpleNamespace(**{k:k for k in Frame().values}))
 def test_rgb_uvc_units_are_not_depth_microseconds(self):
  self.assertEqual(self.clock.frame(Frame(),'color')['actual_exposure_us'],16600)
  self.assertEqual(self.clock.frame(Frame(),'depth')['actual_exposure_us'],166)
 def test_missing_metadata_or_auto_mode_never_fills_exposure(self):
  f=Frame();f.values.pop('actual_exposure')
  with self.assertRaises(ValueError):self.clock.frame(f,'color')
  f=Frame();f.values['auto_exposure']=1
  with self.assertRaises(ValueError):self.clock.frame(f,'color')

class ClockPolicyTests(unittest.TestCase):
 def setUp(self):
  self.policy=dict(SUPPORTED,version=1,verified=True,camera_serial='SYNTHETIC-TEST-CAMERA')
  self.settings={'auto':1,'exposure':10};self.writes=[]
  def set_option(key,value):self.settings[key]=value;self.writes.append((key,value))
  sensor=SimpleNamespace(get_option=lambda k:self.settings[k],set_option=set_option)
  identity={'serial':'SYNTHETIC-TEST-CAMERA','firmware':'5.15.1.55'}
  device=SimpleNamespace(get_info=lambda k:identity[k],first_color_sensor=lambda:sensor)
  self.profile=SimpleNamespace(get_device=lambda:device)
  self.rs=SimpleNamespace(__version__='2.58.4',camera_info=SimpleNamespace(serial_number='serial',firmware_version='firmware'),
                          option=SimpleNamespace(enable_auto_exposure='auto',exposure='exposure'))
 def test_enrolled_device_keeps_manual_exposure_readback(self):
  with patch('rehab_vision.capture_clock.load_capture_policy',return_value=self.policy):
   clock=CaptureClock(self.rs,self.profile,100,30)
  self.assertEqual(clock.error,'')
  self.assertEqual(self.settings,{'auto':0,'exposure':100})
  clock.restore()
  self.assertEqual(self.settings,{'auto':1,'exposure':10})
 def test_wrong_identity_or_unsupported_exposure_never_changes_sensor(self):
  for serial,exposure,fps in [('other',100,30),('SYNTHETIC-TEST-CAMERA',301,30),('SYNTHETIC-TEST-CAMERA',100,60)]:
   with self.subTest(serial=serial,exposure=exposure,fps=fps):
    policy=dict(self.policy,camera_serial=serial)
    with patch('rehab_vision.capture_clock.load_capture_policy',return_value=policy):
     clock=CaptureClock(self.rs,self.profile,exposure,fps)
    self.assertTrue(clock.error)
    self.assertEqual(self.writes,[])
 def test_missing_policy_reports_capture_error_without_sensor_changes(self):
  with patch('rehab_vision.capture_clock.load_capture_policy',side_effect=ValueError('unconfigured')):
   clock=CaptureClock(self.rs,self.profile,100,30)
  self.assertEqual(clock.error,'unconfigured')
  self.assertEqual(self.writes,[])
