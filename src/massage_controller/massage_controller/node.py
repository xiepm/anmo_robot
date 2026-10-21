import argparse
import asyncio
import json
import logging
from pathlib import Path
import time
from .protocol import load_contract
from .server import Server
from .platform_runtime import StopSignals


def default_config():
    local = Path(__file__).resolve().parents[3] / 'config' / 'rehab_protocol'
    if local.exists(): return str(local)
    from ament_index_python.packages import get_package_share_directory
    return str(Path(get_package_share_directory('massage_controller')) / 'rehab_protocol')


def default_robot_config():
    package_root = Path(__file__).resolve().parents[1]
    local = package_root / 'config' / 'real_robot.json'
    # Source checkouts omit machine-local configuration. SIM/help must not
    # require ROS merely to determine the default path; REAL validates it.
    if local.exists() or (package_root / 'package.xml').is_file():
        return str(local)
    from ament_index_python.packages import get_package_share_directory
    return str(Path(get_package_share_directory('massage_controller')) / 'config' / 'real_robot.json')


class RealRosPublisher:
    def __init__(self, node):
        from sensor_msgs.msg import JointState
        from std_msgs.msg import String
        from visualization_msgs.msg import Marker
        self.node, self.JointState, self.String, self.Marker = node, JointState, String, Marker
        self.joints = node.create_publisher(JointState, '/robot/joint_states', 10)
        self.telemetry = node.create_publisher(String, '~/robot_telemetry', 10)
        self.marker = node.create_publisher(Marker, '/robot_mirror/feedback_status', 10)
        self.last_sample = None
        self.last_publish = 0.0
        self.last_valid = None

    def update(self, device, now):
        sample = device.telemetry(now)
        stamp = sample.get('host_monotonic_after_s')
        changed = sample['valid'] and stamp != self.last_sample
        if changed:
            msg = self.JointState()
            ns = sample['received_unix_ns']
            msg.header.stamp.sec, msg.header.stamp.nanosec = divmod(ns, 1_000_000_000)
            msg.name = sample['joint_names']
            msg.position = sample['visualization_joint_positions_rad']
            self.joints.publish(msg)
            self.last_sample = stamp
        if changed or sample['valid'] != self.last_valid or now-self.last_publish >= .2:
            self.telemetry.publish(self.String(data=json.dumps(sample, ensure_ascii=False, allow_nan=False)))
            m = self.Marker()
            m.header.frame_id = 'mirror_world'
            m.header.stamp = self.node.get_clock().now().to_msg()
            m.ns, m.id, m.type, m.action = 'feedback', 0, self.Marker.TEXT_VIEW_FACING, self.Marker.ADD
            m.pose.position.z = 1.65
            m.pose.orientation.w = 1.0
            m.scale.z = .055
            m.color.a = 1.0
            m.color.g = 1.0 if sample['valid'] else .15
            m.color.r = .15 if sample['valid'] else 1.0
            m.text = sample.get('display_label', 'REAL feedback - elfin5_l - motion disabled') if sample['valid'] else 'STALE / DISCONNECTED - pose not current'
            m.lifetime.nanosec = 600_000_000
            self.marker.publish(m)
            self.last_publish, self.last_valid = now, sample['valid']


async def run(args, node=None):
    worker = device = publisher_real = None
    motion_log = None
    route_review = None
    if args.backend == 'real':
        from .real_device import FeedbackWorker, RealDevices, mapped_joints
        cfg = json.loads(Path(args.robot_config).read_text(encoding='utf-8-sig'))
        if args.robot_host: cfg['host'] = args.robot_host
        if args.robot_port: cfg['port'] = args.robot_port
        mapped_joints({'joint_positions_deg':[0]*6}, cfg)
        workspace = Path(default_config()).parents[1]
        sdk_path = Path(args.sdk) if args.sdk else workspace / cfg['sdk_relative_path']
        if getattr(args, 'noncontact_config', None):
            from .noncontact_worker import NoncontactWorker
            from .noncontact_plan import load_profile
            load_profile(args.noncontact_config)  # Fail on unreadable/malformed JSON.
            directory = workspace / 'src/massage_controller/reports/noncontact_runs'
            directory.mkdir(parents=True, exist_ok=True)
            motion_log = (directory / f'{time.time_ns()}.jsonl').open('x', buffering=1, encoding='utf-8')
            def record_motion(value):
                motion_log.write(json.dumps(value, ensure_ascii=False, allow_nan=False)+'\n')
            worker = NoncontactWorker(cfg,sdk_path,record_motion)
        else:
            worker = FeedbackWorker(cfg, sdk_path)
        device = RealDevices(worker, cfg, scan_enabled=getattr(args, 'enable_real_scan', False))
        if node: publisher_real = RealRosPublisher(node)
    server = Server(load_contract(args.config), args.ui_port, args.vision_port, device=device,
                    calibration=args.hand_eye if device else None)
    if getattr(args, 'noncontact_config', None):
        from .noncontact_control import NoncontactControl
        from .route_review import ReviewManager
        route_review=ReviewManager(worker.review_math,workspace/'web/reports/step3/routes',
            defer_virtual_model=load_profile(args.noncontact_config).get('virtual_model_check')=='DEFERRED')
        server.core.noncontact = NoncontactControl(server.core,args.noncontact_config,worker,review=route_review)
    done = asyncio.Event()
    signals = StopSignals(asyncio.get_running_loop(), done.set)
    try:
        # Reserve communication endpoints before opening an SDK connection.
        await server.start()
        if worker: worker.start()
        if node:
            import rclpy
            from std_msgs.msg import String
            publisher = node.create_publisher(String, '~/status', 10)
            node.create_subscription(String, '~/stop', lambda _: server.core.stop('ROS普通停止请求'), 10)
            last_ros_publish = 0.0
            while not done.is_set() and rclpy.ok():
                rclpy.spin_once(node, timeout_sec=0)
                now = time.monotonic()
                if publisher_real: publisher_real.update(device, now)
                if now-last_ros_publish >= .2:
                    publisher.publish(String(data=json.dumps(server.core.status(), ensure_ascii=False)))
                    last_ros_publish = now
                await asyncio.sleep(.02)
        else:
            await done.wait()
    finally:
        try:
            if route_review: route_review.cancel.set()
            await server.close()
            if route_review: await asyncio.to_thread(route_review.close)
            if worker: await asyncio.to_thread(worker.close)
            if motion_log: motion_log.close()
        finally:
            signals.close()


def main():
    parser = argparse.ArgumentParser(description='Control process: real read-only by default; explicit waypoint commissioning', allow_abbrev=False)
    parser.add_argument('--config', default=default_config())
    parser.add_argument('--backend', choices=('real','simulation-test'), default='real')
    parser.add_argument('--robot-config', default=default_robot_config())
    parser.add_argument('--robot-host')
    parser.add_argument('--robot-port', type=int)
    parser.add_argument('--sdk', help='override vendor SDK path')
    parser.add_argument('--hand-eye', default=str(Path(default_config()).parents[1] /
        'zhenjiu/hand_eye/calibration_20261013.json'), help='measured wrist-camera calibration for coordinate preview')
    parser.add_argument('--ui-port', type=int, default=8765)
    parser.add_argument('--vision-port', type=int, default=8766)
    parser.add_argument('--standalone', action='store_true', help='WebSocket tests without ROS publications')
    parser.add_argument('--enable-real-scan', action='store_true',
                        help='allow stationary SCAN/LOCATE acquisition; does not enable motion or accept unverified coordinates')
    parser.add_argument('--noncontact-config', help='opt-in mannequin-only UI waypoint commissioning; rejects unverified calibration/capture/site geometry')
    parser.add_argument('--waypoint-plan', help='local free-space waypoint JSON; offline validation by default')
    parser.add_argument('--execute', action='store_true', help='connect for waypoint commissioning; terminal START still required')
    args, ros_args = parser.parse_known_args()
    if args.noncontact_config and (args.backend != 'real' or not args.enable_real_scan or args.waypoint_plan):
        parser.error('--noncontact-config requires --backend real --enable-real-scan and cannot combine with --waypoint-plan')
    if args.execute and not args.waypoint_plan:
        parser.error('--execute requires --waypoint-plan')
    runner = run
    if args.waypoint_plan:
        from .waypoint_plan import load, digest
        if args.backend != 'real': parser.error('waypoint commissioning requires --backend real')
        try:
            plan = load(args.waypoint_plan)
        except (ValueError, OSError) as exc:
            parser.error(str(exc))
        print(json.dumps(dict(valid=True, points=len(plan['points']), plan_sha256=digest(plan),
                              note='仅静态格式/数值校验；未验证可达性、碰撞或实机执行'), ensure_ascii=False))
        if not args.execute: return
        from .waypoint_runtime import run as runner
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(name)s %(message)s')
    if args.standalone:
        asyncio.run(runner(args))
    else:
        import rclpy
        from rclpy.node import Node
        rclpy.init(args=ros_args)
        node = Node('control_node')
        try: asyncio.run(runner(args, node))
        finally:
            node.destroy_node()
            if rclpy.ok(): rclpy.shutdown()


if __name__ == '__main__': main()
