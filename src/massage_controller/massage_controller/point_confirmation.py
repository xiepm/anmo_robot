"""Versioned final surface coordinates. Deliberately separate from motion poses."""
import copy
import math
import logging
from .geometry import camera_to_base_m

class PointConfirmation:
    def __init__(self, core):
        self.core = core
        self.projector = None
        self.packet = self.value = self.pending = None
        self.scene = self.scene_context = None

    def reset(self, keep_capture=False):
        self.value = self.pending = None
        self.scene = self.scene_context = None
        if not keep_capture:
            self.packet = None

    def capture(self, packet):
        self.reset()
        self.packet = copy.deepcopy(packet['data'])

    def check_capture(self):
        d = self.check_frozen_capture()
        if not self.core.vision_valid(self.core.clock()):
            raise ValueError('设备或视觉状态不允许确认坐标')
        return d

    def check_frozen_capture(self):
        """Check photo integrity only; callers must separately gate live use."""
        c, d, now = self.core, self.packet, self.core.clock()
        if not d or not c.scan_snapshot or not self.projector or c.mode!='REAL':
            raise ValueError('没有可确认的本次照片坐标')
        if (c.state not in ('IDLE','READY') or not c.robot_valid(now) or c.device.moving
                or c.device.estop or c.device.fault or not c.device.observation_ready):
            raise ValueError('设备或视觉状态不允许确认坐标')
        if (c.scan_snapshot['frame_number'] != d['frame_number']
                or c.scan_snapshot['received_monotonic_s'] != d['frame_received_monotonic_s']):
            raise ValueError('照片坐标与当前冻结图像不一致')
        if not 0 <= now-d['frame_received_monotonic_s'] <= 300:
            raise ValueError('本次照片超过五分钟，请重新扫描')
        if self.projector.sha256 != d['calibration_sha256']:
            raise ValueError('标定已改变，请重新扫描')
        q = (c.device.sample or {}).get('joint_positions_deg',[])
        original = d['feedback_window']['joint_positions_deg']
        if len(q)!=6 or any(not math.isfinite(x) or abs(x-y)>.05 for x,y in zip(q,original)):
            raise ValueError('拍照后机械臂位置已改变，请重新扫描')
        return d

    def request(self, data):
        d = self.check_capture()
        if self.pending:
            raise ValueError('上次坐标确认尚未完成')
        # A new request must never retain an older confirmed route on failure.
        self.reset(keep_capture=True)
        self.core.clear_plan()
        self.core.state = 'IDLE'
        if (data['frame_number'],data['received_monotonic_s']) != (d['frame_number'],d['frame_received_monotonic_s']):
            raise ValueError('坐标确认不属于当前照片')
        available = {(p['name'],p['side']) for p in self.core.scan_snapshot['available_points']}
        keys = [(p['name'],p['side']) for p in data['points']]
        if len(set(keys))!=len(keys) or any(key not in available for key in keys):
            raise ValueError('确认点位含重复项或不属于本次识别')
        request = copy.deepcopy(data)
        if self.core.noncontact:
            context = self.core.noncontact.scene_context(data, d)
            if context is not None:
                request['scene_context'] = context
        self.value = dict(revision=data['revision'],frame_number=data['frame_number'],
            received_monotonic_s=data['received_monotonic_s'],state='PENDING',
            requested_points=copy.deepcopy(data['points']),points=[],error='',execution_eligible=False)
        self.pending = dict(start=self.core.clock(),data=request)
        self.core.outbox.append(dict(cmd='REFINE_POINTS',data=copy.deepcopy(request)))

    def consume(self, data):
        if not self.pending or data['revision'] != self.pending['data']['revision']:
            return  # STOP/clear/new capture retired this result.
        try:
            source = self.check_capture()
            req = self.pending['data']
            if (data['frame_number'],data['received_monotonic_s']) != (req['frame_number'],req['received_monotonic_s']):
                raise ValueError('返回坐标不是本次确认照片')
            if not data['ok']:
                raise ValueError(data['error'] or '调整点深度重算失败')
            if len(data['points'])!=len(req['points']):
                raise ValueError('返回点位数量与最终路线不一致')
            final = []
            for p, expected in zip(data['points'],req['points']):
                if {k:p[k] for k in ('name','side','x','y')} != expected:
                    raise ValueError('返回点位位置或顺序与最终调整不一致')
                xyz = p['camera_point_mm']
                if xyz[2]<=0:
                    raise ValueError('深度无效')
                base = [v*1000 for v in camera_to_base_m(xyz,source['feedback_window']['T_base_reference'],
                                                       self.projector.calibration['T_reference_camera'])]
                final.append(dict(copy.deepcopy(p),base_point_mm=base))
            context = req.get('scene_context')
            if context and data.get('scene'):
                from .noncontact_plan import fingerprint
                scene = data['scene']
                if (scene['context_sha256'] != fingerprint(context) or any(
                    scene[k] != context[k] for k in ('revision','frame_number','received_monotonic_s','calibration_sha256'))):
                    raise ValueError('场景分析与本次照片、变换或确认版本不匹配')
                self.scene, self.scene_context = copy.deepcopy(scene), copy.deepcopy(context)
                logging.getLogger('control_node').info(
                    'SCENE_ANALYSIS frame=%s revision=%s state=%s unknown=%s/%s ceiling_base_mm=%s',
                    scene['frame_number'], scene['revision'], scene['state'], scene['unknown_cells'],
                    scene['corridor_cells'], scene['surface_ceiling_base_mm'])
            self.value.update(state='CONFIRMED',points=final,error='')
        except (ValueError,KeyError,TypeError) as exc:
            self.value.update(state='FAILED',points=[],error=str(exc))
            self.scene = self.scene_context = None
        self.pending = None

    def tick(self):
        c = self.core
        if not c.vision_valid(c.clock()) and c.preserve_photo_for_preview_delay(c.clock()):
            return
        if not self.value or self.value['state']=='FAILED':
            # Even a photo awaiting its first/repeated confirmation must retire
            # on observed movement, disconnect, calibration change or expiry.
            if self.packet and c.scan_snapshot:
                try:
                    self.check_capture()
                except (ValueError,KeyError,TypeError) as exc:
                    c.invalidate(str(exc))
            return
        try:
            self.check_capture()
        except (ValueError,KeyError,TypeError) as exc:
            self.value.update(state='FAILED',points=[],execution_eligible=False,error=str(exc))
            self.scene = self.scene_context = None
            self.pending = None
            self.packet = None
            c.scan_snapshot = None
            self.core.clear_plan()
            return
        timeout = 30 if self.pending and 'scene_context' in self.pending['data'] else 5
        if self.pending and self.core.clock()-self.pending['start']>timeout:
            # A bounded REFINE RPC timeout does not invalidate immutable pixels
            # or depth. Fresh health/integrity were checked above; retry requires
            # a new explicit confirmation and cannot accept this late result.
            self.value.update(state='FAILED',points=[],execution_eligible=False,error='确认坐标超时，请重新确认')
            self.scene = self.scene_context = None
            self.pending = None
            self.core.clear_plan()

    def final_surface_points(self, revision, route):
        """Only the exact, latest confirmed order may be handed to a path planner.
        These are surface locations, NOT tool-compensated movement targets.
        """
        self.check_capture()
        v = self.value
        if not v or v['state']!='CONFIRMED' or revision!=v['revision']:
            raise ValueError('最终坐标版本未确认或已失效')
        keys = lambda pts: [(p['name'],p['side']) for p in pts]
        if keys(route)!=keys(v['points']):
            raise ValueError('方案路线与最终确认坐标不一致')
        return copy.deepcopy(v['points'])
