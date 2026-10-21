"""Measured wrist-camera transform and stationary coordinate previews. No motion.

Frame receipt is NOT exposure time. Missing clock evidence remains preview-only;
stationary capture eligibility and independent calibration approval are separate.
"""
import contextlib
import copy
import hashlib
import json
import math
from pathlib import Path
import sys
import time

from .geometry import rigid, camera_to_base_m
from .sdk_readonly import ReadOnlyReader

REFERENCE = 'virtual_flange_with_joint6_zero'
BOOT_ID = Path('/proc/sys/kernel/random/boot_id').read_text().strip()


class ReferenceReader(ReadOnlyReader):
    # Vendor mathematical queries only. No enable/reset/move/output commands.
    allowed_commands = ReadOnlyReader.allowed_commands | {'ACS2PCS', 'RPY2Quaternion'}

    def sample(self):
        sample = super().sample()
        pose, quat = [], []
        q = sample['joint_positions_deg'][:5] + [0.0]
        with contextlib.redirect_stdout(sys.stderr):
            rc = self.sdk.HRIF_GetForwardKin(0, 0, q, [0.0]*6, [0.0]*6, pose)
            if rc or len(pose) != 6 or not all(math.isfinite(float(x)) for x in pose):
                raise ValueError('腕部参考系正运动学失败')
            pose = list(map(float, pose))
            rc = self.sdk.HRIF_RPY2Quaternion(0, *pose[3:], quat)
        if rc or len(quat) != 4:
            raise ValueError('SDK 四元数转换失败')
        w, x, y, z = map(float, quat)
        norm = math.sqrt(w*w+x*x+y*y+z*z)
        # Vendor replies are rounded decimals; use the same checked
        # normalization as the measured calibration capture adapter.
        if not math.isfinite(norm) or abs(norm-1) > .01:
            raise ValueError('SDK 四元数无效')
        w, x, y, z = (v/norm for v in (w,x,y,z))
        matrix = [[1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w), pose[0]/1000],
                  [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w), pose[1]/1000],
                  [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y), pose[2]/1000],
                  [0.,0.,0.,1.]]
        sample.update(reference_frame=REFERENCE, T_base_reference=rigid(matrix))
        return sample


class BaseProjector:
    def __init__(self, calibration_path):
        raw = Path(calibration_path).read_bytes()
        self.calibration = c = json.loads(raw)
        self.sha256 = hashlib.sha256(raw).hexdigest()
        if (c.get('reference_frame') != REFERENCE or c.get('units') != 'm'
                or c.get('status') != 'solved_and_holdout_evaluated'
                or c.get('suitability', {}).get('coordinate_preview') is not True):
            raise ValueError('标定参考系、单位或预览适用性不匹配')
        rigid(c['T_reference_camera'])

    def project(self, request, history, now):
        d = request['data']
        points = [dict(p, base_point_mm=None, base_coordinate_valid=False) for p in d['points']]
        out = dict(type='BASE_COORDINATES', data=dict(
            frame='robot_base', units='mm', camera_serial=d['camera_serial'],
            frame_number=d['frame_number'], frame_received_monotonic_s=d['frame_received_monotonic_s'],
            host_boot_id=BOOT_ID, generated_monotonic_s=now,
            valid_until_monotonic_s=d['frame_received_monotonic_s']+1.0,
            capture_time_valid=False, motion_eligible=False,
            capture_time_error='相机未提供可核对的全局曝光元数据',
            pairing_method='stationary_host_receipt_preview',
            reference_frame=REFERENCE, calibration_sha256=self.sha256,
            feedback_window=None, points=points, error=''))
        try:
            if d['host_boot_id'] != BOOT_ID:
                raise ValueError('视觉与控制不在同一主机时钟域')
            if d['camera_serial'] != self.calibration['camera_serial']:
                raise ValueError('相机序列号与标定不符')
            t = d['frame_received_monotonic_s']
            if not 0 <= now-t <= 1.0:
                raise ValueError('图像接收时间过期或在未来')
            # A 0.4 s stationary history reduces queued-frame ambiguity, but
            # cannot prove exposure matching; do not promote capture_time_valid.
            window_start,window_end=t,t
            if d.get('exposure_clock'):
                from .capture_timing import exposure_window
                try:
                    if (d['exposure_clock']['camera_serial']!=d['camera_serial']
                            or d['exposure_clock']['color']['frame_number']!=d['frame_number']):
                        raise ValueError('曝光证据不是本次照片或相机')
                    exposure=exposure_window(d['exposure_clock'],t,now,time.time()-time.monotonic())
                    window_start,window_end=exposure['start'],exposure['end']
                    out['data'].update(capture_window_monotonic_s=exposure,
                                       pairing_method='stationary_global_exposure',capture_time_error='',
                                       exposure_clock=copy.deepcopy(d['exposure_clock']))
                except (ValueError,KeyError,TypeError) as exc:
                    out['data']['capture_time_error']=str(exc)
            before = [s for s in history if s['host_monotonic_after_s'] <= window_start-.4]
            after = [s for s in history if s['host_monotonic_before_s'] >= window_end]
            if not before or not after:
                raise ValueError('等待图像前后停稳反馈')
            first, last = before[-1], after[0]
            a, b = first['host_monotonic_before_s'], last['host_monotonic_after_s']
            window = [s for s in history if s['host_monotonic_before_s'] >= a]
            if not window or now-window[-1]['host_monotonic_after_s'] > .5 or t-a > .8:
                raise ValueError('机械臂反馈过期或间隔过大')
            q0 = first['joint_positions_deg']
            previous = a
            for s in window:
                state = s.get('robot_state', {})
                if (not s.get('valid') or not s.get('connected')
                        or s.get('controller_simulation') is not False
                        or not state.get('box_connected') or state.get('moving', 1)
                        or any(state.get(k, 1) for k in ('error','emergency_stop','safety_guard','paused'))
                        or s.get('reference_frame') != REFERENCE):
                    raise ValueError('机械臂未停稳、断联或反馈参考系错误')
                if s['host_monotonic_before_s']-previous > .25:
                    raise ValueError('停稳反馈有缺口')
                previous = s['host_monotonic_after_s']
                q = s['joint_positions_deg']
                if len(q) != 6 or any(not math.isfinite(x) or abs(x-y) > .05 for x,y in zip(q,q0)):
                    raise ValueError('采集附近关节位置变化')
            out['data']['feedback_window'] = dict(before_monotonic_s=a, after_monotonic_s=b,
                joint_positions_deg=last['joint_positions_deg'], T_base_reference=last['T_base_reference'])
            seen = set()
            for p in points:
                key = (p['name'], p['side'])
                if key in seen:
                    raise ValueError('重复穴位/侧别')
                seen.add(key)
                xyz = p['camera_point_mm']
                if not p['coordinate_valid']:
                    continue
                if xyz is None or xyz[2] <= 0:
                    raise ValueError('有效相机坐标必须有正深度')
                p['base_point_mm'] = [v*1000 for v in camera_to_base_m(
                    xyz, last['T_base_reference'], self.calibration['T_reference_camera'])]
                p['base_coordinate_valid'] = True
            out['data']['capture_time_valid'] = out['data']['pairing_method']=='stationary_global_exposure'
            suitability=self.calibration.get('suitability',{})
            out['data']['motion_eligible']=bool(out['data']['capture_time_valid']
                and suitability.get('absolute_base_accuracy_verified') is True
                and suitability.get('robot_motion_approved') is True
                and any(p['base_coordinate_valid'] for p in points))
        except (ValueError, KeyError, TypeError) as exc:
            out['data']['error'] = str(exc)
            out['data']['capture_time_valid']=out['data']['motion_eligible']=False
            for p in points:
                p.update(base_point_mm=None, base_coordinate_valid=False)
        return out
