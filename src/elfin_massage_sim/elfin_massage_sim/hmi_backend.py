"""ROS adapter for review-before-execute plans and timestamped frame mapping."""
import time
import json
from collections import deque
import numpy as np
from rclpy.time import Time
from rclpy.clock import Clock,ClockType
from rclpy.qos import qos_profile_sensor_data
from rclpy.qos import QoSProfile,DurabilityPolicy
from sensor_msgs.msg import Image,CameraInfo
from geometry_msgs.msg import TransformStamped
from std_msgs.msg import String
from tf2_ros import TransformBroadcaster,TransformListener
from scipy.spatial.transform import Rotation
from message_filters import Subscriber,ApproximateTimeSynchronizer
from .click_camera import ClickCamera
from .frames import HUMAN_FRAME,matrix,points,inverse,pixel_mapping,same_pose,validate_body_path
from .distance import camera_body_distance
from .registration import VISION_FRAME,estimate_rgbd,compare_reference,registered_model

class HmiNode(ClickCamera):
    def __init__(self):
        super().__init__(gui=False)
        self.declare_parameter('sensing_mode','dual')
        self.sensing_mode=self.get_parameter('sensing_mode').value
        if self.sensing_mode not in ('dual','wrist'):raise ValueError('无效的相机模式')
        expected='/camera' if self.sensing_mode=='wrist' else '/global_camera'
        if self.get_parameter('camera_namespace').value!=expected:raise ValueError('相机订阅与 sensing_mode 配置不一致')
        self.wrist=None;self.display_snapshots={};self.events=[]
        self.frame_queues={'global':deque(maxlen=8),'wrist':deque(maxlen=8)}
        self.registration=None;self.registration_revision=0;self.registered_display_model=None
        self.range_received={};self.range_status={};self.range_clock=None
        self.range_queues={source:deque(maxlen=8) for source in ('wrist','global')}
        self.range_pub=self.create_publisher(String,'/massage/camera_distances',1)
        self.estimated_tf=TransformBroadcaster(self)
        self.registration_pub=self.create_publisher(String,'/human_registration/status',
            QoSProfile(depth=1,durability=DurabilityPolicy.TRANSIENT_LOCAL))
        self.registration_pub.publish(String(data=json.dumps({'valid':False,'reason':'not_registered'})))
        self.wrist_subs=[]
        if self.sensing_mode=='dual':
            self.wrist_subs=[Subscriber(self,t,'/camera/'+name,qos_profile=qos_profile_sensor_data)
                             for t,name in [(Image,'image_raw'),(Image,'depth/image_raw'),(CameraInfo,'camera_info')]]
            self.wrist_sync=ApproximateTimeSynchronizer(self.wrist_subs,10,.04)
            self.wrist_sync.registerCallback(self.receive_wrist)
        # Ranging requires depth + intrinsics, not RGB delivery. Reuse existing subscribers.
        primary='wrist' if self.sensing_mode=='wrist' else 'global'
        self.range_syncs=[]
        for source,subscribers in [(primary,self.subs)]+([('wrist',self.wrist_subs)] if self.wrist_subs else []):
            sync=ApproximateTimeSynchronizer(subscribers[1:],10,.04)
            sync.registerCallback(lambda depth,info,source=source:self.receive_range(source,depth,info))
            self.range_syncs.append(sync)
        self.plan_body_pose=None;self.spec=None;self.preview_world=None
        self.actual_trace=[];self.execution_started=None;self.execution_result=None
        self.create_timer(.05,self.monitor,clock=Clock(clock_type=ClockType.STEADY_TIME))
        self.create_timer(.2,self.update_ranges,clock=Clock(clock_type=ClockType.STEADY_TIME))
        # Keep transform reception independent of Qt rendering and image callbacks.
        self.listener.unregister()
        self.listener=TransformListener(self.tf,None,spin_thread=True)

    def destroy_node(self):
        self.listener.executor.shutdown()
        self.listener.dedicated_listener_thread.join()
        self.listener.unregister()
        self.listener.node.destroy_node()
        return super().destroy_node()

    def notice(self,text):
        self.status=text
        if hasattr(self,'events'):self.events.append((time.strftime('%H:%M:%S'),text));self.events=self.events[-100:]
        self.get_logger().info(text)

    def receive_wrist(self,rgb,depth,info):
        if (rgb.width,rgb.height)!=(depth.width,depth.height) or (info.width,info.height)!=(depth.width,depth.height):return
        if rgb.header.frame_id!=depth.header.frame_id or depth.header.frame_id!=info.header.frame_id:return
        if depth.encoding not in ('32FC1','16UC1'):return
        self.wrist=(rgb,depth,info)
        self.frame_queues['wrist'].append(self.wrist)

    def receive(self,rgb,depth,info):
        super().receive(rgb,depth,info)
        if self.latest is not None and self.latest[0] is rgb:
            source='wrist' if self.sensing_mode=='wrist' else 'global'
            self.frame_queues[source].append(self.latest)
            if source=='wrist':self.wrist=self.latest

    def display_frame(self,source):
        for snapshot in reversed(self.frame_queues[source]):
            stamp=Time.from_msg(snapshot[1].header.stamp)
            age=(self.get_clock().now()-stamp).nanoseconds/1e9
            if -.05<=age<=.5 and self.tf.can_transform('world',snapshot[2].header.frame_id,stamp):
                return snapshot
        return None

    def receive_range(self,source,depth,info):
        if (depth.width,depth.height)!=(info.width,info.height) or depth.header.frame_id!=info.header.frame_id:return
        if depth.encoding not in ('32FC1','16UC1'):return
        self.range_queues[source].append((depth,info))
        self.note_range_frame(source,depth)

    def range_frame(self,source):
        for depth,info in reversed(self.range_queues[source]):
            stamp=Time.from_msg(depth.header.stamp)
            age=(self.get_clock().now()-stamp).nanoseconds/1e9
            if -.05<=age<=.5 and self.tf.can_transform('world',info.header.frame_id,stamp):return depth,info
        return None

    def note_range_frame(self,source,depth):
        stamp=Time.from_msg(depth.header.stamp).nanoseconds
        previous=self.range_received.get(source)
        if previous is None or previous[0]!=stamp:self.range_received[source]=(stamp,time.monotonic())

    def camera_distance(self,source):
        base={'source':source,'valid':False,'surface_valid':False,'origin_distance_m':None,
              'surface_range_m':None,'static_body_assumption':True}
        if self.registration is None:return dict(base,reason='未配准人体')
        clock_state=getattr(self,'range_clock',None)
        if clock_state is not None and time.monotonic()-clock_state[1]>.7:
            return dict(base,reason='仿真时钟停止 / 仿真暂停')
        receipt=self.range_received.get(source)
        if receipt is None or time.monotonic()-receipt[1]>.7:
            return dict(base,reason='图像停止更新 / 仿真暂停')
        snapshot=self.range_frame(source)
        if snapshot is None:return dict(base,reason='等待新鲜深度和曝光时刻 TF')
        try:
            depth,info=snapshot;stamp=Time.from_msg(depth.header.stamp)
            camera=matrix(self.tf.lookup_transform('world',info.header.frame_id,stamp))
            result=camera_body_distance(self.bridge.imgmsg_to_cv2(depth,'passthrough'),info.k,
                                       camera,self.body_pose(),depth.encoding)
            return dict(base,**{k:v for k,v in result.items() if k not in base},
                        valid=True,origin_distance_m=result['origin_distance_m'],
                        surface_range_m=result['surface_range_m'],surface_valid=result['surface_valid'],
                        stamp_ns=stamp.nanoseconds,age_s=(self.get_clock().now()-stamp).nanoseconds/1e9,
                        registration_revision=self.registration_revision,reason='')
        except Exception as exc:return dict(base,reason=str(exc))

    def update_ranges(self):
        now=self.get_clock().now().nanoseconds
        if self.range_clock is None or self.range_clock[0]!=now:self.range_clock=(now,time.monotonic())
        sources=['wrist'] if self.sensing_mode=='wrist' else ['wrist','global']
        self.range_status={source:self.camera_distance(source) for source in sources}
        self.range_pub.publish(String(data=json.dumps(self.range_status,allow_nan=False)))

    def body_pose(self,stamp=Time()):
        if self.registration is None:raise ValueError('请先回观察位并执行 RGB-D 人体配准')
        return np.asarray(self.registration['T_world_human']).copy()

    def register_body(self,source):
        try:return self._register_body(source)
        except Exception as exc:
            if self.registration is None:
                self.registration_pub.publish(String(data=json.dumps({'valid':False,'reason':str(exc)})))
            raise

    def _register_body(self,source):
        if self.motion.busy:raise ValueError('先停止运动，再采集人体配准')
        if source not in ('global','wrist') or (self.sensing_mode=='wrist' and source!='wrist'):
            raise ValueError('当前配置没有该相机')
        self.invalidate();self.registration=None;self.registered_display_model=None
        self.registration_pub.publish(String(data=json.dumps({'valid':False,'reason':'registration_pending'})))
        snapshot=self.display_frame(source)
        if snapshot is None:raise ValueError('等待新鲜图像及曝光时刻 TF；末端模式请先回观察位')
        if not self.motion.ready():raise ValueError('模型或新鲜关节状态尚未就绪')
        rgb,depth,info=snapshot;stamp=Time.from_msg(depth.header.stamp)
        twc=matrix(self.tf.lookup_transform('world',info.header.frame_id,stamp))
        # The estimator receives image/depth/intrinsics/camera extrinsics only.
        result=estimate_rgbd(self.bridge.imgmsg_to_cv2(rgb,'bgr8'),self.bridge.imgmsg_to_cv2(depth,'passthrough'),info.k,twc,depth.encoding)
        self.registration_revision+=1
        result.update(valid=True,source=source,camera_frame=info.header.frame_id,stamp_ns=stamp.nanoseconds,
                      revision=self.registration_revision,T_world_camera=twc.tolist(),static_body_assumption=True)
        # Ground truth is read only AFTER fitting, exclusively for comparison display.
        reference=self.motion.model.transforms(self.motion.q)[HUMAN_FRAME]
        result['reference_comparison']=compare_reference(result['T_world_human'],reference)
        self.registered_display_model=registered_model(self.motion.model,result['T_world_human'])
        self.registration=result
        self.registration_pub.publish(String(data=json.dumps(result)))
        self.notice(f'视觉配准完成：{source}，拟合 RMS {result["fit_rms_mm"]:.2f} mm；人体位姿已锁定')
        return result

    def pick(self,source,u,v,snapshot):
        if snapshot is None:raise ValueError('相机尚无图像')
        rgb,depth,info=snapshot
        age=(self.get_clock().now()-Time.from_msg(depth.header.stamp)).nanoseconds/1e9
        if not -.05<=age<=.5:raise ValueError('画面已过期，请等待实时图像或恢复 Gazebo 播放')
        if not (0<=u<depth.width and 0<=v<depth.height):raise ValueError('像素越界')
        array=self.bridge.imgmsg_to_cv2(depth,'passthrough')
        z=float(array[v,u])*(.001 if depth.encoding=='16UC1' else 1.)
        stamp=Time.from_msg(depth.header.stamp)
        twc=matrix(self.tf.lookup_transform('world',info.header.frame_id,stamp))
        twh=self.body_pose(stamp)
        mapped=pixel_mapping(u,v,z,info.k,depth.width,depth.height,twc,twh)
        p=np.asarray(mapped['human_m'])
        if abs(p[0])>.09 or abs(p[1])>.13 or abs(p[2])>.008:
            raise ValueError('仅可选择人体背部中央平面，不能选择机械臂、头部或床面')
        mapped.update(source=source,frame_id=info.header.frame_id,
                      stamp_ns=stamp.nanoseconds,T_world_camera=twc.tolist(),T_world_human=twh.tolist())
        return mapped

    def prepare(self,body_path,clearance,speed):
        body_path=validate_body_path(body_path)
        if not self.motion.ready():raise ValueError('模型、关节反馈或控制器未就绪；请检查仿真播放状态')
        twh=self.body_pose()
        self.preview_world=None;self.actual_trace=[];self.execution_result=None
        self.plan_body_pose=twh.copy()
        self.spec={'human_id':'human_01','frame_id':VISION_FRAME,'body_path_m':body_path.tolist(),
                   'clearance_m':float(clearance),'speed_m_s':float(speed),'T_world_human':twh.tolist(),
                   'visual_registration':self.registration,'registration_revision':self.registration_revision}
        return self.motion.prepare_trace(body_path,clearance,speed,body_pose=twh)

    def invalidate(self):
        self.motion.invalidate_preview();self.preview_world=None;self.spec=None;self.plan_body_pose=None

    def preview(self):
        if self.motion.prepared is None:return None
        if self.preview_world is None:
            trace=next(s for s in self.motion.prepared if s.name=='TRACE')
            spline=trace.spline()
            self.preview_world=np.asarray([self.motion.model.fk(spline(t))[:3,3] for t in np.linspace(0,trace.times[-1],240)])
        return self.preview_world

    def execute(self):
        if self.spec is None or self.plan_body_pose is None or self.motion.prepared is None:return False
        if not same_pose(self.body_pose(),self.plan_body_pose):
            self.invalidate();raise ValueError('人体位置已改变，请重新规划')
        self.execution_started=time.monotonic();self.actual_trace=[];self.execution_result=None
        if not self.motion.execute_prepared():self.execution_started=None;return False
        return True

    def monitor(self):
        if self.registration is not None:
            t=self.body_pose();msg=TransformStamped();msg.header.stamp=self.get_clock().now().to_msg()
            msg.header.frame_id='world';msg.child_frame_id=VISION_FRAME
            msg.transform.translation.x,msg.transform.translation.y,msg.transform.translation.z=t[:3,3].tolist()
            q=Rotation.from_matrix(t[:3,:3]).as_quat()
            msg.transform.rotation.x,msg.transform.rotation.y,msg.transform.rotation.z,msg.transform.rotation.w=q.tolist()
            self.estimated_tf.sendTransform(msg)
        if self.plan_body_pose is not None:
            try:valid=same_pose(self.body_pose(),self.plan_body_pose)
            except Exception:valid=False
            if not valid:
                if self.motion.busy:self.motion.cancel()
                else:self.invalidate()
                self.notice('视觉配准已改变或失效：已取消执行，请重新规划')
                self.plan_body_pose=None
        if self.execution_started is not None and self.motion.q is not None:
            if self.motion.phase=='TRACE':
                world=self.motion.model.fk(self.motion.q)[:3,3]
                self.actual_trace.append({'t':time.monotonic()-self.execution_started,'world_m':world.tolist(),
                    'human_m':points(inverse(self.plan_body_pose),world).tolist() if self.plan_body_pose is not None else None})
            if not self.motion.busy:
                self.execution_result={'phase':self.motion.phase,'controller_error_code':self.motion.last_result_code,
                    'spec':self.spec,'stages':self.motion.history,'trace_samples':self.actual_trace,
                    'elapsed_s':time.monotonic()-self.execution_started}
                self.execution_started=None
