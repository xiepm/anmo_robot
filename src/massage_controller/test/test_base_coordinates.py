import asyncio
import copy
import json
from pathlib import Path
import time
import unittest
from unittest.mock import patch

from jsonschema import validate
from massage_controller.base_coordinates import BaseProjector, ReferenceReader, REFERENCE, BOOT_ID
from massage_controller.sdk_readonly import ReadOnlyReader
from massage_controller.protocol import load_contract, decode
from massage_controller.real_device import RealDevices
from massage_controller.server import Server
from websockets.legacy.client import connect
from robot_fixture import robot_config

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = Path(__file__).resolve().parent/'fixtures'
CAL = FIXTURES/'calibration.synthetic.json'
EXAMPLE = json.loads((FIXTURES/'projection.synthetic.json').read_text())
SCHEMAS = ROOT/'config/rehab_protocol'


def fixture(t=10.):
    history = []
    for offset in (-.6,-.5,-.4,-.3,-.2,-.1,0,.1):
        history.append(dict(valid=True, connected=True, controller_simulation=False,
            host_monotonic_before_s=t+offset-.01, host_monotonic_after_s=t+offset,
            robot_state=dict(moving=0, box_connected=1, error=0, emergency_stop=0, safety_guard=0, paused=0),
            joint_positions_deg=[0.]*6, reference_frame=REFERENCE,
            T_base_reference=EXAMPLE['T_base_reference']))
    request = dict(cmd='PROJECT_POINTS',data=dict(frame='camera_color_optical_frame',
        camera_serial='SYNTHETIC-TEST-CAMERA',host_boot_id=BOOT_ID,frame_number=42,
        frame_received_monotonic_s=t,points=[dict(name='test corner, not acupoint',side='MIDLINE',
            camera_point_mm=EXAMPLE['camera_point_mm'],coordinate_valid=True)]))
    return request, history


class CoordinatesTests(unittest.TestCase):
    def setUp(self):
        self.projector = BaseProjector(CAL)
        self.request, self.history = fixture()

    def test_synthetic_noncommuting_transforms_and_units(self):
        answer = self.projector.project(self.request,self.history,10.2)
        validate(answer,json.loads((SCHEMAS/'base_coordinates_response.schema.json').read_text()))
        d=answer['data']
        self.assertFalse(d['motion_eligible'])
        self.assertFalse(d['capture_time_valid'])
        self.assertTrue(d['points'][0]['base_coordinate_valid'])
        for actual, expected in zip(d['points'][0]['base_point_mm'],EXAMPLE['base_point_mm']):
            self.assertAlmostEqual(actual, expected, places=8)

    def assert_invalid(self, request, history, now=10.2):
        d=self.projector.project(request,history,now)['data']
        self.assertTrue(d['error'])
        self.assertIsNone(d['points'][0]['base_point_mm'])
        self.assertFalse(d['points'][0]['base_coordinate_valid'])

    def test_reject_identity_clock_stale_future_missing_feedback(self):
        for key,value in [('camera_serial','wrong'),('host_boot_id','other host'),
                          ('frame_received_monotonic_s',8),('frame_received_monotonic_s',11)]:
            request=copy.deepcopy(self.request); request['data'][key]=value
            self.assert_invalid(request,self.history)
        self.assert_invalid(self.request,[])
        self.assert_invalid(self.request,self.history[:6])
        self.assert_invalid(self.request,self.history,10.9)

    def test_reject_transient_motion_between_endpoint_feedback(self):
        for key,value in [('moving',1),('error',1),('emergency_stop',1),('safety_guard',1),('paused',1)]:
            history=copy.deepcopy(self.history);history[3]['robot_state'][key]=value
            self.assert_invalid(self.request,history)
        history=copy.deepcopy(self.history);history[4]['joint_positions_deg'][0]=.1
        self.assert_invalid(self.request,history)
        history=copy.deepcopy(self.history);history[4]['reference_frame']='actual_flange'
        self.assert_invalid(self.request,history)
        self.assert_invalid(self.request,self.history[:2]+self.history[6:])

    def test_bad_depth_duplicate_nonrigid(self):
        for xyz in ([0,0,-1],[0,0,0]):
            request=copy.deepcopy(self.request);request['data']['points'][0]['camera_point_mm']=xyz
            self.assert_invalid(request,self.history)
        request=copy.deepcopy(self.request);request['data']['points']*=2
        self.assert_invalid(request,self.history)
        history=copy.deepcopy(self.history);history[-1]['T_base_reference'][0][0]=2
        self.assert_invalid(self.request,history)

    def test_readonly_fk_uses_first_five_actual_joints_and_zero_tool_user(self):
        calls=[]
        class SDK:
            def HRIF_GetForwardKin(self,box,robot,q,tcp,ucs,out):
                calls.append((q,tcp,ucs));out.extend([100,200,300,0,0,0]);return 0
            def HRIF_RPY2Quaternion(self,box,rx,ry,rz,out):
                # Actual vendor responses contain rounded decimal quaternions.
                out.extend([.999,0,0,0]);return 0
        reader=ReferenceReader.__new__(ReferenceReader);reader.sdk=SDK()
        with patch.object(ReadOnlyReader,'sample',return_value={'joint_positions_deg':[1,2,3,4,5,93]}):
            result=reader.sample()
        self.assertEqual(calls,[([1,2,3,4,5,0],[0]*6,[0]*6)])
        self.assertEqual([row[3] for row in result['T_base_reference']],[.1,.2,.3,1])
        self.assertFalse({'WayPointEx','GrpEnable','GrpStop'} & ReferenceReader.allowed_commands)

    def test_strict_request_and_optional_worker_result(self):
        schema=json.loads((SCHEMAS/'base_coordinates_request.schema.json').read_text())
        decode(json.dumps(self.request),schema)
        request=copy.deepcopy(self.request);request['data']['points'][0]['camera_point_mm']=[1,2]
        with self.assertRaises(ValueError):decode(json.dumps(request),schema)
        response=self.projector.project(self.request,self.history,10.2)
        result=dict(type='LOCATE_RESULT',data=dict(status='SUCCEEDED',region='BACK',view='back',
            capture_host_monotonic_ms=None,capture_time_valid=False,frame='camera_color_optical_frame',
            points=[],message='test',base_preview=response))
        decode(json.dumps(result),load_contract(SCHEMAS)['vision_message.schema'])


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_control_process_coordinate_endpoint(self):
        request,history=fixture(time.monotonic()-.15)
        class Worker:
            def snapshot(self):return None
            def history(self):return history
        cfg=robot_config()
        server=Server(load_contract(SCHEMAS),0,0,RealDevices(Worker(),cfg),CAL)
        await server.start()
        try:
            port=server.servers[1].sockets[0].getsockname()[1]
            async with connect(f'ws://127.0.0.1:{port}/coordinates') as ws:
                await ws.send(json.dumps(request))
                response=json.loads(await asyncio.wait_for(ws.recv(),.5))
                self.assertTrue(response['data']['points'][0]['base_coordinate_valid'])
                self.assertFalse(response['data']['motion_eligible'])
            self.assertFalse(server.core.device.calls)
        finally:
            await server.close()


if __name__=='__main__':unittest.main()
