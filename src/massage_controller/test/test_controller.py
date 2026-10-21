import copy
import json
from pathlib import Path
import unittest
from massage_controller.core import Controller
from massage_controller.node import default_config
from massage_controller.protocol import load_contract


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.t=100.0
        self.c=Controller(load_contract(default_config()),lambda:self.t)
        self.c.vision_connected=True
        self.health()

    def health(self):
        self.c.vision_message(json.dumps({'type':'VISION_STATUS','data':dict(mode='SIMULATION',camera_connected=True,
            model_ready=True,worker_state='IDLE',last_frame_age_ms=0,error='')}))

    def req(self,cmd,data=None):
        return self.c.request(json.dumps(dict(cmd=cmd,data=data or {})))

    def advance(self,seconds,heartbeat=True):
        for _ in range(round(seconds/0.02)):
            self.t+=0.02
            if heartbeat:
                self.c.ui_at=self.t
                self.health()
            self.c.tick()

    def result(self,region='BACK'):
        points=[]
        for x in self.c.contract['acupoints']['regions'][region]:
            for side in x['sides']:
                points.append(dict(name=x['name'],side=side,camera_point_mm=[10,20,800],coordinate_valid=True,normal_camera=None,normal_valid=False))
        return {'type':'LOCATE_RESULT','data':dict(status='SUCCEEDED',region=region,view=self.c.cfg['views'][region],
            capture_host_monotonic_ms=self.t*1000,capture_time_valid=True,frame='camera_color_optical_frame',points=points,message='fixture')}

    def scan(self,region='BACK'):
        self.assertTrue(self.req('SCAN',{'region':region})['ok'])
        self.c.vision_message(json.dumps(self.result(region)))
        self.assertTrue(self.c.points)

    def plan(self,region='BACK',energy='TEMPERATURE',seconds=3):
        p=dict(region=region,points=copy.deepcopy(self.c.points[:3]),total_time_s=seconds,
            energy=dict(type=energy,value=47 if energy=='TEMPERATURE' else 15,unit='C' if energy=='TEMPERATURE' else 'N'),
            dwell_mode='STATIC',transition_mode='LIFT_MOVE_APPROACH')
        if energy=='TEMPERATURE':p['gap_mm']=30
        return p

    def ready(self):
        self.scan()
        self.assertTrue(self.req('SET_PLAN',self.plan())['ok'])

    def test_complete_and_duplicate_start(self):
        self.ready()
        self.assertTrue(self.req('START')['ok'])
        for _ in range(10):self.assertFalse(self.req('START')['ok'])
        self.advance(0.1)
        self.assertEqual(self.c.step,'APPROACH')
        self.assertEqual(self.c.remaining,3)
        self.advance(6)
        self.assertEqual((self.c.state,self.c.result),('IDLE','COMPLETED'))
        self.assertFalse(self.req('START')['ok'])
        self.assertEqual(sum(x[1:3]==('move','APPROACH') for x in self.c.device.calls),3)

    def test_stop_all_steps(self):
        for step in ('APPROACH','DWELL','RETRACT','TRANSFER'):
            self.setUp();self.ready();self.req('START')
            for _ in range(300):
                if self.c.step==step:break
                self.advance(0.02)
            self.assertEqual(self.c.step,step)
            count=len([x for x in self.c.device.calls if x[1]=='move'])
            self.req('STOP')
            self.assertEqual(self.c.state,'STOPPING')
            self.advance(0.06)
            self.assertEqual(self.c.state,'STOPPING')
            self.advance(0.4)
            self.assertEqual((self.c.state,self.c.result),('IDLE','STOPPED'))
            self.assertEqual(count,len([x for x in self.c.device.calls if x[1]=='move']))

    def test_cancel_late_result_barrier(self):
        self.req('SCAN',{'region':'BACK'})
        late=self.result()
        self.req('STOP');self.advance(0.4)
        self.assertFalse(self.req('SCAN',{'region':'BACK'})['ok'])
        self.c.vision_message(json.dumps(late))
        self.assertFalse(self.c.points)
        self.assertIsNotNone(self.c.pending)
        self.c.vision_message(json.dumps(dict(type='REPLY',cmd='CANCEL',ok=True,message='ok',data={})))
        self.assertIsNone(self.c.pending)
        self.assertTrue(self.req('SCAN',{'region':'BACK'})['ok'])

    def test_cancel_timeout_closes_transport(self):
        self.req('SCAN',{'region':'BACK'});self.req('STOP');self.advance(2.2)
        self.assertTrue(self.c.close_vision)
        self.assertFalse(self.c.points)

    def test_stop_timeout_and_repeated_stop(self):
        self.ready();self.req('START');self.c.device.suppress_stop=True
        self.req('STOP');started=self.c.stop_at
        self.advance(1);self.req('STOP');self.assertEqual(self.c.stop_at,started)
        self.advance(1.1)
        self.assertEqual((self.c.state,self.c.result),('ERROR','FAILED'))
        self.req('STOP');self.assertEqual(self.c.state,'ERROR')

    def test_feedback_freeze(self):
        for attr in ('freeze_robot','freeze_energy','freeze_gap'):
            self.setUp();self.ready();self.req('START');self.advance(0.6)
            setattr(self.c.device,attr,True);self.advance(0.7)
            self.assertEqual(self.c.state,'ERROR',attr)
            remaining=self.c.remaining;self.advance(1)
            self.assertEqual(self.c.remaining,remaining)
            self.assertFalse(self.c.status()['data']['actual_energy']['valid'])
            self.assertEqual(self.c.status()['data']['actual_energy']['type'],'TEMPERATURE')

    def test_disconnect_estop_and_invalidation(self):
        for action in ('disconnect','estop','tool','vision','ui'):
            self.setUp();self.ready();self.req('START');self.advance(0.5)
            if action=='disconnect':self.c.device.connected=False
            if action=='estop':self.c.device.estop=True
            if action=='tool':self.c.invalidate('工具变更')
            if action=='vision':self.c.vision_lost()
            if action=='ui':self.c.ui_lost()
            self.advance(0.3)
            self.assertIn(self.c.state,('ERROR','IDLE'))
            self.assertIsNone(self.c.plan)

    def test_ui_timeout_invalid_requests_do_not_keep_alive(self):
        self.ready();self.req('START')
        for _ in range(151):
            self.t+=0.02;self.health();self.c.request('{bad');self.c.tick()
        self.assertEqual(self.c.state,'STOPPING')

    def test_validation_clears_ready_but_not_running(self):
        self.ready();p=self.plan();p['extra']=1
        self.assertFalse(self.req('SET_PLAN',p)['ok']);self.assertEqual(self.c.state,'IDLE')
        self.assertFalse(self.req('START')['ok'])
        self.req('SET_PLAN',self.plan());self.req('START');old=copy.deepcopy(self.c.plan)
        self.assertFalse(self.req('SET_PLAN',p)['ok']);self.assertEqual(self.c.plan,old)
        self.assertFalse(self.req('SCAN',{'region':'BACK'})['ok'])

    def test_six_plans(self):
        for region in ('BACK','UPPER_LIMB','LOWER_LIMB'):
            for energy in ('TEMPERATURE','FORCE'):
                self.setUp();self.scan(region)
                p=self.plan(region,energy,60)
                self.assertTrue(self.req('SET_PLAN',p)['ok'])

    def test_bad_inputs(self):
        for raw in ('{}','[]','null','{"cmd":"STOP","data":"stop"}',
                    '{"cmd":"GET_STATUS","data":{"x":0}}',
                    '{"cmd":"STOP","cmd":"START","data":{}}',
                    '{"cmd":"SET_PLAN","data":{"value":NaN}}'):
            self.assertFalse(self.c.request(raw)['ok'],raw)
        self.scan()
        base=self.plan()
        variants=[]
        for k,v in [('points',[]),('total_time_s',True),('total_time_s',0),('total_time_s',4),('gap_mm',-1),('dwell_mode','CIRCLE')]:
            p=copy.deepcopy(base);p[k]=v;variants.append(p)
        p=copy.deepcopy(base);p['points'][0]['side']='LEFT';variants.append(p)
        p=copy.deepcopy(base);p['points'].append(p['points'][0]);variants.append(p)
        p=copy.deepcopy(base);p['energy']['value']=999;variants.append(p)
        p=copy.deepcopy(base);p['energy']['unit']='N';variants.append(p)
        for p in variants:self.assertFalse(self.req('SET_PLAN',p)['ok'],p)

    def test_old_capture_and_invalid_geometry(self):
        for kind in ('old','invalid_time','duplicate','nan','normal'):
            self.setUp();self.req('SCAN',{'region':'BACK'});r=self.result()
            if kind=='old':r['data']['capture_host_monotonic_ms']-=1000
            if kind=='invalid_time':r['data']['capture_time_valid']=False
            if kind=='duplicate':r['data']['points'].append(r['data']['points'][0])
            if kind=='nan':r['data']['points'][0]['camera_point_mm'][0]=float('nan')
            if kind=='normal':r['data']['points'][0].update(normal_valid=True,normal_camera=[0,0,2])
            try:self.c.vision_message(json.dumps(r))
            except ValueError:pass
            self.assertFalse(self.c.points,kind)

    def test_scan_observation_and_no_sdk_movement(self):
        for attr in ('moving','observation_ready'):
            self.setUp();setattr(self.c.device,attr,attr=='moving')
            self.assertFalse(self.req('SCAN',{'region':'BACK'})['ok'])
        self.setUp();self.scan()
        self.assertFalse(self.c.device.calls)

    def test_real_mode_rejected(self):
        contract=copy.deepcopy(self.c.contract);contract['simulation']['mode']='REAL'
        with self.assertRaises(ValueError):Controller(contract,lambda:self.t)

    def test_documented_plan_examples(self):
        examples=Path(default_config()).parents[1]/'总规划'/'通讯协议示例'/'完整场景'
        accepted=0;rejected=0
        for file in examples.glob('*/01_请求.json'):
            m=json.loads(file.read_text())
            if m.get('cmd')!='SET_PLAN':continue
            number=int(file.parent.name.split('_')[0])
            self.setUp();self.scan()
            if number in (11,12):self.scan('UPPER_LIMB')
            if number in (13,14):self.scan('LOWER_LIMB')
            if number==15:self.c.points=[]
            if number==25:self.c.cfg['capabilities']=[]
            if number==27:self.req('SET_PLAN',self.plan());self.req('START')
            result=self.c.request(file.read_text())
            self.assertEqual(result['ok'],number in range(9,15),file)
            if result['ok']:accepted+=1
            else:rejected+=1
        self.assertEqual(accepted,6);self.assertEqual(rejected,19)


    def test_limb_execution_and_restart(self):
        for region in ('UPPER_LIMB','LOWER_LIMB'):
            self.setUp();self.scan(region)
            p=self.plan(region,'FORCE',2);p['points']=p['points'][:2]
            self.assertTrue(self.req('SET_PLAN',p)['ok']);self.req('START')
            restarted=Controller(self.c.contract,lambda:self.t)
            self.assertEqual(restarted.state,'IDLE')
            self.assertIsNone(restarted.plan)
            self.assertFalse(restarted.device.calls)
            self.advance(5)
            self.assertEqual(self.c.result,'COMPLETED')

    def test_motion_time_never_decrements_dwell(self):
        self.ready();self.c.cfg['motion_delay_s']=1.0;self.req('START')
        for _ in range(650):
            before=self.c.remaining;step=self.c.step
            self.advance(.02)
            if step!='DWELL' and self.c.state=='RUNNING':self.assertEqual(self.c.remaining,before)
        self.assertEqual(self.c.result,'COMPLETED')

    def test_ready_invalidation_and_scan_timeout(self):
        self.ready();self.c.invalidate('人体姿态改变')
        self.assertEqual(self.c.state,'IDLE');self.assertFalse(self.req('START')['ok'])
        self.req('SCAN',{'region':'BACK'});self.c.cfg['scan_timeout_s']=.2
        self.advance(.3)
        self.assertEqual(self.c.state,'IDLE');self.assertEqual(self.c.result,'FAILED')
        self.assertFalse(self.c.points);self.assertIsNotNone(self.c.cancel_at)


    def test_stop_idle_ready_and_force_feedback_loss(self):
        self.assertTrue(self.req('STOP')['ok']);self.assertEqual(self.c.state,'IDLE')
        self.ready();self.req('STOP')
        self.assertEqual(self.c.state,'IDLE');self.assertIsNone(self.c.plan)
        self.assertFalse(self.req('START')['ok'])
        self.scan();self.req('SET_PLAN',self.plan(energy='FORCE'));self.req('START')
        self.advance(.5);self.c.device.freeze_energy=True;self.advance(.7)
        self.assertEqual(self.c.state,'ERROR')
        self.assertEqual(self.c.status()['data']['actual_energy'],dict(type='FORCE',value=None,unit='N',valid=False))

if __name__=='__main__':unittest.main(verbosity=2)
