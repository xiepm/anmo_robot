import copy,json,unittest
from pathlib import Path
from uuid import uuid4
from jsonschema import validate
from test_real_scan import RealScanTests
from test_base_coordinates import BaseProjector,CAL,fixture,SCHEMAS
from massage_controller.geometry import camera_to_base_m

class PointConfirmationTests(unittest.TestCase):
    request=RealScanTests.request
    def setUp(self):
        RealScanTests.setUp(self)
        self.cache.value['joint_positions_deg']=[0.]*6
        self.device.poll(self.now,None,False)
        self.request('SCAN',dict(region='BACK'))
        raw,history=fixture(self.now+.1)
        raw['data']['points'][0].update(name='大椎',side='MIDLINE')
        self.now+=.2
        self.projector=BaseProjector(CAL)
        self.packet=self.projector.project(raw,history,self.now)
        self.core.snapshot_verified=lambda p:p==self.packet
        self.core.confirmation.projector=self.projector
        p=self.packet['data']['points'][0]
        self.core.vision_message(json.dumps(dict(type='LOCATE_RESULT',data=dict(status='SUCCEEDED',region='BACK',view='back',
            frame='camera_color_optical_frame',capture_host_monotonic_ms=None,capture_time_valid=False,
            points=[dict(name=p['name'],side=p['side'],camera_point_mm=p['camera_point_mm'],coordinate_valid=True,normal_camera=None,normal_valid=False)],
            base_preview=self.packet,message=''))))
        self.data=dict(revision=str(uuid4()),frame_number=42,received_monotonic_s=raw['data']['frame_received_monotonic_s'],
            points=[dict(name='大椎',side='MIDLINE',x=120.,y=180.)])
    def result(self,data=None,xyz=None):
        d=data or self.data
        return dict(type='REFINE_RESULT',data=dict(revision=d['revision'],frame_number=d['frame_number'],
            received_monotonic_s=d['received_monotonic_s'],ok=True,error='',
            points=[dict(p,camera_point_mm=xyz or [100.,200.,600.]) for p in d['points']]))
    def send(self,result):self.core.vision_message(json.dumps(result))
    def health(self,age=10,**changes):
        data=dict(mode='REAL',camera_connected=True,model_ready=True,worker_state='IDLE',last_frame_age_ms=age,error='')
        self.send(dict(type='VISION_STATUS',data=dict(data,**changes)))
    def tick_with_fresh_robot(self):
        self.cache.value['host_monotonic_after_s']=self.now
        self.core.ui_at=self.now
        self.device.poll(self.now,None,False)
        self.core.tick()
    def assert_delay_receipt(self):
        value=self.core.confirmation.value
        self.assertEqual(value['revision'],self.data['revision'])
        self.assertEqual(value['state'],'FAILED')
        self.assertEqual(value['requested_points'],self.data['points'])
        self.assertEqual(value['points'],[])
        self.assertFalse(value['execution_eligible'])
        self.assertEqual(value['error'],'实时画面暂时延迟，本次确认已撤销；恢复后请重新确认')
        self.assertIsNone(self.core.confirmation.pending)
    def test_final_edited_coordinates_are_committed_not_original_candidates(self):
        self.assertTrue(self.request('CONFIRM_POINTS',self.data)['ok'])
        self.assertEqual(self.core.outbox[-1],dict(cmd='REFINE_POINTS',data=self.data))
        edited_camera_point=[110.,190.,610.]
        self.send(self.result(xyz=edited_camera_point))
        v=self.core.status()['data']['point_confirmation']
        self.assertEqual(v['state'],'CONFIRMED')
        expected=[x*1000 for x in camera_to_base_m(edited_camera_point,self.packet['data']['feedback_window']['T_base_reference'],self.projector.calibration['T_reference_camera'])]
        self.assertEqual(v['points'][0]['base_point_mm'],expected)
        self.assertNotEqual(expected,self.packet['data']['points'][0]['base_point_mm'])
        self.assertEqual(self.core.confirmation.final_surface_points(self.data['revision'],self.data['points']),v['points'])
        validate(self.core.status(),json.loads((SCHEMAS/'ui_response.schema.json').read_text()))
        self.assertFalse(v['execution_eligible']);self.assertFalse(self.request('START')['ok'])
        self.assertFalse(self.device.calls)
    def test_clear_invalidates_old_version_and_late_reply_cannot_restore_it(self):
        self.request('CONFIRM_POINTS',self.data)
        self.assertTrue(self.request('CLEAR_POINTS')['ok'])
        self.send(self.result())
        self.assertIsNone(self.core.confirmation.value)
        new=copy.deepcopy(self.data);new.update(revision=str(uuid4()));new['points'][0]['x']=130.
        self.assertTrue(self.request('CONFIRM_POINTS',new)['ok'])
        self.send(self.result());self.assertEqual(self.core.confirmation.value['state'],'PENDING')
        self.send(self.result(new,[120.,200.,600.]))
        self.assertEqual(self.core.confirmation.value['points'][0]['x'],130.)
        with self.assertRaises(ValueError):self.core.confirmation.final_surface_points(self.data['revision'],new['points'])
    def test_wrong_frame_or_wrong_final_pixel_is_rejected(self):
        bad=copy.deepcopy(self.data);bad['frame_number']+=1
        self.assertFalse(self.request('CONFIRM_POINTS',bad)['ok'])
        self.assertTrue(self.request('CONFIRM_POINTS',self.data)['ok'])
        result=self.result();result['data']['points'][0]['x']+=1
        self.send(result)
        self.assertEqual(self.core.confirmation.value['state'],'FAILED')
        self.assertFalse(self.core.confirmation.value['points'])
    def test_movement_and_stop_retire_confirmation(self):
        self.request('CONFIRM_POINTS',self.data);self.send(self.result())
        self.cache.value['joint_positions_deg'][0]=1
        self.device.poll(self.now,None,False);self.core.confirmation.tick()
        self.assertEqual(self.core.confirmation.value['state'],'FAILED')
        self.request('STOP');self.send(self.result())
        self.assertIsNone(self.core.confirmation.value)
    def test_timeout_and_disconnect_fail_closed(self):
        self.request('CONFIRM_POINTS',self.data);self.now+=6
        self.core.confirmation.tick()
        self.assertEqual(self.core.confirmation.value['state'],'FAILED')
        self.core.vision_lost();self.send(self.result())
        self.assertIsNone(self.core.confirmation.value)

    def test_short_preview_delay_keeps_photo_but_requires_live_reconfirmation(self):
        packet=copy.deepcopy(self.core.confirmation.packet);snapshot=copy.deepcopy(self.core.scan_snapshot)
        self.health(750)
        self.assertEqual(self.core.confirmation.packet,packet)
        self.assertEqual(self.core.scan_snapshot,snapshot)
        self.assertFalse(self.core.status()['data']['vision_feedback_valid'])
        response=self.request('CONFIRM_POINTS',self.data)
        self.assertFalse(response['ok']);self.assertNotIn('没有可确认',response['message'])
        with self.assertRaises(ValueError):
            self.core.confirmation.final_surface_points(self.data['revision'],self.data['points'])
        self.health()
        self.assertTrue(self.core.status()['data']['vision_feedback_valid'])
        self.assertIsNone(self.core.confirmation.value)
        self.assertTrue(self.request('CONFIRM_POINTS',self.data)['ok'])
        self.send(self.result());self.assertEqual(self.core.confirmation.value['state'],'CONFIRMED')
        self.assertFalse(self.device.calls)

    def test_short_delay_retires_confirmation_scene_plan_and_pending_review(self):
        self.request('CONFIRM_POINTS',self.data);self.send(self.result())
        self.core.confirmation.scene={'old':'scene'};self.core.confirmation.scene_context={'old':'context'}
        self.core.plan={'old':'plan'}
        class Review:
            canceled=False
            def sync(review,confirmation,sample,path):
                self.assertEqual(confirmation.value['state'],'FAILED');review.canceled=True
        class Noncontact:
            active=False;review=Review();profile_path='not-read';revocations=[]
            def revoke_authorization(control,revision):control.revocations.append(revision)
        self.core.noncontact=Noncontact()
        self.health(700)
        self.assertTrue(self.core.noncontact.review.canceled)
        self.assert_delay_receipt()
        self.assertIsNone(self.core.confirmation.scene);self.assertIsNone(self.core.confirmation.scene_context)
        self.assertIsNone(self.core.plan);self.assertIsNotNone(self.core.scan_snapshot)
        self.assertIsNotNone(self.core.confirmation.packet)
        self.health(800);self.core.confirmation.tick()
        self.assertEqual(self.core.noncontact.revocations,[self.data['revision']])

    def test_late_refine_cannot_restore_confirmation_after_preview_delay(self):
        self.request('CONFIRM_POINTS',self.data)
        self.health(800);self.send(self.result())
        self.assert_delay_receipt()
        self.health();self.send(self.result())
        self.assert_delay_receipt()
        new=copy.deepcopy(self.data);new['revision']=str(uuid4())
        self.assertTrue(self.request('CONFIRM_POINTS',new)['ok'])
        self.send(self.result());self.assertEqual(self.core.confirmation.value['state'],'PENDING')
        self.send(self.result(new));self.assertEqual(self.core.confirmation.value['state'],'CONFIRMED')

    def test_preview_delay_boundaries_are_strictly_below_1500ms(self):
        for age,keep,valid in [(500,True,True),(500.001,True,False),(1499.999,True,False),
                               (1500,False,False),(1500.001,False,False)]:
            with self.subTest(age=age):
                self.setUp();self.health(age)
                self.assertEqual(self.core.scan_snapshot is not None,keep)
                self.assertEqual(self.core.confirmation.packet is not None,keep)
                self.assertEqual(self.core.vision_valid(self.now),valid)

    def test_delay_age_continues_expiring_between_status_messages(self):
        self.health(750);self.now+=.75
        self.tick_with_fresh_robot()
        self.assertIsNone(self.core.confirmation.packet);self.assertIsNone(self.core.scan_snapshot)

    def test_smaller_health_timeout_limits_photo_retention(self):
        self.core.cfg['vision_timeout_s']=1.
        self.health(1000)
        self.assertIsNone(self.core.confirmation.packet);self.assertIsNone(self.core.scan_snapshot)

    def test_hard_vision_failure_or_lost_heartbeat_never_preserves_photo(self):
        cases=[dict(camera_connected=False),dict(model_ready=False),dict(worker_state='ERROR'),
               dict(error='camera unavailable'),dict(mode='SIMULATION'),dict(last_frame_age_ms=None)]
        for changes in cases:
            with self.subTest(changes=changes):
                self.setUp();self.health(750,**changes)
                self.assertIsNone(self.core.confirmation.packet);self.assertIsNone(self.core.scan_snapshot)
        self.setUp();self.health(750);self.core.vision_lost()
        self.assertIsNone(self.core.confirmation.packet);self.assertIsNone(self.core.scan_snapshot)
        self.setUp();self.health(750);self.now+=self.core.cfg['vision_timeout_s']+.01
        self.tick_with_fresh_robot()
        self.assertIsNone(self.core.confirmation.packet);self.assertIsNone(self.core.scan_snapshot)

    def test_movement_retires_unconfirmed_retained_photo_even_if_robot_returns(self):
        self.health(750)
        self.cache.value['robot_state']['moving']=1
        self.device.poll(self.now,None,False);self.core.confirmation.tick()
        self.assertIsNone(self.core.confirmation.packet);self.assertIsNone(self.core.scan_snapshot)
        self.cache.value['robot_state']['moving']=0
        self.device.poll(self.now,None,False);self.health()
        self.assertFalse(self.request('CONFIRM_POINTS',self.data)['ok'])

    def test_expiry_calibration_change_and_snapshot_mismatch_do_not_preserve_photo(self):
        for change in ('expiry','calibration','snapshot','joints','feedback'):
            with self.subTest(change=change):
                self.setUp()
                if change=='expiry':
                    self.now+=301
                    self.cache.value['host_monotonic_after_s']=self.now
                elif change=='calibration':self.projector.sha256='b'*64
                elif change=='snapshot':self.core.scan_snapshot['frame_number']+=1
                elif change=='joints':self.cache.value['joint_positions_deg'][0]=1
                else:self.now+=self.core.cfg['feedback_stale_s']+.1
                self.device.poll(self.now,None,False);self.health(750)
                self.assertIsNone(self.core.confirmation.packet);self.assertIsNone(self.core.scan_snapshot)

    def test_point_confirmation_tick_preserves_only_photo_during_age_only_delay(self):
        self.request('CONFIRM_POINTS',self.data)
        self.core.health['last_frame_age_ms']=750
        self.core.confirmation.tick()
        self.assert_delay_receipt()
        self.assertIsNotNone(self.core.confirmation.packet);self.assertIsNotNone(self.core.scan_snapshot)

    def test_repeated_soft_delay_keeps_failed_receipt_without_duplicate_warning(self):
        self.request('CONFIRM_POINTS',self.data)
        with self.assertLogs('control_node',level='WARNING') as logs:
            self.health(750);receipt=copy.deepcopy(self.core.confirmation.value)
            self.health(800);self.core.confirmation.tick()
        self.assertEqual(len(logs.output),1)
        self.assertEqual(self.core.confirmation.value,receipt)
        self.assert_delay_receipt()

    def test_refine_timeout_keeps_valid_photo_and_allows_new_revision(self):
        for timeout in (5,30):
            with self.subTest(timeout=timeout):
                self.setUp();self.request('CONFIRM_POINTS',self.data)
                if timeout==30:self.core.confirmation.pending['data']['scene_context']={}
                packet=copy.deepcopy(self.core.confirmation.packet);snapshot=copy.deepcopy(self.core.scan_snapshot)
                self.now+=timeout+.01
                self.cache.value['host_monotonic_after_s']=self.now
                self.device.poll(self.now,None,False);self.health()
                self.core.confirmation.tick()
                self.assertEqual(self.core.confirmation.value['state'],'FAILED')
                self.assertEqual(self.core.confirmation.value['error'],'确认坐标超时，请重新确认')
                self.assertFalse(self.core.confirmation.value['execution_eligible'])
                self.assertIsNone(self.core.confirmation.pending);self.assertIsNone(self.core.confirmation.scene)
                self.assertEqual(self.core.confirmation.packet,packet);self.assertEqual(self.core.scan_snapshot,snapshot)
                self.send(self.result());self.assertEqual(self.core.confirmation.value['state'],'FAILED')
                new=copy.deepcopy(self.data);new['revision']=str(uuid4())
                self.assertTrue(self.request('CONFIRM_POINTS',new)['ok'])
                self.send(self.result());self.assertEqual(self.core.confirmation.value['state'],'PENDING')
                self.send(self.result(new));self.assertEqual(self.core.confirmation.value['state'],'CONFIRMED')

    def test_ready_state_does_not_use_idle_photo_retention(self):
        self.request('CONFIRM_POINTS',self.data);self.send(self.result())
        self.core.state='READY';self.core.plan={'old':'plan'}
        self.health(750)
        self.assertIsNone(self.core.confirmation.packet);self.assertIsNone(self.core.scan_snapshot)
        self.assertEqual(self.core.state,'IDLE');self.assertIsNone(self.core.plan)
if __name__=='__main__':unittest.main()
