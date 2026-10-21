"""SDK sampling thread and nonblocking state-machine adapter. Read-only commissioning.
The thread never mutates the business state machine and never creates simulated feedback.
"""
import copy
from collections import deque
import math
from pathlib import Path
import threading
import time
from .sdk_readonly import ReadOnlyReader
from .base_coordinates import ReferenceReader

BLOCKER = '实机反馈已接入；运动、输出与停止通路尚未验收，当前禁止业务动作'


def mapped_joints(sample, config):
    names, indices = config['joint_names'], config['source_joint_indices']
    signs, offsets = config['joint_signs'], config['joint_offsets_deg']
    if (len(names) != 6 or len(set(names)) != 6 or sorted(indices) != list(range(6))
            or len(signs) != 6 or any(s not in (-1, 1) for s in signs)
            or len(offsets) != 6 or not all(math.isfinite(o) for o in offsets)):
        raise ValueError('Invalid explicit joint mapping')
    degrees = sample['joint_positions_deg']
    if len(degrees) != 6 or not all(math.isfinite(x) for x in degrees):
        raise ValueError('Invalid real joint feedback')
    return list(names), [math.radians(signs[i]*degrees[indices[i]]+offsets[i]) for i in range(6)]


class FeedbackWorker:
    def __init__(self, config, sdk_path, reader_factory=ReferenceReader):
        self.config, self.sdk_path, self.reader_factory = config, sdk_path, reader_factory
        self._lock = threading.Lock()
        self._snapshot = None
        self._history = deque(maxlen=80)
        self._stop = threading.Event()
        self.thread = None
        self.audit = []

    def snapshot(self):
        with self._lock:
            return copy.deepcopy(self._snapshot)

    def history(self):
        with self._lock:
            return copy.deepcopy(list(self._history))

    def _store(self, value):
        with self._lock:
            self._snapshot = value
            if value.get('valid'):
                self._history.append(value)
            else:
                self._history.clear()

    def start(self):
        if self.thread is not None:
            raise RuntimeError('SDK worker already started')
        self.thread = threading.Thread(target=self._run, name='huayan-feedback', daemon=True)
        self.thread.start()

    def _run(self):
        while not self._stop.is_set():
            reader = None
            try:
                reader = self.reader_factory(self.sdk_path, self.config['host'], self.config['port'],
                                             self.config['read_timeout_s'])
                reader.connect()
                while not self._stop.is_set():
                    began = time.monotonic()
                    sample = reader.sample()
                    if sample.get('controller_simulation') is not False:
                        raise ValueError('真实后端拒绝控制器仿真数据')
                    mapped_joints(sample, self.config)
                    sample['received_unix_ns'] = time.time_ns()
                    self._store(sample)
                    self.process_work(reader)
                    self.wait_for_next_poll(reader, began+self.config['poll_interval_s'])
            except Exception as exc:
                self._store(dict(valid=False, connected=False, mode='REAL_READ_ONLY',
                                 host_monotonic_after_s=time.monotonic(), error=str(exc),
                                 joint_positions_deg=None, tcp_pose=None))
            finally:
                if reader:
                    try:
                        reader.close()
                    except Exception as exc:
                        self._store(dict(valid=False, connected=False, mode='REAL_READ_ONLY',
                                         host_monotonic_after_s=time.monotonic(), error=f'SDK close: {exc}'))
                    finally:
                        self.audit = list(reader.audit)
            if not self._stop.is_set():
                self._stop.wait(self.config['reconnect_interval_s'])

    def process_work(self, reader):
        """Read-only worker has no queued operations."""
        pass

    def wait_for_next_poll(self, reader, deadline):
        """Leave the sampling cadence unchanged for the read-only worker."""
        self._stop.wait(max(0, deadline-time.monotonic()))

    def close(self):
        self._stop.set()
        if self.thread:
            self.thread.join(timeout=3)
        # A stalled SDK call is isolated from the business loop; daemon shutdown
        # cannot hold the process indefinitely. No retry starts after _stop.


class RealDevices:
    mode = 'REAL'
    motion_enabled = False

    def __init__(self, worker, config, scan_enabled=False):
        self.worker, self.config = worker, config
        self.scan_enabled = scan_enabled
        self.created_at = time.monotonic()
        self.ever_valid = False
        self.connected = self.estop = self.fault = self.moving = False
        self.observation_ready = False
        self.robot_at = self.energy_at = self.gap_at = float('-inf')
        self.energy = self.gap = None
        self.arrived_token = None
        self.stopped_at = float('-inf')
        self.sample = None
        self.error = '等待首帧实机反馈'
        self.calls = []

    def poll(self, now, plan, working):
        self.sample = self.worker.snapshot()
        self.observation_ready = False
        sample = self.sample
        self.connected = bool(sample and sample.get('connected'))
        self.error = (sample or {}).get('error', '等待首帧实机反馈')
        if sample and sample.get('valid'):
            self.robot_at = sample['host_monotonic_after_s']
            state = sample['robot_state']
            self.connected = bool(state['box_connected'])
            self.moving = bool(state['moving'])
            self.estop = bool(state['emergency_stop'])
            self.fault = bool(state['error'] or state['safety_guard'])
            self.error = f"机械臂故障/防护状态：{state['error_code']}" if self.fault else ''
            if now-self.robot_at <= self.config['feedback_stale_s'] and self.connected:
                self.ever_valid = True
                # Explicit acquisition-only commissioning: scanning does not move.
                # This is a stationary acquisition condition, NOT path approval.
                self.observation_ready = bool(self.scan_enabled and not self.moving
                    and not self.estop and not self.fault and not state.get('paused', 1)
                    and state.get('in_position', 0) and state.get('blending_done', 0))
        else:
            self.robot_at = float('-inf')
        # Energy, distance, observation poses and stopped-after-command feedback
        # have not been commissioned. Never infer them from connection success.

    def telemetry(self, now):
        value = copy.deepcopy(self.sample or {})
        fresh = bool(value.get('valid') and self.connected and 0 <= now-self.robot_at <= self.config['feedback_stale_s'])
        age = max(0, (now-self.robot_at)*1000) if math.isfinite(self.robot_at) else None
        value.update(type='ROBOT_TELEMETRY', mode='REAL_READ_ONLY', valid=fresh, connected=self.connected,
                     feedback_age_ms=age, feedback_max_age_ms=self.config['feedback_stale_s']*1000,
                     motion_enabled=False, motion_blocker=BLOCKER,
                     robot_model=self.config['robot_model'], model_confirmed=self.config['model_confirmed'],
                     joint_mapping_verified=self.config['joint_mapping_verified'])
        if fresh:
            value['joint_names'], value['visualization_joint_positions_rad'] = mapped_joints(value, self.config)
        else:
            value.update(joint_positions_deg=None, tcp_pose=None, visualization_joint_positions_rad=None,
                         error=self.error or '实机反馈过期')
        return value

    def move(self, *args):
        raise ValueError(BLOCKER)

    def stop(self, now):
        self.calls.append((now, 'stop_unavailable'))
        return False

    def output_off(self, now):
        self.calls.append((now, 'output_unavailable'))
        return False
