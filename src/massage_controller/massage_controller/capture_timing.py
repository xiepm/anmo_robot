"""Check RealSense global clock + exposure metadata against this host clock.

Global timestamps are frame timestamps, not a shared hardware trigger. RGB UVC
exposure uses 100 us units on this device; depth uses us. Bracket RGB's reported
and unit-corrected centers with rolling-readout allowance, then require stationary
robot feedback throughout. The 50 ms clock margin is not certified clock accuracy.
Only the explicitly probed camera/firmware/SDK policy is accepted.
"""
import math
from .capture_policy import load_capture_policy, policy_identity


def exposure_window(proof, received, now, host_wall_minus_monotonic):
    def number(x):
        if type(x) not in (int,float) or not math.isfinite(x):raise ValueError('采集时间含无效数字')
        return x
    policy = load_capture_policy()
    if (proof.get('model')!=policy['model']
            or any(proof.get(key)!=value for key,value in policy_identity(policy).items())):
        raise ValueError('未经核对的相机曝光单位或时间模型')
    mono=number(proof['host_monotonic_s']);wall=number(proof['host_unix_s'])
    width=number(proof['clock_read_width_s'])
    if not 0<=width<=.005 or abs(mono-received)>.02:
        raise ValueError('相机与主机时钟采样不对应')
    offset=wall-mono
    if abs(offset-host_wall_minus_monotonic)>.005:
        raise ValueError('主机墙上时钟发生跳变，拒绝曝光配对')
    windows=[]
    centers=[]
    for key in ('color','depth'):
        frame=proof[key]
        if frame['domain']!='global_time':raise ValueError('相机尚未使用已映射的全局时钟')
        global_s=number(frame['global_timestamp_ms'])/1000
        readout=number(frame['frame_timestamp_us'])
        center=number(frame['sensor_timestamp_us'])
        exposure=number(frame['actual_exposure_us'])/1e6
        unit=100 if key=='color' else 1
        raw=number(frame['exposure_raw'])
        if frame['exposure_unit_us']!=unit or abs(exposure*1e6-raw*unit)>.001:
            raise ValueError('RGB/深度曝光单位或换算不一致')
        if key=='color' and (frame['auto_exposure']!=0 or not 1<=raw<=300):
            raise ValueError('RGB 不是已验证的手动曝光范围')
        delta=(readout-center)/1e6
        if not 0<=delta<=.1 or not 0<exposure<=.1:
            raise ValueError('曝光元数据差值无效或跨越设备时钟回绕')
        mid=global_s-offset-delta
        if not 0<=received-mid<=.25 or not 0<=now-mid<=1:
            raise ValueError('曝光过期或位于未来')
        if key=='color':
            # This firmware reports an RGB center offset in unscaled UVC units.
            # Do not silently call that a calibrated exposure center. Bound both
            # interpretations and add one 30-fps frame for rolling readout.
            corrected=global_s-offset-exposure/2
            centers.append(corrected)
            windows.append((min(mid,corrected)-exposure/2-1/30-.05,
                            max(mid,corrected)+exposure/2+1/30+.05))
        else:
            centers.append(mid)
            windows.append((mid-exposure/2-.05,mid+exposure/2+.05))
    if abs(centers[0]-centers[1])>1/30:
        raise ValueError('彩色与深度曝光时间差超过一帧')
    return dict(start=min(w[0] for w in windows),end=max(w[1] for w in windows))
