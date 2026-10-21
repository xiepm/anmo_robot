import asyncio
import json
import time
import unittest
from websockets.legacy.client import connect
from websockets.exceptions import ConnectionClosed
from massage_controller.server import Server
from massage_controller.protocol import load_contract
from massage_controller.node import default_config
from massage_controller.vision_sim import client


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.s=Server(load_contract(default_config()),0,0)
        await self.s.start()
        self.ui_uri='ws://127.0.0.1:'+str(self.s.servers[0].sockets[0].getsockname()[1])
        self.v_uri='ws://127.0.0.1:'+str(self.s.servers[1].sockets[0].getsockname()[1])+'/vision'
        self.v=asyncio.create_task(client(self.v_uri,default_config()))
        self.ui=await connect(self.ui_uri)
        for _ in range(100):
            if self.s.core.health:break
            await asyncio.sleep(0.01)

    async def asyncTearDown(self):
        await self.ui.close();self.v.cancel()
        await asyncio.gather(self.v,return_exceptions=True)
        await self.s.close()

    async def until(self,predicate,timeout=4):
        async def receive():
            while True:
                m=json.loads(await self.ui.recv())
                if predicate(m):return m
        return await asyncio.wait_for(receive(),timeout)

    async def req(self,cmd,data=None):
        await self.ui.send(json.dumps(dict(cmd=cmd,data=data or {})))
        if cmd=='GET_STATUS':return await self.until(lambda m:m['type']=='STATUS')
        return await self.until(lambda m:m['type']=='REPLY' and m['cmd']==cmd)

    async def test_scan_plan_run_stop(self):
        self.assertTrue((await self.req('SCAN',{'region':'BACK'}))['ok'])
        m=await self.until(lambda m:m['type']=='STATUS' and m['data']['positioning_valid'])
        # One second per selected point, independent of catalogue expansion.
        p=dict(region='BACK',points=m['data']['available_points'],total_time_s=len(m['data']['available_points']),energy=dict(type='FORCE',value=15,unit='N'),dwell_mode='STATIC',transition_mode='LIFT_MOVE_APPROACH')
        self.assertTrue((await self.req('SET_PLAN',p))['ok'])
        self.assertTrue((await self.req('START'))['ok'])
        await self.until(lambda m:m['type']=='STATUS' and m['data']['step']=='DWELL')
        start=time.monotonic()
        self.assertTrue((await self.req('STOP'))['ok'])
        stopped=await self.until(lambda m:m['type']=='STATUS' and m['data']['state']=='IDLE')
        self.assertEqual(stopped['data']['result'],'STOPPED')
        self.assertGreaterEqual(time.monotonic()-start,0.1)
        self.assertLess(time.monotonic()-start,0.5)

    async def test_single_client_and_paths(self):
        for uri in (self.ui_uri,self.v_uri,self.ui_uri+'/bad'):
            ws=await connect(uri)
            with self.assertRaises(ConnectionClosed):await ws.recv()
            self.assertEqual(ws.close_code,1008)
        self.assertEqual((await self.req('GET_STATUS'))['type'],'STATUS')

    async def test_invalid_json_and_binary(self):
        for raw in ('{bad',b'{}','{"cmd":"GET_STATUS","data":{"x":0}}'):
            await self.ui.send(raw)
            m=await self.until(lambda m:m['type']=='REPLY')
            self.assertFalse(m['ok'])
        self.assertEqual((await self.req('GET_STATUS'))['data']['state'],'IDLE')

    async def test_ui_close_and_reconnect(self):
        await self.req('SCAN',{'region':'BACK'})
        m=await self.until(lambda m:m['type']=='STATUS' and m['data']['positioning_valid'])
        self.assertTrue((await self.req('SET_PLAN',dict(region='BACK',points=m['data']['available_points'],total_time_s=3*len(m['data']['available_points']),
            energy=dict(type='FORCE',value=15,unit='N'),dwell_mode='STATIC',transition_mode='LIFT_MOVE_APPROACH')))['ok'])
        self.assertTrue((await self.req('START'))['ok']);start=time.monotonic();await self.ui.close()
        for _ in range(30):
            if self.s.core.state=='IDLE':break
            await asyncio.sleep(0.01)
        self.assertEqual(self.s.core.result,'STOPPED')
        self.assertLess(time.monotonic()-start,0.5)
        self.ui=await connect(self.ui_uri)
        self.assertFalse((await self.req('START'))['ok'])

    async def test_cancel_then_new_scan(self):
        await self.req('SCAN',{'region':'BACK'});await self.req('STOP')
        await self.until(lambda m:m['type']=='STATUS' and m['data']['state']=='IDLE')
        for _ in range(150):
            if self.s.core.pending is None and self.s.core.health['worker_state']=='IDLE':break
            await asyncio.sleep(0.01)
        self.assertTrue((await self.req('SCAN',{'region':'UPPER_LIMB'}))['ok'])
        m=await self.until(lambda m:m['type']=='STATUS' and m['data']['positioning_valid'])
        self.assertEqual(m['data']['region'],'UPPER_LIMB')

    async def test_heartbeat_timeout_wall_clock(self):
        await self.req('SCAN',{'region':'BACK'})
        m=await self.until(lambda m:m['type']=='STATUS' and m['data']['positioning_valid'])
        self.assertTrue((await self.req('SET_PLAN',dict(region='BACK',points=m['data']['available_points'],total_time_s=3*len(m['data']['available_points']),
            energy=dict(type='FORCE',value=15,unit='N'),dwell_mode='STATIC',transition_mode='LIFT_MOVE_APPROACH')))['ok'])
        self.assertTrue((await self.req('START'))['ok'])
        accepted=self.s.core.ui_at
        await self.until(lambda m:m['type']=='STATUS' and m['data']['state']=='STOPPING')
        elapsed=time.monotonic()-accepted
        self.assertGreaterEqual(elapsed,3.0);self.assertLessEqual(elapsed,3.2)
        await self.until(lambda m:m['type']=='STATUS' and m['data']['result']=='STOPPED')
        self.assertIsNone(self.s.core.plan)

    async def test_buffered_scan_stop_keeps_vision_connection(self):
        await self.ui.send(json.dumps(dict(cmd='SCAN',data=dict(region='BACK'))))
        await self.ui.send(json.dumps(dict(cmd='STOP',data={})))
        await self.until(lambda m:m['type']=='STATUS' and m['data']['result']=='STOPPED')
        for _ in range(100):
            if self.s.core.pending is None:break
            await asyncio.sleep(.01)
        self.assertIsNone(self.s.core.pending)
        self.assertTrue(self.s.core.vision_connected)
        self.assertFalse(self.s.core.points)
