import copy
import tempfile
import unittest
from pathlib import Path
from massage_controller.absolute_accuracy import evaluate

I=[[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]]
C=dict(camera_serial='fixture',reference_frame='test',T_reference_camera=I)

class AccuracyTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  Path(self.tmp.name,'survey.txt').write_text('synthetic test evidence, not site data')
  self.data=dict(calibration_sha256='a'*64,camera_serial='fixture',reference_frame='test',
   independent_reference=dict(method='independent_survey',evidence_file='survey.txt',uncertainty_mm=.2),
   acceptance=dict(max_error_mm=2),measurements=[dict(id=str(i),reference_base_mm=p,camera_point_mm=[p[0]+1,p[1],p[2]],
    T_base_reference=I,capture_time_valid=True,capture_evidence_file='survey.txt')
    for i,p in enumerate([[0,0,500],[100,0,500],[0,100,500],[100,100,500],[50,50,500]])])
 def result(self):return evaluate(self.data,C,'a'*64,self.tmp.name)
 def test_reports_bias_without_refitting_and_does_not_approve_motion(self):
  r=self.result();self.assertFalse(r['blockers']);self.assertEqual(r['metrics']['bias_xyz_mm'],[1,0,0])
  self.assertEqual(r['metrics']['max_error_plus_reference_uncertainty_mm'],1.2)
  self.assertFalse(r['robot_motion_approved']);self.assertFalse(r['calibration_modified'])
 def test_outlier_missing_sync_changed_calibration_and_fitted_reference_fail(self):
  self.data['measurements'][-1]['camera_point_mm'][2]+=20
  self.assertTrue(self.result()['blockers']);self.assertGreater(self.result()['metrics']['max_error_mm'],20)
  self.data['measurements'][0]['capture_time_valid']=False
  self.data['calibration_sha256']='b'*64
  self.data['independent_reference']['method']='training_pnp_board_mean'
  r=self.result();self.assertGreaterEqual(len(r['blockers']),4)
 def test_empty_and_collinear_measurements_do_not_pass(self):
  for p in self.data['measurements']:p['reference_base_mm']=[0,0,500]
  self.assertIn('共线','；'.join(self.result()['blockers']))
  self.data['measurements']=[]
  self.assertIsNone(self.result()['metrics']);self.assertTrue(self.result()['blockers'])
