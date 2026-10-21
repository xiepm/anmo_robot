import copy
import json
import math
import threading
import time
import unittest
from massage_controller.core import Controller
from massage_controller.node import default_config
from massage_controller.protocol import load_contract
from massage_controller.real_device import FeedbackWorker, RealDevices, mapped_joints
from robot_fixture import robot_config


def sample(now):
    return dict(valid=True,connected=True,controller_simulation=False,host_monotonic_after_s=now,
                received_unix_ns=time.time_ns(),joint_positions_deg=[10,20,30,40,50,60],tcp_pose=dict(xyz_mm=[1,2,3]),
                robot_state=dict(moving=0,box_connected=1,emergency_stop=0,error=0,safety_guard=0,error_code=0))

class Cache:
    def __init__(self,value):self.value=value
    def snapshot(self):return copy.deepcopy(self.value)

class RealAdapterTests(unittest.TestCase):
    def setUp(self):
        self.cfg=robot_config()
        self.now=time.monotonic()
        self.cache=Cache(sample(self.now))
        self.device=RealDevices(self.cache,self.cfg)
        self.core=Controller(load_contract(default_config()),lambda:self.now,device=self.device)

    def test_exact_joint_correspondence(self):
        names,values=mapped_joints(self.cache.value,self.cfg)
        self.assertEqual(names,[f'elfin_joint{i}' for i in range(1,7)])
        for actual,raw in zip(values,[10,20,30,40,50,60]):self.assertAlmostEqual(actual,math.radians(raw))

    def test_no_simulated_feedback_or_motion(self):
        self.core.tick();s=self.core.status()['data']
        self.assertEqual(s['mode'],'REAL');self.assertTrue(s['robot_feedback_valid'])
        self.assertFalse(s['actual_energy']['valid']);self.assertFalse(s['positioning_valid'])
        for cmd,data in [('START',{}),('SCAN',{'region':'BACK'}),('STOP',{})]:
            response=self.core.request(json.dumps(dict(cmd=cmd,data=data)))
            self.assertFalse(response['ok'])
        self.assertNotEqual(self.core.result,'STOPPED')
        self.assertIsNone(self.core.geometry)
        self.assertFalse(self.device.calls)

    def test_cache_repeat_does_not_refresh_age(self):
        self.now+=.6;self.core.tick()
        self.assertFalse(self.core.status()['data']['robot_feedback_valid'])
        self.assertEqual(self.core.state,'ERROR')
        t=self.device.telemetry(self.now)
        self.assertFalse(t['valid']);self.assertIsNone(t['joint_positions_deg'])
        self.assertIsNone(t['visualization_joint_positions_rad'])
        self.cache.value=sample(self.now);self.now+=.12;self.core.tick()
        self.assertTrue(self.core.robot_valid(self.now));self.assertEqual(self.core.state,'ERROR')
        self.assertIsNone(self.core.plan)

    def test_startup_wait_does_not_pretend_connected(self):
        self.cache.value=None;self.device.ever_valid=False
        self.core.tick()
        self.assertEqual(self.core.state,'IDLE');self.assertFalse(self.core.robot_valid(self.now))
        self.now+=self.cfg['startup_timeout_s']+.1;self.core.tick()
        self.assertEqual(self.core.state,'ERROR')

    def test_fault_and_estop_from_hardware(self):
        for field in ('emergency_stop','error','safety_guard'):
            self.setUp();self.cache.value['robot_state'][field]=1;self.now+=.12;self.core.tick()
            self.assertEqual(self.core.state,'ERROR')
            self.assertNotIn('已请求停止',self.core.error)

    def test_worker_reconnect_and_thread_isolation(self):
        cfg=dict(self.cfg,poll_interval_s=.01,reconnect_interval_s=.01)
        created=[]
        class Reader:
            def __init__(self,*args):self.audit=[];self.n=0;created.append(self)
            def connect(self):pass
            def sample(self):
                self.n+=1
                if len(created)==1:raise ConnectionError('injected connection loss')
                return sample(time.monotonic())
            def close(self):pass
        w=FeedbackWorker(cfg,'unused',Reader);w.start()
        try:
            end=time.monotonic()+1
            while time.monotonic()<end:
                value=w.snapshot()
                if value and value['valid']:break
                time.sleep(.005)
            self.assertTrue(w.snapshot()['valid']);self.assertGreaterEqual(len(created),2)
            self.assertNotEqual(w.thread.ident,threading.get_ident())
        finally:w.close()
        self.assertFalse(w.thread.is_alive())

    def test_worker_rejects_controller_simulation(self):
        class Reader:
            def __init__(self,*args):self.audit=[]
            def connect(self):pass
            def sample(self):return dict(sample(time.monotonic()),controller_simulation=True)
            def close(self):pass
        w=FeedbackWorker(self.cfg,'unused',Reader);w.start()
        try:
            end=time.monotonic()+1
            while w.snapshot() is None and time.monotonic()<end:time.sleep(.005)
            self.assertFalse(w.snapshot()['valid'])
        finally:w.close()
