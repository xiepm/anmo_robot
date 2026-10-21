"""One SDK owner for telemetry and opt-in, reviewed no-contact waypoints."""
import copy
import math
import threading
import queue
import time
from concurrent.futures import Future
from .base_coordinates import ReferenceReader
from .real_device import FeedbackWorker
from .waypoint_motion import MotionReader, WaypointSession
from .noncontact_plan import compile_route, reviewed_execution
from .waypoint_plan import digest
from .stop_preflight import initial_report, check_stop


class ReferenceMotionReader(ReferenceReader, MotionReader):
    allowed_commands = ReferenceReader.allowed_commands | MotionReader.allowed_commands | {'PCS2ACS'}

    def review_math(self, request):
        """Vendor mathematical queries only: never moves, enables or stops."""
        if request['kind'] == 'rotation':
            return self.rotation_for(request['angles'])
        if request['kind'] != 'ik': raise ValueError('未知路线数学查询')
        q, pose = [], []
        rc = self.sdk.HRIF_GetInverseKin(0, 0, request['pose'], request['seed'], [0.]*6, [0.]*6, q)
        if rc or len(q)!=6: raise ValueError(f'控制器逆解失败：{rc}')
        q = list(map(float,q))
        if not all(map(math.isfinite,q)): raise ValueError('控制器逆解包含无效关节角')
        rc = self.sdk.HRIF_GetForwardKin(0, 0, q, [0.]*6, [0.]*6, pose)
        if rc or len(pose)!=6: raise ValueError(f'控制器正解复核失败：{rc}')
        pose = list(map(float,pose))
        if not all(map(math.isfinite,pose)): raise ValueError('控制器正解包含无效坐标')
        return dict(joints_deg=q,pose=pose)

    def rotation_for(self, angles):
        values = []
        rc = self.sdk.HRIF_RPY2Quaternion(0, *angles, values)
        if rc or len(values)!=4: raise ValueError('SDK 姿态换算失败')
        w,x,y,z = map(float,values)
        norm = math.sqrt(w*w+x*x+y*y+z*z)
        if not math.isfinite(norm) or abs(norm-1)>.01: raise ValueError('SDK 四元数无效')
        w,x,y,z = (v/norm for v in (w,x,y,z))
        return [[1-2*(y*y+z*z),2*(x*y-z*w),2*(x*z+y*w),0],
                [2*(x*y+z*w),1-2*(x*x+z*z),2*(y*z-x*w),0],
                [2*(x*z-y*w),2*(y*z+x*w),1-2*(x*x+y*y),0],[0,0,0,1]]


class BorrowedReader:
    """A waypoint session borrows the worker connection, never opens a second one."""
    def __init__(self,reader): self.reader=reader
    @property
    def connected(self): return self.reader.connected
    def connect(self):
        if not self.reader.connected:
            self.reader.close()
            self.reader.connect()  # Only session's best-effort STOP may reconnect.
    def close(self): pass  # Lifetime belongs to FeedbackWorker.
    def sample(self): return self.reader.sample()
    def move(self,*a,**kw): return self.reader.move(*a,**kw)
    def stop(self): return self.reader.stop()
    def read_override(self): return self.reader.read_override()
    def set_override(self,value): return self.reader.set_override(value)


class NoncontactWorker(FeedbackWorker):
    def __init__(self,config,sdk_path,log):
        # Bound SDK I/O so the queued stop can be processed promptly.
        super().__init__(dict(config,read_timeout_s=.15),sdk_path,ReferenceMotionReader)
        self.job_lock=threading.Lock()
        self.job=None
        self.session=None
        self.job_status={'state':'NONE'}
        self.cancel_job=False
        self.log=log
        self.targets=[]
        self.math_jobs=queue.Queue(maxsize=4)
        self.work_ready=threading.Event()
        self.stop_check=initial_report()
        self.stop_check_pending=False

    def request_stop_check(self):
        with self.job_lock:
            if self._stop.is_set() or self.job is not None or self.session is not None:
                raise ValueError('存在执行任务或控制线程已停止，不能启动停止检查')
            if self.stop_check['state']=='CHECKING' or not self.math_jobs.empty():
                raise ValueError('另一个检查正在进行')
            self.stop_check=initial_report('CHECKING')
            self.stop_check_pending=True
        self.work_ready.set()

    def stop_check_snapshot(self):
        with self.job_lock: return copy.deepcopy(self.stop_check)

    def review_math(self, request):
        future=Future()
        if self._stop.is_set(): raise ValueError('控制采集线程已停止')
        self.math_jobs.put_nowait((copy.deepcopy(request),future))
        self.work_ready.set()
        try: return future.result(timeout=2.)
        finally:
            future.cancel()  # Timed-out queued queries must not run later.

    def submit(self,profile,points,revision,*,reviewed=None,authorization=None):
        with self.job_lock:
            if self.job is not None or self.session is not None or self.stop_check['state']=='CHECKING':
                raise ValueError('已有无接触任务或停止检查')
            self.job=copy.deepcopy((profile,points,revision,reviewed,authorization))
            self.cancel_job=False
            self.targets=[]
            self.job_status={'state':'PREPARING','revision':revision}
        self.work_ready.set()

    def execution_snapshot(self):
        with self.job_lock: return copy.deepcopy(self.job_status)

    def start_route(self,sha):
        with self.job_lock:
            if not self.session or self.job_status.get('plan_sha256')!=sha: return False
            return self.session.request_start()

    def stop_route(self):
        with self.job_lock:
            self.cancel_job=True
            if self.session: self.session.request_stop()
        self.work_ready.set()

    def _priority_work_pending(self):
        # Called with job_lock held. A math item already in flight cannot be
        # preempted, but no subsequent one may delay STOP or execution work.
        return (self._stop.is_set() or self.job is not None or self.session is not None
                or self.stop_check_pending or self.stop_check['state']=='CHECKING')

    def _process_math_item(self,reader):
        with self.job_lock:
            if self._priority_work_pending(): return False
            try: request,future=self.math_jobs.get_nowait()
            except queue.Empty: return False
            run=future.set_running_or_notify_cancel()
        # Never hold job_lock during SDK I/O: STOP must remain nonblocking.
        if run:
            try: future.set_result(reader.review_math(request))
            except Exception as exc: future.set_exception(exc)
        return True

    def wait_for_next_poll(self,reader,deadline):
        """Use only this owner's idle time for serial, bounded IK/FK queries.

        Keep the next feedback deadline fixed. One in-flight mathematical query
        retains the existing SDK timeouts; never begin another after deadline.
        """
        while not self._stop.is_set():
            # Clear before checking queue/priority state. A producer arriving
            # after either check leaves the event set, so no wakeup is lost.
            self.work_ready.clear()
            with self.job_lock:
                if self._priority_work_pending(): return
            if time.monotonic()>=deadline: return
            if self._process_math_item(reader): continue
            remaining=deadline-time.monotonic()
            if remaining<=0: return
            self.work_ready.wait(remaining)

    def process_work(self,reader):
        with self.job_lock:
            checking=self.stop_check_pending
            self.stop_check_pending=False
            report=copy.deepcopy(self.stop_check)
        if checking:
            result=check_stop(reader,report,self._store,self.log,self._stop.is_set)
            with self.job_lock: self.stop_check=result
            return
        # Keep one normal-cycle slot; additional queries can use only the idle
        # interval below. All SDK access remains on this feedback thread.
        self._process_math_item(reader)
        with self.job_lock:
            job,self.job=self.job,None
        if job is None: return
        profile,points,revision,reviewed,authorization=job
        def record(message):
            self.log(dict(confirmation_revision=revision,**message))
            if message['type']=='SAMPLE': self._store(message['sample'])
            if message['type']=='WAYPOINT_STATUS':
                with self.job_lock:
                    self.job_status=dict(message,revision=revision,targets=copy.deepcopy(self.targets))
        try:
            sample=reader.sample()
            actual=sample['tcp_pose']['xyz_mm']+sample['tcp_pose']['rxyz_deg']
            if reviewed is not None:
                expected=reviewed['inputs']['sample']['joint_positions_deg']
                joints=sample['joint_positions_deg']
                if len(joints)!=6 or len(expected)!=6 or any(not math.isfinite(q) or not math.isfinite(r) or abs(q-r)>.05 for q,r in zip(joints,expected)):
                    raise ValueError('当前关节位置与 SDK 候选路线起点不一致')
                plan,targets=reviewed_execution(reviewed,profile,points,revision,actual,authorization=authorization)
            elif profile.get('geometry_source')=='FROZEN_DEPTH':
                raise ValueError('自动场景缺少已复核的候选路线，拒绝重新生成执行路线')
            else:
                plan,targets=compile_route(profile,points,actual,reader.rotation_for(actual[3:]))
            self.targets=targets
            self.log(dict(type='NONCONTACT_FINAL_POINTS',revision=revision,points=points,targets=targets,
                          profile=profile,plan_sha256=digest(plan),authorization=authorization))
            extra={}
            if profile.get('temporary_speed_override') is not None:
                if authorization is None: raise ValueError('临时速度倍率需要本次路线授权')
                extra['temporary_speed_override']=profile['temporary_speed_override']
            session=WaypointSession(plan,BorrowedReader(reader),record,**extra)
            with self.job_lock:
                self.session=session
                if self.cancel_job or self._stop.is_set(): session.request_stop()
            session.run()
        except Exception as exc:
            with self.job_lock: self.job_status=dict(state='ERROR',error=str(exc),revision=revision,stop_confirmed=False)
        finally:
            with self.job_lock: self.session=None

    def close(self):
        self.stop_route()
        self._stop.set()
        self.work_ready.set()
        # Do not tear down the SDK while the single owner is confirming STOP.
        if self.thread: self.thread.join(timeout=8)
        if self.thread and self.thread.is_alive():
            raise RuntimeError('无接触工作线程未退出，实际停止未知；请使用现场停止装置')
