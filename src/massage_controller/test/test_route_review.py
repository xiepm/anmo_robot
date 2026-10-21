import copy
import json
import queue
import tempfile
import threading
import unittest
from pathlib import Path
from concurrent.futures import TimeoutError as FutureTimeoutError
import numpy as np

from massage_controller.noncontact_plan import automatic_profile, compile_route, fingerprint, DIRECT_ROUTE, SEGMENTED_ROUTE
from massage_controller.route_review import Reviewer, ReviewManager, candidate_samples, ObservedObstacles
from massage_controller.noncontact_worker import ReferenceMotionReader
from massage_controller.waypoint_plan import validate
from jsonschema import validate as validate_schema
from test_noncontact import PROFILE

ROT=[[1,0,0,0],[0,-1,0,0],[0,0,-1,0],[0,0,0,1]]

def inputs():
    context=dict(revision='00000000-0000-0000-0000-000000000003',frame_number=3,
        received_monotonic_s=10.,calibration_sha256='a'*64,start_flange_mm=[-600,-200,500],
        corridor_radius_mm=50,cell_size_mm=10,T_base_camera=np.eye(4).tolist())
    scene=dict(revision=context['revision'],context_sha256=fingerprint(context),
        surface_ceiling_base_mm=250.,unknown_cells=2,cell_size_mm=10,
        unknown_cell_examples_base_xy_mm=[[-600.,-250.],[-650.,-200.]])
    point=dict(name='大椎',side='MIDLINE',base_point_mm=[-550,-150,240])
    return dict(context=context,scene=scene,profile=dict(PROFILE,path_confirmed=False,geometry_source='FROZEN_DEPTH'),
        capture=dict(capture_time_valid=True),
        confirmation=dict(revision=context['revision'],frame_number=3,state='CONFIRMED',points=[point]),
        sample=dict(configured_tool_pose_mm_deg=[0]*6,configured_user_pose_mm_deg=[0]*6,
            tcp_pose=dict(xyz_mm=[-600,-200,500],rxyz_deg=[180,0,0]),
            joint_positions_deg=[-60,-20,50,0,0,0],T_base_reference=np.eye(4).tolist()))


class MismatchedModel:
    def fk(self,q):
        matrix=np.eye(4);matrix[0,3]=.018
        return matrix


class ReviewTests(unittest.TestCase):
    def query(self,request):
        if request['kind']=='rotation': return ROT
        p=request['pose']
        return dict(joints_deg=[v/10 for v in p[:3]]+[0,0,0],pose=p)

    def reviewer(self,query=None):
        return Reviewer(query or self.query,model_factory=lambda:(MismatchedModel(),None),
                        cloud_loader=lambda _:self.fail('mismatched model cannot run scene collision'))

    def test_candidate_is_nonexecutable_and_compensates_tool(self):
        d=inputs();p=automatic_profile(d['profile'],d['scene'],d['confirmation']['points'],d['context'])
        plan,targets=compile_route(p,d['confirmation']['points'],[-600,-200,500,180,0,0],ROT,review_only=True)
        self.assertFalse(plan['path_confirmed']);self.assertFalse(plan['execution_eligible'])
        self.assertEqual(targets[0]['tool_tip_base_mm'][2],280)
        self.assertEqual(targets[0]['flange_base_mm'][2],300)
        with self.assertRaises(ValueError): validate(plan)
        positions=list(candidate_samples(plan))
        self.assertGreater(len(positions),len(plan['points']))

    def test_model_mismatch_and_unknown_are_not_overridden_by_successful_sdk_ik(self):
        result=self.reviewer().run(inputs(),threading.Event())
        s=result['summary']
        self.assertTrue(s['kinematics_checked']);self.assertFalse(s['self_collision_checked'])
        self.assertTrue(s['unknown_route_clearance']['all_unknown_cells_included'])
        self.assertFalse(s['execution_eligible']);self.assertFalse(s['full_collision_checked'])
        self.assertEqual(s['state'],'BLOCKED');self.assertEqual(s['model_position_error_mm'],18.)
        self.assertTrue(any('未知' in x for x in s['blockers']))
        schema=json.loads((Path(__file__).resolve().parents[3]/'config/rehab_protocol/ui_response.schema.json').read_text())
        validate_schema(s,schema['oneOf'][0]['properties']['data']['properties']['noncontact']['properties']['route_review'])

    def test_lower_height_is_tried_after_high_route_fails(self):
        def query(r):
            if r['kind']=='ik' and r['pose'][0]>-599 and r['pose'][2]>400:
                raise ValueError('高处目标不可达')
            return self.query(r)
        result=self.reviewer(query).run(inputs(),threading.Event())
        s=result['summary']
        self.assertEqual(len(s['attempts']),2)
        self.assertTrue(s['kinematics_checked']);self.assertEqual(s['transfer_height_mm'],290)

    def test_deferred_model_is_never_loaded_and_never_approves_motion(self):
        d=inputs();d['profile']['virtual_model_check']='DEFERRED'
        reviewer=Reviewer(self.query,model_factory=lambda:self.fail('deferred model loaded'),
            cloud_loader=lambda _:self.fail('model-dependent collision invoked'))
        reviewer.initialization_error='previous unavailable model must not block SDK'
        s=reviewer.run(d,threading.Event())['summary']
        self.assertTrue(s['kinematics_checked'])
        self.assertEqual(s['model_check_state'],'DEFERRED')
        self.assertIsNone(s['model_position_error_mm'])
        self.assertFalse(s['execution_eligible']);self.assertFalse(s['full_collision_checked'])
        self.assertFalse(s['self_collision_checked']);self.assertFalse(s['observed_collision_checked'])
        self.assertTrue(any('未知' in item for item in s['blockers']))
        self.assertFalse(any('URDF' in item for item in s['blockers']))

    def test_unavailable_model_does_not_prevent_sdk_checks(self):
        def unavailable(): raise FileNotFoundError('no URDF')
        reviewer=Reviewer(self.query,model_factory=unavailable)
        reviewer.warmup()
        s=reviewer.run(inputs(),threading.Event())['summary']
        self.assertTrue(s['kinematics_checked'])
        self.assertEqual(s['model_check_state'],'UNAVAILABLE')
        self.assertFalse(s['execution_eligible'])

    def test_deferred_manager_skips_model_warmup(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory, patch.object(Reviewer,'warmup') as warmup:
            manager=ReviewManager(self.query,directory,defer_virtual_model=True)
            try: warmup.assert_not_called()
            finally: manager.close()

    def test_deferred_model_cannot_hide_sdk_failure(self):
        d=inputs();d['profile']['virtual_model_check']='DEFERRED'
        def query(r):
            if r['kind']=='ik': raise ValueError('SDK target unreachable')
            return self.query(r)
        s=self.reviewer(query).run(d,threading.Event())['summary']
        self.assertFalse(s['kinematics_checked']);self.assertFalse(s['execution_eligible'])
        self.assertEqual(len(s['attempts']),2)
        self.assertTrue(any('SDK target unreachable' in item for item in s['blockers']))

    def diagnostic_inputs(self):
        d=inputs()
        d['profile'].update(virtual_model_check='DEFERRED',route_mode=DIRECT_ROUTE)
        d['confirmation']['points']=[
            dict(name='肩井',side='LEFT',base_point_mm=[-570,-180,240]),
            dict(name='风池',side='RIGHT',base_point_mm=[-510,-120,230])]
        return d

    def test_unreachable_endpoint_keeps_point_reason_over_later_entry_failure(self):
        d=self.diagnostic_inputs();original=copy.deepcopy(d);calls=[]
        def query(r):
            if r['kind']=='ik':
                calls.append(copy.deepcopy(r))
                x,_,z=r['pose'][:3]
                if (x>-530 and z==300) or (x>-595 and z>490):
                    raise ValueError('控制器逆解失败：20031')
            return self.query(r)
        result=self.reviewer(query).run(d,threading.Event());s=result['summary']
        first=s['attempts'][0]
        self.assertIn('第 2 点右风池当前姿态不可达',first['error'])
        self.assertIn('2_APPROACH',first['error'])
        self.assertIn(f"路线第 {first['samples']+1} /",first['error'])
        self.assertIn('Base 法兰 XYZ=',first['error']);self.assertIn('姿态=[180.000, 0.000, 0.000]',first['error'])
        self.assertIn('1_TRANSFER',s['attempts'][1]['error'])
        self.assertNotIn('第 1 点左肩井当前姿态不可达',s['attempts'][1]['error'])
        self.assertEqual(s['reason'],'当前候选路线均未通过路线预检：'+first['error'])
        failed=calls[first['samples']];diagnostic=calls[first['samples']+1]
        self.assertNotEqual(failed['pose'],diagnostic['pose'])
        self.assertEqual(diagnostic['pose'],[-510,-120,300,180,0,0])
        self.assertEqual(diagnostic['seed'],failed['seed'])
        self.assertEqual(diagnostic['seed'],self.query(calls[first['samples']-1])['joints_deg'])
        self.assertFalse(s['kinematics_checked']);self.assertFalse(s['execution_eligible'])
        self.assertIsNone(result['details']);self.assertEqual(d,original)
        schema=json.loads((Path(__file__).resolve().parents[3]/'config/rehab_protocol/ui_response.schema.json').read_text())
        validate_schema(s,schema['oneOf'][0]['properties']['data']['properties']['noncontact']['properties']['route_review'])

    def test_reachable_endpoint_does_not_accept_failed_continuous_segment(self):
        d=self.diagnostic_inputs();calls=[]
        def query(r):
            if r['kind']=='ik':
                calls.append(copy.deepcopy(r))
                if -530<r['pose'][0]<-511 and r['pose'][2]==300:
                    raise ValueError('控制器逆解失败：20031')
            return self.query(r)
        result=self.reviewer(query).run(d,threading.Event());s=result['summary']
        self.assertEqual(len(s['attempts']),2)
        for attempt in s['attempts']:
            self.assertIn('2_APPROACH',attempt['error'])
            self.assertIn('原段末点单独逆解/正解复核通过，连续路线仍未通过',attempt['error'])
            self.assertNotIn('当前姿态不可达',attempt['error'])
        self.assertEqual(sum(r['pose']==[-510,-120,300,180,0,0] for r in calls),2)
        self.assertFalse(s['kinematics_checked']);self.assertFalse(s['execution_eligible'])
        self.assertIsNone(result['details'])

    def test_return_segment_failure_is_not_mislabeled_as_an_acupoint_endpoint(self):
        d=self.diagnostic_inputs();d['profile'].update(route_mode=SEGMENTED_ROUTE,return_to_start=True)
        profile=automatic_profile(d['profile'],d['scene'],d['confirmation']['points'],d['context'])
        actual=d['sample']['tcp_pose']['xyz_mm']+d['sample']['tcp_pose']['rxyz_deg']
        plan,_=compile_route(profile,d['confirmation']['points'],actual,ROT,review_only=True)
        positions=list(candidate_samples(plan))
        failed_index=next(i for i,(name,_) in enumerate(positions) if name.startswith('RETURN_'))
        calls=[]
        def query(r):
            if r['kind']=='ik':
                calls.append(r)
                if len(calls)>failed_index: raise ValueError('控制器逆解失败：20031')
            return self.query(r)
        result=self.reviewer(query).run(d,threading.Event());s=result['summary']
        self.assertFalse(s['kinematics_checked']);self.assertIsNone(result['details'])
        self.assertEqual(s['attempts'][0]['samples'],failed_index)
        self.assertIn(positions[failed_index][0],s['reason'])
        self.assertIn('原段末点诊断：控制器逆解失败：20031',s['reason'])
        self.assertNotIn('当前姿态不可达',s['reason'])

    def test_other_sdk_errors_are_not_mislabeled_as_unreachable_points(self):
        for failure in (ValueError('控制器逆解失败：20015'),RuntimeError('SDK math queue unavailable')):
            with self.subTest(error=str(failure)):
                def query(r):
                    if r['kind']=='ik': raise failure
                    return self.query(r)
                s=self.reviewer(query).run(self.diagnostic_inputs(),threading.Event())['summary']
                self.assertFalse(s['kinematics_checked']);self.assertFalse(s['execution_eligible'])
                for attempt in s['attempts']:
                    self.assertIn(str(failure),attempt['error'])
                    self.assertIn('路线第 1 /',attempt['error'])
                    self.assertNotIn('当前姿态不可达',attempt['error'])

    def test_endpoint_diagnostic_error_does_not_claim_20031_unreachability(self):
        def query(r):
            if r['kind']=='ik' and r['pose'][0]>-530 and r['pose'][2]==300:
                if r['pose'][0]==-510: raise RuntimeError('SDK endpoint query timed out')
                raise ValueError('控制器逆解失败：20031')
            return self.query(r)
        s=self.reviewer(query).run(self.diagnostic_inputs(),threading.Event())['summary']
        self.assertFalse(s['kinematics_checked'])
        self.assertIn('原段末点诊断：SDK endpoint query timed out',s['reason'])
        self.assertNotIn('当前姿态不可达',s['reason'])

    def transport_errors(self):
        return [(TimeoutError(),'控制器数学查询超时'),
                (FutureTimeoutError(),'控制器数学查询超时'),
                (queue.Full(),'控制器数学查询队列已满'),
                (ConnectionError('socket closed'),'控制器数学查询连接异常：socket closed')]

    def test_sample_transport_failures_keep_context_without_endpoint_queries(self):
        for failure,message in self.transport_errors():
            with self.subTest(error=type(failure)):
                calls=[]
                def query(r):
                    if r['kind']=='ik':
                        calls.append(r);raise failure
                    return self.query(r)
                result=self.reviewer(query).run(self.diagnostic_inputs(),threading.Event());s=result['summary']
                self.assertEqual(len(calls),1);self.assertEqual(len(s['attempts']),1)
                self.assertIn(message,s['reason']);self.assertIn('1_APPROACH',s['reason'])
                self.assertIn('路线第 1 /',s['reason']);self.assertIn('Base 法兰 XYZ=',s['reason'])
                self.assertEqual(s['attempts'][0]['error'],s['reason'])
                self.assertNotIn('当前姿态不可达',s['reason'])
                self.assertFalse(s['kinematics_checked']);self.assertFalse(s['execution_eligible'])
                self.assertIsNone(result['details'])

    def test_endpoint_transport_failure_preserves_original_sample_error(self):
        for failure,message in self.transport_errors():
            with self.subTest(error=type(failure)):
                calls=[]
                def query(r):
                    if r['kind']=='ik':
                        calls.append(r)
                        if len(calls)==1: raise ValueError('控制器逆解失败：20031')
                        raise failure
                    return self.query(r)
                result=self.reviewer(query).run(self.diagnostic_inputs(),threading.Event());s=result['summary']
                self.assertEqual(len(calls),2);self.assertEqual(len(s['attempts']),1)
                self.assertIn('控制器逆解失败：20031',s['reason'])
                self.assertIn('原段末点诊断：'+message,s['reason'])
                self.assertIn('1_APPROACH',s['reason']);self.assertIn('路线第 1 /',s['reason'])
                self.assertEqual(s['attempts'][0]['error'],s['reason'])
                self.assertNotIn('当前姿态不可达',s['reason'])
                self.assertFalse(s['kinematics_checked']);self.assertIsNone(result['details'])

    def test_rotation_transport_failure_has_nonempty_error(self):
        def query(r): raise FutureTimeoutError()
        s=self.reviewer(query).run(self.diagnostic_inputs(),threading.Event())['summary']
        self.assertEqual(s['reason'],'控制器数学查询超时')
        self.assertFalse(s['attempts']);self.assertFalse(s['kinematics_checked'])

    def test_query_interruption_propagates_from_sample_or_endpoint(self):
        for interrupted_call in (1,2):
            with self.subTest(interrupted_call=interrupted_call):
                calls=[]
                def query(r):
                    if r['kind']=='ik':
                        calls.append(r)
                        if len(calls)==interrupted_call: raise InterruptedError('query canceled')
                        raise ValueError('控制器逆解失败：20031')
                    return self.query(r)
                with self.assertRaisesRegex(InterruptedError,'query canceled'):
                    self.reviewer(query).run(self.diagnostic_inputs(),threading.Event())
                self.assertEqual(len(calls),interrupted_call)

    def test_cancel_during_endpoint_query_discards_its_success(self):
        cancel=threading.Event();calls=[]
        def query(r):
            if r['kind']=='ik':
                calls.append(r)
                if len(calls)==1: raise ValueError('控制器逆解失败：20031')
                cancel.set()
            return self.query(r)
        with self.assertRaises(InterruptedError):
            self.reviewer(query).run(self.diagnostic_inputs(),cancel)
        self.assertEqual(len(calls),2)

    def test_cancel_after_failed_sample_skips_endpoint_diagnostic(self):
        cancel=threading.Event();calls=[]
        def query(r):
            if r['kind']=='ik':
                calls.append(r);cancel.set()
                raise ValueError('控制器逆解失败：20031')
            return self.query(r)
        with self.assertRaises(InterruptedError):
            self.reviewer(query).run(self.diagnostic_inputs(),cancel)
        self.assertEqual(len(calls),1)

    def test_fk_mismatch_and_cancel_fail_closed(self):
        def query(r):
            value=self.query(r)
            if r['kind']=='ik': value['pose']=[r['pose'][0]+2]+r['pose'][1:]
            return value
        s=self.reviewer(query).run(inputs(),threading.Event())['summary']
        self.assertFalse(s['kinematics_checked']);self.assertFalse(s['execution_eligible'])
        self.assertIn('路线第 1 /',s['attempts'][0]['error'])
        cancel=threading.Event();cancel.set()
        with self.assertRaises(InterruptedError): self.reviewer().run(inputs(),cancel)

    def test_clear_points_discards_pending_review(self):
        started,release=threading.Event(),threading.Event()
        class Slow:
            def run(self,d,cancel):
                started.set();release.wait(2)
                if cancel.is_set(): raise InterruptedError('retired')
                raise AssertionError('must cancel')
        d=inputs()
        class Confirmation: pass
        c=Confirmation();c.value=d['confirmation'];c.scene=d['scene'];c.scene_context=d['context'];c.packet=d['capture']
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'profile.json';path.write_text(json.dumps(d['profile']))
            m=ReviewManager(self.query,directory,reviewer=Slow())
            try:
                self.assertEqual(m.sync(c,d['sample'],path)['state'],'CHECKING')
                self.assertTrue(started.wait(1))
                c.value=None
                self.assertIsNone(m.sync(c,d['sample'],path));release.set()
            finally: release.set();m.close()
            self.assertFalse(list(Path(directory).glob('000*.json')))

    def test_exported_candidate_is_bound_to_current_configuration_and_confirmation(self):
        d=inputs();result=self.reviewer().run(d,threading.Event())
        class Confirmation: pass
        c=Confirmation();c.value=d['confirmation'];c.scene=d['scene']
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'profile.json';path.write_text(json.dumps(d['profile']))
            manager=ReviewManager(self.query,directory,reviewer=self.reviewer())
            try:
                manager.key=fingerprint(dict(value=c.value,scene=c.scene,profile=d['profile']))
                manager.result=result
                exported=manager.candidate(c,path)
                exported['details']['candidate']['points'].clear()
                self.assertTrue(manager.result['details']['candidate']['points'])
                path.write_text(json.dumps(dict(d['profile'],speed_mm_s=6)))
                with self.assertRaises(ValueError):manager.candidate(c,path)
                path.write_text(json.dumps(d['profile']))
                c.value=dict(c.value,revision='different')
                with self.assertRaises(ValueError):manager.candidate(c,path)
                c.value=d['confirmation'];manager.cancel.set()
                with self.assertRaises(ValueError):manager.candidate(c,path)
            finally:manager.close()

    def test_observed_voxel_collision_is_detected(self):
        import fcl
        obstacles=ObservedObstacles(np.array([[.002,.002,.002]]))
        obj=fcl.CollisionObject(fcl.Sphere(.005),fcl.Transform(np.array([.005,.005,.005])))
        class Checker: objects=[('test_link',None,obj)]
        with self.assertRaisesRegex(ValueError,'可见深度障碍'): obstacles.check(Checker())
        obj.setTransform(fcl.Transform(np.array([1.,1.,1.])))
        obstacles.check(Checker())

    def test_sdk_math_only_uses_inverse_and_forward_queries(self):
        calls=[]
        class SDK:
            def HRIF_GetInverseKin(self,*a): calls.append('IK');a[-1].extend([1]*6);return 0
            def HRIF_GetForwardKin(self,*a): calls.append('FK');a[-1].extend([2]*6);return 0
        reader=object.__new__(ReferenceMotionReader);reader.sdk=SDK()
        result=reader.review_math(dict(kind='ik',pose=[0]*6,seed=[0]*6))
        self.assertEqual(calls,['IK','FK']);self.assertEqual(result['joints_deg'],[1.]*6)
        with self.assertRaises(ValueError):reader.review_math(dict(kind='move'))


if __name__=='__main__':unittest.main()
