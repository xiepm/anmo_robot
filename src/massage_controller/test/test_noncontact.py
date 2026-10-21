import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from jsonschema import validate
import test_point_confirmation
from massage_controller.noncontact_plan import profile_errors,eligibility_errors,compile_route
from massage_controller.noncontact_control import NoncontactControl
from massage_controller.waypoint_plan import pose,command_tcp

PROFILE=dict(scope='mannequin_or_fixture_noncontact_only',physical_tool_offset_flange_mm=[0,0,20],
 minimum_surface_clearance_mm=20,workspace_base_mm={'min':[-900,-500,100],'max':[-300,500,700]},
 execution_rxyz_deg=[180,0,0],safe_transfer_height_base_mm=400,surface_ceiling_base_mm=260,
 path_confirmed=True,speed_mm_s=5,acceleration_mm_s2=10,dwell_per_point_s=2,extra_clearance_mm=10)
START=[-600,-200,450,180,0,0]
ROT=[[1,0,0,0],[0,-1,0,0],[0,0,-1,0],[0,0,0,1]]
POINTS=[dict(name='大椎',side='MIDLINE',base_point_mm=[-610,-220,230])]

class NoncontactGeometryTests(unittest.TestCase):
 def test_tool_offset_and_clearance_are_separate_and_all_stages_bounded(self):
  plan,targets=compile_route(PROFILE,POINTS,START,ROT)
  self.assertEqual(targets[0]['tool_tip_base_mm'],[-610,-220,290])
  self.assertEqual(targets[0]['flange_base_mm'],[-610,-220,310])
  self.assertEqual(command_tcp(plan),[0.]*6)
  self.assertEqual([q['name'] for q in plan['points']],['ENTRY_LIFT','1_TRANSFER','1_APPROACH','1_RETRACT'])
  for q in plan['points']:
   p=pose(q['pose'],q['name']);self.assertEqual(p[3:],START[3:])
   self.assertGreaterEqual(p[2]-20,PROFILE['surface_ceiling_base_mm']+30)
 def test_unknown_geometry_wrong_attitude_and_unsafe_entry_rejected(self):
  for key,value in [('workspace_base_mm',None),('execution_rxyz_deg',None),('path_confirmed',False),('surface_ceiling_base_mm',None)]:
   with self.subTest(key=key):
    with self.assertRaises(ValueError):compile_route(dict(PROFILE,**{key:value}),POINTS,START,ROT)
  for start in [[-600,-200,450,170,0,0],[-600,-200,270,180,0,0]]:
   with self.assertRaises(ValueError):compile_route(PROFILE,POINTS,start,ROT)
  with self.assertRaises(ValueError):compile_route(dict(PROFILE,safe_transfer_height_base_mm=280),POINTS,START,ROT)
 def test_edited_point_changes_target_and_out_of_bounds_is_not_clamped(self):
  changed=copy.deepcopy(POINTS);changed[0]['base_point_mm'][0]=-620
  _,targets=compile_route(PROFILE,changed,START,ROT)
  self.assertEqual(targets[0]['flange_base_mm'][0],-620)
  changed[0]['base_point_mm'][0]=-2000
  with self.assertRaises(ValueError):compile_route(PROFILE,changed,START,ROT)
  changed[0]['base_point_mm']=[-610,-220,300]
  with self.assertRaises(ValueError):compile_route(PROFILE,changed,START,ROT)
 def test_preview_calibration_or_unsynchronized_capture_never_authorizes_motion(self):
  self.assertFalse(profile_errors(PROFILE))
  self.assertEqual(len(eligibility_errors(PROFILE,{},{})),3)
  calibration={'suitability':{'absolute_base_accuracy_verified':True,'robot_motion_approved':True}}
  self.assertEqual(len(eligibility_errors(PROFILE,calibration,{'capture_time_valid':False,'motion_eligible':False})),1)
  self.assertFalse(eligibility_errors(PROFILE,calibration,{'capture_time_valid':True,'motion_eligible':True}))

class Worker:
 def __init__(self): self.calls=[];self.state={'state':'NONE'}
 def submit(self,profile,points,revision):self.calls.append(('prepare',copy.deepcopy(points)));self.state={'state':'PREPARING','revision':revision}
 def execution_snapshot(self):return self.state
 def start_route(self,sha):self.calls.append(('start',sha));return sha==self.state.get('plan_sha256')
 def stop_route(self):self.calls.append(('stop',))
 def request_stop_check(self):self.calls.append(('check_stop',))

class NoncontactControlTests(unittest.TestCase):
 request=test_point_confirmation.PointConfirmationTests.request
 result=test_point_confirmation.PointConfirmationTests.result
 send=test_point_confirmation.PointConfirmationTests.send
 def setUp(self):
  test_point_confirmation.PointConfirmationTests.setUp(self)
  self.request('CONFIRM_POINTS',self.data);self.send(self.result())
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
  self.path=Path(self.temp.name)/'profile.json';self.path.write_text(json.dumps(PROFILE))
  self.worker=Worker();self.nc=NoncontactControl(self.core,self.path,self.worker);self.core.noncontact=self.nc
  self.command={'revision':self.data['revision'],'points':[{'name':'大椎','side':'MIDLINE'}]}
 def authorized_fixture(self):
  # Synthetic approvals ONLY in tests, never written into real calibration.
  self.core.confirmation.projector.calibration['suitability']={'absolute_base_accuracy_verified':True,'robot_motion_approved':True}
  self.core.confirmation.packet.update(capture_time_valid=True,motion_eligible=True)
 def ready(self):
  self.authorized_fixture();self.assertTrue(self.request('PREPARE_NONCONTACT',self.command)['ok'])
  self.worker.state={'state':'READY','plan_sha256':'a'*64}
  self.core.tick();self.assertEqual(self.core.state,'READY')
 def test_real_preview_is_rejected_before_worker_or_sdk(self):
  response=self.request('PREPARE_NONCONTACT',self.command)
  self.assertFalse(response['ok']);self.assertFalse(self.worker.calls)
  self.assertIn('采集同步',response['message'])
 def test_exact_confirmed_points_are_frozen_and_start_digest_required(self):
  self.ready()
  self.assertEqual(self.worker.calls[0][1],self.core.confirmation.value['points'])
  self.assertFalse(self.request('START_NONCONTACT',{'plan_sha256':'b'*64})['ok'])
  self.assertEqual(self.worker.calls[-1],('stop',))
 def test_preview_delay_blocks_prepare_and_start_without_motion(self):
  from massage_controller.commissioning_authorization import authorization_path
  self.authorized_fixture()
  sidecar=authorization_path(self.path);sidecar.write_text(json.dumps({'binding':{'revision':self.data['revision']}}))
  test_point_confirmation.PointConfirmationTests.health(self,750)
  self.assertIsNotNone(self.core.confirmation.packet)
  self.assertEqual(self.core.confirmation.value['state'],'FAILED')
  self.assertTrue(self.nc.authorization_revoked)
  self.assertFalse(sidecar.exists())
  self.assertFalse(self.request('PREPARE_NONCONTACT',self.command)['ok'])
  self.assertFalse(self.request('START_NONCONTACT',{'plan_sha256':'a'*64})['ok'])
  self.assertFalse(self.worker.calls)
 def test_preview_delay_still_stops_every_active_noncontact_phase(self):
  for phase in ('PREPARING','READY','RUNNING'):
   with self.subTest(phase=phase):
    self.setUp();self.ready()
    self.worker.state={'state':phase,'plan_sha256':'a'*64}
    self.core.state='RUNNING' if phase=='RUNNING' else 'READY' if phase=='READY' else 'IDLE'
    test_point_confirmation.PointConfirmationTests.health(self,750)
    self.assertIn(('stop',),self.worker.calls)
    self.assertEqual(self.core.state,'STOPPING');self.assertTrue(self.nc.faulted)
 def test_preview_delay_without_confirmation_does_not_touch_other_authorization(self):
  from massage_controller.commissioning_authorization import authorization_path
  self.request('CLEAR_POINTS')
  sidecar=authorization_path(self.path);saved=json.dumps({'binding':{'revision':'previous-route'}})
  sidecar.write_text(saved)
  test_point_confirmation.PointConfirmationTests.health(self,750)
  self.assertEqual(sidecar.read_text(),saved)
  self.assertFalse(self.nc.authorization_revoked)
  self.assertIsNotNone(self.core.confirmation.packet)
 def test_start_waits_for_observed_running_and_stop_for_observed_stop(self):
  self.ready();self.assertTrue(self.request('START_NONCONTACT',{'plan_sha256':'a'*64})['ok'])
  self.assertEqual(self.core.state,'READY')
  self.worker.state={'state':'RUNNING','step':'MOVE','point':'1_APPROACH'}
  self.core.tick();self.assertEqual(self.core.state,'RUNNING')
  self.assertTrue(self.request('STOP')['ok']);self.assertEqual(self.core.state,'STOPPING')
  self.worker.state={'state':'STOPPED','stop_confirmed':True}
  self.core.tick();self.assertEqual((self.core.state,self.core.result),('IDLE','STOPPED'))
  self.assertIsNone(self.core.confirmation.value)
 def test_ui_loss_requests_stop_never_resumes_and_unconfirmed_stop_is_error(self):
  self.ready();self.core.ui_lost()
  self.assertEqual(self.worker.calls[-1],('stop',))
  self.worker.state={'state':'STOP_UNCONFIRMED','error':'STOP 无反馈','stop_confirmed':False}
  self.core.tick();self.assertEqual(self.core.state,'ERROR')
  self.assertFalse(self.core.feedback_recoverable)
 def test_profile_change_invalidates_prepared_route(self):
  self.ready();self.path.write_text(json.dumps(dict(PROFILE,speed_mm_s=6)))
  self.assertFalse(self.request('START_NONCONTACT',{'plan_sha256':'a'*64})['ok'])
  self.assertEqual(self.worker.calls[-1],('stop',))
 def test_status_matches_shared_contract(self):
  root=Path('/workspace/config/rehab_protocol')
  if not root.exists():root=Path(__file__).resolve().parents[3]/'config/rehab_protocol'
  validate(self.core.status(),json.loads((root/'ui_response.schema.json').read_text()))
 def test_manual_profile_without_depth_review_keeps_existing_execution_gates(self):
  self.authorized_fixture()
  class Review:
   def sync(self,*args):return None
   def candidate(self,*args):raise ValueError('没有当前已确认路线')
  self.nc.review=Review()
  status=self.nc.status()
  self.assertTrue(status['available'])
  self.assertFalse(status['authorization_available'])
  self.assertEqual(status['blockers'],[])
  self.assertEqual(self.worker.calls,[])
 def test_stop_check_requires_idle_observation_and_does_not_prepare_motion(self):
  self.core.state='IDLE';self.core.device.observation_ready=True
  self.assertTrue(self.request('CHECK_NONCONTACT_STOP')['ok'])
  self.assertEqual(self.worker.calls,[('check_stop',)])
  self.core.state='RUNNING'
  self.assertFalse(self.request('CHECK_NONCONTACT_STOP')['ok'])
  self.assertEqual(self.worker.calls,[('check_stop',)])
 def test_selected_candidate_profile_is_used_without_overriding_execution_gates(self):
  configured=dict(PROFILE,geometry_source='FROZEN_DEPTH')
  self.path.write_text(json.dumps(configured))
  selected=dict(configured,safe_transfer_height_base_mm=300)
  class Review:
   def candidate(self,confirmation,path):return {'details':{'profile':copy.deepcopy(selected)}}
  self.nc.review=Review();self.core.confirmation.scene={'unknown_cells':0,'corridor_cells':1}
  self.assertEqual(self.nc.effective_profile()['safe_transfer_height_base_mm'],300)
  with self.assertRaisesRegex(ValueError,'整臂碰撞'):
   self.nc.prepare(self.command)
  self.assertEqual(self.worker.calls,[])

if __name__=='__main__':unittest.main()

class NoncontactWorkerTests(unittest.TestCase):
 def execute(self,stop_before_start=False,reject=False):
  from test_waypoint import Clock,FakeReader
  from massage_controller.noncontact_worker import NoncontactWorker
  from massage_controller.waypoint_motion import WaypointSession
  clock=Clock();reader=FakeReader(clock);reader.connect()
  reader.target=reader.last_target=START[:]
  reader.rotation_for=lambda angles:ROT
  reader.reject_move=reject
  events=[];worker=NoncontactWorker({},'unused-test-sdk',events.append)
  def sample():
   status=worker.execution_snapshot()
   if status['state']=='READY':
    if stop_before_start:worker.stop_route()
    else:self.assertTrue(worker.start_route(status['plan_sha256']))
  reader.on_sample=sample
  worker.submit(PROFILE,POINTS,'test-revision')
  def factory(plan,borrowed,log):return WaypointSession(plan,borrowed,log,clock=clock,sleep=clock.sleep)
  with patch('massage_controller.noncontact_worker.WaypointSession',side_effect=factory):worker.process_work(reader)
  return worker,reader,events
 def test_existing_sdk_connection_is_reused_and_real_stop_feedback_is_required(self):
  worker,reader,events=self.execute()
  self.assertEqual(worker.execution_snapshot()['state'],'COMPLETED')
  self.assertIs(worker.execution_snapshot()['motion_attempted'],True)
  self.assertTrue(worker.execution_snapshot()['stop_confirmed'])
  self.assertTrue(reader.connected)
  self.assertEqual(len(reader.moves),4)
  self.assertEqual(reader.stops,2)
  self.assertEqual(reader.moves[2][:3],[-610,-220,310])
  self.assertTrue(worker.snapshot()['valid'])
 def test_stop_before_start_sends_no_waypoints(self):
  worker,reader,_=self.execute(stop_before_start=True)
  self.assertEqual(reader.moves,[])
  self.assertEqual(worker.execution_snapshot()['state'],'STOPPED')
  self.assertIs(worker.execution_snapshot()['motion_attempted'],False)
 def test_uncertain_sdk_ack_stops_once_and_does_not_retry_motion(self):
  worker,reader,_=self.execute(reject=True)
  self.assertEqual(len(reader.moves),1)
  self.assertEqual(worker.execution_snapshot()['state'],'ERROR')
  self.assertIs(worker.execution_snapshot()['motion_attempted'],True)
  self.assertTrue(worker.execution_snapshot()['stop_confirmed'])
