import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from collections import deque

from massage_controller.waypoint_plan import validate, load, pose, near, compile_groups, command_tcp
from massage_controller.waypoint_motion import WaypointSession, MotionReader
from massage_controller.robot_lock import RobotLock


def plan():
    def p(x):
        return dict(x_mm=x, y_mm=0, z_mm=100, rx_deg=0, ry_deg=0, rz_deg=0)
    return dict(version=1, mode='FREE_SPACE_WAYPOINTS', robot_model='elfin5_l', frame='Base',
                path_confirmed=True, tool_pose_mm_deg=[0]*6, start_pose=p(0), speed=5, acceleration=10,
                max_segment_mm=20, max_rotation_deg=10, workspace_mm=dict(min=[-30,-30,80], max=[30,30,120]),
                move_timeout_s=5, points=[dict(name='A',pose=p(5),dwell_s=.2),dict(name='B',pose=p(10),dwell_s=.2)])


class Clock:
    def __init__(self): self.now = 10.
    def __call__(self): return self.now
    def sleep(self, n): self.now += n


class FakeReader:
    """Only a test double; deliberately never imported by the real runner."""
    def __init__(self, clock):
        self.clock, self.connected = clock, False
        self.moves, self.stops = [], 0
        self.radii = []
        self.target = [0,0,100,0,0,0]
        self.command_id = ''
        self.pending = 0
        self.last_target = self.target
        self.reject_move = self.stop_failure = self.wrong_id = self.disconnected = self.stale = False
        self.bad_tcp = self.bad_ucs = self.sim = self.fault = False
        self.clear_completed_id = self.blank_ids = False
        self.on_move = self.on_sample = None
    def connect(self): self.connected = True
    def close(self): self.connected = False
    def stop(self):
        self.stops += 1
        if self.stop_failure and self.moves: raise RuntimeError('stop failed')
        self.pending = 0
    def move(self, target, joints, p, command_id, radius=0):
        self.moves.append(copy.deepcopy(target))
        self.radii.append(radius)
        self.last_target, self.target = self.target, target
        self.command_id = command_id
        self.pending = 2
        if self.on_move: self.on_move()
        if self.reject_move: raise RuntimeError('uncertain ACK')
    def sample(self):
        if self.on_sample: self.on_sample()
        if self.disconnected:
            self.connected = False
            raise ConnectionError('lost connection')
        moving = self.pending > 0
        if moving: self.pending -= 1
        actual = self.last_target if moving else self.target
        t = self.clock()-(1 if self.stale else 0)
        s = dict(moving=int(moving),enabled=1,powered=1,error=int(self.fault),paused=0,
                 emergency_stop=0,safety_guard=0,box_connected=1,in_position=int(not moving),
                 blending_done=int(not moving),error_code=0)
        waypoint_id = 'stale_command' if self.wrong_id else self.command_id
        if self.blank_ids or (self.clear_completed_id and not moving): waypoint_id = ''
        return dict(valid=True,connected=True,controller_simulation=self.sim,robot_state=s,
                    host_monotonic_before_s=t,host_monotonic_after_s=t,
                    joint_positions_deg=[0]*6,tcp_pose=dict(xyz_mm=actual[:3],rxyz_deg=actual[3:]),
                    configured_tool_pose_mm_deg=[1 if self.bad_tcp else 0]*6,
                    configured_user_pose_mm_deg=[1 if self.bad_ucs else 0]*6,
                    waypoint_id=waypoint_id)


class PlanTests(unittest.TestCase):
    def test_valid_and_angle_wrap(self):
        self.assertEqual(validate(plan()), plan())
        self.assertTrue(near([0,0,0,180,0,0],[0,0,0,-180,0,0]))
    def test_unfilled_bad_values_unknown_fields_and_limits(self):
        variants = []
        for field, value in [('path_confirmed',False),('speed',True),('speed',21),('speed',float('nan')),
                             ('frame','World'),('acceleration',5),('max_segment_mm',2),('version',True)]:
            p=plan(); p[field]=value; variants.append(p)
        p=plan();p['points'][0]['pose']['x_mm']=None;variants.append(p)
        p=plan();p['points'][0]['pose']['x_mm']=50;variants.append(p)
        p=plan();p['points'][0]['pose']['rx_deg']=20;variants.append(p)
        p=plan();p['points'][1]['name']='A';variants.append(p)
        p=plan();p['extra']='bad';variants.append(p)
        for p in variants:
            with self.subTest(p=p), self.assertRaises(ValueError): validate(p)
    def test_duplicate_json_keys(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'plan.json';path.write_text('{"version":1,"version":1}')
            with self.assertRaisesRegex(ValueError,'重复'):load(path)
    def test_robot_ownership(self):
        first, second = RobotLock('test-only-no-socket',12345), RobotLock('test-only-no-socket',12345)
        try:
            first.acquire()
            with self.assertRaises(RuntimeError):second.acquire()
            first.close();second.acquire()
        finally:first.close();second.close()


class SessionTests(unittest.TestCase):
    def make(self):
        c=Clock();r=FakeReader(c);events=[]
        session=WaypointSession(plan(),r,events.append,clock=c,sleep=c.sleep)
        def sample_hook():
            if session.status_snapshot()['state']=='READY':session.request_start()
        r.on_sample=sample_hook
        return session,r,events
    def test_sequential_real_feedback_gates_and_final_stop(self):
        s,r,events=self.make();s.run()
        self.assertEqual(s.status_snapshot()['state'],'COMPLETED')
        self.assertTrue(s.status_snapshot()['stop_confirmed'])
        self.assertEqual([v[0] for v in r.moves],[5,10]);self.assertEqual(r.stops,2)
        dwell=[e for e in events if e.get('step')=='DWELL']
        move=[e for e in events if e.get('step')=='MOVE']
        self.assertGreaterEqual(move[1]['monotonic_s']-dwell[0]['monotonic_s'],.2)
        self.assertFalse(r.connected)
        self.assertFalse(s.snapshot()['valid'])
    def test_no_early_start(self):
        s,r,_=self.make();self.assertFalse(s.request_start());self.assertFalse(s.start_event.is_set())
    def test_controller_clears_id_only_after_observed_execution(self):
        s,r,_=self.make();r.clear_completed_id=True;s.run()
        self.assertEqual(s.status_snapshot()['state'],'COMPLETED')
    def test_blank_id_without_observed_execution_is_not_arrival(self):
        s,r,_=self.make();r.blank_ids=True;s.run()
        self.assertEqual(s.status_snapshot()['state'],'ERROR')
        self.assertEqual(len(r.moves),1)
    def test_preflight_rejects_frames_simulation_fault_and_stale(self):
        for attr in ('bad_tcp','bad_ucs','sim','fault','stale'):
            s,r,_=self.make();setattr(r,attr,True);s.run()
            self.assertEqual(r.moves,[],attr);self.assertEqual(s.status_snapshot()['state'],'ERROR')
    def test_start_pose_mismatch_sends_no_commands(self):
        s,r,_=self.make();r.target=[15,0,100,0,0,0];s.run()
        self.assertEqual(r.moves,[]);self.assertEqual(r.stops,0)
    def test_wrong_waypoint_id_never_advances(self):
        s,r,_=self.make();r.wrong_id=True;s.run()
        self.assertEqual(len(r.moves),1);self.assertEqual(s.status_snapshot()['state'],'ERROR')
        self.assertTrue(s.status_snapshot()['stop_confirmed'])
    def test_uncertain_motion_ack_is_not_retried(self):
        s,r,_=self.make();r.reject_move=True;s.run()
        self.assertEqual(len(r.moves),1);self.assertEqual(r.stops,2)
        self.assertEqual(s.status_snapshot()['state'],'ERROR')
    def test_stop_interrupts_before_next_waypoint(self):
        s,r,_=self.make();r.on_move=s.request_stop;s.run()
        self.assertEqual(len(r.moves),1);self.assertEqual(s.status_snapshot()['state'],'STOPPED')
    def test_stop_failure_is_never_reported_as_stopped(self):
        s,r,_=self.make();r.stop_failure=True;r.on_move=s.request_stop;s.run()
        self.assertEqual(s.status_snapshot()['state'],'STOP_UNCONFIRMED')
        self.assertFalse(s.status_snapshot()['stop_confirmed'])
    def test_disconnect_stops_sequence_no_resume(self):
        s,r,_=self.make();r.on_move=lambda:setattr(r,'disconnected',True);s.run()
        self.assertEqual(len(r.moves),1)
        self.assertEqual(s.status_snapshot()['state'],'STOP_UNCONFIRMED')
    def test_log_failure_still_attempts_stop(self):
        s,r,_=self.make()
        def log(e):
            if r.moves:raise OSError('disk full')
        s.log=log;s.run()
        self.assertEqual(len(r.moves),1);self.assertEqual(r.stops,2)
        self.assertEqual(s.status_snapshot()['state'],'ERROR')
    def test_prestart_cancel_log_failure_is_not_a_clean_stop(self):
        s,r,events=self.make()
        r.on_sample=lambda:s.request_stop() if s.status_snapshot()['state']=='READY' else None
        def log(e):
            if e.get('type')=='WAYPOINT_STATUS' and e.get('state')=='STOPPING':
                raise OSError('disk full during cancellation')
            events.append(e)
        s.log=log;s.run()
        result=s.status_snapshot()
        self.assertEqual(r.moves,[])
        self.assertEqual(r.stops,2)
        self.assertEqual(result['state'],'ERROR')
        self.assertTrue(result['stop_confirmed'])
        self.assertIs(result['motion_attempted'],False)
        self.assertIn('运行日志写入失败',result['error'])
        self.assertIn('disk full during cancellation',result['error'])
        self.assertEqual(events[-1]['state'],'ERROR')
    def test_stop_before_connect(self):
        s,r,_=self.make();s.request_stop();s.run()
        self.assertEqual(r.moves,[]);self.assertEqual(r.stops,0)
        self.assertEqual(s.status_snapshot()['state'],'CANCELLED_BEFORE_MOTION')


class MotionTransportTests(unittest.TestCase):
    def test_enable_reset_output_commands_remain_blocked(self):
        r=MotionReader.__new__(MotionReader);r.audit=deque();r.timeout_s=.1
        for name in ('GrpEnable','GrpReset','SetBoxDO','WayPoint','MoveJ','SetSimulation'):
            with self.assertRaises(ValueError):r.exchange(None,name+',0,;')
        self.assertFalse(r.audit)
    def test_sdk_linear_arguments_explicit_frames_no_blending(self):
        r=MotionReader.__new__(MotionReader)
        class SDK:
            def HRIF_WayPointEx(self,*args):self.args=args;return 0
        r.sdk=SDK();p=plan();target=pose(p['points'][0]['pose'],'A')
        r.move(target,[1]*6,p,'abc')
        self.assertEqual(r.sdk.args,(0,0,1,target,[1]*6,[0]*6,[0.]*6,5,10,0,0,0,0,0,'abc'))


class EntryPointTests(unittest.TestCase):
    def test_validate_only_never_loads_sdk_and_template_cannot_execute(self):
        import subprocess
        import sys
        with tempfile.TemporaryDirectory() as d:
            file=Path(d)/'plan.json';file.write_text(json.dumps(plan()))
            base=[sys.executable,'-m','massage_controller.node','--standalone',
                  '--waypoint-plan',str(file),'--sdk','/nonexistent-sdk.py']
            checked=subprocess.run(base,capture_output=True,text=True,timeout=5)
            self.assertEqual(checked.returncode,0,checked.stderr)
            self.assertTrue(json.loads(checked.stdout)['valid'])
            executed=subprocess.run(base+['--execute'],input='',capture_output=True,text=True,timeout=5)
            self.assertNotEqual(executed.returncode,0)
            self.assertIn('交互终端',executed.stderr)
            p=plan();p['path_confirmed']=False;file.write_text(json.dumps(p))
            invalid=subprocess.run(base+['--execute'],input='',capture_output=True,text=True,timeout=5)
            self.assertEqual(invalid.returncode,2)
            self.assertIn('path_confirmed',invalid.stderr)


class SubdivisionTests(unittest.TestCase):
    def test_long_fixed_orientation_line_is_collinear_and_no_added_dwell(self):
        p=plan();p['auto_subdivide']=True;p['max_segment_mm']=3
        groups=compile_groups(validate(p))
        self.assertEqual([len(g) for g in groups],[2,2])
        self.assertEqual(groups[0][0]['pose']['x_mm'],2.5)
        self.assertEqual(groups[0][0]['dwell_s'],0)
        self.assertTrue(groups[0][0]['generated'])
        self.assertFalse(groups[0][-1]['generated'])
        self.assertEqual(groups[0][-1]['dwell_s'],.2)
    def test_long_orientation_change_rejected_without_guessing_euler_path(self):
        p=plan();p['auto_subdivide']=True;p['max_segment_mm']=3
        p['points'][0]['pose']['ry_deg']=2
        with self.assertRaisesRegex(ValueError,'改变姿态'):validate(p)
    def test_impossible_timeout_caught_offline(self):
        p=plan();p['move_timeout_s']=1
        with self.assertRaisesRegex(ValueError,'无法'):validate(p)
    def test_flange_reference_does_not_move_flange_by_tool_offset(self):
        p=plan();p['target_reference']='FLANGE';p['tool_pose_mm_deg']=[0,0,20,0,0,0]
        self.assertEqual(command_tcp(validate(p)),[0]*6)
        p['target_reference']='TCP';self.assertEqual(command_tcp(p),[0,0,20,0,0,0])
    def test_blended_generated_points_finish_at_original_points(self):
        c=Clock();r=FakeReader(c);events=[];p=plan();p['auto_subdivide']=True;p['max_segment_mm']=3
        s=WaypointSession(p,r,events.append,c,c.sleep)
        r.on_sample=lambda:s.request_start() if s.status_snapshot()['state']=='READY' else None
        s.run()
        self.assertEqual(s.status_snapshot()['state'],'COMPLETED',s.status_snapshot())
        self.assertEqual([m[0] for m in r.moves],[2.5,5,7.5,10])
        self.assertEqual(r.radii,[.625,0,.625,0])
        self.assertEqual([e['point'] for e in events if e.get('step')=='DWELL'],['A','B'])
    def test_stop_during_queueing_never_enqueues_rest(self):
        c=Clock();r=FakeReader(c);p=plan();p['auto_subdivide']=True;p['max_segment_mm']=3
        s=WaypointSession(p,r,lambda _:None,c,c.sleep)
        r.on_sample=lambda:s.request_start() if s.status_snapshot()['state']=='READY' else None
        r.on_move=s.request_stop;s.run()
        self.assertEqual(len(r.moves),1);self.assertEqual(s.status_snapshot()['state'],'STOPPED')
    def test_queueing_time_counts_toward_original_segment_timeout(self):
        c=Clock();r=FakeReader(c);p=plan();p['auto_subdivide']=True;p['max_segment_mm']=3
        s=WaypointSession(p,r,lambda _:None,c,c.sleep)
        r.on_sample=lambda:s.request_start() if s.status_snapshot()['state']=='READY' else None
        r.on_move=lambda:c.sleep(6)
        s.run()
        self.assertEqual(len(r.moves),1)
        self.assertEqual(s.status_snapshot()['state'],'ERROR')
        self.assertTrue(s.status_snapshot()['stop_confirmed'])
    def test_return_to_start_before_targets(self):
        c=Clock();r=FakeReader(c);r.target=[15,0,100,0,0,0];p=plan()
        p['return_from_pose']=dict(p['start_pose'],x_mm=15)
        p['target_reference']='FLANGE';p['tool_pose_mm_deg']=[0,0,20,0,0,0]
        s=WaypointSession(p,r,lambda _:None,c,c.sleep)
        r.on_sample=lambda:s.request_start() if s.status_snapshot()['state']=='READY' else None
        s.run()
        self.assertEqual(s.status_snapshot()['state'],'COMPLETED',s.status_snapshot())
        self.assertEqual([m[0] for m in r.moves],[0,5,10])
    def test_return_from_unconfirmed_pose_refused(self):
        c=Clock();r=FakeReader(c);r.target=[18,0,100,0,0,0];p=plan()
        p['return_from_pose']=dict(p['start_pose'],x_mm=15)
        s=WaypointSession(p,r,lambda _:None,c,c.sleep);s.run()
        self.assertEqual(s.status_snapshot()['state'],'ERROR');self.assertFalse(r.moves)
