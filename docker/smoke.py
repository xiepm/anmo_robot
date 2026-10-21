"""Verify the container's same-origin WebSocket path in simulation only."""
import asyncio
import json
import time
from websockets.legacy.client import connect


async def main():
    async with connect('ws://127.0.0.1:8080/control', close_timeout=1) as ws:
        deadline = time.monotonic() + 12
        sent = accepted = False
        while time.monotonic() < deadline:
            await ws.send(json.dumps({'cmd': 'GET_STATUS', 'data': {}}))
            msg = json.loads(await asyncio.wait_for(ws.recv(), 2))
            if msg.get('type') == 'STATUS':
                data = msg['data']
                if data['mode'] != 'SIMULATION':
                    raise RuntimeError('Refusing smoke test against a real backend')
                if data.get('vision_feedback_valid') and not sent:
                    await ws.send(json.dumps({'cmd': 'SCAN', 'data': {'region': 'BACK'}}))
                    sent = True
                if accepted and data.get('positioning_valid'):
                    print('PASS: nginx /control, SIM health and scan; no START command sent')
                    return
            elif msg.get('type') == 'REPLY' and msg.get('cmd') == 'SCAN':
                if not msg['ok']:
                    raise RuntimeError(str(msg))
                accepted = True
            await asyncio.sleep(.05)
        raise RuntimeError('Simulation did not finish its scan before timeout')


if __name__ == '__main__':
    asyncio.run(main())
