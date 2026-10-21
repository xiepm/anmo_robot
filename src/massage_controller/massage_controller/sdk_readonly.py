"""Real read-only telemetry through the unmodified vendor SDK.
Only an explicit read-command allowlist can pass this adapter's transport.
No robot enable, reset, stop, motion or output commands are sent.
"""
import argparse
from collections import deque
import contextlib
from datetime import datetime, timezone
import importlib.util
import json
import math
from pathlib import Path
import socket
import signal
import sys
import time
import types

READ_COMMANDS = {'IsSimulation', 'ReadFastCmdPort', 'ReadCmdList', 'IsHRAppCmdExist',
                 'ReadRobotState', 'ReadActPos', 'ReadActACS'}
STATE_FIELDS = ('moving', 'enabled', 'error', 'error_code', 'error_axis', 'brake',
                'paused', 'emergency_stop', 'safety_guard', 'powered',
                'box_connected', 'blending_done', 'in_position')


def parse_actual_position(raw):
    if len(raw) != 24:
        raise ValueError(f'ReadActPos expected 24 values, received {len(raw)}')
    values = [float(x) for x in raw]
    if not all(math.isfinite(x) for x in values):
        raise ValueError('ReadActPos contains non-finite values')
    return dict(joint_positions_deg=values[:6],
                tcp_pose=dict(xyz_mm=values[6:9], rxyz_deg=values[9:12],
                              angle_convention='vendor Rx/Ry/Rz; not converted to ROS RPY'),
                configured_tool_pose_mm_deg=values[12:18],
                configured_user_pose_mm_deg=values[18:24],
                coordinate_reference='controller reported TCP/UCS; no hand-eye or flange conversion')


class ReadOnlyReader:
    allowed_commands = frozenset(READ_COMMANDS)

    def __init__(self, sdk_file, host, port=10003, timeout_s=1.0):
        self.host, self.port, self.timeout_s = host, port, timeout_s
        self.connected = False
        self.controller_simulation = None
        self.audit = deque(maxlen=10000)
        from .robot_lock import RobotLock
        self.robot_lock = RobotLock(host, port)
        spec = importlib.util.spec_from_file_location('huayan_readonly_vendor_sdk', sdk_file)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.sdk = module.CPSClient()
        # Replace only this imported instance's I/O routine, never the SDK file.
        # Keep vendor command formatting, response parsing and connection logic.
        def bounded_transport(client, sock, command, timeout=10):
            return self.exchange(sock, command)
        for client in self.sdk.g_clients:
            client._send_and_recv = types.MethodType(bounded_transport, client)

    def exchange(self, sock, command):
        name = command.split(',')[0]
        if name not in self.allowed_commands:
            raise ValueError(f'Non-read command blocked: {name}')
        self.audit.append(dict(host_monotonic_s=time.monotonic(), command=command))
        if sock is None:
            raise ConnectionError('SDK socket is not connected')
        deadline = time.monotonic() + self.timeout_s
        try:
            sock.settimeout(self.timeout_s)
            sock.sendall(command.encode('ascii'))
            response = bytearray()
            while True:
                left = deadline-time.monotonic()
                if left <= 0:
                    raise TimeoutError('read response deadline exceeded')
                sock.settimeout(left)
                part = sock.recv(4096)
                if not part:
                    raise ConnectionError('robot closed the connection')
                response.extend(part)
                if len(response) > 65536:
                    raise ValueError('SDK response too large')
                if b';' in response:
                    text = response.decode('ascii').strip()
                    if not text.endswith(';') or text.count(';') != 1:
                        raise ValueError('unexpected SDK response framing')
                    return text
        except Exception:
            # A timed-out response must never be mistaken for a later read.
            sock.close()
            self.connected = False
            raise

    def connect(self):
        self.robot_lock.acquire()
        with contextlib.redirect_stdout(sys.stderr):
            code = self.sdk.HRIF_Connect(0, self.host, self.port)
            if code:
                raise ConnectionError(f'HRIF_Connect failed: {code}')
            flags = []
            code = self.sdk.g_clients[0].sendAndRecv('IsSimulation,;', flags)
        if code or len(flags) != 1 or flags[0] not in ('0', '1'):
            raise ValueError(f'Cannot confirm controller simulation flag: {code}, {flags}')
        self.controller_simulation = flags[0] == '1'
        self.connected = True

    def sample(self):
        if not self.connected:
            raise ConnectionError('reader is not connected')
        started = time.monotonic()
        state, pose = [], []
        with contextlib.redirect_stdout(sys.stderr):
            rc_state = self.sdk.HRIF_ReadRobotState(0, 0, state)
            rc_pose = self.sdk.HRIF_ReadActPos(0, 0, pose) if rc_state == 0 else None
        if rc_state != 0 or rc_pose != 0:
            self.connected = False
            raise ConnectionError(f'SDK read failed: state={rc_state}, position={rc_pose}')
        if len(state) != 13:
            raise ValueError(f'ReadRobotState expected 13 values, received {len(state)}')
        parsed = parse_actual_position(pose)
        finished = time.monotonic()
        return dict(type='ROBOT_TELEMETRY', mode='REAL_READ_ONLY', valid=True, connected=True,
                    controller_simulation=self.controller_simulation, host=self.host, port=self.port,
                    received_at_utc=datetime.now(timezone.utc).isoformat(),
                    host_monotonic_before_s=started, host_monotonic_after_s=finished,
                    read_duration_ms=(finished-started)*1000, feedback_max_age_ms=500,
                    robot_state=dict(zip(STATE_FIELDS, (int(x) for x in state))),
                    raw_read_act_pos=pose, **parsed)

    def close(self):
        self.connected = False
        try:
            with contextlib.redirect_stdout(sys.stderr):
                self.sdk.HRIF_DisConnect(0)
            thread = self.sdk.g_clients[0].hb_thread
            if thread and thread.is_alive():
                thread.join(timeout=self.timeout_s+1.1)
        finally:
            self.robot_lock.close()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name+'.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    temporary.replace(path)


def probe(sdk_file, host, port):
    reader = ReadOnlyReader(sdk_file, host, port)
    try:
        reader.connect()
        return reader.sample()
    finally:
        reader.close()


class RosTelemetry:
    def __init__(self):
        import rclpy
        from rclpy.node import Node
        from sensor_msgs.msg import JointState
        from std_msgs.msg import String
        rclpy.init()
        self.rclpy, self.JointState, self.String = rclpy, JointState, String
        self.node = Node('robot_readonly_reader')
        self.joints = self.node.create_publisher(JointState, '/robot_readonly/joint_states', 10)
        self.telemetry = self.node.create_publisher(String, '/robot_readonly/telemetry', 10)

    def publish(self, sample):
        self.telemetry.publish(self.String(data=json.dumps(sample, ensure_ascii=False)))
        if sample['valid']:
            msg = self.JointState()
            msg.header.stamp = self.node.get_clock().now().to_msg()
            msg.name = [f'huayan_joint_{i}' for i in range(1, 7)]
            msg.position = [math.radians(x) for x in sample['joint_positions_deg']]
            self.joints.publish(msg)
        self.rclpy.spin_once(self.node, timeout_sec=0)

    def close(self):
        self.node.destroy_node()
        if self.rclpy.ok():
            self.rclpy.shutdown()


def main():
    p = argparse.ArgumentParser(description='Read real robot joints and TCP, without motion commands')
    p.add_argument('--host', required=True)
    p.add_argument('--port', default=10003, type=int)
    p.add_argument('--sdk', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path, help='latest snapshot (atomic write)')
    p.add_argument('--jsonl', type=Path, help='optional append-only sample log')
    p.add_argument('--audit', type=Path, help='optional list of SDK read commands')
    p.add_argument('--count', default=1, type=int, help='0 = continuous; default one sample')
    p.add_argument('--interval', default=.1, type=float, help='seconds between reads')
    p.add_argument('--timeout', default=1., type=float, help='total deadline per socket exchange')
    p.add_argument('--ros', action='store_true', help='also publish read-only ROS telemetry')
    p.add_argument('--quiet', action='store_true', help='suppress each successful sample on stdout')
    p.add_argument('--format', choices=('json','compact'), default='json')
    args = p.parse_args()
    if args.count < 0 or not .05 <= args.interval <= 60 or not .05 <= args.timeout <= 10:
        p.error('count >= 0, interval 0.05..60s, timeout 0.05..10s required')
    def terminate(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, terminate)
    reader = ReadOnlyReader(args.sdk, args.host, args.port, args.timeout)
    ros = RosTelemetry() if args.ros else None
    log = None
    if args.jsonl:
        args.jsonl.parent.mkdir(parents=True, exist_ok=True)
        log = args.jsonl.open('a', buffering=1, encoding='utf-8')
    success = False
    try:
        reader.connect()
        i = 0
        while args.count == 0 or i < args.count:
            began = time.monotonic()
            sample = reader.sample()
            atomic_json(args.output, sample)
            if log: log.write(json.dumps(sample, ensure_ascii=False, allow_nan=False)+'\n')
            if ros: ros.publish(sample)
            if not args.quiet:
                if args.format == 'json':
                    print(json.dumps(sample, ensure_ascii=False, allow_nan=False), flush=True)
                else:
                    values = lambda xs: ', '.join(f'{x:.3f}' for x in xs)
                    print(f"J[deg]=[{values(sample['joint_positions_deg'])}]  "
                          f"TCP[mm]=[{values(sample['tcp_pose']['xyz_mm'])}]  "
                          f"RxRyRz[deg]=[{values(sample['tcp_pose']['rxyz_deg'])}]  "
                          f"read={sample['read_duration_ms']:.2f}ms", flush=True)
            i += 1
            if args.count == 0 or i < args.count:
                time.sleep(max(0, args.interval-(time.monotonic()-began)))
        success = True
    except (Exception, KeyboardInterrupt) as exc:
        failure = dict(type='ROBOT_TELEMETRY', mode='REAL_READ_ONLY', valid=False, connected=False,
                       received_at_utc=datetime.now(timezone.utc).isoformat(),host_monotonic_after_s=time.monotonic(),
                       joint_positions_deg=None, tcp_pose=None, error=str(exc) or 'reader interrupted')
        atomic_json(args.output, failure)
        if log: log.write(json.dumps(failure, ensure_ascii=False)+'\n')
        if ros and ros.rclpy.ok(): ros.publish(failure)
        print(json.dumps(failure, ensure_ascii=False), file=sys.stderr)
    finally:
        reader.close()
        if args.audit: atomic_json(args.audit, list(reader.audit))
        if log: log.close()
        if ros: ros.close()
    return 0 if success else 1


if __name__ == '__main__':
    raise SystemExit(main())
