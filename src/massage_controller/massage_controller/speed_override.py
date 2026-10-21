"""Temporary global speed ratio, restored only after confirmed physical STOP."""
import math


class TemporaryOverride:
    def __init__(self, reader, record, target):
        if type(target) not in (int,float) or target != 1.0:
            raise ValueError('本次速度倍率只支持显式的 100% 设置')
        self.reader, self.record, self.target = reader, record, target
        self.original = None
        self.restore_required = False

    def read(self):
        value = self.reader.read_override()
        if type(value) not in (int,float) or not math.isfinite(value) or not .01 <= value <= 1:
            raise ValueError('控制器速度倍率读取无效')
        return value

    def apply(self):
        self.original = self.read()
        self.record(dict(type='SPEED_OVERRIDE_BEFORE',original=self.original,target=self.target))
        if abs(self.original-self.target)>1e-6:
            # An uncertain acknowledgment can still have changed global state.
            self.restore_required = True
            self.reader.set_override(self.target)
        actual = self.read()
        if abs(actual-self.target)>1e-6:
            raise RuntimeError('速度倍率设置后的实读不匹配，不启动运动')
        self.record(dict(type='SPEED_OVERRIDE_APPLIED',original=self.original,actual=actual))

    def restore(self):
        if self.original is None: return
        if self.restore_required:
            self.reader.set_override(self.original)
        actual=self.read()
        if abs(actual-self.original)>1e-6:
            raise RuntimeError(f'原速度倍率 {self.original} 尚未恢复，当前实读 {actual}')
        self.restore_required=False
        self.record(dict(type='SPEED_OVERRIDE_RESTORED',original=self.original,actual=actual))
