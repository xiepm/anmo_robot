"""UI-to-worker bridge for explicitly configured no-contact commissioning."""
import copy
import json
import logging
import math
from .noncontact_plan import load_profile, eligibility_errors, fingerprint, automatic_profile
from .geometry import compose
from .commissioning_authorization import read_authorization, make_authorization, authorization_path
from .sdk_readonly import atomic_json

ACTIVE = {'PREPARING','CONNECTING','PREFLIGHT','READY','RUNNING','STOPPING'}

class NoncontactControl:
    def __init__(self,core,profile_path,worker,review=None):
        self.core,self.profile_path,self.worker=core,profile_path,worker
        self.active=False
        self.revision=None
        self.profile_hash=None
        self.stop_reason=''
        self.faulted=False
        self.fault_kinds=set()
        self.start_requested=False
        self.return_required=False
        self.report={'state':'NONE'}
        self.review=review
        self.authorization_revoked=False

    def scene_context(self, data, source):
        if load_profile(self.profile_path).get('geometry_source') != 'FROZEN_DEPTH':
            return None
        c = self.core
        for key in ('configured_tool_pose_mm_deg', 'configured_user_pose_mm_deg'):
            value = c.device.sample.get(key, [])
            if len(value) != 6 or any(not math.isfinite(x) or abs(x) > .01 for x in value):
                raise ValueError('自动场景要求控制器当前 TCP/UCS 为零变换，不能把 TCP 当成 Base 法兰')
        return dict(revision=data['revision'], frame_number=data['frame_number'],
            received_monotonic_s=data['received_monotonic_s'], calibration_sha256=source['calibration_sha256'],
            T_base_camera=compose(source['feedback_window']['T_base_reference'],
                                  c.confirmation.projector.calibration['T_reference_camera']),
            start_flange_mm=copy.deepcopy(c.device.sample['tcp_pose']['xyz_mm']),
            corridor_radius_mm=50, cell_size_mm=10)

    def effective_profile(self):
        profile = load_profile(self.profile_path)
        if profile.get('geometry_source') == 'FROZEN_DEPTH':
            confirmation = self.core.confirmation
            if not confirmation.value or confirmation.value['state'] != 'CONFIRMED' or not confirmation.scene:
                raise ValueError('请确认穴位坐标，自动分析本次照片的深度场景')
            if self.review:
                profile=self.review.candidate(confirmation,self.profile_path)['details']['profile']
            else:
                profile = automatic_profile(profile, confirmation.scene,
                                            confirmation.value['points'], confirmation.scene_context)
        return profile

    def blockers(self):
        c=self.core
        try:
            calibration=c.confirmation.projector.calibration if c.confirmation.projector else {}
            raw = load_profile(self.profile_path)
            errors = []
            if raw.get('geometry_source') == 'FROZEN_DEPTH':
                try:
                    profile = self.effective_profile()
                except ValueError as exc:
                    return [str(exc)]+eligibility_errors(raw,calibration,c.confirmation.packet,pending_scene=True)
                approval = self.authorization()
                scene = c.confirmation.scene
                if approval is None:
                    if scene['unknown_cells']:
                        errors.append(f"路线附近有 {scene['unknown_cells']} / {scene['corridor_cells']} 个深度栅格未知，禁止穿越")
                    errors.append('自动场景尚未完成整臂碰撞及相机盲区检查')
            else:
                profile = raw
                approval = None
            return errors+eligibility_errors(profile,calibration,c.confirmation.packet,
                                             limited_authorization=approval is not None)
        except (OSError,ValueError,TypeError,KeyError) as exc: return [f'无接触配置不可用：{exc}']

    def authorization(self):
        if self.authorization_revoked or not self.review: return None
        bundle = self.review.candidate(self.core.confirmation,self.profile_path)
        return read_authorization(self.profile_path,bundle,self.core.clock())

    def revoke_authorization(self,revision):
        """Retire idle authorization even if the same UUID is submitted again."""
        if self.active:
            raise ValueError('不能撤销执行中的现场授权；须先请求停止')
        if self.authorization_revoked:
            return
        self.authorization_revoked=True
        try:
            path=authorization_path(self.profile_path)
            approval=json.loads(path.read_text())
            binding=approval.get('binding') if isinstance(approval,dict) else None
            if isinstance(binding,dict) and binding.get('revision')==revision:
                path.unlink(missing_ok=True)
        except FileNotFoundError:
            pass
        except (OSError,ValueError) as exc:
            # The in-memory latch still blocks this sidecar if removal fails.
            logging.getLogger('control_node').warning('旧现场授权文件未能删除，已拒绝继续使用：%s',exc)

    def authorization_candidate(self):
        c=self.core
        if self.active or not self.review:
            raise ValueError('当前状态不能确认新路线')
        c.confirmation.check_capture()
        bundle=self.review.candidate(c.confirmation,self.profile_path)
        # Feasibility only; never persists approval or grants execution.
        make_authorization(bundle,'可用性检查，不是现场授权',c.clock())
        return bundle

    def authorize(self,data):
        c=self.core
        if data.get('operator_observed_no_obstacles') is not True or data.get('reuse_existing_calibration') is not True:
            raise ValueError('须明确确认本次现场无障碍及沿用现有标定')
        c.confirmation.final_surface_points(data['revision'],data['points'])
        bundle=self.authorization_candidate()
        approval=make_authorization(bundle,
            '操作员在 UI 点击“确认现场无障碍并准备路线”：本次为假人/工装、无接触；现场确认当前路线无障碍，沿用现有标定。',c.clock())
        self.worker.log(dict(type='NONCONTACT_SITE_AUTHORIZATION',authorization=approval))
        atomic_json(authorization_path(self.profile_path),approval)
        self.authorization_revoked=False

    def prepare(self,data):
        c=self.core
        if self.active or c.state!='IDLE': raise ValueError('须在空闲状态准备无接触路线')
        points=c.confirmation.final_surface_points(data['revision'],data['points'])
        errors=self.blockers()
        if errors: raise ValueError('；'.join(errors))
        profile=self.effective_profile()
        if profile.get('geometry_source')=='FROZEN_DEPTH':
            if not self.review: raise ValueError('自动场景须先完成本次 SDK 路线预检')
            bundle=self.review.candidate(c.confirmation,self.profile_path)
            self.worker.submit(profile,points,data['revision'],reviewed=bundle,authorization=self.authorization())
        else:
            self.worker.submit(profile,points,data['revision'])
        self.active=True
        self.return_required=profile.get('return_to_start', False)
        self.revision=data['revision']
        self.profile_hash=fingerprint(profile)
        self.stop_reason=''
        self.faulted=False
        self.fault_kinds.clear()
        self.start_requested=False
        self.report={'state':'PREPARING'}
        c.clear_plan()
        c.result='NONE'

    def reset_terminal(self):
        """A newly accepted photo starts a new cycle, never resumes old motion."""
        if self.active:
            raise ValueError('运行期间不能清除无接触执行状态')
        self.report={'state':'NONE'}
        self.revision=None
        self.profile_hash=None
        self.return_required=False
        self.stop_reason=''
        self.faulted=False
        self.fault_kinds.clear()
        self.start_requested=False

    def check_stop(self):
        c=self.core
        if self.active or c.state!='IDLE' or not c.robot_valid(c.clock()) or not c.device.observation_ready:
            raise ValueError('须在真实机械臂空闲、停稳且反馈有效时检查停止通路')
        if self.review and self.review.future and not self.review.future.done():
            raise ValueError('候选路线计算期间不能检查停止通路')
        self.worker.request_stop_check()

    def start(self,data):
        c=self.core
        if not self.active or self.report.get('state')!='READY': raise ValueError('无接触路线尚未就绪')
        try:
            if fingerprint(self.effective_profile())!=self.profile_hash:
                raise ValueError('现场配置已变化，须重新准备路线')
            errors=self.blockers()
            if errors: raise ValueError('；'.join(errors))
            c.confirmation.final_surface_points(self.revision,c.confirmation.value['requested_points'])
            # The worker can move before the next status poll. Even an uncertain
            # START acknowledgment must never qualify for pre-start recovery.
            self.start_requested=True
            if not self.worker.start_route(data['plan_sha256']): raise ValueError('路线版本不匹配或启动已取消')
        except Exception:
            self.stop('开始前校验失败')
            raise

    def stop(self,reason='操作员停止',fault=False,fault_kind='OTHER'):
        if self.active:
            if not self.faulted: self.stop_reason=reason
            self.faulted=self.faulted or fault
            if fault: self.fault_kinds.add(fault_kind)
            self.worker.stop_route()
            self.core.state='STOPPING'
            self.core.step='NONE'

    def tick(self,now):
        if not self.active: return
        c=self.core
        if c.ui_at is None or now-c.ui_at>=c.cfg['ui_timeout_s']:
            self.stop('UI 连接中断或超时',fault=True,fault_kind='UI_CONNECTION')
        if not c.robot_valid(now) or c.device.estop or c.device.fault:
            self.stop('设备反馈失效或硬件故障',fault=True)
        if not c.vision_valid(now): self.stop('视觉连接或健康状态失效',fault=True)
        s=self.worker.execution_snapshot()
        self.report=copy.deepcopy(s)
        state=s.get('state')
        if state in ('PREPARING','CONNECTING','PREFLIGHT','READY') and not self.stop_reason:
            try:
                c.confirmation.check_capture()
                if c.confirmation.value is None or c.confirmation.value['revision']!=self.revision:
                    raise ValueError('坐标确认版本已失效')
            except (ValueError,KeyError,TypeError) as exc: self.stop(str(exc))
        if state=='READY' and not self.stop_reason: c.state='READY'
        elif state=='RUNNING' and not self.stop_reason:
            c.state='RUNNING'
            name=s.get('point','')
            c.step='DWELL' if s.get('step')=='DWELL' else ('APPROACH' if name.endswith('_APPROACH') else 'RETRACT' if name.endswith('_RETRACT') else 'TRANSFER')
        elif state=='STOPPING' or self.stop_reason: c.state='STOPPING'
        if state not in ACTIVE and state!='NONE':
            self.active=False
            c.confirmation.reset()
            c.scan_snapshot=None
            c.points=[]
            c.clear_plan()
            good=state in ('COMPLETED','STOPPED','CANCELLED_BEFORE_MOTION') and not self.faulted
            if state=='COMPLETED' and self.return_required and s.get('returned_to_start') is not True:
                good=False
                self.report.update(state='ERROR', error='本次路线要求返回起始点位，但尚未收到归位确认')
            stopped=s.get('stop_confirmed') is True or state=='CANCELLED_BEFORE_MOTION'
            c.state='IDLE' if good and stopped else 'ERROR'
            c.result='COMPLETED' if good and state=='COMPLETED' and stopped else 'STOPPED' if good and stopped else 'FAILED'
            c.error='' if c.state=='IDLE' else (self.stop_reason if self.faulted and stopped else self.report.get('error') or '实际停止未确认')
            # A page refresh while waiting for START cancels the old route.
            # Recover acquisition only after explicit no-motion and STOP proof;
            # running, unknown-stop, SDK and hardware failures remain latched.
            c.feedback_recoverable=bool(c.state=='ERROR'
                and self.fault_kinds=={'UI_CONNECTION'} and not self.start_requested
                and state=='STOPPED' and s.get('stop_confirmed') is True
                and s.get('motion_attempted') is False)
            c.recovery_requires_ui=c.feedback_recoverable
            c.recovery_since=c.recovery_last_tick=None

    def status(self):
        confirmation = self.core.confirmation
        reviewed = self.review.sync(confirmation,self.core.device.sample,self.profile_path) if self.review else None
        errors=self.blockers()
        authorization_available=False
        try:
            self.authorization_candidate()
            authorization_available=True
        except (OSError,ValueError,TypeError,KeyError) as exc:
            # An active route is not asking to authorize another route. Preserve
            # its existing execution gates rather than adding that status error.
            if reviewed is not None and not self.active:
                reason=str(exc)
                if reason=='本次 SDK 路线预检尚未通过或已失效' and reviewed:
                    reason=reviewed.get('reason') or reason
                if reason:
                    errors=[reason]+[error for error in errors if error!=reason]
        value = dict(kind='NONCONTACT_COMMISSIONING',available=not errors,blockers=errors,
                    active=self.active,phase=self.report.get('state','NONE'),
                    revision=self.revision,plan_sha256=self.report.get('plan_sha256'),
                    stop_confirmed=self.report.get('stop_confirmed',False),
                    returning_to_start=self.active and self.report.get('returning_to_start') is True,
                    returned_to_start=(self.report.get('state')=='COMPLETED' and self.core.result=='COMPLETED'
                        and self.report.get('stop_confirmed') is True and self.report.get('returned_to_start') is True),
                    targets=self.report.get('targets',[]),error=self.report.get('error',''),
                    authorization_available=authorization_available,
                    note='假人/工装无接触联调；不加热、不输出按摩力')
        if not errors:
            value['note']='本次路线已按现场无障碍确认放行；沿用现有标定，10 mm/s 以内无接触过渡，不加热、不输出按摩力'
        if hasattr(self.worker,'stop_check_snapshot'):
            value['stop_preflight']=self.worker.stop_check_snapshot()
        if reviewed is not None: value['route_review']=reviewed
        if confirmation.scene and confirmation.value and confirmation.value['state'] == 'CONFIRMED':
            scene = copy.deepcopy(confirmation.scene)
            try:
                profile = self.effective_profile()
                scene.update(workspace_base_mm=profile['workspace_base_mm'],
                             safe_transfer_height_base_mm=profile['safe_transfer_height_base_mm'])
            except ValueError:
                scene.update(workspace_base_mm=None,safe_transfer_height_base_mm=None)
            value['scene_analysis'] = scene
        return value
