"""Loopback controller test stub. Cannot connect to or command a robot."""
import argparse
import asyncio
import json
from pathlib import Path
import time
from websockets.legacy.server import serve
from .contract import Contract, strict_json


async def run(args):
    contract, records, done = Contract(args.contract), [], asyncio.Event()
    async def handler(ws, path):
        if path != '/vision':
            await ws.close(1008, 'wrong path')
            return
        requested = False
        async for raw in ws:
            msg = strict_json(raw)
            contract.message.validate(msg)
            records.append(dict(host_monotonic_ms=time.monotonic() * 1000, message=msg))
            print(json.dumps(msg, ensure_ascii=False), flush=True)
            if msg['type'] == 'VISION_STATUS' and not requested:
                h = msg['data']
                if h['camera_connected'] and h['model_ready'] and h['worker_state'] == 'IDLE':
                    command = dict(cmd='LOCATE', data=dict(region=args.region, view=args.view))
                    await ws.send(json.dumps(command))
                    records.append(dict(sent=command))
                    requested = True
            elif msg['type'] == 'LOCATE_RESULT':
                done.set()
                break
    try:
        async with serve(handler, '127.0.0.1', args.port, max_size=65536):
            await asyncio.wait_for(done.wait(), args.timeout)
    finally:
        Path(args.output).write_text(json.dumps(records, ensure_ascii=False, indent=2))


def main():
    from .contract import DEFAULT_CONTRACT
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--port', type=int, default=18766)
    p.add_argument('--contract', default=DEFAULT_CONTRACT)
    p.add_argument('--region', choices=['BACK', 'UPPER_LIMB', 'LOWER_LIMB'], default='BACK')
    p.add_argument('--view', choices=['front', 'back'], default='back')
    p.add_argument('--timeout', type=float, default=45)
    p.add_argument('--output', required=True)
    asyncio.run(run(p.parse_args()))


if __name__ == '__main__': main()
