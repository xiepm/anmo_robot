"""Refine XY unknown overlaps using the exact capture's observed viewing volume.

Only a tool-tip centerline observation check, never full-body collision approval.
No depth hole is filled. Every projected interval requires a complete surrounding
pixel patch and stays in front of its nearest measured depth by at least 10 mm.
"""
import hashlib
import math
import numpy as np
from .noncontact_plan import fingerprint

METHOD = 'FROZEN_NATIVE_DEPTH_PROJECTED_SEGMENTS_V1'
MARGIN_MM = 10.


def evidence_binding(plan, scene, context, offset):
    return dict(candidate_sha256=fingerprint(plan),scene_sha256=fingerprint(scene),
                context_sha256=fingerprint(context),tool_offset_base_mm=list(offset),frame_number=context['frame_number'],
                received_monotonic_s=context['received_monotonic_s'])


def _overlap(a, b, center, half):
    lower, upper = 0., 1.
    for axis in (0, 1):
        delta=b[axis]-a[axis]
        if delta==0:
            if abs(a[axis]-center[axis])>half: return None
        else:
            lo,hi=sorted(((center[axis]-half-a[axis])/delta,(center[axis]+half-a[axis])/delta))
            lower,upper=max(lower,lo),min(upper,hi)
            if lower>upper: return None
    return lower,upper


def _pinhole(rays):
    h,w,_=rays.shape
    if h<4 or w<4 or not np.isfinite(rays).all() or np.any(rays[...,2]<=0):
        raise ValueError('冻结相机射线无效')
    xy=rays[...,:2]/rays[...,2,None]
    sx=(xy[0,-1,0]-xy[0,0,0])/(w-1)
    sy=(xy[-1,0,1]-xy[0,0,1])/(h-1)
    if sx<=0 or sy<=0: raise ValueError('冻结射线不是支持的正向相机投影')
    fx,fy=1/sx,1/sy
    cx,cy=-xy[0,0,0]*fx,-xy[0,0,1]*fy
    yy,xx=np.mgrid[:h,:w]
    residual=max(float(np.max(abs(xy[...,0]*fx+cx-xx))),float(np.max(abs(xy[...,1]*fy+cy-yy))))
    # The one-pixel patch border below exceeds this validated projection error.
    # Distorted/non-pinhole captures cannot silently use a pinhole approximation.
    if residual>.01: raise ValueError('冻结射线不满足当前投影模型，保留未知区域')
    return fx,fy,cx,cy,residual


def _visible_interval(a, b, lo, hi, depth, intrinsics):
    """Continuous rational projection, partitioned at integer pixel coordinates."""
    fx,fy,cx,cy,_=intrinsics
    h,w=depth.shape
    delta=b-a
    ends=np.array([a+delta*lo,a+delta*hi])
    if np.any(ends[:,2]<=0): return False,None,'路线位于相机后方或穿过相机平面'
    uv=ends[:,:2]/ends[:,2,None]*[fx,fy]+[cx,cy]
    if (np.any(uv[:,0]<1) or np.any(uv[:,0]>=w-2) or
            np.any(uv[:,1]<1) or np.any(uv[:,1]>=h-2)):
        return False,None,'路线的未知部分在本次照片视野外'
    cuts=[lo,hi]
    for axis,f,c in ((0,fx,cx),(1,fy,cy)):
        for edge in range(math.ceil(float(uv[:,axis].min())),math.floor(float(uv[:,axis].max()))+1):
            denominator=f*delta[axis]-(edge-c)*delta[2]
            if denominator!=0:
                t=((edge-c)*a[2]-f*a[axis])/denominator
                if lo<t<hi: cuts.append(t)
    cuts=sorted(set(cuts))
    pairs=list(zip(cuts,cuts[1:])) or [(lo,hi)]
    minimum=math.inf
    # The route is a closed interval. At an integer projected coordinate the
    # endpoint's floor-neighborhood can differ from both adjacent midpoints.
    # Explicitly include every split point, including isolated/tangent hits.
    for cut in cuts:
        p=a+delta*cut
        px,py=np.floor(p[:2]/p[2]*[fx,fy]+[cx,cy]).astype(int)
        patch=depth[py-1:py+3,px-1:px+3]
        if patch.shape!=(4,4) or not np.isfinite(patch).all() or np.any(patch<=0):
            return False,None,'实际路线投影端点邻域包含无效深度，未填补缺测'
        margin=float(patch.min())*1000-float(p[2])
        if margin<MARGIN_MM:
            return False,margin,'实际路线端点靠近或位于实测表面之后'
        minimum=min(minimum,margin)
    for left,right in pairs:
        mid=a+delta*((left+right)/2)
        px,py=np.floor(mid[:2]/mid[2]*[fx,fy]+[cx,cy]).astype(int)
        patch=depth[py-1:py+3,px-1:px+3]
        if patch.shape!=(4,4) or not np.isfinite(patch).all() or np.any(patch<=0):
            return False,None,'实际路线投影邻域包含无效深度，未填补缺测'
        nearest=float(patch.min())*1000
        farthest=max(float(a[2]+delta[2]*left),float(a[2]+delta[2]*right))
        margin=nearest-farthest
        if margin<MARGIN_MM:
            return False,margin,'实际路线靠近或位于实测表面之后'
        minimum=min(minimum,margin)
    return True,minimum,''


def assess_visibility(plan, scene, offset, context, geometry, capture=None):
    report=dict(method=METHOD,assessment='UNAVAILABLE',execution_eligible=False,
        binding=evidence_binding(plan,scene,context,offset),geometry_sha256=None,
        cell_count=0,unresolved_cells=0,checked_intervals=0,
        minimum_depth_margin_mm=None,required_depth_margin_mm=MARGIN_MM,
        projection_residual_px=None,reason='')
    try:
        centers=scene.get('unknown_cell_examples_base_xy_mm',[])
        if (len(centers)!=scene['unknown_cells'] or len(set(map(tuple,centers)))!=len(centers)
                or not all(len(c)==2 and all(math.isfinite(x) for x in c) for c in centers)):
            raise ValueError('未知区域记录不完整')
        if (scene.get('context_sha256')!=fingerprint(context)
                or any(scene.get(k)!=context.get(k) for k in ('frame_number','received_monotonic_s','revision','calibration_sha256'))):
            raise ValueError('深度场景与本次确认上下文不一致')
        half=scene['cell_size_mm']/2
        if half!=5: raise ValueError('未知区域须使用本次 10 mm 栅格')
        vertices=[plan['start_pose']]+[p['pose'] for p in plan['points']]
        route=np.array([[p[k] for k in ('x_mm','y_mm','z_mm')] for p in vertices])+np.asarray(offset)
        if len(route)<2 or not np.isfinite(route).all(): raise ValueError('实际工具尖端路线无效')
        intervals=[]
        for index,center in enumerate(centers):
            for segment,(a,b) in enumerate(zip(route,route[1:])):
                overlap=_overlap(a,b,center,half)
                if overlap is not None: intervals.append((index,segment,*overlap))
        cells=set(i[0] for i in intervals)
        report['cell_count']=len(cells)
        if not intervals:
            report.update(assessment='NOT_REQUIRED',reason='路线平面投影未经过记录中的未知方格')
            return report
        if geometry is None: raise ValueError('缺少本次冻结深度几何')
        if (geometry['frame_number']!=context['frame_number'] or
                float(geometry['received_monotonic_s'])!=context['received_monotonic_s']):
            raise ValueError('冻结几何与本次照片不一致')
        if str(geometry.get('geometry_frame',''))!='DEPTH_OPTICAL_NATIVE':
            raise ValueError('本次照片没有原生深度视角数据，请重新拍照')
        native_frame=int(geometry['native_depth_frame_number'])
        aligned_frame=int(geometry['aligned_depth_frame_number'])
        if (native_frame!=geometry['native_depth_frame_number'] or
                aligned_frame!=geometry['aligned_depth_frame_number']):
            raise ValueError('深度帧号必须为整数')
        native_time=float(geometry['native_depth_timestamp_ms'])
        aligned_time=float(geometry['aligned_depth_timestamp_ms'])
        if (native_frame<0 or native_frame!=aligned_frame or not math.isfinite(native_time)
                or native_time!=aligned_time):
            raise ValueError('原生深度与本次对齐深度不是同一帧')
        if capture is not None:
            clock=capture.get('exposure_clock',{})
            expected=clock.get('depth',{})
            if (capture.get('capture_time_valid') is not True or
                    capture.get('frame_number')!=context['frame_number'] or
                    capture.get('frame_received_monotonic_s')!=context['received_monotonic_s'] or
                    clock.get('color',{}).get('frame_number')!=context['frame_number'] or
                    expected.get('frame_number')!=native_frame or
                    expected.get('global_timestamp_ms')!=native_time):
                raise ValueError('原生深度曝光与控制器已验证的照片证据不一致，请重新拍照')
            report['capture_sha256']=fingerprint(capture)
        depth=np.asarray(geometry['native_depth_m'],dtype=float)
        rays=np.asarray(geometry['native_rays'],dtype=float)
        if depth.ndim!=2 or depth.size>2_000_000 or rays.shape!=(*depth.shape,3):
            raise ValueError('冻结深度几何尺寸无效')
        base_color=np.asarray(context['T_base_camera'],dtype=float)
        color_depth=np.asarray(geometry['T_color_depth'],dtype=float)
        for transform in (base_color,color_depth):
            if (transform.shape!=(4,4) or not np.isfinite(transform).all() or
                    not np.allclose(transform[3],[0,0,0,1],atol=1e-8) or
                    not np.allclose(transform[:3,:3].T@transform[:3,:3],np.eye(3),atol=1e-6) or
                    abs(np.linalg.det(transform[:3,:3])-1)>1e-6):
                raise ValueError('原生深度到基座变换无效')
        t=base_color@color_depth
        intrinsics=_pinhole(rays)
        report['projection_residual_px']=intrinsics[-1]
        sha=hashlib.sha256()
        sha.update(str((METHOD,depth.shape,native_frame,native_time)).encode())
        sha.update(np.ascontiguousarray(color_depth,dtype='<f8').tobytes())
        sha.update(np.ascontiguousarray(depth,dtype='<f8').tobytes())
        sha.update(np.ascontiguousarray(rays,dtype='<f8').tobytes())
        report['geometry_sha256']=sha.hexdigest()
        report['geometry_frame']='DEPTH_OPTICAL_NATIVE'
        report['native_depth_frame_number']=native_frame
        report['native_depth_timestamp_ms']=native_time
        camera=(route-t[:3,3]*1000)@t[:3,:3]
        unresolved=set();reasons=[];margins=[]
        for cell,segment,lo,hi in intervals:
            ok,margin,reason=_visible_interval(camera[segment],camera[segment+1],lo,hi,depth,intrinsics)
            report['checked_intervals']+=1
            if not ok:
                unresolved.add(cell)
                if reason not in reasons: reasons.append(reason)
            if margin is not None: margins.append(margin)
        report.update(assessment='UNRESOLVED' if unresolved else 'OBSERVED_CLEAR',
            unresolved_cells=len(unresolved),minimum_depth_margin_mm=min(margins) if margins else None,
            reason='；'.join(reasons) if unresolved else '原二维重叠路段均位于本次有效深度观测的表面前方')
    except (ValueError,KeyError,TypeError,IndexError,OverflowError) as exc:
        report.update(assessment='UNAVAILABLE',unresolved_cells=report['cell_count'],reason=str(exc))
    return report


def validated_visibility(bundle):
    """Only native-depth evidence for this exact candidate refines the XY gate."""
    from .route_coverage import unknown_clearance
    try:
        details,inputs=bundle['details'],bundle['inputs']
        evidence=details.get('depth_visibility')
        rotation=details['tool_rotation']
        offset=[sum(rotation[i][j]*details['profile']['physical_tool_offset_flange_mm'][j]
                    for j in range(3)) for i in range(3)]
        coverage=unknown_clearance(details['candidate'],inputs['scene'],offset)
        if 'capture' in inputs:
            capture=inputs['capture']
            expected=capture.get('exposure_clock',{}).get('depth',{})
            if (not isinstance(evidence,dict) or evidence.get('capture_sha256')!=fingerprint(capture)
                    or expected.get('frame_number')!=evidence.get('native_depth_frame_number')
                    or expected.get('global_timestamp_ms')!=evidence.get('native_depth_timestamp_ms')):
                return False
        if (not isinstance(evidence,dict) or evidence.get('method')!=METHOD or
                evidence.get('assessment')!='OBSERVED_CLEAR' or evidence.get('execution_eligible') is not False or
                evidence.get('binding')!=evidence_binding(details['candidate'],inputs['scene'],inputs['context'],offset) or
                evidence.get('geometry_frame')!='DEPTH_OPTICAL_NATIVE' or
                type(evidence.get('native_depth_frame_number')) is not int or evidence['native_depth_frame_number']<0 or
                type(evidence.get('native_depth_timestamp_ms')) not in (int,float) or
                not math.isfinite(evidence['native_depth_timestamp_ms']) or
                evidence.get('required_depth_margin_mm')!=MARGIN_MM or evidence.get('unresolved_cells')!=0 or
                type(evidence.get('cell_count')) is not int or evidence['cell_count']<=0 or
                coverage['assessment']!='CENTERLINE_NEAR_UNKNOWN' or
                evidence['cell_count']!=coverage['possibly_intersecting_cells'] or
                type(evidence.get('checked_intervals')) is not int or evidence['checked_intervals']<evidence['cell_count'] or
                not isinstance(evidence.get('geometry_sha256'),str) or len(evidence['geometry_sha256'])!=64 or
                any(c not in '0123456789abcdef' for c in evidence['geometry_sha256'])):
            return False
        margin=evidence.get('minimum_depth_margin_mm')
        return type(margin) in (int,float) and math.isfinite(margin) and margin>=MARGIN_MM
    except (KeyError,TypeError,ValueError,IndexError):
        return False
