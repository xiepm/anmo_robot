"""Single-owner state machine; all methods run on the communication event loop."""
import copy
import math
import logging
from .devices import SimDevices
from .geometry import SimulationGeometry
from .protocol import decode, reply
from .point_confirmation import PointConfirmation


class Controller:
    def __init__(self, contract, clock, device=None):
        self.contract, self.clock = contract, clock
        self.cfg = copy.deepcopy(contract['simulation'])
        if self.cfg['mode'] != 'SIMULATION':
            raise ValueError('真实运动未实现：缺少已验证标定、路径和末端适配')
        self.device = device if device is not None else SimDevices(self.cfg)
        self.mode = self.device.mode
        self.geometry = SimulationGeometry('SIMULATION') if self.mode == 'SIMULATION' else None
        if self.mode == 'REAL':
            self.cfg['capabilities'] = []
            self.cfg['feedback_stale_s'] = self.device.config['feedback_stale_s']
        self.state, self.step, self.result, self.error = 'IDLE', 'NONE', 'NONE', ''
        self.region = self.plan = self.current = None
        self.selected_energy = None
        self.points = []
        self.scan_snapshot = None
        self.snapshot_verified = lambda packet: False
        self.confirmation = PointConfirmation(self)
        self.noncontact = None
        self.remaining = 0.0
        self.ui_at = None
        self.vision_at = None
        self.health = None
        self.vision_connected = False
        self.pending = None
        self.cancel_at = None
        self.cancel_ack = self.terminal = False
        self.outbox = []
        self.close_vision = False
        self.last_tick = self.clock()
        self.last_poll = float('-inf')
        self.stop_at = None
        self.feedback_recoverable = False
        self.recovery_requires_ui = False
        self.recovery_since = self.recovery_last_tick = None
        self.device.poll(self.clock(), None, False)

    def known(self, region, p):
        return any(x['name'] == p['name'] and p['side'] in x['sides']
                   for x in self.contract['acupoints']['regions'].get(region, []))

    def robot_valid(self, now):
        return self.device.connected and now - self.device.robot_at <= self.cfg['feedback_stale_s']

    def vision_valid(self, now):
        h = self.health
        return bool(self.vision_connected and h and self.vision_at is not None
                    and now - self.vision_at <= self.cfg['vision_timeout_s']
                    and h['mode'] == self.mode and h['camera_connected'] and h['model_ready']
                    and h['worker_state'] != 'ERROR' and not h['error']
                    and h['last_frame_age_ms'] is not None and h['last_frame_age_ms'] <= 500)

    def preserve_photo_for_preview_delay(self, now):
        """Keep only an idle frozen photo through a brief, age-only delay.

        This never retains confirmed coordinates or execution permission. Live
        health gates, including active noncontact STOP handling, stay strict.
        """
        h = self.health
        if (self.mode != 'REAL' or self.state != 'IDLE'
                or (self.noncontact and self.noncontact.active)
                or not self.vision_connected or not h or self.vision_at is None):
            return False
        elapsed = now-self.vision_at
        age = h['last_frame_age_ms']
        if (not 0 <= elapsed <= self.cfg['vision_timeout_s'] or h['mode'] != self.mode
                or not h['camera_connected'] or not h['model_ready']
                or h['worker_state'] != 'IDLE' or h['error'] or age is None
                or not 500 < age
                or age+elapsed*1000 >= min(1.5,self.cfg['vision_timeout_s'])*1000):
            return False
        try:
            self.confirmation.check_frozen_capture()
        except (ValueError,KeyError,TypeError):
            return False
        value = self.confirmation.value
        retiring = value is not None and value['state'] in ('PENDING','CONFIRMED')
        if retiring or self.plan:
            logging.getLogger('control_node').warning(
                '实时视觉帧暂时延迟 %.1f ms：保留本次照片，撤销坐标确认和路线，恢复后须重新确认', age+elapsed*1000)
        self.confirmation.reset(keep_capture=True)
        if value is not None:
            # Preserve an explicit terminal receipt for UI waiters. Repeated
            # stale ticks must not erase it or revive a retired REFINE request.
            if retiring:
                value.update(state='FAILED',points=[],execution_eligible=False,
                    error='实时画面暂时延迟，本次确认已撤销；恢复后请重新确认')
            self.confirmation.value = value
        self.points = []
        self.clear_plan()
        revoke = getattr(self.noncontact,'revoke_authorization',None)
        if retiring and revoke is not None:
            revoke(value['revision'])
        review = getattr(self.noncontact,'review',None)
        if review is not None:
            # sync's non-confirmed branch cancels any in-flight review and
            # drops its result without reading configuration or querying the SDK.
            review.sync(self.confirmation,self.device.sample,self.noncontact.profile_path)
        return True

    def clear_plan(self):
        self.plan = self.current = None
        self.selected_energy = None
        self.remaining = 0.0
        self.step = 'NONE'

    def invalidate(self, reason):
        if self.confirmation.value or self.scan_snapshot:
            import logging
            logging.getLogger('control_node').warning('定位结果失效: %s', reason)
        if self.noncontact and self.noncontact.active:
            self.noncontact.stop(reason, fault=True)
            return
        self.confirmation.reset()
        self.points = []
        self.scan_snapshot = None
        if self.state == 'RUNNING':
            self.fail(reason)
        elif self.state == 'READY':
            self.clear_plan()
            self.state, self.error = 'IDLE', reason

    def cancel_scan(self, now):
        if self.pending is not None:
            self.points = []
        if self.pending is not None and self.cancel_at is None:
            self.cancel_at = now
            self.cancel_ack = self.terminal = False
            self.outbox.append({'cmd': 'CANCEL', 'data': {}})

    def stop(self, reason='', completed=False):
        if self.noncontact and self.noncontact.active:
            self.noncontact.stop(reason or '操作员停止')
            return
        self.confirmation.reset()
        now = self.clock()
        if not self.device.motion_enabled:
            self.cancel_scan(now)
            self.clear_plan()
            if self.state != 'ERROR':
                self.state = 'IDLE'
            return
        if self.state == 'STOPPING':
            return
        if self.state == 'ERROR':
            return  # Initial fault already requested stop; do not reset timeout.
        if self.state in ('IDLE', 'READY'):
            self.clear_plan()
            self.state, self.result = 'IDLE', 'STOPPED'
            self.error = reason
            return
        self.cancel_scan(now)
        self.state, self.step = 'STOPPING', 'NONE'
        self.stop_at, self.stop_result = now, 'COMPLETED' if completed else 'STOPPED'
        self.error = reason
        self.device.output_off(now)
        self.device.stop(now)

    def fail(self, reason, feedback_only=False):
        if self.noncontact and self.noncontact.active:
            self.noncontact.stop(reason, fault=True)
            return
        self.confirmation.reset()
        if self.state == 'ERROR':
            # A hardware/other fault must never inherit transient recovery permission.
            if not feedback_only:
                self.feedback_recoverable = False
                self.recovery_requires_ui = False
                self.error = reason.replace('已请求停止', '真实停止通路未开放') if not self.device.motion_enabled else reason
            return
        self.feedback_recoverable = bool(feedback_only and self.mode == 'REAL'
            and not self.device.motion_enabled and getattr(self.device, 'scan_enabled', False)
            and self.state in ('IDLE', 'READY', 'SCANNING'))
        self.recovery_requires_ui = False
        self.recovery_since = self.recovery_last_tick = None
        now = self.clock()
        self.cancel_scan(now)
        if not self.device.motion_enabled:
            reason = reason.replace('已请求停止', '真实停止通路未开放')
        if self.feedback_recoverable:
            reason = '机械臂反馈失效或断联，拍照已暂停，旧定位已清除'
        self.state, self.step, self.result, self.error = 'ERROR', 'NONE', 'FAILED', reason
        self.plan = self.current = None
        self.points = []
        self.scan_snapshot = None
        self.device.output_off(now)
        self.device.stop(now)
        self.stop_at = now
        logging.getLogger('control_node').warning('控制故障：%s；拍照通讯恢复许可=%s', reason, self.feedback_recoverable)

    def recover_acquisition_feedback(self, now):
        """Recover acquisition after feedback loss or a proven pre-start UI loss."""
        ready = (self.state == 'ERROR' and self.feedback_recoverable
                 and self.mode == 'REAL' and not self.device.motion_enabled
                 and getattr(self.device, 'scan_enabled', False)
                 and (not self.recovery_requires_ui or (self.ui_at is not None
                      and 0 <= now-self.ui_at < self.cfg['ui_timeout_s']))
                 and self.robot_valid(now) and not self.device.estop and not self.device.fault
                 and not self.device.moving and self.device.observation_ready
                 and self.vision_valid(now) and self.health['worker_state'] == 'IDLE'
                 and self.pending is None and self.cancel_at is None)
        if not ready:
            self.recovery_since = self.recovery_last_tick = None
            return
        if (self.recovery_since is None or self.recovery_last_tick is None
                or now-self.recovery_last_tick > self.cfg['feedback_stale_s']):
            self.recovery_since = now
        self.recovery_last_tick = now
        if now-self.recovery_since < 2.0:
            return
        self.clear_plan()
        self.confirmation.reset()
        self.points, self.scan_snapshot = [], None
        self.state, self.step, self.result, self.error = 'IDLE', 'NONE', 'NONE', ''
        self.stop_at = None
        self.feedback_recoverable = False
        self.recovery_requires_ui = False
        self.recovery_since = self.recovery_last_tick = None
        logging.getLogger('control_node').info('拍照模式通讯恢复：反馈连续有效且停稳 2 秒，回到 IDLE；旧定位与方案已清除')

    def ui_lost(self):
        self.ui_at = None
        if self.recovery_requires_ui:
            self.recovery_since = self.recovery_last_tick = None
        if self.noncontact and self.noncontact.active:
            # Classify immediately: reconnect may happen before the next tick.
            self.noncontact.stop('UI 连接中断或超时', fault=True, fault_kind='UI_CONNECTION')
            return
        self.stop('UI连接中断或存活超时')

    def vision_lost(self):
        self.vision_connected = False
        self.health = self.vision_at = None
        self.pending = self.cancel_at = None
        self.invalidate('视觉连接中断，定位失效')
        if self.state == 'SCANNING':
            self.state, self.result, self.error = 'IDLE', 'FAILED', '视觉连接中断'

    def request(self, raw):
        cmd = None
        try:
            # Echo a recognizable cmd even when the rest of the message is invalid.
            import json
            try:
                v = json.loads(raw)
                cmd = v.get('cmd') if isinstance(v, dict) and isinstance(v.get('cmd'), str) else None
            except (ValueError, TypeError):
                pass
            try:
                msg = decode(raw, self.contract['ui_request.schema'])
            except ValueError:
                if cmd == 'SET_PLAN' and self.state in ('IDLE', 'READY'):
                    self.clear_plan()
                    self.state = 'IDLE'
                raise
            cmd, data = msg['cmd'], msg['data']
            now = self.clock()
            self.ui_at = now
            if cmd == 'GET_STATUS':
                return self.status()
            if cmd in ('AUTHORIZE_NONCONTACT','PREPARE_NONCONTACT','START_NONCONTACT','CHECK_NONCONTACT_STOP'):
                if not self.noncontact: raise ValueError('未配置假人无接触联调模式')
                if cmd == 'AUTHORIZE_NONCONTACT': self.noncontact.authorize(data)
                elif cmd == 'PREPARE_NONCONTACT': self.noncontact.prepare(data)
                elif cmd == 'START_NONCONTACT': self.noncontact.start(data)
                else: self.noncontact.check_stop()
                return reply(cmd, True, '请求已受理，实际状态以控制反馈为准')
            if self.noncontact and self.noncontact.active:
                if cmd != 'STOP': raise ValueError('无接触任务期间仅允许 STOP')
                self.noncontact.stop()
                return reply(cmd, True, '已请求停止，等待真实停止反馈')
            if cmd == 'CLEAR_POINTS':
                if self.state not in ('IDLE','READY'):
                    raise ValueError('当前状态不能撤销坐标确认')
                self.confirmation.reset(keep_capture=True)
                self.clear_plan()
                self.state = 'IDLE'
                return reply(cmd, True, '旧坐标确认已撤销，可以继续调整')
            if cmd == 'CONFIRM_POINTS':
                self.confirmation.request(data)
                return reply(cmd, True, '最终像素位置已受理，等待深度重算与基座坐标确认')
            if cmd == 'STOP':
                if not self.device.motion_enabled:
                    canceling = self.state == 'SCANNING' and getattr(self.device, 'scan_enabled', False)
                    self.stop()
                    if canceling:
                        return reply(cmd, True, '已请求取消定位；此模式未发起机械臂运动，不表示机械臂停止确认')
                    return reply(cmd, False, '实机只读调试：真实停止通路未开放，不能确认机械臂已停止；请使用现场停止装置')
                self.stop()
                return reply(cmd, True, '停止请求已受理；实际结果以STATUS为准')
            scan_allowed = cmd == 'SCAN' and getattr(self.device, 'scan_enabled', False)
            if not self.device.motion_enabled and cmd in ('SCAN', 'SET_PLAN', 'START') and not scan_allowed:
                from .real_device import BLOCKER
                raise ValueError(BLOCKER)
            if cmd in ('SCAN', 'SET_PLAN') and self.state not in ('IDLE', 'READY'):
                if self.state == 'ERROR':
                    suffix = '；反馈恢复且停稳后将自动恢复拍照，请稍后重试' if self.feedback_recoverable else ''
                    raise ValueError(f'控制进程故障：{self.error}{suffix}')
                raise ValueError(f'当前状态 {self.state} 不允许此操作')
            if cmd == 'SCAN':
                if not self.robot_valid(now) or self.device.moving or not self.device.observation_ready:
                    raise ValueError('机械臂未在有效观察位停稳')
                if not self.vision_valid(now) or self.health['worker_state'] != 'IDLE':
                    raise ValueError('视觉未就绪或健康状态无效')
                if self.pending is not None or self.cancel_at is not None:
                    raise ValueError('旧定位任务尚未结束或取消尚未完成')
                if self.noncontact:
                    self.noncontact.reset_terminal()
                self.clear_plan()
                self.confirmation.reset()
                self.points = []
                self.scan_snapshot = None
                self.result = 'NONE'
                self.region, self.state, self.error = data['region'], 'SCANNING', ''
                self.pending = dict(region=self.region, view=self.cfg['views'][self.region], start=now)
                self.outbox.append({'cmd': 'LOCATE', 'data': {k:self.pending[k] for k in ('region','view')}})
            elif cmd == 'SET_PLAN':
                self.clear_plan()
                self.state, self.result, self.error = 'IDLE', 'NONE', ''
                self.validate_plan(data)
                self.plan = copy.deepcopy(data)
                if self.mode == 'REAL':
                    self.plan['confirmed_surface_points'] = self.confirmation.final_surface_points(
                        data.get('confirmation_revision'), data['points'])
                self.selected_energy = copy.deepcopy(data['energy'])
                self.plan['time_per_point_s'] = data['total_time_s'] // len(data['points'])
                self.remaining, self.state = float(data['total_time_s']), 'READY'
                return reply(cmd, True, '方案已确认', copy.deepcopy(self.plan))
            elif cmd == 'START':
                if self.state != 'READY' or not self.plan:
                    raise ValueError('没有已确认方案')
                if self.mode == 'REAL':
                    final = self.confirmation.final_surface_points(self.plan.get('confirmation_revision'), self.plan['points'])
                    if final != self.plan.get('confirmed_surface_points'):
                        raise ValueError('方案坐标不是最后确认版本')
                if not self.points or not self.vision_valid(now):
                    self.invalidate('定位失效，请重新扫描')
                    raise ValueError('定位失效，请重新扫描')
                if not self.robot_valid(now) or self.device.estop:
                    self.fail('设备反馈无效或急停')
                    raise ValueError(self.error)
                if self.device.moving:
                    raise ValueError('机器人仍在移动，不能开始新任务')
                self.state, self.result, self.error = 'RUNNING', 'NONE', ''
                self.index = 0
                self.point_left = float(self.plan['time_per_point_s'])
                self.current = self.plan['points'][0]
                self.begin_motion('APPROACH', now)
            return reply(cmd, True, '请求已受理')
        except (ValueError, TypeError, KeyError) as exc:
            return reply(cmd, False, str(exc))

    def validate_plan(self, p):
        if self.mode == 'REAL':
            self.confirmation.final_surface_points(p.get('confirmation_revision'), p['points'])
        if p['region'] != self.region or not self.points:
            raise ValueError('当前部位尚未完成有效定位')
        if not self.robot_valid(self.clock()) or not self.vision_valid(self.clock()):
            raise ValueError('设备或视觉反馈无效')
        available = {(x['name'], x['side']) for x in self.points}
        if any(not self.known(p['region'], x) or (x['name'], x['side']) not in available for x in p['points']):
            raise ValueError('穴位名称、侧别或当前定位不匹配')
        if type(p['total_time_s']) is not int or p['total_time_s'] % len(p['points']):
            raise ValueError('时间必须为可均分的正整数')
        e = p['energy']
        if e['type'] not in self.cfg['capabilities']:
            raise ValueError('末端能力未配置')
        if e['unit'] != {'TEMPERATURE':'C','FORCE':'N'}[e['type']]:
            raise ValueError('能量单位错误')
        low, high = self.cfg['limits'][e['type']]
        if not low <= e['value'] <= high:
            raise ValueError('能量超出模拟范围')
        if e['type'] == 'TEMPERATURE':
            low, high = self.cfg['limits']['gap_mm']
            if 'gap_mm' not in p or not low <= p['gap_mm'] <= high:
                raise ValueError('温度方案缺少有效间隙')
        elif 'gap_mm' in p:
            raise ValueError('力方案不接受gap_mm')
        self.geometry.plan(p)

    def vision_message(self, raw):
        m = decode(raw, self.contract['vision_message.schema'])
        now = self.clock()
        d = m.get('data', {})
        if m['type'] == 'REFINE_RESULT':
            self.confirmation.consume(d)
            return
        if m['type'] == 'VISION_STATUS':
            self.health, self.vision_at = d, now
            if not self.vision_valid(now) and not self.preserve_photo_for_preview_delay(now):
                self.invalidate('视觉健康状态失效')
            return
        if m['type'] == 'REPLY':
            if m['cmd'] == 'CANCEL' and self.cancel_at is not None:
                self.cancel_ack = m['ok']
                self.finish_cancel()
            elif m['cmd'] == 'LOCATE' and self.pending and self.cancel_at is None and not m['ok']:
                self.pending = None
                self.state, self.result, self.error = 'IDLE', 'FAILED', m['message']
            return
        if not self.pending:
            return
        if d['region'] != self.pending['region'] or d['view'] != self.pending['view']:
            raise ValueError('定位结果部位或拍摄面不匹配')
        if self.cancel_at is not None:
            self.terminal = True
            self.finish_cancel()
            return
        if self.state != 'SCANNING':
            return
        pending, self.pending = self.pending, None
        self.state = 'IDLE'
        try:
            if d['status'] != 'SUCCEEDED':
                raise ValueError(d['message'] or '定位失败')
            if self.mode == 'REAL' and getattr(self.device, 'scan_enabled', False):
                # Photo recognition is separate from eligibility to execute.
                # Receipt time establishes a new acquisition packet; it is not
                # relabelled as a validated exposure time or motion permission.
                packet = d.get('base_preview')
                if not packet or not self.snapshot_verified(packet):
                    raise ValueError('拍照坐标不是本控制进程确认的采集结果')
                capture = packet['data']
                t = capture['frame_received_monotonic_s']
                if not pending['start'] <= t <= now or now > capture['valid_until_monotonic_s']:
                    raise ValueError('本次照片已过期或来自扫描请求之前')
                if capture.get('capture_time_valid') and capture['capture_window_monotonic_s']['start']<pending['start']:
                    raise ValueError('本次曝光发生在扫描请求之前')
                if capture['error'] or not self.robot_valid(now) or not self.device.observation_ready:
                    raise ValueError(capture['error'] or '拍照时机械臂反馈或停稳条件失效')
                measured = {(p['name'], p['side']): p for p in d['points']}
                points = []
                for p in capture['points']:
                    key = (p['name'], p['side'])
                    raw = measured.get(key)
                    if not self.known(self.region, p) or not raw:
                        raise ValueError('拍照结果包含未知穴位或坐标包不匹配')
                    if raw['camera_point_mm'] != p['camera_point_mm'] or raw['coordinate_valid'] != p['coordinate_valid']:
                        raise ValueError('拍照与坐标转换不是同一组测量')
                    if p['base_coordinate_valid']:
                        points.append(dict(name=p['name'], side=p['side']))
                if not points:
                    raise ValueError('本次拍照未得到有效穴位坐标')
                self.scan_snapshot = dict(region=self.region, frame_number=capture['frame_number'],
                    received_monotonic_s=t, available_points=points, execution_eligible=False,
                    execution_blocker='拍照识别完成；无接触执行路径与采集同步尚未验证')
                self.confirmation.capture(packet)
                self.error = ''
                return
            t = d['capture_host_monotonic_ms']
            if not d['capture_time_valid'] or t is None or not pending['start'] * 1000 <= t <= now * 1000:
                raise ValueError('无法证明图像来自本次采集窗口')
            if not self.robot_valid(now) or self.device.moving or not self.device.observation_ready:
                raise ValueError('采集结束时机器人未停稳')
            seen, valid = set(), []
            for p in d['points']:
                key = (p['name'], p['side'])
                if key in seen or not self.known(self.region, p):
                    raise ValueError('未知或重复穴位/侧别')
                seen.add(key)
                if p['coordinate_valid'] != (p['camera_point_mm'] is not None):
                    raise ValueError('坐标有效性与数据不一致')
                if p['normal_valid'] != (p['normal_camera'] is not None):
                    raise ValueError('法向有效性与数据不一致')
                if p['normal_valid'] and abs(sum(x*x for x in p['normal_camera']) - 1) > 0.02:
                    raise ValueError('法向不是单位向量')
                if p['coordinate_valid']:
                    valid.append({'name':p['name'], 'side':p['side']})
            if not valid:
                raise ValueError('无有效深度点')
            # Geometry is a SIMULATION fixture, never a real hand-eye transform.
            if self.geometry is None:
                raise ValueError('真实定位已回传；尚无经验证的执行几何与无接触路径，不能开放执行')
            self.geometry.locate(d['points'])
            self.points = valid
            self.error = ''
        except ValueError as exc:
            self.points = []
            self.result, self.error = 'FAILED', str(exc)

    def finish_cancel(self):
        if self.cancel_ack and self.terminal:
            self.pending = self.cancel_at = None

    def begin_motion(self, step, now):
        self.step, self.motion_at = step, now
        self.device.output_off(now)
        try:
            self.motion_token = self.device.move(now, step, dict(self.current, target_m=self.geometry.target(self.current, step)))
        except ValueError as exc:
            self.fail(str(exc))

    def tick(self):
        now = self.clock()
        dt, self.last_tick = max(0, now-self.last_tick), now
        if now - self.last_poll >= 0.099:
            self.last_poll = now
            self.device.poll(now, self.plan, self.state == 'RUNNING' and self.step == 'DWELL')
        if self.noncontact and self.noncontact.active:
            self.noncontact.tick(now)
            return
        starting = (self.mode == 'REAL' and not self.device.ever_valid
                    and now-self.device.created_at < self.device.config['startup_timeout_s'])
        if self.device.estop or self.device.fault:
            self.fail('机械臂急停或故障/防护状态；已请求停止，实际停止未知')
        elif not self.robot_valid(now) and not starting:
            self.fail('机器人反馈失效或断联；已请求停止，实际停止未知', feedback_only=True)
        if self.ui_at is not None and now - self.ui_at >= self.cfg['ui_timeout_s']:
            self.ui_lost()
        if self.state == 'SCANNING' and (self.device.moving or not self.device.observation_ready):
            self.cancel_scan(now)
            self.state, self.result, self.error = 'IDLE', 'FAILED', '采集期间观察位条件失效'
        if self.health and not self.vision_valid(now):
            if not self.preserve_photo_for_preview_delay(now):
                self.invalidate('视觉健康状态过期或无效')
            if self.state == 'SCANNING':
                self.cancel_scan(now)
                self.state, self.result, self.error = 'IDLE', 'FAILED', '视觉健康状态失效'
        self.confirmation.tick()
        if self.cancel_at is not None and now-self.cancel_at >= self.cfg['cancel_timeout_s']:
            self.close_vision = True
        elif self.pending and self.cancel_at is None and now-self.pending['start'] >= self.cfg['scan_timeout_s']:
            self.cancel_scan(now)
            self.state, self.result, self.error = 'IDLE', 'FAILED', '扫描超时'
        if self.state == 'STOPPING':
            if self.robot_valid(now) and self.device.stopped_at > self.stop_at and not self.device.moving:
                self.clear_plan()
                self.state, self.result = 'IDLE', self.stop_result
            elif now-self.stop_at >= self.cfg['stop_timeout_s']:
                self.fail('停止确认超时，实际停止未知')
        self.recover_acquisition_feedback(now)
        if self.state != 'RUNNING':
            return
        if self.step == 'DWELL':
            stale = self.cfg['feedback_stale_s']
            energy_ok = now-self.device.energy_at <= stale
            gap_ok = self.plan['energy']['type'] != 'TEMPERATURE' or now-self.device.gap_at <= stale
            if not energy_ok or not gap_ok:
                # Allow first feedback one poll after entering dwell, without counting it.
                if now-self.dwell_at > stale:
                    self.fail('工作能量或间隙反馈失效，已请求停止')
                return
            used = min(dt, self.point_left, max(0, now-self.dwell_at))
            self.point_left -= used
            self.remaining = max(0, self.remaining-used)
            if self.point_left <= 1e-9:
                self.begin_motion('RETRACT', now)
        elif self.device.arrived_token == self.motion_token and self.device.robot_at > self.motion_at and not self.device.moving:
            if self.step == 'APPROACH':
                self.step, self.dwell_at = 'DWELL', now
            elif self.step == 'RETRACT':
                if self.index + 1 == len(self.plan['points']):
                    self.stop(completed=True)
                else:
                    self.index += 1
                    self.current = self.plan['points'][self.index]
                    self.point_left = float(self.plan['time_per_point_s'])
                    self.begin_motion('TRANSFER', now)
            elif self.step == 'TRANSFER':
                self.begin_motion('APPROACH', now)
        elif now-self.motion_at >= self.cfg['motion_timeout_s']:
            self.fail('动作到位反馈超时')

    def status(self):
        now, stale = self.clock(), self.cfg['feedback_stale_s']
        e = self.selected_energy
        valid = bool(e and self.state == 'RUNNING' and self.step == 'DWELL' and now-self.device.energy_at <= stale)
        gap = bool(valid and e['type'] == 'TEMPERATURE' and now-self.device.gap_at <= stale)
        message = {'type':'STATUS','data':dict(mode=self.mode,robot_connected=self.device.connected,
            robot_feedback_valid=self.robot_valid(now),vision_feedback_valid=self.vision_valid(now),
            state=self.state,step=self.step,region=self.region,
            positioning_valid=bool(self.points),available_points=copy.deepcopy(self.points),current_point=copy.deepcopy(self.current),
            remaining_time_s=max(0,math.ceil(self.remaining-1e-9)),actual_energy=dict(type=e['type'] if e else None,
            value=self.device.energy if valid else None,unit=e['unit'] if e else None,valid=valid),
            actual_gap_mm=self.device.gap if gap else None,gap_valid=gap,result=self.result,error=self.error)}
        if self.scan_snapshot is not None:
            message['data']['scan_snapshot'] = copy.deepcopy(self.scan_snapshot)
        if self.confirmation.value is not None:
            message['data']['point_confirmation'] = copy.deepcopy(self.confirmation.value)
        if self.noncontact:
            message['data']['noncontact'] = self.noncontact.status()
        return message
