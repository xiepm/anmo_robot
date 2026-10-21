"""Asynchronous stage executor for ros2_control FollowJointTrajectory."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import time
import numpy as np
from rclpy.action import ActionClient
from rclpy.clock import Clock, ClockType
from rclpy.qos import QoSProfile, DurabilityPolicy, qos_profile_sensor_data
from action_msgs.msg import GoalStatus
from control_msgs.action import FollowJointTrajectory
from control_msgs.msg import JointTolerance
from trajectory_msgs.msg import JointTrajectoryPoint
from builtin_interfaces.msg import Duration
from sensor_msgs.msg import JointState
from std_msgs.msg import String
from .kinematics import Kinematics
from .collision import CollisionChecker
from .trajectory import Planner


def duration(seconds):
    ns = round(float(seconds)*1e9)
    return Duration(sec=ns//1_000_000_000, nanosec=ns%1_000_000_000)


class DirectMotion:
    def __init__(self, node):
        self.node = node
        self.action = ActionClient(node, FollowJointTrajectory, '/elfin_arm_controller/follow_joint_trajectory')
        self.model = self.collisions = None
        self.q = None
        self.joint_received = 0.
        self.busy = False
        self.phase = 'WAITING'
        self.last_result_code = None
        self.last_action_status = None
        self.history = []
        self.stages = []
        self.goal_handle = None
        self.goal_pending = False
        self.stop_event = Event()
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='independent_ik')
        self.future = None
        self.planning_only = False
        self.prepared = None
        self.current_stage = None
        self.state_pub = node.create_publisher(String, '/massage/motion_state', 10)
        self.description_sub = node.create_subscription(String, '/robot_description', self.description,
            QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL))
        self.joint_sub = node.create_subscription(JointState, '/joint_states', self.joints, qos_profile_sensor_data)
        self.timer = node.create_timer(0.05, self.tick, clock=Clock(clock_type=ClockType.STEADY_TIME))

    def description(self, msg):
        if self.model is not None:
            return
        try:
            model = Kinematics(msg.data)
            collisions = CollisionChecker(model)
            self.model, self.collisions = model, collisions
            self.node.get_logger().info('Independent URDF kinematics and FCL ready')
        except Exception as exc:
            self.node.notice(f'Robot model error: {exc}')

    def joints(self, msg):
        names = [f'elfin_joint{i}' for i in range(1,7)]
        if not all(n in msg.name for n in names) or len(msg.position) != len(msg.name):
            return
        q = np.array([msg.position[msg.name.index(n)] for n in names])
        if np.isfinite(q).all():
            self.q = q
            self.joint_received = time.monotonic()

    def ready(self):
        return self.model is not None and self.q is not None and time.monotonic()-self.joint_received < 1. and self.action.server_is_ready()

    def state(self, phase):
        self.phase = phase
        self.state_pub.publish(String(data=phase))
        self.node.notice(phase)

    def start(self, kind, surface=None, clearance=0.04):
        if self.busy:
            self.node.notice('Motion busy; SPACE cancels')
            return False
        if not self.ready():
            if self.model is None:
                reason = 'Waiting for robot model'
            elif self.q is None or time.monotonic()-self.joint_received >= 1.:
                reason = 'No fresh joint feedback: check Gazebo Play / paused simulation'
            else:
                reason = 'Trajectory controller unavailable: check controller status'
            self.node.notice(reason)
            return False
        self.stop_event.clear()
        self.prepared = None
        self.planning_only = False
        self.busy = True
        self.history = []
        self.last_result_code = self.last_action_status = None
        self.state('PLANNING')
        planner = Planner(self.model, self.collisions, self.stop_event.is_set)
        q = self.q.copy()
        if kind == 'approach':
            self.future = self.pool.submit(planner.approach, q, surface, clearance)
        elif kind == 'observe':
            self.future = self.pool.submit(planner.observe, q)
        elif kind == 'home':
            self.future = self.pool.submit(planner.home, q)
        elif kind == 'demo':
            self.future = self.pool.submit(planner.demo, q, surface)
        else:
            self.complete('FAILED: unknown motion', -100)
            return False
        return True

    def tick(self):
        if self.future is not None and self.future.done():
            future, self.future = self.future, None
            try:
                stages = future.result()
                if self.stop_event.is_set():
                    self.complete('CANCELLED', None, GoalStatus.STATUS_CANCELED)
                    return
                if self.planning_only:
                    self.prepared = stages
                    self.busy = False
                    self.state('PREVIEW_READY')
                else:
                    self.stages = stages
                    self.next_stage()
            except InterruptedError:
                self.complete('CANCELLED', None, GoalStatus.STATUS_CANCELED)
            except Exception as exc:
                self.complete(f'REJECTED: {exc}', -100)
        if self.busy and self.goal_handle is not None and time.monotonic()-self.joint_received > 1.:
            self.node.notice('Joint feedback stale; cancelling trajectory')
            self.cancel()

    def next_stage(self):
        if self.stop_event.is_set():
            self.complete('CANCELLED', None, GoalStatus.STATUS_CANCELED)
            return
        if not self.stages:
            self.complete('COMPLETE', 0, GoalStatus.STATUS_SUCCEEDED)
            return
        stage = self.stages.pop(0)
        if not self.ready() or np.max(np.abs(self.q-stage.positions[0])) > 0.03:
            self.complete('REJECTED: joint state changed since path validation', -100)
            return
        self.current_stage = stage
        goal = FollowJointTrajectory.Goal()
        goal.trajectory.joint_names = self.model.names
        for t,q,v,a in zip(stage.times, stage.positions, stage.velocities, stage.accelerations):
            goal.trajectory.points.append(JointTrajectoryPoint(positions=q.tolist(), velocities=v.tolist(),
                accelerations=a.tolist(), time_from_start=duration(t)))
        goal.goal_tolerance = [JointTolerance(name=n, position=0.008) for n in self.model.names]
        goal.goal_time_tolerance = duration(2.)
        self.state(stage.name)
        self.history.append({'stage':stage.name, 'duration_s':float(stage.times[-1]),
                             'target_tcp_m':self.model.fk(stage.positions[-1])[:3,3].tolist()})
        self.goal_pending = True
        self.action.send_goal_async(goal).add_done_callback(self.accepted)

    def accepted(self, future):
        self.goal_pending = False
        try:
            self.goal_handle = future.result()
            if not self.goal_handle.accepted:
                self.goal_handle = None
                self.complete('FAILED: controller rejected goal', -101)
                return
            self.goal_handle.get_result_async().add_done_callback(self.finished)
            if self.stop_event.is_set():
                self.goal_handle.cancel_goal_async()
        except Exception as exc:
            self.complete(f'FAILED: {exc}', -101)

    def finished(self, future):
        self.goal_handle = None
        try:
            result = future.result()
            if self.stop_event.is_set() or result.status == GoalStatus.STATUS_CANCELED:
                self.complete('CANCELLED', result.result.error_code, GoalStatus.STATUS_CANCELED)
            elif result.status != GoalStatus.STATUS_SUCCEEDED or result.result.error_code != 0:
                self.complete(f'FAILED: {result.result.error_string}', result.result.error_code, result.status)
            else:
                self.next_stage()
        except Exception as exc:
            self.complete(f'FAILED: {exc}', -101)

    def cancel(self):
        self.prepared = None
        if not self.busy:
            if self.phase == 'PREVIEW_READY':self.state('CANCELLED')
            return
        self.stop_event.set()
        self.stages = []
        self.state('CANCELLING')
        if self.goal_handle is not None:
            self.goal_handle.cancel_goal_async()
        elif self.future is None and not self.goal_pending:
            self.complete('CANCELLED', None, GoalStatus.STATUS_CANCELED)

    def complete(self, phase, code, status=None):
        self.busy = False
        self.stages = []
        self.last_result_code = code
        self.last_action_status = status
        self.state(phase)

    def prepare_trace(self, body_path, clearance, speed, body_pose=None):
        if self.busy or not self.ready():
            self.node.notice('机器人未就绪或正在运动，请先等待 / 停止')
            return False
        self.stop_event.clear();self.prepared=None;self.planning_only=True
        self.busy=True;self.history=[]
        self.last_result_code=self.last_action_status=None
        self.state('PLANNING')
        q=self.q.copy();path=np.array(body_path,copy=True)
        pose=None if body_pose is None else np.array(body_pose,copy=True)
        def calculate():
            model,collisions=self.model,self.collisions
            if pose is not None:
                from .registration import registered_model
                model=registered_model(model,pose);collisions=CollisionChecker(model)
            return Planner(model,collisions,self.stop_event.is_set).trace(q,path,clearance,speed)
        self.future=self.pool.submit(calculate)
        return True

    def execute_prepared(self):
        if self.busy or self.prepared is None or not self.ready():return False
        if np.max(np.abs(self.q-self.prepared[0].positions[0]))>.03:
            self.prepared=None
            self.state('REJECTED: 起始关节已改变，请重新规划')
            return False
        self.stop_event.clear();self.busy=True;self.planning_only=False
        self.stages=list(self.prepared);self.prepared=None;self.history=[]
        self.next_stage()
        return self.busy

    def invalidate_preview(self):
        self.prepared=None
        if self.busy and self.planning_only:self.cancel()
        elif not self.busy:self.state('EDITING')

    def close(self):
        self.stop_event.set()
        self.pool.shutdown(wait=True, cancel_futures=True)
