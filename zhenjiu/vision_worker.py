#!/usr/bin/env python3
"""Headless vision worker entry; REAL by default, no SDK or robot operations."""
import argparse
import asyncio
import fcntl
import json
import logging
import math
from pathlib import Path
import signal
from urllib.parse import urlsplit

from rehab_vision.backends import PoseModel, RealSource, ReplayModel, ReplaySource
from rehab_vision.client import run_client
from rehab_vision.contract import Contract, DEFAULT_CONTRACT
from rehab_vision.engine import Engine

APP = Path(__file__).resolve().parent


def positive(value):
    n = float(value)
    if not math.isfinite(n) or n <= 0:
        raise argparse.ArgumentTypeError('必须为有限正数')
    return n


async def run(args):
    contract = Contract(args.contract)
    source = ReplaySource(args.replay) if args.replay else RealSource()
    model = ReplayModel() if args.replay else PoseModel(contract, args.model, args.pose_backend, args.pose_rotation)
    from rehab_vision.base_client import BaseClient
    base = None if args.replay or args.no_base_coordinates else BaseClient(args.base_uri)
    engine = Engine(source, model, contract, base_client=base)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    timer = loop.call_later(args.run_seconds, stop.set) if args.run_seconds else None
    engine.start()
    try:
        await run_client(engine, contract, args.uri, stop, args.locate_timeout)
    finally:
        if timer:
            timer.cancel()
        diagnostics = dict(health=engine.health(), diagnostics=engine.diagnostics())
        await asyncio.to_thread(engine.close)
        if args.diagnostics:
            Path(args.diagnostics).write_text(json.dumps(diagnostics, ensure_ascii=False, indent=2))
        logging.info('视觉进程已退出；相机已释放或底层超时已记录')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--uri', default='ws://127.0.0.1:8766/vision')
    p.add_argument('--contract', type=Path, default=DEFAULT_CONTRACT)
    p.add_argument('--model', type=Path, default=APP / 'models/pose_landmarker_full.task')
    p.add_argument('--pose-backend', choices=('mediapipe','dwpose-l','rtmw-l'), default='rtmw-l')
    p.add_argument('--pose-rotation', type=int, choices=(0,90,180,270), default=90)
    p.add_argument('--base-uri', default='ws://127.0.0.1:8766/coordinates')
    p.add_argument('--no-base-coordinates', action='store_true', help='仅返回原有相机坐标')
    p.add_argument('--replay', type=Path, help='显式 SIMULATION 原始点回放 JSON，不打开相机')
    p.add_argument('--locate-timeout', type=positive, default=30.0)
    p.add_argument('--run-seconds', type=positive, help='调试时限；默认持续运行')
    p.add_argument('--diagnostics', type=Path, help='退出时保存健康、帧/推理统计和时间域证据')
    args = p.parse_args()
    uri = urlsplit(args.uri)
    if uri.scheme != 'ws' or uri.hostname not in ('127.0.0.1', 'localhost', '::1') or uri.path != '/vision' or uri.query or uri.fragment:
        p.error('仅允许本机 ws://.../vision 接口')
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    # No process-killing fallback: second worker must fail rather than steal the device.
    lock = None
    if not args.replay:
        lock = (APP / '.vision_worker.lock').open('a')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            p.error('已有 REAL vision_worker 运行；请先退出原进程')
    try:
        asyncio.run(run(args))
    finally:
        if lock:
            lock.close()


if __name__ == '__main__':
    main()
