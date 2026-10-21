"""Explicit free-space commissioning; no enable/reset/energy or motion retries.
Only the SDK worker owns command I/O. UI business plans remain disabled for REAL.
"""
import copy
import math
import threading
import time
import uuid
from .sdk_readonly import ReadOnlyReader, READ_COMMANDS
from .waypoint_plan import validate, pose, near, in_workspace, check_segment, digest, command_tcp, compile_groups, split_segment
from .speed_override import TemporaryOverride


class MotionReader(ReadOnlyReader):
    allowed_commands = frozenset(READ_COMMANDS | {'WayPointEx', 'GrpStop', 'ReadCurWayPointID', 'ReadOverride', 'SetOverride'})

    def read_override(self):
        values=[]
        rc=self.sdk.HRIF_ReadOverride(0,0,values)
        if rc or len(values)!=1: raise RuntimeError(f'读取速度倍率失败: {rc}')
        return float(values[0])

    def set_override(self,value):
        if type(value) not in (int,float) or not math.isfinite(value) or not .01<=value<=1:
            raise ValueError('速度倍率超出 0.01..1')
        rc=self.sdk.HRIF_SetOverride(0,0,value)
        if rc: raise RuntimeError(f'设置速度倍率响应失败/不确定: {rc}')

    def move(self, target, joints, plan, command_id, radius=0):
        rc = self.sdk.HRIF_WayPointEx(0, 0, 1, target, joints,
                                    command_tcp(plan), [0.]*6,
                                    plan['speed'], plan['acceleration'], radius, 0, 0, 0, 0, command_id)
        if rc != 0:
            raise RuntimeError(f'WayPointEx 失败/响应不确定: {rc}；禁止重发运动')

    def stop(self):
        rc = self.sdk.HRIF_GrpStop(0, 0)
        if rc != 0:
            raise RuntimeError(f'GrpStop 未确认受理: {rc}')

    def sample(self):
        sample = super().sample()
        waypoint = []
        rc = self.sdk.HRIF_ReadCurWaypointID(0, 0, waypoint)
        if rc or len(waypoint) != 1:
            raise RuntimeError(f'无法读取当前路点 ID: {rc}, {waypoint}')
        sample['waypoint_id'] = str(waypoint[0])
        return sample


class Cancelled(Exception):
    pass


class WaypointSession:
    def __init__(self, plan, reader, log, clock=time.monotonic, sleep=time.sleep, *, temporary_speed_override=None):
        self.plan = validate(plan)
        self.reader, self.log, self.clock, self.sleep = reader, log, clock, sleep
        self.start_event, self.stop_event = threading.Event(), threading.Event()
        self.lock = threading.Lock()
        self.latest, self.status = None, {'state': 'CONNECTING'}
        self.done = threading.Event()
        self.run_id = uuid.uuid4().hex[:12]
        self.stop_required = False
        # STOP preflight also sets stop_required; track movement independently.
        self.motion_attempted = False
        self.log_error = None
        self.override_guard = None
        self.returning_to_start = False
        if temporary_speed_override is not None:
            if self.plan['speed']>10 or self.plan['acceleration']>20 or self.plan.get('target_reference')!='FLANGE':
                raise ValueError('临时倍率仅允许本次法兰无接触路线，速度不超过10 mm/s')
            self.override_guard=TemporaryOverride(reader,self.record,temporary_speed_override)

    def record(self, value):
        try:
            self.log(dict(monotonic_s=self.clock(), **value))
        except Exception as exc:
            # Logging failures must never prevent the stop attempt in finally.
            self.log_error = str(exc)
            self.stop_event.set()

    def emit(self, state, **detail):
        value = dict(type='WAYPOINT_STATUS', mode='REAL_WAYPOINT_COMMISSIONING', state=state,
                     plan_sha256=digest(self.plan),
                     motion_attempted=self.motion_attempted,
                     returning_to_start=self.returning_to_start and state in ('RUNNING', 'STOPPING'), **detail)
        with self.lock:
            self.status = value
        self.record(value)

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.latest)

    def status_snapshot(self):
        with self.lock:
            return copy.deepcopy(self.status)

    def request_start(self):
        # Early START must not be latched through preflight.
        with self.lock:
            if self.status['state'] != 'READY' or self.stop_event.is_set():
                return False
            self.start_event.set()
            return True

    def request_stop(self):
        self.stop_event.set()

    def cancelled(self):
        if self.stop_event.is_set():
            if self.log_error:
                raise RuntimeError(f'运行日志写入失败: {self.log_error}')
            raise Cancelled('收到 STOP / 中断 / 终端关闭')

    def read(self, healthy=True):
        sample = self.reader.sample()
        now = self.clock()
        if (not sample.get('valid') or not sample.get('connected') or
                sample.get('controller_simulation') is not False or
                not 0 <= now-sample['host_monotonic_before_s'] <= .5 or
                not 0 <= now-sample['host_monotonic_after_s'] <= .5 or
                not sample['robot_state']['box_connected']):
            raise RuntimeError('真实反馈无效、读取过慢或已过期')
        sample['received_unix_ns'] = time.time_ns()
        with self.lock:
            self.latest = sample
        self.record(dict(type='SAMPLE', sample=sample))
        if healthy:
            state = sample['robot_state']
            if (not state['enabled'] or not state['powered'] or state['moving'] not in (0, 1) or
                    any(state[k] for k in ('error', 'paused', 'emergency_stop', 'safety_guard'))):
                raise RuntimeError('设备未使能/未上电/故障/暂停/急停/防护触发；不会自动使能或复位')
            # ReadActPos uses the active TCP/UCS. Require it to match the explicit
            # numerical transforms sent with WayPointEx; no guessed frame conversion.
            if not near(sample['configured_tool_pose_mm_deg'], command_tcp(self.plan), .01, .01):
                raise RuntimeError('控制器当前 TCP 与所选目标参考点不一致（FLANGE 要求零 TCP）')
            if not near(sample['configured_user_pose_mm_deg'], [0.]*6, .01, .01):
                raise RuntimeError('控制器当前 UCS 不是 Base 的零变换')
            if not in_workspace(self.actual(sample), self.plan):
                raise RuntimeError('实际 TCP 超出填写的工作范围')
        return sample

    @staticmethod
    def actual(sample):
        return sample['tcp_pose']['xyz_mm'] + sample['tcp_pose']['rxyz_deg']

    @staticmethod
    def settled(sample):
        s = sample['robot_state']
        return not s['moving'] and bool(s['in_position']) and bool(s['blending_done'])

    def check_start(self, sample):
        if not self.settled(sample) or not near(self.actual(sample), pose(self.plan['start_pose'], 'start_pose')):
            raise RuntimeError('机械臂未停稳或与填写的 start_pose 不一致（1 mm / 姿态各分量 0.5°）')

    def check_entry(self, sample):
        if not self.settled(sample):
            raise RuntimeError('准备阶段机械臂未停稳')
        if near(self.actual(sample), pose(self.plan['start_pose'], 'start_pose')):
            return False
        entry = self.plan.get('return_from_pose')
        if entry is None or not near(self.actual(sample), pose(entry, 'return_from_pose')):
            raise RuntimeError('当前位置既不在 start_pose，也不在已确认返回路径的 return_from_pose')
        return True

    def stop_and_confirm(self):
        # Never reconnect to resume motion. A failed connection can only be
        # replaced here for one best-effort STOP attempt.
        if not self.reader.connected:
            self.reader.close()
            self.reader.connect()
        self.reader.stop()
        after = self.clock()
        consecutive = 0
        while self.clock()-after <= 3:
            sample = self.read(healthy=False)
            if sample['host_monotonic_before_s'] >= after and not sample['robot_state']['moving']:
                consecutive += 1
            else:
                consecutive = 0
            if consecutive >= 3:
                return sample
            self.sleep(.1)
        raise RuntimeError('GrpStop 已返回，但 3 秒内未确认连续停止反馈')

    def visit(self, index, point, sample):
        return self.visit_group(index, [dict(point, generated=False)], sample)

    def visit_group(self, index, group, sample):
        self.cancelled()
        point = group[-1]
        self.returning_to_start = (index > 0 and self.plan.get('return_to_start', False)
                                   and point['name'].startswith('RETURN_'))
        target = pose(point['pose'], point['name'])
        previous = self.actual(sample)
        if near(previous, target, 1e-6, 1e-6) and len(group) == 1:
            # A no-op can clear its ID before we can read it. Confirm fresh,
            # stable feedback instead, then honour any requested dwell.
            command_id = sample['waypoint_id']
            for _ in range(3):
                self.cancelled()
                self.sleep(.1)
                sample = self.read()
                if (not self.settled(sample) or not near(self.actual(sample), target, 1e-6, 1e-6)
                        or sample['waypoint_id'] != command_id):
                    raise RuntimeError('零距离路点等待期间位置/路点/停稳状态变化')
            return self.dwell(index, point, sample, command_id)
        segment_start = previous
        group_started = self.clock()
        # Only generated, collinear, fixed-orientation waypoints may blend.
        # Original user points always finish with radius=0 and retain dwell.
        for subindex, subpoint in enumerate(group):
            if self.clock()-group_started > self.plan['move_timeout_s']:
                raise RuntimeError('当前原始段在下发插点期间已超时')
            subtarget = pose(subpoint['pose'], subpoint['name'])
            check_segment(previous, subtarget, self.plan)
            radius = 0
            if subindex < len(group)-1:
                following = pose(group[subindex+1]['pose'], 'next')
                if not subpoint.get('generated'):
                    raise RuntimeError('仅自动生成的同向直线插点允许连续过渡')
                radius = min(5., math.dist(previous[:3], subtarget[:3])/4,
                             math.dist(subtarget[:3], following[:3])/4)
            self.cancelled()
            if not 0 <= self.clock()-sample['host_monotonic_before_s'] <= .5:
                raise RuntimeError('发出运动命令前反馈已过期')
            command_id = f'{self.run_id}_{index}_{subindex}'
            self.emit('RUNNING', step='RETURN_TO_START' if index == 0 or self.returning_to_start else 'MOVE', index=index,
                      point=subpoint['name'], command_id=command_id, blend_radius_mm=radius,
                      target_pose=subtarget, generated=subpoint.get('generated', False))
            self.cancelled()
            if not 0 <= self.clock()-sample['host_monotonic_before_s'] <= .5:
                raise RuntimeError('记录命令后反馈已过期，拒绝下发运动')
            # Set before sending: a lost acknowledgment does not prove no motion.
            self.stop_required = True
            self.motion_attempted = True
            self.reader.move(subtarget, sample['joint_positions_deg'], self.plan, command_id, radius=radius)
            previous = subtarget
            if subindex < len(group)-1:
                # Keep observing while filling only this original straight segment.
                # Never enqueue a later dwell point before this one has settled.
                sample = self.read()
        accepted_at = self.clock()
        consecutive = 0
        seen_command = False
        while True:
            self.cancelled()
            sample = self.read()
            # On this controller ReadCurWayPointID clears to "" after arrival.
            # Require positive observation of our unique ID during execution;
            # an idle robot with a blank ID alone is never proof of completion.
            if (sample['host_monotonic_before_s'] >= accepted_at and
                    sample['waypoint_id'] == command_id):
                seen_command = True
            if seen_command and sample['waypoint_id'] not in ('', command_id):
                raise RuntimeError('执行期间出现其他路点 ID')
            if len(group) > 1:
                # Blending collinear points must not turn into an off-path move.
                start_xyz, end_xyz = segment_start[:3], target[:3]
                delta = [b-a for a,b in zip(start_xyz,end_xyz)]
                length2 = sum(x*x for x in delta)
                fraction = sum((x-a)*d for x,a,d in zip(self.actual(sample)[:3],start_xyz,delta))/length2
                nearest = [a+min(1.,max(0.,fraction))*d for a,d in zip(start_xyz,delta)]
                if math.dist(self.actual(sample)[:3],nearest) > 2.:
                    raise RuntimeError('连续直线插点段偏离原直线超过 2 mm')
            if self.clock()-group_started > self.plan['move_timeout_s']:
                raise RuntimeError(f'{point["name"]}: 等待到位超时')
            if (sample['host_monotonic_before_s'] >= accepted_at and
                    seen_command and sample['waypoint_id'] in ('', command_id) and self.settled(sample) and
                    near(self.actual(sample), target)):
                consecutive += 1
            else:
                consecutive = 0
            if consecutive >= 3:
                break
            self.sleep(.1)
        return self.dwell(index, point, sample, command_id)

    def dwell(self, index, point, sample, command_id):
        target = pose(point['pose'], point['name'])
        self.emit('RUNNING', step='DWELL', index=index, point=point['name'])
        last = sample['host_monotonic_after_s']
        elapsed = 0.
        while elapsed < point['dwell_s']:
            self.cancelled()
            self.sleep(.1)
            sample = self.read()
            if (not self.settled(sample) or not near(self.actual(sample), target) or
                    sample['waypoint_id'] not in ('', command_id)):
                raise RuntimeError('停留期间位置/路点/停稳状态变化')
            current = sample['host_monotonic_after_s']
            if not 0 < current-last <= .5:
                raise RuntimeError('停留期间采样间隔无效')
            elapsed += current-last
            last = current
        return sample

    def run(self):
        outcome, error, stop_confirmed = 'ERROR', '', False
        try:
            groups = compile_groups(self.plan)
            self.record(dict(type='PLAN', plan=self.plan, compiled_groups=groups, run_id=self.run_id))
            self.cancelled()
            self.reader.connect()
            self.cancelled()
            sample = self.read()
            needs_return = self.check_entry(sample)
            entry_pose = self.actual(sample)
            # Test ordinary STOP while already stationary before offering START.
            self.stop_required = True
            self.emit('PREFLIGHT', detail='验证普通停止命令及停止反馈')
            self.stop_and_confirm()
            self.cancelled()
            self.emit('READY', detail='输入 START 执行；STOP 或 Ctrl+C 退出')
            while True:
                self.cancelled()
                sample = self.read()
                if not self.settled(sample) or not near(self.actual(sample), entry_pose):
                    raise RuntimeError('等待 START 期间位置已改变，需重新核对返回路径')
                if self.start_event.is_set():
                    break
                self.sleep(.1)
            if self.override_guard:
                self.cancelled()
                self.check_start(self.read())
                self.override_guard.apply()
                self.cancelled()
                self.check_start(self.read())
            if needs_return:
                return_group = split_segment(entry_pose, dict(name='RETURN_TO_START', pose=self.plan['start_pose'], dwell_s=0), self.plan)
                sample = self.visit_group(0, return_group, sample)
            self.check_start(self.read())
            previous_target = pose(self.plan['start_pose'], 'start_pose')
            for index, group in enumerate(groups, 1):
                sample = self.read()
                if not self.settled(sample) or not near(self.actual(sample), previous_target):
                    raise RuntimeError('发出下一路点前机械臂未停稳或偏离上一位置')
                sample = self.visit_group(index, group, sample)
                previous_target = pose(group[-1]['pose'], group[-1]['name'])
            self.cancelled()
            outcome = 'COMPLETED'
        except Cancelled as exc:
            outcome, error = 'STOPPED', str(exc)
        except Exception as exc:
            outcome, error = 'ERROR', str(exc)
            with self.lock:
                self.latest = dict(valid=False, connected=self.reader.connected, error=error)
        finally:
            if self.stop_required:
                self.emit('STOPPING', cause=error or '全部路点结束')
                try:
                    self.stop_and_confirm()
                    stop_confirmed = True
                except Exception as exc:
                    outcome = 'STOP_UNCONFIRMED'
                    error = f'{error}; 实际停止未确认: {exc}。请使用示教器/硬件停止。'
            if outcome == 'STOPPED' and not stop_confirmed:
                outcome = 'CANCELLED_BEFORE_MOTION'
            if self.override_guard and self.override_guard.original is not None:
                if stop_confirmed:
                    try:
                        self.override_guard.restore()
                    except Exception as exc:
                        outcome='ERROR'
                        error += f'; 机械臂已停止，但速度倍率恢复失败: {exc}'
                elif self.override_guard.restore_required:
                    error += f'; 未确认停止，未改写运动中的倍率；原倍率为 {self.override_guard.original}'
            if self.log_error and outcome in ('COMPLETED', 'STOPPED', 'CANCELLED_BEFORE_MOTION'):
                # A logging failure during cancellation is still a fault, even
                # when STOP succeeded and no motion command was attempted.
                outcome, error = 'ERROR', f'运行日志写入失败: {self.log_error}'
            if outcome == 'COMPLETED' and self.plan.get('return_to_start', False):
                try:
                    # Arrival earlier in the loop is insufficient: verify the
                    # actual pose again after STOP and restoring the override.
                    self.cancelled()
                    self.check_start(self.read())
                    self.cancelled()
                except Cancelled as exc:
                    outcome, error = 'STOPPED', str(exc)
                except Exception as exc:
                    outcome, error = 'ERROR', f'回到本次起始位姿未确认: {exc}'
            try:
                self.reader.close()
            except Exception as exc:
                error += f'; SDK 断开异常: {exc}'
                if outcome == 'COMPLETED':
                    outcome = 'ERROR'
            with self.lock:
                self.latest = dict(valid=False, connected=False, error='调试会话结束；采样停止')
            self.emit(outcome, error=error, stop_confirmed=stop_confirmed,
                      returned_to_start=bool(outcome == 'COMPLETED' and stop_confirmed and self.plan.get('return_to_start', False)))
            self.done.set()
