"""Wall-clock 60-second three-point acceptance run over both real local sockets."""
import asyncio
import json
from pathlib import Path
import time
from websockets.legacy.client import connect
from massage_controller.server import Server
from massage_controller.node import default_config
from massage_controller.protocol import load_contract
from massage_controller.vision_sim import client


async def main():
    out=Path('/workspace/src/massage_controller/reports')
    server=Server(load_contract(default_config()),0,0)
    events=[]; push_times=[];poll_times=[];query_times=[]; dwell=[]
    original_publish=server.publish
    def publish():
        push_times.append(time.monotonic());original_publish()
    server.publish=publish
    original_poll=server.core.device.poll
    def poll(*args):poll_times.append(time.monotonic());return original_poll(*args)
    server.core.device.poll=poll
    original_request=server.core.request
    def request(raw):
        before=time.monotonic();response=original_request(raw)
        if response['type']=='STATUS':query_times.append((before,time.monotonic()-before))
        return response
    server.core.request=request
    original_tick=server.core.tick
    last_step='NONE';entered=None
    def tick():
        nonlocal last_step,entered
        original_tick();step=server.core.step
        if step!=last_step:
            now=time.monotonic()
            if last_step=='DWELL':dwell.append(now-entered)
            if step=='DWELL':entered=now
            last_step=step
    server.core.tick=tick
    await server.start()
    ui_uri='ws://127.0.0.1:'+str(server.servers[0].sockets[0].getsockname()[1])
    vision_uri='ws://127.0.0.1:'+str(server.servers[1].sockets[0].getsockname()[1])+'/vision'
    vision=asyncio.create_task(client(vision_uri,default_config()))
    try:
        async with connect(ui_uri) as ws:
            async def send(cmd,data=None):
                m=dict(cmd=cmd,data=data or {});events.append(dict(t=time.monotonic(),direction='UI->control',message=m))
                await ws.send(json.dumps(m))
            async def heartbeat():
                while True:await send('GET_STATUS');await asyncio.sleep(1)
            hb=asyncio.create_task(heartbeat())
            async def until(predicate):
                while True:
                    m=json.loads(await asyncio.wait_for(ws.recv(),2))
                    events.append(dict(t=time.monotonic(),direction='control->UI',message=m))
                    if predicate(m):return m
            try:
                while not server.core.health:await asyncio.sleep(.01)
                await send('SCAN',dict(region='BACK'))
                m=await until(lambda m:m['type']=='STATUS' and m['data']['positioning_valid'])
                await send('SET_PLAN',dict(region='BACK',points=m['data']['available_points'],total_time_s=60,
                    energy=dict(type='TEMPERATURE',value=47,unit='C'),gap_mm=30,dwell_mode='STATIC',transition_mode='LIFT_MOVE_APPROACH'))
                await until(lambda m:m['type']=='REPLY' and m['cmd']=='SET_PLAN' and m['ok'])
                started=time.monotonic();await send('START')
                await until(lambda m:m['type']=='STATUS' and m['data']['result']=='COMPLETED')
                wall=time.monotonic()-started
            finally:
                hb.cancel();await asyncio.gather(hb,return_exceptions=True)
        intervals=lambda xs:[b-a for a,b in zip(xs,xs[1:])]
        report=dict(mode='SIMULATION',wall_seconds=wall,dwell_seconds=dwell,
            max_status_gap_s=max(intervals(push_times)),max_device_poll_gap_s=max(intervals(poll_times)),
            max_get_status_processing_s=max(t[1] for t in query_times),
            heartbeat_intervals_s=intervals([t[0] for t in query_times]),
            final_status=server.core.status(),motion_calls=server.core.device.calls)
        report['passed']=(len(dwell)==3 and all(abs(d-20)<.2 for d in dwell) and abs(sum(dwell)-60)<.6
            and report['max_status_gap_s']<.5 and report['max_device_poll_gap_s']<.2
            and all(.8<=d<=1.2 for d in report['heartbeat_intervals_s']))
        (out/'wall_clock_60s.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        (out/'wall_clock_messages.jsonl').write_text(''.join(json.dumps(e,ensure_ascii=False)+'\n' for e in events))
        print(json.dumps({k:v for k,v in report.items() if k not in ('heartbeat_intervals_s','motion_calls')},ensure_ascii=False,indent=2))
        assert report['passed'],report
    finally:
        vision.cancel();await asyncio.gather(vision,return_exceptions=True);await server.close()

if __name__=='__main__':asyncio.run(main())
