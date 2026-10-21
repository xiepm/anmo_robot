import copy,json,unittest
from unittest.mock import patch
from jsonschema import validate
from massage_controller.capture_timing import exposure_window
from massage_controller.base_coordinates import BaseProjector
from massage_controller.protocol import decode, load_contract
from test_base_coordinates import fixture,CAL,SCHEMAS
from massage_controller.capture_policy import SUPPORTED

SYNTHETIC_POLICY=dict(SUPPORTED,version=1,verified=True,camera_serial='SYNTHETIC-TEST-CAMERA')


def proof():
 frame=dict(domain='global_time',global_timestamp_ms=1009980.,frame_timestamp_us=10000000,sensor_timestamp_us=9995000,actual_exposure_us=10000,exposure_raw=10000,exposure_unit_us=1,auto_exposure=1,frame_number=42)
 color=dict(frame,exposure_raw=100,exposure_unit_us=100,auto_exposure=0)
 return dict(model="D435I_MANUAL_RGB_INTERVAL_V1",camera_serial="SYNTHETIC-TEST-CAMERA",firmware="5.15.1.55",sdk_version="2.58.4",host_monotonic_s=10.,host_unix_s=1010.,clock_read_width_s=.0001,color=color,depth=copy.deepcopy(frame))

class TimingTests(unittest.TestCase):
 def setUp(self):
  policy=patch('massage_controller.capture_timing.load_capture_policy',return_value=copy.deepcopy(SYNTHETIC_POLICY))
  policy.start();self.addCleanup(policy.stop)
 def test_rgb_units_model_auto_mode_and_photo_identity_fail_closed(self):
  for field,value in [('exposure_unit_us',1),('actual_exposure_us',100),('auto_exposure',1)]:
   p=proof();p['color'][field]=value
   with self.assertRaises(ValueError):exposure_window(p,10.,10.2,1000.)
  p=proof();p['model']='legacy'
  with self.assertRaises(ValueError):exposure_window(p,10.,10.2,1000.)
  projector=BaseProjector(CAL);request,history=fixture();request['data']['exposure_clock']=proof()
  request['data']['exposure_clock']['color']['frame_number']=41
  with patch('massage_controller.base_coordinates.time.time',return_value=1010.2),patch('massage_controller.base_coordinates.time.monotonic',return_value=10.2):
   d=projector.project(request,history,10.2)['data']
  self.assertFalse(d['capture_time_valid']);self.assertIn('本次照片',d['capture_time_error'])
  self.assertNotIn('exposure_clock',d)
 def test_exposure_center_correction_and_both_sensor_window(self):
  w=exposure_window(proof(),10.,10.2,1000.)
  self.assertAlmostEqual(w['start'],9.92-1/30)
  self.assertAlmostEqual(w['end'],10.03+1/30)
 def test_old_future_hardware_only_and_wall_clock_jump_rejected(self):
  for field,value in [('domain','hardware_clock'),('global_timestamp_ms',1009000.),('global_timestamp_ms',1011000.),('sensor_timestamp_us',10010000),('actual_exposure_us',0)]:
   p=proof();p['color'][field]=value
   with self.subTest(field=field,value=value),self.assertRaises(ValueError):exposure_window(p,10.,10.2,1000.)
  with self.assertRaises(ValueError):exposure_window(proof(),10.,10.2,1000.01)
  p=proof();p['depth']['global_timestamp_ms']-=50
  with self.assertRaises(ValueError):exposure_window(p,10.,10.2,1000.)
 def test_valid_exposure_still_cannot_promote_unapproved_calibration(self):
  projector=BaseProjector(CAL);request,history=fixture();request['data']['exposure_clock']=proof()
  with patch('massage_controller.base_coordinates.time.time',return_value=1010.2),patch('massage_controller.base_coordinates.time.monotonic',return_value=10.2):
   answer=projector.project(request,history,10.2)
   self.assertEqual(answer['data']['error'],'')
   self.assertTrue(answer['data']['capture_time_valid']);self.assertFalse(answer['data']['motion_eligible'])
   validate(answer,json.loads((SCHEMAS/'base_coordinates_response.schema.json').read_text()))
   projector.calibration['suitability'].update(absolute_base_accuracy_verified=True,robot_motion_approved=True)
   approved=projector.project(request,history,10.2)
   self.assertTrue(approved['data']['motion_eligible'])
   history[1]['robot_state']['moving']=1
   invalid=projector.project(request,history,10.2)
   self.assertFalse(invalid['data']['motion_eligible']);self.assertFalse(invalid['data']['capture_time_valid'])
 def test_receipt_only_packet_remains_preview(self):
  request,history=fixture();answer=BaseProjector(CAL).project(request,history,10.2)
  self.assertFalse(answer['data']['capture_time_valid'])
  self.assertFalse(answer['data']['motion_eligible'])
  self.assertNotIn('exposure_clock',answer['data'])
 def test_verified_exposure_is_copied_and_accepted_by_both_base_response_contracts(self):
  request,history=fixture();request['data']['exposure_clock']=proof()
  # RGB and depth have independent counters; retain the verified depth counter.
  request['data']['exposure_clock']['depth']['frame_number']=73
  with patch('massage_controller.base_coordinates.time.time',return_value=1010.2),patch('massage_controller.base_coordinates.time.monotonic',return_value=10.2):
   answer=BaseProjector(CAL).project(request,history,10.2)
  self.assertTrue(answer['data']['capture_time_valid'])
  self.assertEqual(answer['data']['exposure_clock'],request['data']['exposure_clock'])
  request['data']['exposure_clock']['depth']['frame_number']=999
  request['data']['exposure_clock']['depth']['global_timestamp_ms']=0.
  self.assertEqual(answer['data']['exposure_clock']['depth']['frame_number'],73)
  self.assertEqual(answer['data']['exposure_clock']['depth']['global_timestamp_ms'],1009980.)
  schema=json.loads((SCHEMAS/'base_coordinates_response.schema.json').read_text())
  decode(json.dumps(answer),schema)
  result=dict(type='LOCATE_RESULT',data=dict(status='SUCCEEDED',region='BACK',view='back',
   capture_host_monotonic_ms=None,capture_time_valid=False,frame='camera_color_optical_frame',
   points=[],message='test',base_preview=answer))
  decode(json.dumps(result),load_contract(SCHEMAS)['vision_message.schema'])
  # Optional proof is still strict when present, including native frame IDs.
  answer['data']['exposure_clock']['depth']['frame_number']=73.5
  with self.assertRaises(ValueError):decode(json.dumps(answer),schema)
  with self.assertRaises(ValueError):decode(json.dumps(result),load_contract(SCHEMAS)['vision_message.schema'])
 def test_unverified_exposure_is_not_forwarded_as_evidence(self):
  for change in ('unit','domain','camera'):
   request,history=fixture();request['data']['exposure_clock']=proof()
   if change=='unit':request['data']['exposure_clock']['depth']['exposure_unit_us']=100
   elif change=='domain':request['data']['exposure_clock']['depth']['domain']='hardware_clock'
   else:request['data']['exposure_clock']['camera_serial']='other'
   with patch('massage_controller.base_coordinates.time.time',return_value=1010.2),patch('massage_controller.base_coordinates.time.monotonic',return_value=10.2):
    answer=BaseProjector(CAL).project(request,history,10.2)
   self.assertNotIn('exposure_clock',answer['data'])
   self.assertFalse(answer['data']['capture_time_valid'])

if __name__=='__main__':unittest.main()
