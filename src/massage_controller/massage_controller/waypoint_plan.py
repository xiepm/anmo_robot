"""Strict, offline-only validation for manually taught free-space waypoints."""
import copy
import hashlib
import json
import math
from pathlib import Path


POSE_KEYS = ('x_mm', 'y_mm', 'z_mm', 'rx_deg', 'ry_deg', 'rz_deg')


def fields(obj, keys, where):
    if not isinstance(obj, dict) or set(obj) != set(keys):
        raise ValueError(f'{where}: 字段必须是 {", ".join(keys)}')


def number(value, name, low=None, high=None):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f'{name}: 请填写有限数字，不能保留 null')
    if (low is not None and value < low) or (high is not None and value > high):
        raise ValueError(f'{name}: 必须在 {low}..{high} 范围内')
    return value


def vector(values, size, name):
    if not isinstance(values, list) or len(values) != size:
        raise ValueError(f'{name}: 必须填写 {size} 个数字')
    return [number(x, name) for x in values]


def pose(obj, name):
    fields(obj, POSE_KEYS, name)
    values = [number(obj[k], f'{name}.{k}') for k in POSE_KEYS]
    for v in values[3:]:
        number(v, f'{name}: 姿态角', -180, 180)
    return values


def angle_error(a, b):
    # Conservative component check in vendor Euler coordinates. This does not
    # claim a rotation-geodesic tolerance or resolve Euler singularities.
    return max(abs((x-y+180) % 360-180) for x, y in zip(a, b))


def near(actual, target, xyz_mm=1., angle_deg=.5):
    return math.dist(actual[:3], target[:3]) <= xyz_mm and angle_error(actual[3:], target[3:]) <= angle_deg


def check_segment(a, b, plan):
    if math.dist(a[:3], b[:3]) > plan['max_segment_mm']:
        raise ValueError('移动段超过 max_segment_mm（含当前位置到首点）')
    if angle_error(a[3:], b[3:]) > plan['max_rotation_deg']:
        raise ValueError('移动段姿态分量变化超过 max_rotation_deg')


def in_workspace(pose_values, plan):
    box = plan['workspace_mm']
    return all(lo <= x <= hi for x, lo, hi in zip(pose_values[:3], box['min'], box['max']))


def command_tcp(plan):
    """Taught flange coordinates must never silently become tool-tip targets."""
    return [0.]*6 if plan.get('target_reference', 'TCP') == 'FLANGE' else plan['tool_pose_mm_deg']


def split_segment(previous, point, plan):
    target = pose(point['pose'], point['name'])
    distance = math.dist(previous[:3], target[:3])
    count = max(1, math.ceil(distance/plan['max_segment_mm']))
    if count > 1 and not plan.get('auto_subdivide', False):
        raise ValueError('移动段超过 max_segment_mm（含当前位置到首点）')
    if count > 1000:
        raise ValueError('自动插点超过 1000 段，请重新规划')
    if count > 1 and angle_error(previous[3:], target[3:]) > 1e-8:
        raise ValueError('长段同时改变姿态：暂不自动插值姿态，请补充已确认的中间位姿')
    if angle_error(previous[3:], target[3:]) > plan['max_rotation_deg']:
        raise ValueError('移动段姿态分量变化超过 max_rotation_deg')
    # A blended straight group is continuous, so its timeout covers the whole
    # original segment, not just one generated subsegment.
    if distance/plan['speed'] >= plan['move_timeout_s']:
        raise ValueError(f'{point["name"]}: 即使忽略加减速也无法在 move_timeout_s 内到达')
    result = []
    for i in range(1, count):
        value = [a+(b-a)*i/count for a,b in zip(previous[:3],target[:3])] + target[3:]
        result.append(dict(name=f'{point["name"]}/auto{i}/{count}',
                           pose=dict(zip(POSE_KEYS,value)), dwell_s=0, generated=True))
    result.append(dict(copy.deepcopy(point), generated=False))
    return result


def compile_groups(plan):
    previous = pose(plan['start_pose'], 'start_pose')
    groups = []
    for point in plan['points']:
        groups.append(split_segment(previous, point, plan))
        previous = pose(point['pose'], point['name'])
    if sum(map(len, groups)) > 1000:
        raise ValueError('整份方案超过 1000 个执行点')
    return groups


def validate(raw):
    if not isinstance(raw, dict):
        raise ValueError('plan 必须为 JSON 对象')
    plan = copy.deepcopy(raw)
    optional = ('auto_subdivide', 'target_reference', 'return_from_pose', 'return_to_start')
    fields({k:v for k,v in plan.items() if k not in optional}, ('version', 'mode', 'robot_model', 'frame', 'path_confirmed',
                  'tool_pose_mm_deg', 'start_pose', 'speed', 'acceleration',
                  'max_segment_mm', 'max_rotation_deg', 'workspace_mm',
                  'move_timeout_s', 'points'), 'plan')
    if 'auto_subdivide' in plan and type(plan['auto_subdivide']) is not bool:
        raise ValueError('auto_subdivide 必须为布尔值')
    if 'return_to_start' in plan and type(plan['return_to_start']) is not bool:
        raise ValueError('return_to_start 必须为布尔值')
    if plan.get('target_reference', 'TCP') not in ('TCP', 'FLANGE'):
        raise ValueError('target_reference 必须为 TCP 或 FLANGE')
    if (type(plan['version']) is not int or plan['version'] != 1 or
            plan['mode'] != 'FREE_SPACE_WAYPOINTS' or plan['robot_model'] != 'elfin5_l' or plan['frame'] != 'Base'):
        raise ValueError('仅支持 version=1 / FREE_SPACE_WAYPOINTS / elfin5_l / Base')
    if plan['path_confirmed'] is not True:
        raise ValueError('请填写并现场确认起点到首点及各点之间的完整空载路径，再设 path_confirmed=true')
    vector(plan['tool_pose_mm_deg'], 6, 'tool_pose_mm_deg')
    for angle in plan['tool_pose_mm_deg'][3:]:
        number(angle, '工具姿态', -180, 180)
    # Software ceilings for initial commissioning, not a certified safety envelope.
    number(plan['speed'], 'speed (mm/s; rotation deg/s)', .1, 20)
    number(plan['acceleration'], 'acceleration (mm/s²; rotation deg/s²)', .1, 40)
    if plan['acceleration'] <= plan['speed']:
        raise ValueError('本 SDK 文档要求 acceleration > speed')
    number(plan['max_segment_mm'], 'max_segment_mm', .1, 500)
    number(plan['max_rotation_deg'], 'max_rotation_deg', .1, 30)
    number(plan['move_timeout_s'], 'move_timeout_s', 1, 300)
    fields(plan['workspace_mm'], ('min', 'max'), 'workspace_mm')
    low = vector(plan['workspace_mm']['min'], 3, 'workspace_mm.min')
    high = vector(plan['workspace_mm']['max'], 3, 'workspace_mm.max')
    if any(a >= b for a, b in zip(low, high)):
        raise ValueError('workspace_mm 每一轴 min 必须小于 max')
    previous = pose(plan['start_pose'], 'start_pose')
    if not in_workspace(previous, plan):
        raise ValueError('start_pose 不在填写的工作范围内')
    if 'return_from_pose' in plan:
        entry = pose(plan['return_from_pose'], 'return_from_pose')
        if not in_workspace(entry, plan):
            raise ValueError('return_from_pose 不在工作范围内')
        split_segment(entry, dict(name='RETURN_TO_START', pose=plan['start_pose'], dwell_s=0), plan)
    if not isinstance(plan['points'], list) or not 1 <= len(plan['points']) <= 100:
        raise ValueError('points 必须包含 1..100 个点')
    names = set()
    for i, p in enumerate(plan['points']):
        fields(p, ('name', 'pose', 'dwell_s'), f'points[{i}]')
        if not isinstance(p['name'], str) or not p['name'].strip() or len(p['name']) > 64 or p['name'] in names:
            raise ValueError('点名称须为非空、唯一且不超过 64 字符')
        names.add(p['name'])
        current = pose(p['pose'], p['name'])
        number(p['dwell_s'], f'{p["name"]}.dwell_s', 0, 60)
        if not in_workspace(current, plan):
            raise ValueError(f'{p["name"]} 不在填写的工作范围内')
        previous = current
    if plan.get('return_to_start', False):
        final = plan['points'][-1]
        if (final['name'] != 'RETURN_TO_START' or final['dwell_s'] != 0 or
                not near(pose(final['pose'], 'return_to_start'), pose(plan['start_pose'], 'start_pose'), 1e-6, 1e-6)):
            raise ValueError('回程末点须为本次 start_pose，且不重复停留')
    compile_groups(plan)
    return plan


def load(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f'JSON 重复字段: {key}')
            result[key] = value
        return result
    return validate(json.loads(Path(path).read_text(), object_pairs_hook=unique))


def digest(plan):
    return hashlib.sha256(json.dumps(plan, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
