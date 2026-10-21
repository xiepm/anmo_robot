import asyncio
import json
import time
import unittest
from websockets.legacy.client import connect
from massage_controller.node import default_config
from massage_controller.protocol import load_contract
from massage_controller.real_device import FeedbackWorker,RealDevices
from massage_controller.server import Server
from robot_fixture import robot_config

class SlowSDKTests(unittest.IsolatedAsyncioTestCase):
    async def test_blocked_sdk_cannot_block_status_or_stop_reply(self):
        class Reader:
            def __init__(self,*args):self.audit=[]
            def connect(self):pass
            def sample(self):time.sleep(.6);raise ConnectionError('injected slow SDK')
            def close(self):pass
        cfg=robot_config()
        worker=FeedbackWorker(cfg,'unused',Reader)
        server=Server(load_contract(default_config()),0,0,device=RealDevices(worker,cfg))
        await server.start();worker.start()
        try:
            port=server.servers[0].sockets[0].getsockname()[1]
            async with connect(f'ws://127.0.0.1:{port}') as ws:
                await ws.recv()
                for cmd in ('GET_STATUS','STOP'):
                    start=time.monotonic();await ws.send(json.dumps(dict(cmd=cmd,data={})))
                    while True:
                        m=json.loads(await asyncio.wait_for(ws.recv(),.2))
                        if (cmd=='GET_STATUS' and m['type']=='STATUS') or (m['type']=='REPLY' and m['cmd']==cmd):break
                    self.assertLess(time.monotonic()-start,.2)
                    if cmd=='STOP':self.assertFalse(m['ok'])
        finally:await server.close();await asyncio.to_thread(worker.close)
