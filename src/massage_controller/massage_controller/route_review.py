"""Stage 3 candidate planning. No executable plan or movement permission output.

Vendor IK/FK runs via the existing SDK owner's bounded math queue. URDF collision
results are suppressed on a model mismatch. Depth voxels are observed obstacles,
never a declaration that unobserved space is free. Discrete samples cannot certify
the controller's swept trajectory, tool, camera bracket or cable clearances.
"""
import copy
import io
import json
import math
import queue
import threading
import time
from itertools import islice
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from pathlib import Path
from urllib.request import urlopen

from .noncontact_plan import (automatic_profile, compile_route, fingerprint, load_profile,
                              LEGACY_ROUTE, DIRECT_ROUTE, SEGMENTED_ROUTE)
from .waypoint_plan import POSE_KEYS, angle_error
from .sdk_readonly import atomic_json
from .route_coverage import unknown_clearance
from .route_visibility import assess_visibility


MATH_TRANSPORT_ERRORS=(TimeoutError,FutureTimeoutError,queue.Full,ConnectionError)


def math_transport_error(error):
    if isinstance(error,(TimeoutError,FutureTimeoutError)):
        label='控制器数学查询超时'
    elif isinstance(error,queue.Full):
        label='控制器数学查询队列已满'
    else:
        label='控制器数学查询连接异常'
    detail=str(error).strip()
    return label+('：'+detail if detail else '')


def candidate_samples(plan, step_mm=10.):
    previous=[plan['start_pose'][k] for k in POSE_KEYS]
    for point in plan['points']:
        target=[point['pose'][k] for k in POSE_KEYS]
        count=max(1,math.ceil(math.dist(previous[:3],target[:3])/step_mm))
        for i in range(1,count+1):
            yield point['name'],[a+(b-a)*i/count for a,b in zip(previous[:3],target[:3])]+target[3:]
        previous=target


def stage_description(name, points):
    index, _, action=name.partition('_')
    if index.isdigit() and 1<=int(index)<=len(points):
        point=points[int(index)-1]
        side={'LEFT':'左','RIGHT':'右','MIDLINE':''}.get(point['side'],point['side'])
        label=f"第 {index} 点{side}{point['name']}"
        return label, {'APPROACH':'到位段','TRANSFER':'过渡段','RETRACT':'撤离段'}.get(action,action)
    return name, ''


def load_model():
    import xacro
    from ament_index_python.packages import get_package_share_directory
    from elfin_massage_sim.kinematics import Kinematics
    from elfin_massage_sim.collision import CollisionChecker
    source=Path(get_package_share_directory('elfin5_l_ros2_gazebo'))/'urdf/elfin5_l.urdf.xacro'
    xml=xacro.process_file(str(source),mappings={'use_real_hardware':'false','use_fake_hardware':'false'}).toxml()
    model=Kinematics(xml,world_frame='world',tcp_frame='elfin_end_link')
    return model,CollisionChecker(model)


def model_error(model,joints,reference):
    import numpy as np
    from scipy.spatial.transform import Rotation
    observed=np.asarray(reference,dtype=float)
    predicted=model.fk(np.radians(joints))
    return (float(np.linalg.norm(predicted[:3,3]-observed[:3,3])*1000),
            float(np.degrees(Rotation.from_matrix(predicted[:3,:3]@observed[:3,:3].T).magnitude())))


def frozen_geometry(context):
    import numpy as np
    url=('http://127.0.0.1:8767/snapshot/geometry.npz?frame='+str(context['frame_number'])
         +'&received='+str(context['received_monotonic_s']))
    with urlopen(url,timeout=5) as response:
        blob=response.read(24_000_001)
    if len(blob)>24_000_000: raise ValueError('冻结深度几何数据过大')
    with np.load(io.BytesIO(blob),allow_pickle=False) as data:
        if (int(data['frame_number'])!=context['frame_number']
                or float(data['received_monotonic_s'])!=context['received_monotonic_s']):
            raise ValueError('深度几何数据与确认照片不一致')
        return {key:data[key].copy() for key in data.files}


def frozen_cloud(context):
    import numpy as np
    data=frozen_geometry(context)
    depth,rays=data['depth_m'],data['rays']
    if depth.ndim!=2 or depth.size>2_000_000 or rays.shape!=(*depth.shape,3):
        raise ValueError('冻结深度几何维度无效')
    valid=np.isfinite(depth)&(depth>0)&np.isfinite(rays).all(axis=2)
    cloud=rays[valid]*depth[valid,None]
    t=np.asarray(context['T_base_camera'])
    return cloud@t[:3,:3].T+t[:3,3]


class ObservedObstacles:
    """10 mm occupied voxels covering measured surfaces. No free-space claims."""
    def __init__(self, cloud):
        import numpy as np
        import fcl
        self.fcl=fcl
        cells=np.unique(np.floor(cloud/.01).astype(int),axis=0)
        if not len(cells) or len(cells)>100_000: raise ValueError('可见障碍体素数量无效')
        self.objects=[fcl.CollisionObject(fcl.Box(.01,.01,.01),
                      fcl.Transform((cell+.5)*.01)) for cell in cells]
        self.manager=fcl.DynamicAABBTreeCollisionManager()
        self.manager.registerObjects(self.objects);self.manager.setup()
        self.count=len(cells)

    def check(self,checker):
        # CollisionChecker.check must first update every link's transform.
        for name,_,obj in checker.objects:
            data=self.fcl.CollisionData()
            self.manager.collide(obj,data,self.fcl.defaultCollisionCallback)
            if data.result.is_collision: raise ValueError('可见深度障碍碰撞：'+name)


class Reviewer:
    def __init__(self,math_query,model_factory=load_model,cloud_loader=frozen_cloud,geometry_loader=frozen_geometry):
        self.math_query,self.model_factory,self.cloud_loader=math_query,model_factory,cloud_loader
        self.geometry_loader=geometry_loader
        self.model_pair=None
        self.initialization_error=''

    def warmup(self):
        # Parse meshes/import FCL before opening live feedback and UI endpoints.
        # First-confirmation CPU load must not starve camera health publication.
        try: self.model_pair=self.model_factory()
        except Exception as exc: self.initialization_error=str(exc)

    def run(self, inputs, cancel, on_progress=None):
        started=time.monotonic()
        def progress(done, total, waypoints):
            if on_progress:
                on_progress(dict(sample_count=done,waypoint_count=waypoints,
                    reason=f'正在进行控制器逆解和路线预检：{done} / {total} 个采样位置'))
        def alive():
            if cancel.is_set(): raise InterruptedError('坐标版本已失效，丢弃路线')
            if time.monotonic()-started>90: raise ValueError('候选路线预检超过 90 秒')
        value,scene,context,sample,profile=(inputs[k] for k in ('confirmation','scene','context','sample','profile'))
        report=dict(state='BLOCKED',revision=value['revision'],frame_number=value['frame_number'],
            execution_eligible=False,reason='',blockers=[],targets=[],waypoint_count=0,sample_count=0,
            model_position_error_mm=None,model_rotation_error_deg=None,kinematics_checked=False,
            model_check_state='NOT_CHECKED',model_check_note='',
            self_collision_checked=False,observed_collision_checked=False,full_collision_checked=False,
            transfer_height_mm=None,unknown_cells=scene['unknown_cells'],attempts=[])
        try:
            if not inputs['capture'].get('capture_time_valid'): raise ValueError('当前照片采集同步未通过')
            for key in ('configured_tool_pose_mm_deg','configured_user_pose_mm_deg'):
                if len(sample.get(key,[]))!=6 or any(abs(x)>.01 for x in sample[key]):
                    raise ValueError('候选路线要求控制器 TCP/UCS 为零变换')
            actual=sample['tcp_pose']['xyz_mm']+sample['tcp_pose']['rxyz_deg']
            rotation=self.math_query(dict(kind='rotation',angles=actual[3:]))
            q=sample['joint_positions_deg']
            model_ok=False
            policy=profile.get('virtual_model_check','ENABLED')
            if policy not in ('ENABLED','DEFERRED'):
                raise ValueError('未知虚拟模型检查策略')
            if policy=='DEFERRED':
                report.update(model_check_state='DEFERRED',
                    model_check_note='按用户要求暂缓虚拟模型检查；路线使用实机 SDK 逆解和正解复核')
            else:
                # Model availability must not prevent vendor mathematical checks.
                try:
                    if self.initialization_error: raise ValueError(self.initialization_error)
                    if self.model_pair is None: self.model_pair=self.model_factory()
                    model,checker=self.model_pair
                    err,angle=model_error(model,q[:5]+[0.],sample['T_base_reference'])
                    report.update(model_position_error_mm=err,model_rotation_error_deg=angle)
                    model_ok=err<=2 and angle<=.2
                    report['model_check_state']='CONSISTENT' if model_ok else 'MISMATCH'
                    if not model_ok:
                        report['blockers'].append(f'URDF 与控制器参考位姿不一致：{err:.2f} mm / {angle:.3f}°，不能据此批准碰撞通路')
                except Exception as exc:
                    report.update(model_check_state='UNAVAILABLE',model_check_note=str(exc))
                    report['blockers'].append('机器人模型不可用，碰撞检查未完成：'+str(exc))
            obstacles=None
            if model_ok:
                obstacles=ObservedObstacles(self.cloud_loader(context))
            derived=automatic_profile(profile,scene,value['points'],context)
            mode=profile.get('route_mode',LEGACY_ROUTE)
            if mode not in (LEGACY_ROUTE,DIRECT_ROUTE,SEGMENTED_ROUTE):
                raise ValueError('未知无接触路线生成方式')
            if mode == LEGACY_ROUTE:
                low=scene['surface_ceiling_base_mm']+profile['minimum_surface_clearance_mm']+profile['extra_clearance_mm']+10
                candidates=[dict(derived,safe_transfer_height_base_mm=height) for height in
                            dict.fromkeys([derived['safe_transfer_height_base_mm'],low])]
            else:
                # Direct MoveL targets first. If that cannot pass the vendor's
                # continuous branch checks, try a horizontal first leg at the
                # *actual tool-tip* height. Neither candidate adds an initial
                # vertical drop or an artificial +40 mm lift.
                tip_z=actual[2]+sum(rotation[2][j]*profile['physical_tool_offset_flange_mm'][j] for j in range(3))
                segmented=dict(derived,route_mode=SEGMENTED_ROUTE,safe_transfer_height_base_mm=tip_z)
                candidates=[derived,segmented] if mode == DIRECT_ROUTE else [segmented]
            accepted=None;endpoint_failure=None
            for candidate in candidates:
                alive()
                height=candidate['safe_transfer_height_base_mm']
                attempt=dict(transfer_height_mm=height,samples=0,error='')
                report['attempts'].append(attempt)
                sample_context='';unreachable_point=False;endpoint_transport_failure=False
                try:
                    plan,targets=compile_route(candidate,value['points'],actual,rotation,review_only=True)
                    report.update(targets=targets,waypoint_count=len(plan['points']),transfer_height_mm=height)
                    positions=list(islice(candidate_samples(plan),1001))
                    if len(positions)>1000: raise ValueError('路线采样超过 1000 点')
                    endpoints={point['name']:[point['pose'][k] for k in POSE_KEYS] for point in plan['points']}
                    progress(0,len(positions),len(plan['points']))
                    seed=list(q);checked=[]
                    if model_ok:
                        import numpy as np
                        checker.check(np.radians(seed));obstacles.check(checker)
                    for name,target in positions:
                        alive()
                        if len(checked)>=1000: raise ValueError('路线采样超过 1000 点')
                        label,stage=stage_description(name,value['points'])
                        xyz=', '.join(f'{v:.3f}' for v in target[:3])
                        angles=', '.join(f'{v:.3f}' for v in target[3:])
                        sample_context=(f'{label}{stage}（{name}），路线第 {len(checked)+1} / {len(positions)} 个采样，'
                                        f'Base 法兰 XYZ=[{xyz}] mm，姿态=[{angles}]°')
                        diagnostic_seed=list(seed)
                        try:
                            answer=self.math_query(dict(kind='ik',pose=target,seed=seed))
                        except (ValueError,RuntimeError) as exc:
                            # Diagnose the original segment endpoint without changing
                            # the continuous route, its seed, or its failed status.
                            alive()
                            endpoint_error=''
                            endpoint=endpoints[name]
                            try:
                                last=self.math_query(dict(kind='ik',pose=list(endpoint),seed=diagnostic_seed))
                                if (math.dist(last['pose'][:3],endpoint[:3])>.5
                                        or angle_error(last['pose'][3:],endpoint[3:])>.2):
                                    endpoint_error='控制器逆解/正解复核不一致'
                            except MATH_TRANSPORT_ERRORS as diagnostic:
                                endpoint_error=math_transport_error(diagnostic)
                                endpoint_transport_failure=True
                            except (ValueError,RuntimeError) as diagnostic:
                                endpoint_error=str(diagnostic)
                            alive()
                            code='控制器逆解失败：20031'
                            unreachable_point=(str(exc).strip()==code and endpoint_error.strip()==code
                                               and name.endswith('_APPROACH'))
                            if unreachable_point:
                                detail=f'{label}当前姿态不可达（原段末点逆解也返回 20031）'
                            elif endpoint_error:
                                detail='原段末点诊断：'+endpoint_error
                            else:
                                detail='原段末点单独逆解/正解复核通过，连续路线仍未通过'
                            raise ValueError(str(exc)+'；'+detail) from exc
                        joints,pose=answer['joints_deg'],answer['pose']
                        if math.dist(pose[:3],target[:3])>.5 or angle_error(pose[3:],target[3:])>.2:
                            raise ValueError(name+'：控制器逆解/正解复核不一致')
                        # Never wrap 360 degrees silently: actual execution could
                        # choose a different turn or cross a joint limit.
                        if max(abs(a-b) for a,b in zip(seed,joints))>10:
                            raise ValueError(name+'：相邻采样关节变化超过 10°，需缩短步长或更换分支')
                        if model_ok:
                            import numpy as np
                            matrix=copy.deepcopy(rotation)
                            for i in range(3): matrix[i][3]=pose[i]/1000
                            e,a=model_error(model,joints,matrix)
                            if e>2 or a>.2: raise ValueError(name+'：沿途模型与控制器正解不一致')
                            checker.check(np.radians(joints));obstacles.check(checker)
                        checked.append(dict(stage=name,flange_pose=target,joints_deg=joints))
                        seed=joints;attempt['samples']=len(checked)
                        sample_context=''
                        if len(checked)%5==0 or len(checked)==len(positions):
                            progress(len(checked),len(positions),len(plan['points']))
                    accepted=dict(candidate=plan,samples=checked,profile=candidate,tool_rotation=rotation)
                    offset=[sum(rotation[i][j]*candidate['physical_tool_offset_flange_mm'][j]
                                for j in range(3)) for i in range(3)]
                    report['unknown_route_clearance']=unknown_clearance(plan,scene,offset)
                    if (candidate.get('unknown_check_mode')=='FROZEN_3D_VISIBILITY'
                            and report['unknown_route_clearance']['assessment']=='CENTERLINE_NEAR_UNKNOWN'):
                        geometry=None;load_error=''
                        try: geometry=self.geometry_loader(context)
                        except Exception as exc: load_error=str(exc)
                        alive()
                        evidence=assess_visibility(plan,scene,offset,context,geometry,inputs['capture'])
                        if load_error: evidence['reason']='本次冻结深度读取失败：'+load_error
                        accepted['depth_visibility']=evidence
                        report['depth_visibility']={k:evidence[k] for k in (
                            'assessment','cell_count','unresolved_cells','checked_intervals',
                            'minimum_depth_margin_mm','required_depth_margin_mm','reason')}
                    report.update(kinematics_checked=True,sample_count=len(checked),
                        self_collision_checked=model_ok,observed_collision_checked=model_ok)
                    break
                except MATH_TRANSPORT_ERRORS as exc:
                    alive()
                    attempt['error']=(sample_context+'：' if sample_context else '')+math_transport_error(exc)
                    # Transport failure terminates this review, as before; do not
                    # turn a queue/socket outage into another endpoint query.
                    raise ValueError(attempt['error']) from exc
                except (ValueError,RuntimeError) as exc:
                    attempt['error']=(sample_context+'：' if sample_context else '')+str(exc)
                    if endpoint_transport_failure:
                        raise ValueError(attempt['error']) from exc
                    if unreachable_point and endpoint_failure is None:
                        endpoint_failure=attempt['error']
            if accepted is None:
                report['blockers'].append('当前候选路线均未通过路线预检：'+(endpoint_failure or report['attempts'][-1]['error']))
            if scene['unknown_cells']:
                if report.get('depth_visibility',{}).get('assessment')!='OBSERVED_CLEAR':
                    report['blockers'].append(f"路线附近 {scene['unknown_cells']} 个深度栅格未知，不能推断为可通行")
            report['blockers'].extend(['按摩末端、相机及支架的实际外形尺寸和通行间隙尚未核实',
                '实际整段路径与相机盲区尚未核验'])
            report['reason']=report['blockers'][0]
            return dict(summary=report,details=accepted,inputs=inputs,review_elapsed_s=time.monotonic()-started)
        except InterruptedError:
            raise
        except MATH_TRANSPORT_ERRORS as exc:
            message=math_transport_error(exc)
            report['blockers'].append(message);report['reason']=message
            return dict(summary=report,details=None,inputs=inputs,review_elapsed_s=time.monotonic()-started)
        except Exception as exc:
            report['blockers'].append(str(exc));report['reason']=str(exc)
            return dict(summary=report,details=None,inputs=inputs,review_elapsed_s=time.monotonic()-started)


class ReviewManager:
    """Latest confirmed revision only; stale calculations cannot unlock anything."""
    def __init__(self,math_query,output_dir,reviewer=None,*,defer_virtual_model=False):
        self.reviewer=reviewer or Reviewer(math_query)
        if reviewer is None and not defer_virtual_model: self.reviewer.warmup()
        self.output_dir=Path(output_dir)
        self.executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='route-review')
        self.future=None;self.cancel=threading.Event();self.key=None;self.report=None
        self.result=None
        self.progress_lock=threading.Lock()
        self.progress=None

    def run_review(self, inputs, cancel, key):
        def update(value):
            with self.progress_lock:
                if self.cancel is cancel and not cancel.is_set():
                    self.progress=(key,copy.deepcopy(value))
        if isinstance(self.reviewer,Reviewer):
            return self.reviewer.run(inputs,cancel,on_progress=update)
        return self.reviewer.run(inputs,cancel)

    def sync(self,confirmation,sample,profile_path):
        value=confirmation.value
        if not value or value['state']!='CONFIRMED' or not confirmation.scene:
            self.cancel.set();self.key=None;self.report=None;self.result=None
            with self.progress_lock: self.progress=None
            return None
        profile=load_profile(profile_path)
        key=fingerprint(dict(value=value,scene=confirmation.scene,profile=profile))
        if self.key!=key:
            self.result=None
            self.cancel.set()
            if self.future and not self.future.done():
                self.report=None
                return None  # At most one running/pending calculation.
            self.cancel=threading.Event();self.key=key
            with self.progress_lock: self.progress=None
            inputs=copy.deepcopy(dict(confirmation=value,scene=confirmation.scene,
                context=confirmation.scene_context,capture=confirmation.packet,sample=sample,profile=profile))
            self.report=dict(state='CHECKING',revision=value['revision'],frame_number=value['frame_number'],
                execution_eligible=False,reason='正在进行控制器逆解和路线预检',blockers=[],targets=[],
                waypoint_count=0,sample_count=0,model_position_error_mm=None,model_rotation_error_deg=None,
                model_check_state='DEFERRED' if profile.get('virtual_model_check')=='DEFERRED' else 'NOT_CHECKED',
                model_check_note='',
                kinematics_checked=False,self_collision_checked=False,observed_collision_checked=False,
                full_collision_checked=False,transfer_height_mm=None,unknown_cells=confirmation.scene['unknown_cells'],attempts=[])
            self.future=self.executor.submit(self.run_review,inputs,self.cancel,key)
        if self.report and self.report['state']=='CHECKING':
            with self.progress_lock:
                if self.progress and self.progress[0]==key:
                    self.report.update(self.progress[1])
        if self.future and self.future.done() and self.report and self.report['state']=='CHECKING':
            try:
                result=self.future.result()
                self.report=result['summary']
                atomic_json(self.output_dir/(value['revision']+'.json'),result)
                self.result=result
            except Exception as exc:
                self.report.update(state='BLOCKED',reason=str(exc),blockers=[str(exc)])
        return copy.deepcopy(self.report)

    def candidate(self,confirmation,profile_path):
        """Return only the exact current review; this grants no motion approval."""
        if not confirmation.value or confirmation.value['state']!='CONFIRMED' or not confirmation.scene:
            raise ValueError('没有当前已确认路线')
        expected=fingerprint(dict(value=confirmation.value,scene=confirmation.scene,profile=load_profile(profile_path)))
        result=self.result
        if (self.key!=expected or self.cancel.is_set() or not result or not result['details']
                or not result['summary']['kinematics_checked']):
            raise ValueError('本次 SDK 路线预检尚未通过或已失效')
        return copy.deepcopy(result)

    def close(self):
        self.cancel.set();self.executor.shutdown(wait=True,cancel_futures=True)
