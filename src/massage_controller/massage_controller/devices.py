"""Nonblocking simulation adapter. No vendor module is loaded by this backend."""
class SimDevices:
    mode = 'SIMULATION'
    motion_enabled = True
    fault = False
    def __init__(self, cfg):
        self.cfg = cfg
        self.connected = True
        self.estop = False
        self.freeze_robot = self.freeze_energy = self.freeze_gap = False
        self.reject_motion = self.suppress_stop = False
        self.observation_ready = True
        self.robot_at = self.energy_at = self.gap_at = float('-inf')
        self.moving = False
        self.due = self.stop_due = None
        self.token = self.arrived_token = 0
        self.stopped_at = float('-inf')
        self.energy = self.gap = None
        self.calls = []

    def poll(self, now, plan, working):
        if self.connected and not self.freeze_robot:
            self.robot_at = now
            if self.stop_due is not None and now >= self.stop_due and not self.suppress_stop:
                self.moving = False
                self.stop_due = None
                self.stopped_at = now
            if self.due is not None and now >= self.due:
                self.moving = False
                self.arrived_token = self.token
                self.due = None
        if plan and working:
            if not self.freeze_energy:
                self.energy_at = now
                # Synthetic measured response; explicitly SIMULATION throughout.
                self.energy = plan['energy']['value'] - 0.1
            if plan['energy']['type'] == 'TEMPERATURE' and not self.freeze_gap:
                self.gap_at = now
                self.gap = plan['gap_mm'] + 0.2

    def move(self, now, step, point):
        self.calls.append((now, 'move', step, point))
        if self.reject_motion:
            raise ValueError('模拟设备拒绝运动')
        self.token += 1
        self.moving = True
        self.due = now + self.cfg['motion_delay_s']
        return self.token

    def stop(self, now):
        self.calls.append((now, 'stop'))
        self.due = None
        self.stop_due = now + self.cfg['stop_delay_s']
        self.energy = self.gap = None
        self.energy_at = self.gap_at = float('-inf')

    def output_off(self, now):
        self.calls.append((now, 'output_off'))
        self.energy = self.gap = None
        self.energy_at = self.gap_at = float('-inf')
