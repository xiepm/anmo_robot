"""One captured route authorized for an operator-observed fixture air trial.

This records an operational decision, not calibration or collision certification.
The original SDK report and calibration suitability flags remain unchanged.
"""
import json
import math
import time
from pathlib import Path

from .noncontact_plan import fingerprint, profile_errors
from .route_coverage import unknown_clearance
from .route_visibility import validated_visibility


def authorization_path(profile_path):
    return Path(profile_path).with_suffix('.authorization.json')


def binding(bundle):
    d, i = bundle['details'], bundle['inputs']
    result = dict(revision=i['confirmation']['revision'],
        frame_number=i['confirmation']['frame_number'],
        calibration_sha256=i['capture']['calibration_sha256'],
        capture_sha256=fingerprint(i['capture']),
        points_sha256=fingerprint(i['confirmation']['points']),
        candidate_sha256=fingerprint(d['candidate']),
        profile_sha256=fingerprint(d['profile']),
        scene_sha256=fingerprint(i['scene']),
        input_profile_sha256=fingerprint(i['profile']))
    if d['profile'].get('unknown_check_mode')=='FROZEN_3D_VISIBILITY':
        result['depth_visibility_sha256']=fingerprint(d.get('depth_visibility'))
    return result


def make_authorization(bundle, statement, now=None):
    now = time.monotonic() if now is None else now
    approval = dict(kind='SINGLE_CAPTURE_FIXTURE_AIR_TRIAL', binding=binding(bundle),
        operator_observed_no_obstacles=True, reuse_existing_calibration=True,
        operator_statement=statement, issued_monotonic_s=now,
        expires_monotonic_s=bundle['inputs']['capture']['frame_received_monotonic_s']+300)
    return validate_authorization(approval,bundle,now)


def validate_authorization(approval, bundle, now=None):
    now = time.monotonic() if now is None else now
    if not isinstance(approval, dict) or approval.get('kind') != 'SINGLE_CAPTURE_FIXTURE_AIR_TRIAL':
        raise ValueError('缺少本次假人无接触路线的现场授权')
    if approval.get('binding') != binding(bundle):
        raise ValueError('现场授权与本次照片、点位或路线不一致')
    if (approval.get('operator_observed_no_obstacles') is not True
            or approval.get('reuse_existing_calibration') is not True
            or not isinstance(approval.get('operator_statement'), str)
            or not approval['operator_statement'].strip()):
        raise ValueError('缺少本次现场通行确认或沿用标定的记录')
    capture, profile = bundle['inputs']['capture'], bundle['details']['profile']
    times = [approval.get('issued_monotonic_s'), approval.get('expires_monotonic_s'),
             capture.get('frame_received_monotonic_s')]
    if any(type(t) not in (int, float) or not math.isfinite(t) for t in times):
        raise ValueError('现场授权时间无效')
    issued, expires, captured = times
    if not captured <= issued <= now < expires <= captured + 300:
        raise ValueError('本次现场授权已过期，请重新拍照确认路线')
    if capture.get('capture_time_valid') is not True:
        raise ValueError('本次照片采集同步未通过')
    if (not bundle['summary']['kinematics_checked']
            or bundle['summary']['revision'] != bundle['inputs']['confirmation']['revision']):
        raise ValueError('本次 SDK 路线尚未通过')
    errors = profile_errors(profile, require_path_confirmation=False)
    if errors: raise ValueError('；'.join(errors))
    if (profile.get('geometry_source') != 'FROZEN_DEPTH'
            or profile['execution_rxyz_deg'] != 'KEEP_CURRENT'
            or profile['speed_mm_s'] > 10 or profile['acceleration_mm_s2'] > 20
            or profile['extra_clearance_mm'] < 10):
        raise ValueError('现场授权仅适用于固定姿态、10 mm/s 以内的本次无接触路线')
    if profile.get('temporary_speed_override') not in (None,1.0):
        raise ValueError('临时速度倍率配置无效')
    rotation = bundle['details']['tool_rotation']
    offset = [sum(rotation[i][j]*profile['physical_tool_offset_flange_mm'][j] for j in range(3)) for i in range(3)]
    coverage = unknown_clearance(bundle['details']['candidate'], bundle['inputs']['scene'], offset)
    if coverage['assessment'] == 'CENTERLINE_NEAR_UNKNOWN':
        if profile.get('unknown_check_mode')=='FROZEN_3D_VISIBILITY':
            if validated_visibility(bundle): return approval
            evidence=bundle['details'].get('depth_visibility',{})
            raise ValueError('路线三维深度核对尚未通过：'+evidence.get('reason','缺少与本次最终路线一致的原生深度依据'))
        raise ValueError(f"路线平面投影与 {coverage['possibly_intersecting_cells']} 个深度未测清区域重叠（不代表检测到障碍物），请补充观测后重新确认")
    if coverage['assessment'] == 'INCOMPLETE':
        raise ValueError('未知区域记录不完整，无法核对工具尖端路线，请补充拍摄后重新确认')
    if coverage['assessment'] not in ('UNKNOWN_OFF_CENTERLINE', 'NO_UNKNOWN_REPORTED'):
        raise ValueError('工具尖端路线的未知深度检查未通过，请补充拍摄后重新确认')
    return approval


def read_authorization(profile_path, bundle, now=None):
    path = authorization_path(profile_path)
    if not path.exists(): return None
    approval = json.loads(path.read_text(encoding='utf-8-sig'))
    expected = binding(bundle)
    saved = approval.get('binding') if isinstance(approval, dict) else None
    # A sidecar for a previous capture is not authorization for this route.
    # Keep it untouched; only an explicit operator action can replace it.
    if (isinstance(approval, dict) and approval.get('kind') == 'SINGLE_CAPTURE_FIXTURE_AIR_TRIAL'
            and isinstance(saved, dict) and saved != expected):
        return None
    # A matching but expired/invalid approval must still fail explicitly.
    return validate_authorization(approval, bundle, now)
