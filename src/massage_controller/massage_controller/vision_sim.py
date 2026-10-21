"""Explicit test client; does not open a camera or load any robot SDK."""
import argparse
import asyncio
import json
import time
from websockets.legacy.client import connect
from .node import default_config
from .protocol import load_contract, reply


async def client(uri, config, delay=0.15):
    contract = load_contract(config)
    async with connect(uri) as ws:
        task = None
        async def send(m): await ws.send(json.dumps(m,ensure_ascii=False))
        async def health():
            while True:
                await send({'type':'VISION_STATUS','data':dict(mode='SIMULATION',camera_connected=True,
                    model_ready=True,worker_state='LOCATING' if task and not task.done() else 'IDLE',last_frame_age_ms=0,error='')})
                await asyncio.sleep(1)
        async def locate(d):
            result = dict(status='SUCCEEDED',region=d['region'],view=d['view'],capture_host_monotonic_ms=None,
                capture_time_valid=False,frame='camera_color_optical_frame',points=[],message='模拟夹具定位；无真实相机')
            try:
                captured = time.monotonic()*1000
                await asyncio.sleep(delay)
                result.update(capture_host_monotonic_ms=captured,capture_time_valid=True)
                for entry in contract['acupoints']['regions'][d['region']]:
                    for side in entry['sides']:
                        result['points'].append(dict(name=entry['name'],side=side,camera_point_mm=[10,20,800],
                            coordinate_valid=True,normal_camera=None,normal_valid=False))
            except asyncio.CancelledError:
                result.update(status='CANCELED',message='模拟任务已取消')
            await send({'type':'LOCATE_RESULT','data':result})
        heartbeat = asyncio.create_task(health())
        try:
            async for raw in ws:
                m = json.loads(raw)
                if m['cmd'] == 'LOCATE':
                    if task and not task.done():
                        await send(reply('LOCATE',False,'已有定位任务'))
                    else:
                        await send(reply('LOCATE',True,'模拟定位受理'))
                        task = asyncio.create_task(locate(m['data']))
                        # Enter the coroutine before a buffered CANCEL can cancel it,
                        # so its CANCELED terminal handler is always installed.
                        await asyncio.sleep(0)
                elif m['cmd'] == 'CANCEL':
                    if task and not task.done():
                        task.cancel()
                        await task
                    await send(reply('CANCEL',True,'模拟取消完成'))
        finally:
            heartbeat.cancel()
            if task and not task.done(): task.cancel()
            await asyncio.gather(heartbeat,*([task] if task else []),return_exceptions=True)


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--uri',default='ws://127.0.0.1:8766/vision')
    p.add_argument('--config',default=default_config())
    args=p.parse_args()
    asyncio.run(client(args.uri,args.config))


if __name__ == '__main__': main()
