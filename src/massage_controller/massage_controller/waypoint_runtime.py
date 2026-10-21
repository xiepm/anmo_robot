"""Terminal-controlled commissioning mode of control_node, using one SDK worker."""
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import select
import signal
import socket
import sys
import threading
import time

from .real_device import RealDevices
from .waypoint_motion import MotionReader, WaypointSession
from .waypoint_plan import load, digest


class WaypointTelemetry(RealDevices):
    def telemetry(self, now):
        sample = super().telemetry(now)
        sample.update(mode='REAL_WAYPOINT_COMMISSIONING', motion_enabled=True,
                      motion_blocker='', display_label='REAL elfin5_l - waypoint commissioning',
                      target_reference=self.worker.plan.get('target_reference', 'TCP'),
                      physical_tool_pose_mm_deg=self.worker.plan['tool_pose_mm_deg'])
        return sample


async def run(args, node=None):
    from .node import default_config, RealRosPublisher
    plan = load(args.waypoint_plan)
    if not sys.stdin.isatty():
        raise ValueError('--execute 必须从交互终端运行；启动后在 READY 输入 START')
    cfg = json.loads(Path(args.robot_config).read_text())
    if cfg.get('robot_model') != 'elfin5_l' or cfg.get('model_confirmed') is not True:
        raise ValueError('实机配置必须确认 elfin5_l 型号')
    if args.robot_host: cfg['host'] = args.robot_host
    if args.robot_port: cfg['port'] = args.robot_port
    workspace = Path(default_config()).parents[1]
    sdk = Path(args.sdk) if args.sdk else workspace / cfg['sdk_relative_path']
    # Keep the business ports exclusively reserved so a second control process
    # cannot start alongside this mode. No UI or vision server runs here.
    sockets = []
    session = None
    thread = None
    try:
        for port in (args.ui_port, args.vision_port):
            sock = socket.socket()
            sockets.append(sock)
            sock.bind(('127.0.0.1', port))
        directory = workspace / 'src/massage_controller/reports/waypoint_runs'
        directory.mkdir(parents=True, exist_ok=True)
        name = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
        path = directory / f'{name}.jsonl'
        with path.open('x', buffering=1) as log:
            def record(value):
                log.write(json.dumps(value, ensure_ascii=False, allow_nan=False)+'\n')
            reader = MotionReader(sdk, cfg['host'], cfg['port'], timeout_s=.15)
            session = WaypointSession(plan, reader, record)
            device = WaypointTelemetry(session, cfg)
            real_ros = RealRosPublisher(node) if node else None
            status_pub = None
            if node:
                import rclpy
                from std_msgs.msg import String
                status_pub = node.create_publisher(String, '~/status', 10)
                node.create_subscription(String, '~/stop', lambda _: session.request_stop(), 10)
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, session.request_stop)
            print(f'方案 SHA256: {digest(plan)}\n日志: {path}\n仅 READY 时接受 START；STOP / Ctrl+C 请求停止。', flush=True)
            thread = threading.Thread(target=session.run, name='huayan-waypoint', daemon=True)
            thread.start()
            previous, last_status_at = None, 0.
            while True:
                # SDK I/O never blocks ROS callbacks or terminal STOP reception.
                if node:
                    if not rclpy.ok(): session.request_stop()
                    else: rclpy.spin_once(node, timeout_sec=0)
                if select.select([sys.stdin], [], [], 0)[0]:
                    line = sys.stdin.readline()
                    if not line or line.strip().upper() == 'STOP':
                        session.request_stop()
                    elif line.strip().upper() == 'START':
                        if not session.request_start():
                            print('START 未受理：当前不是 READY。', flush=True)
                    elif line.strip():
                        print('只接受 START / STOP。', flush=True)
                now = time.monotonic()
                device.poll(now, None, False)
                if real_ros and rclpy.ok(): real_ros.update(device, now)
                status = session.status_snapshot()
                if status != previous:
                    print(json.dumps(status, ensure_ascii=False), flush=True)
                    previous = status
                if status_pub and rclpy.ok() and now-last_status_at >= .2:
                    status_pub.publish(String(data=json.dumps(status, ensure_ascii=False)))
                    last_status_at = now
                if session.done.is_set():
                    if status_pub and rclpy.ok():
                        status_pub.publish(String(data=json.dumps(status, ensure_ascii=False)))
                    break
                await asyncio.sleep(.02)
            thread.join()
            # Persist transport audit after the worker has disconnected.
            record(dict(type='SDK_AUDIT', commands=list(reader.audit)))
            if status['state'] not in ('COMPLETED', 'STOPPED', 'CANCELLED_BEFORE_MOTION'):
                raise RuntimeError(status.get('error') or status['state'])
    finally:
        if session and thread and thread.is_alive():
            session.request_stop()
            # Let the worker finish STOP before disposing of SDK/ROS/process state.
            while thread.is_alive():
                await asyncio.sleep(.05)
        for sock in sockets:
            sock.close()
