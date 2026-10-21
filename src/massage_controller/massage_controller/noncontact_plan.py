"""Compile confirmed surface points into a bounded, constant-attitude air route.

This is not a collision detector. The site profile must describe an independently
checked free corridor, including tool/camera/link clearance. Unapproved camera
coordinates cannot be promoted to motion by this module.
"""
import copy
import hashlib
import json
import math
from pathlib import Path
from .geometry import rigid
from .waypoint_plan import validate, pose, POSE_KEYS, near, angle_error

LEGACY_ROUTE = 'LIFT_MOVE_APPROACH'
DIRECT_ROUTE = 'DIRECT_ABOVE_POINTS'
SEGMENTED_ROUTE = 'CURRENT_HEIGHT_THEN_ABOVE_POINTS'


def load_profile(path):
    return json.loads(Path(path).read_text())


def profile_errors(p, pending_scene=False, require_path_confirmation=True):
    errors = []
    def finite(x): return type(x) in (int,float) and math.isfinite(x)
    if p.get('scope') != 'mannequin_or_fixture_noncontact_only':
        errors.append('仅允许假人或工装无接触联调')
    if p.get('route_mode', LEGACY_ROUTE) not in (LEGACY_ROUTE, DIRECT_ROUTE, SEGMENTED_ROUTE):
        errors.append('未知无接触路线生成方式')
    if p.get('unknown_check_mode','XY_PROJECTION') not in ('XY_PROJECTION','FROZEN_3D_VISIBILITY'):
        errors.append('未知深度核对方式无效')
    if 'return_to_start' in p and type(p['return_to_start']) is not bool:
        errors.append('return_to_start 必须为布尔值')
    if p.get('physical_tool_offset_flange_mm') != [0,0,20]:
        errors.append('本轮工具须为法兰 +Z 伸出 20 mm')
    if not finite(p.get('minimum_surface_clearance_mm')) or p['minimum_surface_clearance_mm'] < 20:
        errors.append('最小离表面距离须至少 20 mm')
    box = p.get('workspace_base_mm')
    if not pending_scene and (not isinstance(box,dict) or set(box) != {'min','max'}
        or any(not isinstance(box[k],list) or len(box[k])!=3 or not all(map(finite,box[k])) for k in ('min','max'))
        or any(a>=b for a,b in zip(box['min'],box['max']))):
        errors.append('缺少现场确认的 Base 法兰 XYZ 可通行范围')
    angles = p.get('execution_rxyz_deg')
    if angles != 'KEEP_CURRENT' and (not isinstance(angles,list) or len(angles)!=3 or not all(finite(x) and -180<=x<=180 for x in angles)):
        errors.append('缺少固定执行姿态 Rx/Ry/Rz')
    for key,label in [('safe_transfer_height_base_mm','缺少安全过渡高度（工具尖端 Base Z）'),
                      ('surface_ceiling_base_mm','缺少工装及路径障碍物最高表面 Base Z')]:
        if not pending_scene and not finite(p.get(key)): errors.append(label)
    if require_path_confirmation and p.get('path_confirmed') is not True: errors.append('当前观察位至整条路线尚未现场确认无障碍')
    for key,low,high in [('speed_mm_s',.1,10),('acceleration_mm_s2',.1,20),
                         ('dwell_per_point_s',0,10),('extra_clearance_mm',5,50)]:
        if not finite(p.get(key)) or not low<=p[key]<=high: errors.append(f'{key} 必须在 {low}..{high} 内')
    if finite(p.get('speed_mm_s')) and finite(p.get('acceleration_mm_s2')) and p['acceleration_mm_s2']<=p['speed_mm_s']:
        errors.append('加速度参数必须大于速度参数')
    return errors


def eligibility_errors(profile, calibration, capture, pending_scene=False, *, limited_authorization=False):
    # limited_authorization is supplied only after validating the exact, expiring
    # fixture-route record. It never changes calibration suitability facts.
    errors = profile_errors(profile, pending_scene=pending_scene,
                            require_path_confirmation=not limited_authorization)
    suitability = calibration.get('suitability',{})
    if not limited_authorization and suitability.get('absolute_base_accuracy_verified') is not True:
        errors.append('相机到 Base 的独立绝对坐标精度尚未验证')
    if not limited_authorization and suitability.get('robot_motion_approved') is not True:
        errors.append('当前标定未通过运动使用验收')
    if not capture or capture.get('capture_time_valid') is not True:
        errors.append('当前照片尚无可用于运动的采集同步依据')
    elif (suitability.get('absolute_base_accuracy_verified') is True and suitability.get('robot_motion_approved') is True
          and capture.get('motion_eligible') is not True):
        errors.append('当前坐标未通过运动完整性校验')
    return errors


def automatic_profile(profile, scene, final_points, context):
    """Generate numeric route bounds, never certify them as collision-free space."""
    if not scene or scene.get('surface_ceiling_base_mm') is None:
        raise ValueError('本次路线没有足够的可见深度，尚不能生成几何参数')
    if (not context or scene.get('context_sha256') != fingerprint(context)
            or scene.get('revision') != context['revision']):
        raise ValueError('自动场景的照片或坐标版本已失效')
    start = context['start_flange_mm']
    vertices = [start]+[q['base_point_mm'] for q in final_points]
    if not final_points:
        raise ValueError('请先确认最终穴位坐标')
    p = copy.deepcopy(profile)
    ceiling = scene['surface_ceiling_base_mm']
    gap = p['minimum_surface_clearance_mm']+p['extra_clearance_mm']
    tool_length = math.sqrt(sum(x*x for x in p['physical_tool_offset_flange_mm']))
    # Older saved/manual profiles keep their original geometry. Simplified
    # routes use the observed clearance plane; their start is the actual SDK
    # pose, not a fabricated flange-Z-plus-tool-length lift.
    simplified = p.get('route_mode', LEGACY_ROUTE) != LEGACY_ROUTE
    travel = ceiling+gap if simplified else max(ceiling+gap+10, start[2]+tool_length)
    radius = context['corridor_radius_mm']
    p.update(surface_ceiling_base_mm=ceiling, safe_transfer_height_base_mm=travel,
        workspace_base_mm=dict(min=[min(v[0] for v in vertices)-radius,
                                    min(v[1] for v in vertices)-radius,
                                    min(start[2],ceiling+gap)-tool_length-10],
                               max=[max(v[0] for v in vertices)+radius,
                                    max(v[1] for v in vertices)+radius,max(start[2],travel)+tool_length+10]))
    return p


def compile_route(profile, final_points, actual, rotation, *, review_only=False):
    """Pure geometry only; caller must also check eligibility before SDK execution.
    actual is the controller's zero-TCP/Base flange pose. rotation is obtained
    from the vendor's quaternion query for that exact Rx/Ry/Rz, not guessed RPY.
    """
    errors = profile_errors(profile, require_path_confirmation=not review_only)
    if errors: raise ValueError('；'.join(errors))
    p = copy.deepcopy(profile)
    start = pose(dict(zip(POSE_KEYS,actual)), 'actual')
    if p['execution_rxyz_deg'] != 'KEEP_CURRENT' and angle_error(start[3:],p['execution_rxyz_deg']) > .5:
        raise ValueError('当前姿态不等于确认姿态；须先现场示教到位再重新拍照，不自动旋转工具')
    rigid(rotation)
    offset = [sum(rotation[i][j]*p['physical_tool_offset_flange_mm'][j] for j in range(3)) for i in range(3)]
    if rotation[2][2] > -.9:
        raise ValueError('当前法兰 +Z 未朝向下方；拒绝套用背部上方无接触路线')
    if not isinstance(final_points,list) or not 1<=len(final_points)<=30:
        raise ValueError('最终点集须包含 1..30 个点')
    gap = p['minimum_surface_clearance_mm'] + p['extra_clearance_mm']
    ceiling, travel = p['surface_ceiling_base_mm'], p['safe_transfer_height_base_mm']
    if travel < ceiling + gap:
        raise ValueError('过渡高度不足以覆盖最高表面、最小距离及余量')
    start_tip = [a+b for a,b in zip(start[:3],offset)]
    if start_tip[2] < ceiling+gap:
        raise ValueError('当前位置低于已确认的无接触区域；不能自动穿越未知区域抬升')
    points, targets, seen = [], [], set()
    def add(name, tip, dwell=0):
        xyz = [a-b for a,b in zip(tip,offset)]
        points.append(dict(name=name,pose=dict(zip(POSE_KEYS,xyz+start[3:])),dwell_s=dwell))
    # Hold orientation throughout. All endpoints are above the observed
    # ceiling+gap, so their straight interpolants also remain above that plane.
    # Unknown space is still evaluated separately; this is not free-space proof.
    mode = p.get('route_mode', LEGACY_ROUTE)
    if mode == LEGACY_ROUTE:
        add('ENTRY_LIFT', [*start_tip[:2],travel])
    for i,q in enumerate(final_points,1):
        key = (q['name'],q['side'])
        if key in seen: raise ValueError('最终路线包含重复点位')
        seen.add(key)
        xyz = q['base_point_mm']
        if len(xyz)!=3 or any(type(v) not in (int,float) or not math.isfinite(v) for v in xyz):
            raise ValueError('最终 Base 坐标无效')
        if xyz[2]>ceiling: raise ValueError('识别表面高于已确认的场景上界，请重新核对现场')
        tip = [xyz[0],xyz[1],max(xyz[2]+gap,ceiling+gap)]
        if mode == LEGACY_ROUTE:
            add(f'{i}_TRANSFER', [*tip[:2],travel])
            add(f'{i}_APPROACH', tip, p['dwell_per_point_s'])
            add(f'{i}_RETRACT', [*tip[:2],travel])
        else:
            if i == 1 and mode == SEGMENTED_ROUTE:
                # Reach the first point's XY at the actual current tip height,
                # then descend there. Never descend first at the start's XY.
                add('1_TRANSFER', [*tip[:2],start_tip[2]])
            add(f'{i}_APPROACH', tip, p['dwell_per_point_s'])
        targets.append(dict(name=q['name'],side=q['side'],surface_base_mm=xyz,
                            tool_tip_base_mm=tip,flange_base_mm=[a-b for a,b in zip(tip,offset)]))
    plan = dict(version=1,mode='FREE_SPACE_WAYPOINTS',robot_model='elfin5_l',frame='Base',path_confirmed=True,
        target_reference='FLANGE',tool_pose_mm_deg=p['physical_tool_offset_flange_mm']+[0,0,0],
        start_pose=dict(zip(POSE_KEYS,start)),speed=p['speed_mm_s'],acceleration=p['acceleration_mm_s2'],
        max_segment_mm=500,max_rotation_deg=.5,workspace_mm=p['workspace_base_mm'],move_timeout_s=180,
        auto_subdivide=True,points=points)
    # Remove exact zero-length stages; some controllers clear their ID before
    # it can be observed for a no-op command. Preserve nonzero dwell explicitly.
    prev, kept = start, []
    for q in points:
        target = pose(q['pose'], q['name'])
        if near(prev,target,1e-6,1e-6) and q['dwell_s']==0: continue
        kept.append(q); prev=target
    plan['points'] = kept
    if p.get('return_to_start', False):
        # Retrace the outbound polyline; never invent a final-point-to-start
        # chord outside the reviewed corridor. Return legs have no dwell.
        # Keep the measured flange pose exactly, without compensating it twice.
        reverse = [copy.deepcopy(q['pose']) for q in reversed(kept[:-1])]
        reverse.append(copy.deepcopy(plan['start_pose']))
        for index, target in enumerate(reverse, 1):
            name = 'RETURN_TO_START' if index == len(reverse) else f'RETURN_VIA_{index}'
            kept.append(dict(name=name, pose=target, dwell_s=0))
        plan['return_to_start'] = True
        if len(kept) > 100:
            raise ValueError('完整往返路线超过 100 个路点，请减少本轮点位数量')
    if review_only:
        # Deliberately incompatible with waypoint_plan.validate / WaypointSession.
        # A computed route cannot manufacture a site's path approval.
        plan.update(mode='NONCONTACT_ROUTE_REVIEW',path_confirmed=False,execution_eligible=False)
        return plan, targets
    return validate(plan), targets


def fingerprint(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,allow_nan=False).encode()).hexdigest()


def reviewed_execution(bundle, profile, final_points, revision, actual, *, authorization=None, now=None):
    """Bind execution geometry to the exact accepted candidate after other gates.

    Callers must still enforce capture/calibration/site eligibility. A profile
    needs explicit path confirmation or a validated, single-capture fixture
    authorization; neither changes the original review/calibration facts.
    """
    if authorization is not None:
        from .commissioning_authorization import validate_authorization
        validate_authorization(authorization,bundle,now)
    errors=profile_errors(profile,require_path_confirmation=authorization is None)
    if errors: raise ValueError('；'.join(errors))
    summary,details,inputs=bundle['summary'],bundle['details'],bundle['inputs']
    if (not summary['kinematics_checked'] or not details or summary['revision']!=revision
            or inputs['confirmation']['revision']!=revision
            or fingerprint(inputs['confirmation']['points'])!=fingerprint(final_points)
            or fingerprint(details['profile'])!=fingerprint(profile)):
        raise ValueError('执行请求与通过 SDK 复核的路线版本、点位或配置不一致')
    candidate=details['candidate']
    expected_start=pose(candidate['start_pose'],'reviewed_start')
    if not near(actual,expected_start):
        raise ValueError('当前位置已偏离通过 SDK 检查的路线起点')
    rebuilt,targets=compile_route(profile,final_points,expected_start,details['tool_rotation'],review_only=True)
    if fingerprint(rebuilt)!=fingerprint(candidate) or fingerprint(targets)!=fingerprint(details['targets'] if 'targets' in details else summary['targets']):
        raise ValueError('候选路线几何与确认输入不一致')
    executable=copy.deepcopy(candidate)
    executable.update(mode='FREE_SPACE_WAYPOINTS',path_confirmed=True)
    executable.pop('execution_eligible')
    return validate(executable),targets
